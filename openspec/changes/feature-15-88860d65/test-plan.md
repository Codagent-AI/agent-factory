## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only the
integration, end-to-end, agent-acceptance, and human-only obligations that go beyond them.

This change is dangerous in one way: it deletes files and registry manifests. The plan
concentrates on three things:

- deleting only recorded, factory-owned paths;
- never touching work that can still run;
- keeping registry failures isolated from Machine disposal.

**Layer choices.**

| Layer | What it proves |
|---|---|
| Unit | Arithmetic and decision logic: idle clock, periods, backoff, config validation |
| Integration | Real SQLite store, real directories and git worktrees, the real HTTP client against the repository's fake Fly API server |
| End-to-end | Three full `run_cycle` / CLI journeys, using the existing fake-GitHub and fake-Fly harnesses |
| Agent acceptance | Read-only `status` against the live store; one test-owned delete against the real Fly registry; one CLI journey on an isolated root |

**Existing tests that change on purpose.** Two tests in `tests/integration/test_retention.py`
encode behavior this change replaces:

- `test_superseded_claim_prunes_without_cleanup_complete`
- `test_cancelled_claim_prunes_without_a_cleanup_pass`

Pruning now requires `cleanup.complete` on every lifecycle (design decision G2). These tests must
be updated to the new rule, not deleted.

**What must never run.** No automated test may touch `~/.agent-factory`, the live Project board,
or the live Fly registry.

**Commands.** All automated tests run with `uv run pytest`, in the default selection. None needs
Docker.

## Integration Tests

### INT-001: Idle-path pruning over a real store and artifact tree

- **Covers:** factory-operations "Retain evidence for a bounded period" (the idle path, the guards,
  and the candidate worktree); factory-eval-execution "Preserve suite-owned evidence and
  candidate outputs".
- **Boundary:** `retention.reconcile` against a real `ClaimStore` (SQLite in `tmp_path`) and real
  eval and fix artifact directories.
- **Setup:** Build trees with the `_make_eval_tree` and `_make_fix_tree` helpers, extended with a
  `.runtime/candidate-worktree` that contains a `.git` directory and a read-only file inside
  `node_modules`. Create claims in each of these lifecycles:
  - settled;
  - cancelled;
  - superseded;
  - active;
  - waiting;
  - blocked.

  Give each claim an explicit `now` and backdated `terminal_observed_at` values.
- **Action:** Call `reconcile` with `on_board` true and false, a board status of Review, Done, or
  empty, and a `capture_settled` stub.
- **Assertions:**
  - Cancelled and superseded claims prune after `abandoned_retention_days`, and not one day
    earlier.
  - A settled claim in Review prunes after `settled_retention_days`.
  - A settled claim whose card is Done is judged only by the Done path, from its Done
    observation.
  - Active, waiting, and blocked claims are never pruned.
  - A recorded `fly:machine:*` setting for the claim blocks pruning.
  - An incomplete capture blocks pruning.
  - `cleanup.complete` not being true blocks pruning.
  - The candidate worktree, including its read-only file, is removed.
  - `result.json`, provenance, the issue input, and unknown files remain.
  - The first observation after an upgrade never prunes.
- **Execution:** `tests/integration/test_retention.py`

### INT-002: Idle release of eval worktrees with real git worktrees

- **Covers:** factory-operations "Clean up worktrees after review" for settled evals in Review,
  cancelled evals, and superseded evals.
- **Boundary:** `WorktreeCleanup.reconcile(idle=True)` with a real `GitWorktreeManager` over git
  repositories created by the test.
- **Setup:**
  - Two eval claims for the same project item. The first is superseded by the second, and each
    has its own recorded worktrees.
  - A cancelled eval claim.
  - A settled eval claim.
- **Action:** Call `EvalHandler.cleanup` with and without `idle`, then call it a second time.
- **Assertions:**
  - With `idle` false and no Review-then-Done observation, nothing is removed.
  - With `idle` true:
    - the superseded, cancelled, and settled claims' worktrees are removed and `git worktree
      list` no longer shows them;
    - the replacing claim's worktrees remain;
    - `complete` is true and `released_by` is `"idle"`.
  - A claim with nothing recorded completes without error.
  - A second call is idempotent.
  - A removal failure, simulated with a read-only parent directory, is recorded in `last_error`
    and retried on the next call.
- **Execution:** `tests/integration/test_eval_worktree_cleanup.py` (new)

### INT-003: Idle release of fix and feature clones and credential copies

- **Covers:** factory-operations "Clean up worktrees after review" for settled and superseded fix
  and feature claims.
- **Boundary:** `PullRequestCleanup.reconcile(idle=True)` against real clone directories, private
  credential copies, and a stubbed `docker rmi`.
- **Setup:**
  - A settled fix claim whose card is in Review.
  - A superseded feature claim with recorded `preparation.clones`.
  - Credential files under a temporary private root.
- **Action:** Call `reconcile` with `idle` true, then with `idle` false.
- **Assertions:**
  - Clones and credential copies are removed only when `idle` is true, or on the existing
    Review-then-Done and cancelled paths.
  - The existing cancelled path is unchanged.
  - The operator's working clone and the mirror paths are untouched.
- **Reactivation cycle:** A settled fix claim is idle-released and then pruned. Next:
  1. A review round reactivates it (lifecycle `active`), with fresh clones and a new credential
     copy prepared through `prepare_review`.
  2. The round settles.
  3. The new `terminal_observed_at` is backdated past `settled_retention_days`.

  Assert:
  - the reactivation cleared `complete`, `released_by`, `review_observed`, and
    `retention.pruned_at`;
  - the second idle release removes the round's clones and credential copy;
  - the round's attempt evidence is then pruned.
- **Done without Review:** A settled fix claim has a Done card and no `review_observed`. Assert:
  - before `settled_retention_days`, nothing is released;
  - after it, idle release removes its clones and credential copies;
  - pruning then follows the Done path's own period.
- **Execution:** `tests/integration/test_fix_cleanup.py` and
  `tests/integration/test_retention.py`

### INT-004: The results-capture guard follows real publication state

- **Covers:** factory-operations "Retain evidence for a bounded period" (the results-capture
  condition).
- **Boundary:** `publication.capture_settled` together with `publish_eval_results`, using a
  recording `ResultsClient` fake and real run directories.
- **Setup:** A settled eval claim with two consumed repetitions and curated files, and a results
  repository configured.
- **Actions and assertions:**

  | Situation | Guard |
  |---|---|
  | Before any publication | false |
  | After `publish_eval_results` commits both repetitions | true |
  | A `human-review.json` is written to one repetition | false again |
  | That repetition is republished | true |
  | A repetition with incomplete curated files, on a terminal claim | true |
  | No results repository configured | true |

  **A settled claim that is idle-released, in Review or off the board.**
  `publish_eval_results` commits outstanding changes and then sets `results_final`. A later tick
  does not stat that claim's run directories, which is checked with a spy on `_signature`.
  `capture_finished` gives the same answer for the claim in both publication and the guard.

  Pruning under INT-001 leaves the signature unchanged, so a later `publish_eval_results`
  commits nothing new.
- **Execution:** `tests/integration/test_eval_result_publication.py`

### INT-005: Registry manifest delete over HTTP

- **Covers:** factory-fly-execution "Delete a claim's image once it can no longer be used"
  (registry outcomes).
- **Boundary:** `FlyMachinesClient.resolve_manifest` and `delete_manifest`, over real HTTP to the
  `FakeMachinesApi` registry routes, which are extended with manifest DELETE and scripted replies.
- **Setup:** The fake registry holds tag `claim-<12>`, pointing to digest D, and a `base` tag
  pointing to digest B.
- **Actions and assertions:**
  - `delete_manifest(repo, D)` sends `DELETE /v2/<repo>/manifests/D` with basic auth: user `x`
    and the token as password.
  - A 202 reply returns normally, and afterwards `base` still resolves.
  - A 404 `MANIFEST_UNKNOWN` reply returns normally.
  - A 405 reply with an `UNSUPPORTED` code raises `FlyApiError`, with status 405 and reason
    `UNSUPPORTED`.
  - A 500 reply and a closed socket raise with a transient status.
  - No bearer header is sent.
- **Execution:** `tests/integration/test_fly_api.py`

### INT-006: Claim image deletion policy and isolation from disposal

- **Covers:** factory-fly-execution "Delete a claim's image once it can no longer be used" (the
  gate, verify-then-delete, retry, persistence, and isolation).
- **Boundary:** `fly/images.reconcile_claim_image` with a real `ClaimStore`, the real client, and
  the fake registry. `FlyBackend.dispose` uses the same store.
- **Setup:**
  - Eval claims with recorded digests, some recorded in `run.progress["image_build"]` and some
    only in `.factory/image-build.json`.
  - `fly:machine:*` records present on some claims.
  - A claim with no digest.
  - A tag scripted to resolve to another digest.
- **Actions and assertions:**
  - While a claim is active, or has a Machine record (including a stopped quota-hold Machine),
    no registry DELETE is sent.
  - Once the claim is terminal and the record is gone, exactly one DELETE is sent for the
    recorded digest, and `fly_image.state` is `complete`.
  - A missing tag gives `gone` and `complete`.
  - A mismatch sends no DELETE and records `persistent` as true. A later call sends nothing.
  - A 405 `UNSUPPORTED` reply records a persistent failure, which is not retried.
  - A 500 reply records `failed` with a `retry_after`:
    - a call before `retry_after` sends nothing;
    - a call after it retries and completes.
  - A record whose repository differs from `[fly] image` is never deleted.
  - At most 5 claims are processed per call batch.
  - With the registry returning 500, `FlyBackend.dispose` of another claim's Machine still
    destroys the Machine and clears its record.
- **Execution:** `tests/integration/test_fly_image_cleanup.py` (new)

### INT-007: Configuration reaches retention, handoff text, and status

- **Covers:**
  - factory-operations "Clean up worktrees after review" (configurable periods);
  - factory-eval-reporting "Provide executable human-review instructions" (the stated window);
  - factory-operations "Expose current operational status" (the aggregate, read from cached
    estimates).
- **Boundary:** Loading a TOML config, then `EvalHandler.from_config`, then
  `AndSceneAdapter.review_handoff`, then `retention`. Status rendering over a real store.
- **Setup:**
  - A config file with `settled_retention_days = 9` and no `abandoned_retention_days`.
  - A store holding 40 terminal claims with `size_estimate` values, one `retention.errors`
    failure, and one failed `fly_image`.
- **Assertions:**
  - The abandoned period defaults to 3 days.
  - Zero or negative values are rejected with a configuration error.
  - The handoff comment says "9 days" and names both Done and the period.
  - Status output:
    - has one `cleanup:` line with the pending count, the pruned count, and a failing count of 2;
    - with 7 measured and 5 unmeasured pending claims, reads
      `~<sum> GiB across 7 measured, 5 not yet measured`;
    - with every pending claim measured, reads just `~<sum> GiB`;
    - with none measured, reads `size not yet measured`;
    - lists exactly the 2 failing claims individually;
    - does not open any artifact directory, checked by pointing the claims' evidence paths at a
      directory without read permission.
- **Execution:** `tests/integration/test_retention_config.py` (new) and
  `tests/integration/test_cli_operations.py`

## End-to-End Tests

### E2E-001: Settled eval left in Review is released, pruned, and announced

- **Covers:**
  - factory-operations "Clean up worktrees after review" and "Retain evidence for a bounded
    period";
  - factory-eval-reporting "Provide executable human-review instructions" (the lapse event);
  - factory-eval-execution "Preserve suite-owned evidence and candidate outputs";
  - factory-operations "Expose current operational status".
- **Surface:** The `agent-factory tick` and `status` CLI, in process, as
  `tests/e2e/test_factory_cycle.py` drives it, with the fake GitHub board and a fake results
  client.
- **Setup:**
  - An eval request is admitted and finishes one repetition as `pending-human-review`, and its
    handoff comment carries the review command.
  - Its results are published and its card is in Review.
  - A second tick records `terminal_observed_at`. The test then backdates it past
    `settled_retention_days`.
- **Journey:** Run `tick` three times, then `status`.
- **Assertions:** These follow the design's tick sequence: release, then the event is posted,
  then pruning.
  - After the first tick:
    - the suite worktrees are gone;
    - `review-window-lapsed` is queued but not yet posted;
    - evidence is intact;
    - the card stays in Review.
  - After the second tick:
    - the event has been posted once to the issue;
    - evidence is still intact, because the event was pending when pruning was judged.
  - After the third tick:
    - the repetition's logs, session state, and candidate worktree are removed;
    - `result.json` and the curated files remain;
    - `results_final` is true for the claim.
  - A fourth tick posts nothing new.
  - `status` shows the claim as pruned in its aggregate line.
  - A control claim moved to Done before the period gets the Done cleanup and no lapse event.
- **Execution:** `tests/e2e/test_factory_cycle.py`

### E2E-002: Off-board terminal claims are swept; a failed board read releases nothing

- **Covers:** factory-operations "Clean up worktrees after review" and "Retain evidence for a
  bounded period" (claims off the board and failed board reads); factory-eval-reporting (no
  event for off-board claims).
- **Surface:** The CLI `tick`, with the fake GitHub board.
- **Setup:**
  - A cancelled fix claim with clones and evidence, whose card has been removed from the fake
    board.
  - A superseded eval claim whose card has also been removed.
  - Both claims' terminal observations are backdated past `abandoned_retention_days`.
- **Journey:**
  1. Run `tick` with the board query scripted to fail on its second page.
  2. Run `tick` with the board query succeeding.
- **Assertions:**
  - After the failed read, no path is removed and no cleanup state changes.
  - After the successful read:
    - both claims' workspaces and evidence are removed;
    - no event is recorded for either claim;
    - active claims on the board are unaffected.
  - With 4 eligible on-board claims and 4 eligible off-board claims seeded, one tick completes at
    most 5 releases or prunes in total, and on-board claims go first. Observations are still
    recorded for all 8 claims. Admission of a Ready card still happens in the same tick. The next
    tick completes the rest.
- **Execution:** `tests/e2e/test_factory_cycle.py`

### E2E-003: A Fly eval claim's image is deleted after its Machine is gone

- **Covers:** factory-fly-execution "Delete a claim's image once it can no longer be used".
- **Surface:** The factory cycle with `fly` execution, as `tests/e2e/test_fly_eval_cycle.py`
  drives it, with `FakeMachinesApi` (Machines and registry).
- **Setup:** An eval claim runs through a real launcher against the fake API. The build records
  its digest.
- **Journey:**
  1. Settle the repetition, with the registry scripted to return 500 on DELETE.
  2. Let the next ticks dispose of the Machine.
  3. Switch the registry to succeed.
  4. Advance past `retry_after` and tick again.
- **Assertions:**
  - No DELETE is sent while the Machine record exists.
  - The Machine is destroyed and its record cleared, even though the registry fails.
  - `status` lists the claim's registry failure.
  - After the retry, the fake registry no longer has `claim-<12>` and still has `base`.
  - The failure is no longer listed.
- **Execution:** `tests/e2e/test_fly_eval_cycle.py`

## Agent Acceptance Tests

### AT-001: Status summarises the real history without flooding

- **Classification:** Required.
- **Covers:** factory-operations "Expose current operational status" (the aggregate summary).
- **Actor and surface:** The operator, running the `agent-factory status` CLI on the factory Mac.
- **Setup:**
  - A checkout of the change branch with `uv sync`.
  - Paul's live config, `~/.agent-factory/config.toml`, which points at the live
    `state.sqlite3`: about 67 claims, 50 of them terminal.
  - `status` is read-only. Before running it, confirm it opens no write transaction by reading
    the implementation.
- **Steps:**
  1. Run `uv run agent-factory --config ~/.agent-factory/config.toml status`.
  2. Run the same command with `--all`.
- **Expected:**
  - The default output has one `cleanup:` summary line, with pending and pruned counts, and a
    size shown as not yet measured, because no new tick has run.
  - Terminal claims are not listed one per line unless they have a failure or are in Review, as
    today.
  - `--all` still lists every claim.
- **Evidence:** Both outputs captured as text in the acceptance report.
- **Effects and cleanup:** None. It is read-only, and the state file's modification time is
  unchanged (check it before and after).
- **Permitted substitutes:** If the live store is unavailable, use a copy of `state.sqlite3` in
  a temporary root and state the substitution.

### AT-002: A real Fly registry deletes a test-owned claim image and leaves others intact

- **Classification:** Conditional. It runs when the Fly deploy token file configured in
  `[fly] token_file` is readable on the machine running acceptance, which is true on the factory
  Mac.
- **Covers:** factory-fly-execution "Delete a claim's image once it can no longer be used"
  (verify-then-delete against `registry.fly.io`).
- **Actor and surface:** A client of the delivered library: `FlyMachinesClient.resolve_manifest`
  and `delete_manifest`, plus `fly/images.reconcile_claim_image` called on a scratch
  `ClaimStore` in a temporary directory.
- **Setup:**
  1. With the deploy token as basic-auth password, GET the manifest of an existing `claim-` tag in
     `agent-factory-sandbox`.
  2. Add a unique `annotations` entry, such as
     `{"org.codagent.acceptance": "<uuid>"}`, to get a new digest.
  3. PUT it under tag `claim-accept<6 random hex>`.
  4. Record its digest D. Confirm D differs from every other tag's digest, and record the digests
     of `base` and of every other tag.
  5. Seed a scratch store with a settled eval claim whose id starts with `accept<same hex>`, with
     `image_build = {repository, tag, digest: D}` and no Machine record.
- **Steps:** Call `reconcile_claim_image` for that claim with the real client, then call it a
  second time.
- **Expected:**
  - After the first call, `fly_image.state` is `complete` and the test tag no longer resolves
    (404).
  - `base` and every other tag still resolve to their recorded digests.
  - The second call sends no DELETE.
- **Evidence:** The tag list and digests before and after, and the stored `fly_image` record.
  Tokens must be redacted.
- **Effects and cleanup:**
  - Authorized effects: one manifest PUT, and one DELETE of that same test-owned manifest, in
    the sandbox app's registry.
  - No Machines are created and there is no cost beyond registry requests.
  - If the step fails midway, delete the test tag's digest by hand and report it.
  - Never delete any other digest.
- **Permitted substitutes:** None. If the token is unavailable, report acceptance for this flow
  as incomplete.

### AT-003: The CLI cleans an isolated factory root end to end

- **Classification:** Required.
- **Covers:**
  - factory-operations "Clean up worktrees after review", "Retain evidence for a bounded
    period", and "Expose current operational status";
  - factory-eval-reporting (the handoff wording and the lapse event).
- **Actor and surface:** The operator, running `agent-factory tick` and `status` against an
  isolated storage root.
- **Setup:**
  - A temporary storage root and config, with `abandoned_retention_days = 1` and
    `settled_retention_days = 2`.
  - GitHub is provided by the repository's fake-GitHub test harness (`tests/fixtures`), driven
    from a scratch script as the E2E tests do. The live Project board must not be used.
  - Seed these claims:
    - a settled eval in Review whose handoff was posted;
    - a cancelled eval;
    - a superseded fix;
    - a blocked fix;
    - an off-board cancelled fix.

    Give them real worktrees or clones and evidence directories that include
    `candidate-worktree`.
- **Steps:**
  1. Run `tick`.
  2. Backdate `terminal_observed_at` in the scratch store, as an operator would after waiting.
  3. Run `tick` three times, once for each step: release, event delivery, prune.
  4. Run `status`.
- **Expected:**
  - The first tick removes nothing.
  - After backdating:
    - the settled, cancelled, superseded, and off-board claims lose their workspaces and then
      their evidence;
    - the blocked claim keeps everything;
    - the settled eval's issue receives exactly one review-window event;
    - kept records remain.
  - `status` shows the aggregate line with the pruned count and no failures.
- **Evidence:** A directory listing (`find -maxdepth 3`) before and after, the fake board's
  recorded comments, and the `status` output.
- **Effects and cleanup:** Local temporary files only. Delete the temporary root afterwards.
- **Permitted substitutes:** The fake-GitHub harness in place of the real GitHub API. It is
  required, because a real tick would edit the live board.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | AT | HT |
| --- | --- | --- | --- | --- |
| factory-operations: Clean up worktrees after review | INT-002, INT-003, INT-007 | E2E-001, E2E-002 | AT-003 | — |
| factory-operations: Retain evidence for a bounded period | INT-001, INT-004 | E2E-001, E2E-002 | AT-003 | — |
| factory-operations: Expose current operational status | INT-007 | E2E-001, E2E-003 | AT-001, AT-003 | — |
| factory-eval-execution: Preserve suite-owned evidence and candidate outputs | INT-001 | E2E-001 | — | — |
| factory-eval-reporting: Provide executable human-review instructions | INT-007 | E2E-001, E2E-002 | AT-003 | — |
| factory-fly-execution: Delete a claim's image once it can no longer be used | INT-005, INT-006 | E2E-003 | AT-002 | — |
