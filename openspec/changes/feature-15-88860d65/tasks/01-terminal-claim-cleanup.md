# Task: Clean up finished claims outside Done and delete their Fly images

## Goal

Stop factory and eval leftovers from filling the Mac's disk, and stop old `claim-` tags from
piling up in the Fly registry (issue #15).

When this task is done:

- **Workspaces.** Terminal claims (settled, cancelled, superseded) release their recorded
  worktrees, clones, run-specific images, and credential copies after a lifecycle-specific idle
  period, even when their card never reaches Done or has left the board.
- **Evidence.** Their evidence is pruned, now including each eval repetition's
  `.runtime/candidate-worktree`.
- **Fly images.** Each Fly eval claim's registry image is deleted once no work can use it.
- **Status.** `status` summarises all of this in aggregate.

Active, waiting, and blocked claims are never touched.

## Background

Read these first, in the change directory `openspec/changes/feature-15-88860d65/`:

- `design.md`, which is the implementation blueprint: every section of "Approach" (§1–§7 and
  §4a), "Decisions", "Risks / Trade-offs", and "Migration Plan";
- `specs/factory-operations/spec.md`;
- `specs/factory-eval-execution/spec.md`;
- `specs/factory-eval-reporting/spec.md`;
- `specs/factory-fly-execution/spec.md`;
- `test-plan.md`;
- `decisions.md`, for the rationale behind each choice.

Where things are (paths relative to `src/agent_factory/`):

- **`retention.py`:**
  - `reconcile` (Done observation and pruning);
  - `_eligible` (guards; today it exempts cancelled and superseded claims from `complete`);
  - `_prune` and `_removal_targets`;
  - `_EVAL_REP_REMOVE` and `_FIX_ATTEMPT_REMOVE`;
  - `eval_rep_targets` and `pull_request_attempt_targets`.
- **`runtime.py` `run_cycle`:**
  - `list_project_items` raises on any page failure;
  - the per-card loop calls `retention.reconcile` first. It then skips superseded claims with
    `continue`, calls `review_round`, and calls `_report`, which delivers events and needs a
    factory-owned card. `handler.cleanup` runs last;
  - there is no store-driven pass today.
- **`suites/and_scene/__init__.py`:**
  - `WorktreeCleanup` (`record`, `reconcile`: Review-then-Done, settled only);
  - `AndSceneAdapter.__init__` and `review_handoff` (the text "Moving the item to Done
    releases the retained suite worktree, reviewed or not.").
- **`work_kinds/pull_request/cleanup.py`:**
  - `PullRequestCleanup.reconcile`, which returns early when `complete` is true and has an
    immediate path for cancelled claims;
  - `_release`, `_remove_credential_copies`, and `_remove_tree`, which tolerates read-only
    files.
- **`work_kinds/eval/handler.py`:**
  - `EvalHandler.cleanup`, `from_config`, and `attach_store`;
  - `report_events` (event key `<unit>:review-command`);
  - `_claim_image_digest` and `_fly_image_build`, which read `run.progress["image_build"]` or
    `<evidence>/.factory/image-build.json`.
- **`work_kinds/eval/publication.py`:** `publish_eval_results` (`results_final`, and
  `terminal = cancelled/superseded`), `_signature`, and `_snapshot`.
- **`work_kinds/base.py`:** the `WorkKindHandler.cleanup` protocol and `card_status`.
- **`work_kinds/images.py`:** `run_image_tags` and `remove_images`.
- **`fly/api.py`:** `FlyMachinesClient.resolve_manifest` (registry basic auth, user `x` and the
  token as password) and `FlyApiError(status, reason)`.
- **`fly/transport.py`:** `build_claim_image`, which writes `{repository, tag: claim-<12>,
  digest}`.
- **`fly/backend.py`:** Machine records `settings(runtime, "fly:machine:<run_id>")` with
  `claim_id`, cleared only when disposal is confirmed. A Machine stopped for a quota hold keeps
  its record. Do not add registry calls here.
- **`config.py`:** `LimitsConfig` and `_optional_positive_int`.
- **`operations.py`:** `_status_lines`, `_is_live`, and `_cleanup_lines`.
- **`store.py`:**
  - `set_cleanup`, `record_event` (deduped by key), `pending_events`, `all_claims`,
    `get_setting`, and `get_settings_by_prefix`;
  - `NONTERMINAL_RUN_STATUSES`.
- **Tests:**
  - `tests/integration/test_retention.py`, `test_fix_cleanup.py`, `test_fly_api.py`,
    `test_eval_result_publication.py`, `test_and_scene_adapter.py`, and
    `test_cli_operations.py`;
  - `tests/fixtures/fly/api.py` (`FakeMachinesApi`, which already serves registry manifest
    GETs);
  - `tests/e2e/test_factory_cycle.py` and `tests/e2e/test_fly_eval_cycle.py`.
- **Docs:**
  - `docs/operations.md`, sections "Service management and storage" and "Evidence retention";
  - the notes in `AGENTS.md` that point at issue #15 ("Fly eval images", "Disk space").

### Decisions to implement

The design names each of these.

**1. Configuration.**

- `LimitsConfig` gains `abandoned_retention_days` (default 3) and `settled_retention_days`
  (default 14).
- Both are optional positive integers under `[limits]`. Zero or negative values raise
  `ConfigurationError`.

**2. The idle clock.** In `retention.py`:

- `observe_terminal` records `cleanup.terminal_observed_at` the first time it sees a claim in
  `settled`, `cancelled`, or `superseded`.
- When it sees a non-terminal lifecycle while `terminal_observed_at` is set, it clears the
  following in the same `set_cleanup` write:
  - `terminal_observed_at`
  - `complete`
  - `released_by`
  - `last_error`
  - `review_observed`
  - `retention`
  - `results_final`
  - `size_estimate`
- It keeps `paths`, `fly_image`, and `done_observed_at`.
- It never acts retroactively: the first observation after upgrade starts the clock.

**3. `idle_due`.**

- For a cancelled or superseded claim: `abandoned_retention_days` has elapsed since
  `terminal_observed_at`.
- For a settled claim: `settled_retention_days` has elapsed, and either the card is not Done
  (or not on the board), or the card is Done but `cleanup.complete` is not true. The second
  case covers a claim never observed in Review.

**4. Eligibility for pruning.**

- Pruning happens when the Done path (today's check, but only when `on_board`) or the idle path
  is satisfied. A settled claim whose card is Done is judged by the Done path only.
- Every lifecycle needs all of the following:
  - no non-terminal runs;
  - no pending events or delivery failures;
  - no pending sync;
  - no `fly:machine:*` record for the claim;
  - `capture_settled(claim)`;
  - `cleanup.complete is True`.
- This replaces the cancelled and superseded exemption. Update the two existing retention tests
  that assert the exemption:
  - `test_superseded_claim_prunes_without_cleanup_complete`
  - `test_cancelled_claim_prunes_without_a_cleanup_pass`
- An off-board claim's `done_observed_at` is cleared.

**5. Pruning targets.**

- `_EVAL_REP_REMOVE` gains `.runtime/candidate-worktree`.
- `_prune` removes read-only trees, using the same `onexc` approach as `_remove_tree`.
- Kept records and unknown files are untouched.

**6. Capture.** In `publication.py`:

- Add `capture_finished(claim)`. It is true for:
  - cancelled or superseded claims;
  - claims with `done_observed_at`;
  - settled claims with `cleanup.released_by == "idle"`.
- `publish_eval_results` uses it in place of its current `terminal or done` test, so these
  claims get `results_final`.
- Add `capture_settled(store, shared, claim)`. It is true when any of these holds:
  - no results repository is configured;
  - the claim is not an eval;
  - `results_final` is true;
  - every consumed capturable run's `eval-publication.signature` equals its current
    `_signature`.
- A `waiting` snapshot blocks the guard only while `capture_finished` is false.

**7. Idle release.**

- `WorkKindHandler.cleanup(claim, *, board_status="", idle=False, on_board=True)`.
- **`WorktreeCleanup.reconcile(..., idle=True)`:**
  - for any terminal claim, it skips the Review-then-Done gate;
  - it removes the recorded worktrees and `run_image_tags`;
  - it sets `complete`, `last_error`, and `released_by="idle"`;
  - a claim with nothing recorded completes trivially.
- **`PullRequestCleanup.reconcile(..., idle=True)`:** for settled and superseded claims it calls
  `_release` and sets `released_by="idle"`. The cancelled path is unchanged.
- **In `runtime`:**
  - pass `idle` only when `idle_due` holds, no run is non-terminal, and no Machine is recorded,
    computed from the claim re-read after `review_round`;
  - call `handler.cleanup` for superseded claims before their `continue`.

**8. The lapse event.**

- After an idle release completes for a settled eval claim whose card is on the board and
  outside Done, `EvalHandler.cleanup` records the event `review-window-lapsed`. It does so only
  when `claim.reporting.events` has a key ending in `:review-command`.
- The event text states that the optional human-review window ended after the settled
  retention period and that the retained worktree was released.
- There is no event for off-board claims.
- Keep the loop order. The expected sequence is: release and queue on tick 1, post on tick 2,
  prune on tick 3.

**9. Handoff text.**

- `AndSceneAdapter(settled_retention_days=...)` is passed from `local.limits` in
  `EvalHandler.from_config`.
- The closing sentence says the command remains usable until the item moves to Done or until
  N days after the request settles while the item is outside Done, whichever comes first, and
  that either releases the retained suite worktree, reviewed or not.

**10. The off-board sweep.**

- After the per-card loop, `_sweep_off_board` iterates over `store.all_claims()` in order of
  oldest `terminal_observed_at`.
- It skips claims visited this tick, non-terminal claims, and claims already fully done
  (`retention.pruned_at` set, and `fly_image` complete or not applicable).
- It runs the same observe, reconcile, and cleanup steps with `on_board=False` and
  `board_status=""`.
- It is reached only when `list_project_items` returned.

**11. The per-tick budget.**

- One `CleanupBudget(removals=5, registry=5, measurements=2)` is created in `run_cycle` and
  shared by the per-card loop and the sweep. The on-board loop runs first.
- Each heavy step takes a unit just before acting and is skipped silently when none is left:
  - a prune or release, including the existing Done-path release;
  - a claim's registry calls;
  - a size measurement.
- Observations are always recorded.

**12. Fly image deletion.**

- **Client.** `FlyMachinesClient.delete_manifest(repository, digest)` sends
  `DELETE /v2/<repo>/manifests/<digest>` with the same basic auth as `resolve_manifest`.
  - 202 or 404 return normally.
  - Any other status raises `FlyApiError`, with the status, and the registry's `errors[0].code`
    as `reason`.
- **Policy.** A new `fly/images.py` provides
  `reconcile_claim_image(store, claim, local, *, client_factory, now, budget)`. It runs only
  for eval claims, and only when `local.fly` is set. It then:
  1. collects the claim's records whose `digest` matches `sha256:[0-9a-f]{64}` and whose
     `tag == f"claim-{claim.id[:12]}"`, de-duplicated. With no records it stores
     `fly_image={"state": "none"}`;
  2. checks the gate: the claim is terminal, no run is non-terminal, and no Machine is
     recorded;
  3. requires the record's repository to equal the repository derived from `[fly] image`.
     Anything else is a persistent mismatch;
  4. calls `resolve_manifest(f"{repository}:{tag}")`:
     - 404 gives `gone`;
     - a different digest gives a persistent `mismatch`, with no DELETE;
     - an equal digest leads to `delete_manifest`, giving `deleted`.
- **Stored state.** It stores
  `cleanup["fly_image"] = {state, digests, error, persistent, attempts, retry_after}`.
- **Transient failures:** status `None`, 401, 403, 429, or 5xx. They retry after
  `min(5 min × 2^(attempts-1), 6 h)`.
- **Persistent failures:** 405, `UNSUPPORTED`, or a mismatch. They are never retried
  automatically.
- **Where it runs.** `EvalHandler.cleanup` calls it on every visit, including superseded and
  off-board claims, and catches every exception. Never call it from `FlyBackend.dispose` or
  `reconcile`.

**13. Status.**

- **Measuring.** The tick measures a terminal claim's remaining release and prune targets once,
  using `os.scandir` without following symlinks, through the `measurements` budget. It stores
  `cleanup.size_estimate={bytes, measured_at}` and sets `bytes` to 0 after a prune.
- **The summary line.** `_status_lines` adds one line, for example
  `cleanup: 12 claims pending (~4.3 GiB across 7 measured, 5 not yet measured), 31 pruned, 2 failing`.
  - When every pending claim is measured, it reads `(~4.3 GiB)`.
  - When none is measured, it reads `(size not yet measured)`.
- **Failures.** `_cleanup_lines` also shows `retention.errors` and a failed `fly_image`.
- **Listing.** `_is_live` lists failing claims. No claim is listed only because its cleanup is
  pending. `status` never walks the filesystem.

**14. Docs.** Update:

- `docs/operations.md`: the new limits, the idle rule per lifecycle, off-board claims, the
  candidate worktree, Fly image deletion, how to clear a persistent `fly_image` failure, and the
  status line;
- the `AGENTS.md` Fly-image and disk-space notes, which should no longer say old tags are not
  removed.

## Spec

The normative requirements are in the four delta specs listed under Background. They are:

- **factory-operations:**
  - "Clean up worktrees after review" (modified);
  - "Retain evidence for a bounded period" (modified);
  - "Expose current operational status" (modified).
- **factory-eval-execution:** "Preserve suite-owned evidence and candidate outputs" (modified).
- **factory-eval-reporting:** "Provide executable human-review instructions" (modified).
- **factory-fly-execution:** "Delete a claim's image once it can no longer be used" (added).

Every scenario in those files must hold.

## Test Plan

Implement these automated obligations from `test-plan.md`, with test-driven development for the
unit-level logic in each:

- INT-001, INT-002, INT-003, INT-004, INT-005, INT-006, INT-007;
- E2E-001, E2E-002, E2E-003.

The fakes need these extensions:

- `FakeMachinesApi` gains manifest DELETE and scripted registry replies.
- The fake board gains a way to fail on a later page and to drop a card.

No automated test may touch `~/.agent-factory`, the live board, or the live registry. Agent
acceptance (AT-001 to AT-003) is an operator gate after this task.

## Done When

- Every scenario in the four delta specs is implemented and covered by the tests named above.
- The two existing retention tests are updated to the rule that `complete` is required on every
  lifecycle.
- `uv run pytest` passes, including the updated and new integration and e2e tests. So do
  `uv run ruff format --check . && uv run ruff check .` and `uv run pyright`.
- `docs/operations.md` and `AGENTS.md` describe the new cleanup and the configuration keys.
- `config/codagent.toml` carries no uncommitted pins. The new limits are local config with
  defaults, so no shared-config change is required.
