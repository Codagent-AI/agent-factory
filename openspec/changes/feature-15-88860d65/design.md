## Context

Cleanup is split across three places today, and all of them are gated on the Project board.

**Workspace release.**

- `WorktreeCleanup` in `src/agent_factory/suites/and_scene/__init__.py` releases an eval claim's
  Runner, Skills, and evals worktrees and its per-run Docker images. It acts only for a settled
  claim that was observed in Review and then in Done.
- `PullRequestCleanup` in `src/agent_factory/work_kinds/pull_request/cleanup.py` releases a fix or
  feature claim's clones, run images, and credential copies. It uses the same Review-then-Done
  gate, plus an immediate path for cancelled claims.
- Both record progress in `claim.cleanup`, using `review_observed`, `complete`, and `last_error`.
  Handlers expose this through `WorkKindHandler.cleanup(claim, board_status=...)`.

**Evidence pruning.**

- `retention.reconcile` in `src/agent_factory/retention.py` records `done_observed_at` and prunes
  the targets returned by `handler.retention_targets(run)`:
  - fixes and features: `pull_request_attempt_targets`;
  - evals: `eval_rep_targets`, built from `_EVAL_REP_REMOVE`.
- It prunes only while the card is Done.

**The per-card loop.** `runtime.run_cycle` iterates over `cards` from
`client.list_project_items`. That call pages through the whole board and raises on any GraphQL
failure, so a partial board never reaches the loop. For each claim of a card, the loop:

1. calls `retention.reconcile`;
2. skips superseded claims (`continue`), so `handler.cleanup` never sees them;
3. finally calls `handler.cleanup`.

Claims whose card has left the board are never visited.

**Eval results capture.** `publish_eval_results` in `work_kinds/eval/publication.py` commits each
consumed repetition's curated files. It records per-run `eval-publication` settings with the
file-stat signature and content digest. It sets `cleanup["results_final"]` only for Done,
cancelled, or superseded claims.

**Fly images.**

- `build_claim_image` in `src/agent_factory/fly/transport.py` writes
  `<evidence>/.factory/image-build.json` as `{repository, tag, digest}`.
- The launcher also copies that record into `run.progress["image_build"]`.
- `FlyMachinesClient.resolve_manifest` in `src/agent_factory/fly/api.py` already talks to
  `registry.fly.io`, using basic auth with the deploy token.
- A Machine is recorded in `settings(runtime, "fly:machine:<run_id>")` with its `claim_id` until
  disposal is confirmed. A Machine stopped for a quota hold keeps that record, with
  `decision: "stop"`.

**Measured state on 2026-09-27.**

- `~/.agent-factory/artifacts` holds 7.7 GiB, of which 6.06 GiB is 26 eval
  `.runtime/candidate-worktree` directories. Each is a standalone clone with a `.git` directory.
- Nothing has been pruned yet. 2 of the 50 terminal claims have an undelivered event.
- The registry holds 6 `claim-` tags. A DELETE for an unknown digest returns 404
  `MANIFEST_UNKNOWN`, which shows the route is handled.
- Retention limits live in the per-Mac `LocalConfig.limits`, not in shared config.

## Goals / Non-Goals

**Goals:**

- Release workspaces and prune evidence of terminal claims (settled, cancelled, superseded)
  whose cards never reach Done, after lifecycle-specific idle periods.
- Include `.runtime/candidate-worktree` in eval pruning. This covers most of the reclaimable
  bytes.
- Visit terminal claims whose card has left the board.
- Delete each Fly eval claim's registry image once no work can use it, without coupling this to
  Machine disposal.
- Keep `status` readable: an aggregate summary, with only failures listed individually.

**Non-Goals:**

- Changing Done-path timing.
- Pruning active, waiting, or blocked claims.
- Cleaning mirrors, releases, Docker Desktop, or registry tags the factory did not record.
- Cleanup driven by disk pressure.

## Approach

### 1. Idle clock and eligibility (`retention.py`)

Add to `retention.py`, alongside the existing Done logic:

```python
TERMINAL = frozenset({"settled", "cancelled", "superseded"})


def observe_terminal(cleanup: dict[str, object], lifecycle: str, now: datetime) -> bool:
    """Record the first terminal observation; on a non-terminal lifecycle, reset the cycle."""


def idle_due(claim: Claim, *, card_done: bool, now: datetime, limits: LimitsConfig) -> bool:
    """cancelled/superseded: now - terminal_observed_at >= abandoned_retention_days.
    settled: (not card_done or cleanup.complete is not True)
             and now - terminal_observed_at >= settled_retention_days."""


def machine_recorded(store: ClaimStore, claim_id: str) -> bool:
    """Any settings(runtime, 'fly:machine:*') record whose claim_id matches."""
```

**`reconcile`.** `reconcile(store, local, claim, board_status, now, *, handler, on_board,
capture_settled)` now does the following:

1. Calls `_observe_done`, but only when `on_board` is true. An off-board claim's Done
   observation is cleared, because it is not currently Done.
2. Calls `observe_terminal`.
3. Evaluates eligibility as `done_path or idle_path`:
   - `done_path` is today's check: the card is Done and `evidence_retention_days` has passed
     since `done_observed_at`.
   - `idle_path` is `idle_due(claim, card_done=(on_board and board_status == "Done"), ...)`.
4. Applies the shared guards:
   - no non-terminal runs;
   - no pending events or delivery failures;
   - no pending sync;
   - `not machine_recorded(...)`;
   - `capture_settled(claim)`;
   - `cleanup.get("complete") is True`, for **every** lifecycle.

Requiring `complete` for every lifecycle replaces today's exemption for cancelled and superseded
claims. Idle release, described next, now sets `complete` for them. A claim with nothing recorded
to release completes trivially.

**Reactivation resets the cleanup cycle.** A settled fix or feature claim can become active again
for a review round. When `observe_terminal` sees a non-terminal lifecycle while
`terminal_observed_at` is set, it clears these keys from `cleanup`:

- `terminal_observed_at`
- `complete`
- `released_by`
- `last_error`
- `review_observed`
- `retention`, which holds `pruned_at`, `removed`, and `errors`
- `results_final`
- `size_estimate`

Without this, two things go wrong:

- **Clones and tokens leak.** `PullRequestCleanup.reconcile` returns early whenever `complete` is
  true. The round's fresh clones and private `GH_TOKEN` copies, made by `prepare_review`, would
  never be released, which breaks the rule that a token must not outlive Done.
- **Pruning is skipped.** `retention._eligible` returns early whenever `pruned_at` is set, so the
  round's evidence would never be pruned.

The following keys are kept, because they are facts about work that is already finished and
cannot become stale:

- `paths`, the eval worktree record;
- `fly_image`;
- `done_observed_at`, which `_observe_done` already manages from the board.

Clearing is written in the same `set_cleanup` call as the lifecycle observation, so a restart
cannot leave a half-reset record.

**A settled claim in Done that was never seen in Review.** `idle_due` also covers this case: a
settled claim whose card is Done but whose Done cleanup never ran, because `review_observed` was
never set. After `settled_retention_days` it receives an idle workspace release. Its evidence is
then pruned by the Done path, which is judged on `done_observed_at` and needs `complete`. The
live store has 2 such fix claims today; without this path they would be pinned forever.

**Pruning targets.** `_EVAL_REP_REMOVE` gains `.runtime/candidate-worktree`. `_prune` already
removes directories with `shutil.rmtree`. It gains the same read-only-tolerant `onexc` handler
that `pull_request/cleanup.py::_remove_tree` uses, because `node_modules` can contain read-only
files.

**The capture guard.** `capture_settled` is built in `runtime` from a new function in
`publication.py`:

```python
def capture_settled(store: ClaimStore, shared: SharedConfig, claim: Claim, *, idle: bool) -> bool:
```

It returns true when any of these holds:

- no results repository is configured for the suite;
- the claim is not an eval;
- `cleanup["results_final"]` is true;
- every consumed run whose snapshot is capturable has an `eval-publication` record whose
  `signature` equals the run directory's current `_signature`.

**One shared definition of "finished for capture".** `publication.py` gains one predicate that
both `publish_eval_results` and `capture_settled` use:

```python
def capture_finished(claim: Claim) -> bool:
    """cancelled or superseded, or Done observed, or settled and idle-released."""
```

"Idle-released" means `cleanup.released_by == "idle"`.

- **In `publish_eval_results`,** this replaces today's
  `terminal or done_observed_at is not None` test. A settled claim that was idle-released, in
  Review or off the board, therefore gets `results_final` once its outstanding commits succeed.
  It stops being scanned on every tick, which keeps the rule that history does not grow the
  per-tick cost.
- **In the capture guard,** a `waiting` snapshot (a capturable status with missing curated files)
  blocks only while `capture_finished` is false. This stops one broken repetition from pinning a
  claim's disk forever.
- **Ordering.** Idle release sets `released_by` before pruning is judged, because pruning needs
  `complete`. The capture guard and publication therefore agree on every claim the prune step
  considers.

Pruning never touches the curated files, so pruning cannot change a run's signature.

### 2. Idle workspace release (handlers)

`WorkKindHandler.cleanup` gains a keyword: `cleanup(claim, *, board_status="", idle=False)`.
Runtime passes `idle=retention.idle_due(...) and not retention.machine_recorded(...) and no
non-terminal runs`.

**`WorktreeCleanup.reconcile(claim_id, *, board_status, idle=False)`.**

- When `idle` is true and the claim is terminal, it skips the Review-then-Done gate.
- It removes the recorded worktrees in `cleanup["paths"]` and the `run_image_tags`, using the
  existing `_recorded_worktrees` and `remove_images`.
- It sets `complete` and `last_error` exactly as the Done path does, and adds
  `released_by: "idle"`.
- A claim with no recorded paths is marked `complete` without doing anything.
- The existing settled-only early return still applies to the non-idle path.

**`PullRequestCleanup.reconcile(claim_id, *, board_status, idle=False)`.** When `idle` is true
and the claim is settled or superseded, it calls the existing `_release`. Cancelled claims keep
their immediate path.

**Superseded claims.** In `runtime`, the per-card loop moves `handler.cleanup(claim, ...,
idle=...)` before the superseded `continue`, for superseded claims only. Nothing else about
superseded handling changes.

**Review-window lapse event.** After an idle release completes for a settled eval claim whose
card is outside Done,
`EvalHandler.cleanup` records the event `review-window-lapsed`. It records it only when both of
these hold:

- the claim's `reporting.events` contains at least one `*:review-command` key, which is the
  event `report_events` posts with the human-review command;
- the card is on the board this poll.

The runtime passes this as an `on_board` keyword. `record_event` dedupes on the key, so the event
is recorded once per claim. An off-board claim gets no event: `_report` needs a factory-owned
card to deliver events, so an undeliverable event would pin the claim's evidence forever through
the pending-events guard. The event text states that the optional human-review window ended
after the settled retention period and that the retained worktree was released.

**Tick sequence for the lapse event.** The per-card loop runs three steps in order:

1. `retention.reconcile` (prune);
2. `_report` (posts events);
3. `handler.cleanup` (release).

The loop order is kept, so a lapsed settled eval claim moves one step per tick:

| Tick | What happens |
|---|---|
| 1 | Workspace released; lapse event queued |
| 2 | Prune skipped because the event is still pending; `_report` posts the event |
| 3 | Evidence pruned |

The one-tick gap is harmless. Reordering the loop would move release ahead of `review_round`
admission, which matters for the reactivation reasoning under Risks.

### 3. Handoff wording (`and_scene`)

`AndSceneAdapter.__init__` gains `settled_retention_days: int = 14`, passed from
`local.limits.settled_retention_days` in `EvalHandler.from_config`. The closing sentence of
`review_handoff` becomes:

> The command remains usable until the item moves to Done or until {N} days after the request
> settles while the item is outside Done, whichever comes first; either releases the retained
> suite worktree, reviewed or not.

### 4. Off-board sweep (`runtime.py`)

After the per-card loop, `run_cycle` calls a new function:

```python
_sweep_off_board(store, controller, local, shared, visited_claim_ids, now, capture_settled)
```

It is reached only when `list_project_items` returned, which means the poll succeeded.

1. It iterates over `store.all_claims()`.
2. It skips claims visited in the loop, claims that are not terminal, and claims already fully
   done: `retention.pruned_at` set and `fly_image` complete or not applicable.
3. For each remaining claim, it does the following, with `on_board=False` and
   `board_status=""`:
   - record observations: `observe_terminal`, and clear any `done_observed_at`;
   - call `retention.reconcile`;
   - call `handler.cleanup(claim, idle=...)`.
4. Claims are visited in order of oldest `terminal_observed_at`. Heavy work draws on the shared
   per-tick budget (§4a). Recording observations is cheap and is not limited.

### 4a. Per-tick cleanup budget

`run_cycle` creates one `CleanupBudget` per tick and passes it to the per-card loop and then to
the sweep:

```python
@dataclass
class CleanupBudget:
    removals: int = 5  # idle or Done workspace releases and evidence prunes, combined
    registry: int = 5  # claims whose Fly image deletion makes registry calls
    measurements: int = 2  # size estimates

    def take(self, kind: str) -> bool: ...
```

**What the budget gates:**

- `retention.reconcile` asks for a `removals` unit just before `_prune`;
- `handler.cleanup` asks for one just before any release;
- `reconcile_claim_image` asks for a `registry` unit;
- the size estimator asks for a `measurements` unit.

**When the budget is spent:**

- The step is skipped for this tick and nothing is recorded as a failure.
- Observations (Done, terminal, review) are always recorded.
- Existing Done-path cleanup of a claim just moved to Done also draws on the budget. It is a
  heavy removal like any other, and delaying it by a tick is harmless.

The on-board loop runs first, so on-board claims get the budget ahead of off-board ones.

**Worst case.** On day 3 after deploy, about 32 cancelled and superseded claims become eligible
at once. Each needs a release and a prune, so the backlog drains in about 13 ticks: roughly an
hour at the production 5-minute poll. Each tick does at most 5 heavy removals, about 1.2 GiB of
`rmtree` at worst, inside the cycle lock.

### 5. Fly image deletion

**Registry client.** `FlyMachinesClient` in `src/agent_factory/fly/api.py` gains:

```python
def delete_manifest(self, repository: str, digest: str) -> None:
    """DELETE /v2/<repo>/manifests/<digest>; 202 or 404 MANIFEST_UNKNOWN return normally."""
```

It uses the same basic-auth header as `resolve_manifest`. Any other HTTP status raises
`FlyApiError(status=..., reason=<registry error code>)`. The registry's `errors[0].code`, for
example `UNSUPPORTED`, is carried in `reason`.

**The deletion module.** A new module, `src/agent_factory/fly/images.py`, owns the policy:

```python
def reconcile_claim_image(store, claim, local, *, client_factory, now) -> None:
```

1. **Only when Fly is configured.** It runs only for eval claims, and only when `local.fly` is
   configured. Otherwise the state is left untouched.
2. **Collect records.** It gathers the claim's image records from `run.progress["image_build"]`,
   falling back to `<evidence>/.factory/image-build.json`. It keeps records whose `digest`
   matches `sha256:[0-9a-f]{64}` and whose `tag == f"claim-{claim.id[:12]}"`, and de-duplicates
   by digest. No records means `fly_image = {"state": "none"}`.
3. **Gate.** It checks that the claim is terminal, no run is non-terminal, and
   `machine_recorded` is false. A quota-hold stop is covered by the Machine record.
4. **Repository check.** A record's `repository` must equal the repository derived from
   `[fly] image`, which is the part before `:` or `@`. A different repository is a mismatch
   failure.
5. **Verify, then delete.** For each digest:
   - It calls `resolve_manifest(f"{repository}:{tag}")`.
   - A 404 means the digest is `gone`.
   - A different digest means `mismatch`: recorded, no delete, persistent.
   - An equal digest leads to `delete_manifest(repository, digest)`, and the digest becomes
     `deleted`.
6. **Persist.** The result is stored in `claim.cleanup["fly_image"]`:

```json
{"state": "complete|pending|failed", "digests": {"sha256:…": "deleted|gone|mismatch|unsupported|error"},
 "error": "...", "persistent": false, "attempts": 3, "retry_after": "<iso>"}
```

**Error handling.**

- Transient errors are `FlyApiError` with status `None` (network or timeout), 401, 403, 429, or
  5xx. They set `state: failed`, `persistent: false`, and schedule a retry.
- The retry uses exponential backoff: `retry_after = now + min(5 min × 2^(attempts-1), 6 h)`.
- A 405 or a registry code of `UNSUPPORTED` sets `persistent: true` with no retry. A mismatch
  also sets `persistent: true`.
- A persistent failure is not retried by the factory. The operator can clear
  `cleanup.fly_image` to force a retry.
- Deletions draw on the shared budget's `registry` units (§4a), and each registry call keeps the
  existing 20-second timeout.

**Where it is called.** `EvalHandler.cleanup` calls `reconcile_claim_image` on every visit: the
per-card loop, including superseded claims, and the sweep. The call is independent of idle
timing, and every exception is caught and recorded. It is never called from `FlyBackend.dispose`
or `reconcile`, so registry behavior cannot affect Machine disposal.

### 6. Configuration

`LimitsConfig` gains two fields, parsed with `_optional_positive_int`:

- `abandoned_retention_days: int = 3`
- `settled_retention_days: int = 14`

`docs/operations.md` and the example config document both.

### 7. Status summary (`operations.py`)

**The size estimate.** The factory estimates the local space held by pending claims during the
tick. `status` never walks the filesystem.

- When a terminal claim is visited and has not yet been measured, the tick sums file sizes under
  the claim's remaining release and prune targets, using `os.scandir` without following symlinks.
- It stores the sum in `cleanup["size_estimate"] = {"bytes": n, "measured_at": iso}`.
- It measures at most 2 claims per tick, using the budget's `measurements` units (§4a).
- After a successful prune, or an idle release with nothing else pending, it sets `bytes` to 0.
- The estimate is taken once and is not refreshed. Terminal claims do not grow, apart from a late
  `human-review.json`.

**The summary line.** `_status_lines` adds one aggregate line:

```text
cleanup: 12 claims pending (~4.3 GiB across 7 measured, 5 not yet measured), 31 pruned, 2 failing
```

The size figure sums only measured claims and always names how many are unmeasured, so a partial
sum is never shown as the whole. When every pending claim is measured, the clause shortens to
`(~4.3 GiB)`. When none is measured, it reads `(size not yet measured)`.

- **Pending:** terminal claims that are not fully done.
- **Pruned:** `retention.pruned_at` is set.
- **Failing:** any of `last_error`, `retention.errors`, or a failed `fly_image`.

**Listing claims.** `_is_live` keeps listing failing claims individually, and `_cleanup_lines`
gains the retention and Fly-image failures. Settled claims in Review stay listed as they are
today. No new claim is listed only because its cleanup is pending.

### Data flow per tick

```text
list_project_items ──► per-card loop (on_board=True)
                        ├─ retention.reconcile(observe Done/terminal → maybe prune)
                        ├─ handler.cleanup(idle=…)   ─► release worktrees/clones/images
                        │                           └► eval: reconcile_claim_image, lapse event
                        └─ (superseded: cleanup, then continue)
                     ──► _sweep_off_board (on_board=False)
   (one CleanupBudget shared by both passes gates every release, prune, registry call, measurement)
                        └─ same calls, no lapse event
```

## Decisions

- **The idle clock uses `terminal_observed_at` in `claim.cleanup`, not `claim.updated_at`.**
  `updated_at` changes on every cleanup write. The observation mirrors `done_observed_at`, so
  history recorded before the upgrade is never cleaned up retroactively.
- **`complete` is required on every lifecycle.** It replaces the cancelled and superseded
  exemption in `_eligible`. One flag governs "workspace released" for all claims, which makes the
  guard uniform and matches the spec's "any release due has settled".
- **Idle release is a flag on `handler.cleanup`.** No new handler method is needed. It keeps the
  protocol small and reuses each kind's audited removal code.
- **The lapse event is recorded only for claims whose card is on the board.** Events are
  delivered only through `_report`, which needs the card. An undeliverable event would block
  pruning permanently. This is written into the eval reporting delta.
- **Size estimates are cached in `cleanup` and measured in the tick.** This resolves the
  deferred-to-design marker: `status` stays read-only and fast.
- **Fly deletion verifies the tag before deleting by digest.** Deleting by digest removes every
  tag pointing at that manifest. Resolving the claim's own tag first proves the digest belongs to
  that claim. Unique `FACTORY_CLI_REFRESH` builds make sharing unlikely, but the check costs one
  GET.
- **Fly deletion lives in a new `fly/images.py` called from `EvalHandler.cleanup`.** Keeping it
  out of `FlyBackend` makes the isolation from disposal structural, not merely conventional.
- **Candidate worktrees are removed whole.** The directory is a standalone clone with its own
  `.git`, so no git metadata elsewhere refers to it. Its curated outputs, including
  `implementation.diff`, are captured before pruning.

- **Leaving a terminal lifecycle resets the whole cleanup cycle.** It clears `complete`,
  `released_by`, `last_error`, `review_observed`, `retention`, `results_final`, and
  `size_estimate`, not just the clock. The alternative was tracking, path by path, what each
  release removed. That is more state and more code, for the same outcome.
- **One `CleanupBudget` per tick is shared by the per-card loop and the sweep.** Separate
  per-pass limits would leave the on-board backlog, which is most of the bytes, unbounded.
- **Keep the loop order and accept a one-tick lag between the lapse event and pruning.**
  Reordering the loop would put release ahead of review-round admission.
- **One predicate, `capture_finished`, decides when a claim is finished for capture.**
  Publication and the capture guard use the same definition, so they cannot disagree about a
  settled claim.

## Risks / Trade-offs

- **The first real registry DELETE is unverified.** The probe showed the route is handled, not
  that a real manifest is removed. The `unsupported` and `persistent` path keeps a refusal
  visible without retry storms. The e2e test uses a fake registry, and the first live deletion
  confirms the behavior. Check `status` after deploying.
- **Tick latency.** Registry calls, filesystem walks, and `rmtree` of 230 MB trees run inside the
  cycle lock. The shared `CleanupBudget` (§4a) bounds all of them, on the board and off it. The
  first hour after day 3 and after day 14 runs slightly slower ticks while the backlog drains.
- **A reactivated claim loses its earlier removal record.** The cleanup-cycle reset clears
  `retention.removed`. The earlier attempts' evidence stays pruned. Only the audit record of what
  was removed is lost, and the SQLite run history remains. This is accepted to keep one cleanup
  cycle per terminal period.
- **Human review can be lost.** A settled eval left in Review loses its review command 14 days
  after settling. The handoff text states this, and the lapse event marks it on the card. Paul
  can raise `settled_retention_days`.
- **A pending event can pin a claim.** A superseded claim with an undelivered event is never
  pruned, because superseded claims skip `_report`. Two claims are affected today. They are
  counted as pending in status. Delivering superseded claims' events is out of scope.
- **Idle release is not atomic with admission.** Could a settled fix claim be reactivated for a
  review round in the same tick as its release? In the per-card loop, `review_round` runs before
  `handler.cleanup`. `idle` is computed after it, from the re-read claim, and requires the claim
  to be terminal with no non-terminal runs. An admitted round makes the claim active, or leaves a
  reserved run, so release is skipped. A review round prepares fresh clones anyway.

## Migration Plan

- No schema change. New `claim.cleanup` keys are additive: `terminal_observed_at`,
  `released_by`, `fly_image`, and `size_estimate`.
- **First tick after deploy:** records `terminal_observed_at` for every terminal claim, both
  on-board and off-board, and deletes Fly images for already-terminal claims whose Machines are
  gone. It limits deletions to 5 claims per tick.
- **Day 3:** cancelled and superseded claims release their workspaces and are pruned.
- **Day 14:** settled claims outside Done follow, including the 2 settled fix claims in Done
  that were never seen in Review.
- **Draining the backlog:** each day's backlog drains at the budget's pace, about an hour.
- **Rollback:** deploy the previous release. The new keys are ignored by old code. Released
  workspaces and pruned evidence are not restored, and neither is anything else the rule removed.

## Testing Strategy

**Unit and integration** (`tests/integration/test_retention.py`, `test_fix_cleanup.py`,
`test_and_scene_adapter.py`, `test_fly_api.py`, `test_eval_result_publication.py`, and new
`test_fly_image_cleanup.py`):

- the idle clock: set, cleared on reactivation, and never retroactive;
- per-lifecycle periods;
- the settled claim in Done judged only by the Done path;
- the guards: Machine record, capture, pending events, and `complete`;
- the candidate-worktree target, including read-only files;
- superseded and cancelled-eval release;
- the lapse event: once, on-board only, and only after a review command;
- handoff text with the configured days;
- `delete_manifest` for 202, 404, 405 `UNSUPPORTED`, 5xx, and a network error;
- verify-then-delete with a mismatch;
- backoff and persistent states;
- per-tick limits;
- configuration defaults and validation.

**E2E** (`tests/e2e/test_factory_cycle.py`, `test_fly_eval_cycle.py`):

- a full `run_cycle` with a fake board where a terminal claim's card is absent, confirming the
  sweep releases and prunes it;
- a board read that raises, confirming nothing is released;
- a Fly eval claim whose Machine record is present, then cleared, confirming the image is deleted
  only after the record clears and that a registry 500 does not affect disposal;
- the status aggregate line.
