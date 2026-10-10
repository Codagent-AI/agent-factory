## Coverage Strategy

The specifications remain the source of unit-test requirements. This plan records only the extra
obligations: integration and end-to-end tests, the envelope for the acceptance pass, and any
human-only checks.

Unit tests cover the logic that needs no real boundary, as design.md "Testing" lists:

- `lanes.lane_for` and `rank` for every level, every letter case, unset, and unknown values;
- the `classify_reservation` table: no runs; a started initial run followed by a recovery; a
  planning failure with no `started_at`; a review start; a review pre-suite retry before and after
  it started; an unblock after a settled episode; eval repetition 2;
- the gate's cause selection for each mode and occupancy;
- the admission-pass sort key, and the status line rendering.

The obligations below cover the boundaries where unit tests would rely on mocks:

- real SQLite locking between connections and processes;
- schema compatibility with the previous release's SQL and its supervisors;
- the runtime cycle wiring the controller, the handlers, and the store together;
- the `lanes` CLI and the resident's start;
- `status` output parsed by `scripts/slots.sh`;
- `scripts/deploy.sh` with `scripts/lane-guard.sh` and its EXIT trap;
- one public `agent-factory tick` journey across several lanes.

No automated test uses GitHub, Fly, Docker, or a model. A fake board client or the existing fake-`gh`
harness stands in for GitHub. A hermetic stub `agent-runner` stands in for the Runner. Stub
`launchctl`, `PlistBuddy`, `plutil`, and release executables stand in for the deploy environment.

## Integration Tests

### INT-001: Atomic lane reservation across processes

- **Covers:** `factory-claim-lifecycle` "Prevent overlapping execution": concurrent reservations
  for one lane, concurrent reservations across lanes, and simultaneous dispatch. Also "Classify new
  starts and continuations": the classification is taken inside the reservation transaction.
- **Boundary:** `ClaimStore.reserve_run` in two OS processes against one temporary database file,
  using real WAL and `BEGIN IMMEDIATE` locking.
- **Setup:** fix claims seeded in lanes mode (after `enable_lanes()`):
  - two new High claims;
  - one new Low claim;
  - one Low claim that already has a started run in its episode.

  The processes synchronize on a barrier file or `multiprocessing.Barrier`, so both attempt the
  reservation at once.
- **Action:** run each pair 20 times, resetting the runs between rounds:
  - (a) both High claims reserve the High lane;
  - (b) one High claim reserves High while the new Low claim reserves Low;
  - (c) one High claim reserves High while the continuing Low claim reserves Low.
- **Assertions:**
  - (a) Exactly one reservation succeeds every round, and the other raises `LaneBusy` with cause
    `lane-busy`.
  - (b) High always succeeds. Low succeeds only when its transaction committed before High's
    (ordered by `created_at`). Otherwise it fails with `higher-lane`. No round ends with a Low run
    created after an unfinished High run.
  - (b) is also run in both forced orders: one process holds its `BEGIN IMMEDIATE` until the other
    is blocked waiting. With High first, Low fails with `higher-lane`. With Low first, both
    succeed.
  - (c) Both always succeed.
  - The database never holds two unfinished runs with the same `(kind, lane)`.
- **Execution:** `tests/integration/test_priority_lanes_store.py` under `uv run pytest`.

### INT-002: Upgrade and rollback compatibility with the previous release's SQL

- **Covers:**
  - `factory-claim-lifecycle` "Hold every lane for attempts that predate lanes": upgrade while a
    fix runs, a previous-release supervisor finishing after the upgrade, and doctor leaving the old
    guard;
  - `factory-operations` "Restore the per-kind guard before rolling back past lanes": the
    atomicity of the restore.
- **Boundary:** the real store against a database built with the shipped v4 schema. The previous
  release is emulated by its verbatim SQL:
  - the v4 `CREATE TABLE`/index script;
  - `reserve_run`'s `INSERT INTO run (...)` column list;
  - the `mark_running`, `update_progress`, and result `UPDATE` statements;
  - the `user_version` check.

  These are copied into a test fixture from `origin/main` at the time of writing.
- **Setup:** a v4 database holding one unfinished fix run and one unfinished eval run, plus
  settled history, a pause, and a quota hold.
- **Action:**
  1. Open it with the new `ClaimStore`, as `doctor` would.
  2. Run the previous release's INSERT and UPDATE statements.
  3. Call `enable_lanes()`.
  4. Try a new Medium fix start and a continuation of another fix claim.
  5. Finish the legacy run with the previous release's UPDATE, then reserve again.
  6. Seed a second fix lane, call `restore_kind_guard(False)`, then finish one run and call it
     again.
  7. Run the previous release's INSERT for a second fix run.
- **Assertions:**
  - After step 1, `user_version` is still 4, and `one_nonterminal_run_per_kind` still exists.
  - The previous release's INSERT and UPDATE statements succeed after the `lane` column is added.
  - No history, pause, or quota hold is lost.
  - After `enable_lanes()`, the legacy fix run blocks both reservations with cause `legacy`, and
    eval admission is unaffected by it.
  - After the legacy run finishes, lane admission works.
  - The first `restore_kind_guard` changes nothing and returns both fix runs. The second swaps the
    indexes.
  - The previous release's second INSERT then fails with `IntegrityError`.
- **Execution:** `tests/integration/test_priority_lanes_migration.py`.

### INT-003: Runtime admission across lanes, re-entries, and cycles

- **Covers:**
  - "Prevent overlapping execution", every new lane scenario;
  - "Assign each attempt a Priority lane", all scenarios;
  - "Classify new starts and continuations", all scenarios;
  - "Fill eval Priority lanes", all scenarios;
  - the bug, feature, and task intake lane scenarios;
  - `factory-pull-request-lifecycle` "Re-admit a review round through the claim's kind slot", the
    modified and new scenarios;
  - "Notify when the job cap holds work": a card waiting for a busy lane;
  - one reservation per kind per cycle (design decision 27).
- **Boundary:** one runtime cycle at a time (`runtime` cycle entry), through the real `Controller`,
  the real eval and pull-request handlers, `review.py`, `blocked.py`, and `ClaimStore`. It uses
  the fake board and GitHub client used by `tests/integration/test_per_kind_slots.py` and
  `test_job_cap.py`. `launch_supervisor` is replaced by a recorder that marks the run `running`
  with `started_at`. The test finishes runs explicitly through `controller.record_result`.
- **Setup:** board snapshots with Priority values on Bug, Feature, Task, and Eval cards; a settled
  fix claim with a recorded PR and an eligible review comment; a blocked feature claim whose
  `needs-input` was answered; a reached job cap for the job-cap case.
- **Action:** a scripted sequence of cycles that walks each scenario, re-reading the board between
  cycles, including:
  - Low running, Medium arrives, then High arrives;
  - Low and Medium Ready while High runs;
  - a second High;
  - Urgent holding everything below it;
  - a cross-kind Low eval;
  - an eval repetition continuing beside a higher eval;
  - a never-launched claim waiting for its first attempt;
  - a planning failure before launch;
  - an unblock, and a review round;
  - a High Ready bug and a Low review round in the same cycle;
  - a card reprioritized while its attempt runs, and its next repetition;
  - two kinds each admitting once in one cycle while a second card of one kind waits;
  - an unset-Priority review round against an explicit Low Ready card in one cycle;
  - two explicit Medium candidates, a review round and a Ready card, in one cycle;
  - writer feedback after a review attempt ended in `needs-input` (`blocked_by == "review"`), for
    a fix, a feature, and a task claim;
  - a review-candidate claim whose PR is recorded only on its latest run (the
    `prior_pull_request` fallback);
  - a settled fix and a settled task, each with a PR and no eligible review feedback, dragged back
    to Ready.
- **Assertions:**
  - The recorder sees exactly the reservations the spec scenarios require, with each run's recorded
    `lane` and `reason`.
  - Rejected cards stay Ready with no `needs-input` label, decline, or job-cap comment.
  - `lane-wait` settings name the lane, cause, and holder for each waiting card, and are cleared
    once the card is admitted.
  - A Low review round that cannot start still records `waiting_review`.
  - An unblock that cannot start still reconciles against its earlier branch.
  - No clones are cut (`prepare` is not called) for a card the lane prefilter rejects.
  - The explicit Low Ready card is admitted before the unset-Priority review round, which then
    waits for the Low lane.
  - At equal Priority, the review round is admitted first.
  - Each review-blocked claim with writer feedback reserves a `review` run through the lane gate.
    So does the claim with the `prior_pull_request` fallback.
  - Each settled claim dragged to Ready with no eligible feedback reserves a fresh claim's
    `initial` run through the lane gate.
- **Execution:** `tests/integration/test_priority_lanes_admission.py`.

### INT-004: `lanes` CLI commands and the resident's start

- **Covers:**
  - "Restore the per-kind guard before rolling back past lanes": the `lanes supported`,
    `lanes downgrade [--check]`, and `lanes enable` contracts;
  - lanes taking effect when the resident starts;
  - kind mode allowing at most one attempt per kind.
- **Boundary:** `agent-factory` run as a subprocess with a temporary local configuration and
  state, plus the `resident` command entry in-process with its poll sleep patched to stop after the
  first cycle.
- **Setup:** a temporary state database in kind mode, with fixed unfinished runs: none, one per
  kind, and two fix runs.
- **Action:**
  - `lanes supported` without `--config`;
  - `--config L lanes downgrade --check` and `lanes downgrade` on each fixture;
  - `lanes enable`;
  - start `resident` on a kind-mode database;
  - run `tick` and `doctor` on a kind-mode database;
  - in kind mode, run one cycle with two eligible fix cards of different Priority.
- **Assertions:**
  - `supported` prints `priority-lanes` and exits 0.
  - `--check` changes nothing. It exits 1 and lists `<kind>: <claim> <repo>#<n> <lane>` for the
    two-fix fixture, and exits 0 otherwise.
  - `downgrade` swaps the indexes only when the check passes. It is a no-op success when already in
    kind mode.
  - `enable` and the resident's start create the lane index. `tick` and `doctor` never change the
    mode.
  - In kind mode only one fix attempt starts.
- **Execution:** `tests/integration/test_priority_lanes_cli.py`.

### INT-005: Status output and the deploy slot predicates

- **Covers:** `factory-operations` "Expose current operational status": the modified and new
  scenarios, the lanes-off line, and that `scripts/slots.sh` reads the new format.
- **Boundary:** `agent-factory status` run as a subprocess on seeded databases. Its output is fed
  to the real `slots_free` and `host_slots_free` bash functions.
- **Setup:** seeded states:
  - all kinds idle;
  - Low and High fix running;
  - a legacy fix holder;
  - kind mode;
  - an eval running with a fix blocked on `needs-input`;
  - a review round waiting on a higher lane;
  - an unclaimed Ready card recorded in `lane-wait`.
- **Action:** run `status`, then source `scripts/slots.sh` and evaluate the predicates.
- **Assertions:**
  - `<kind> slot: free` appears exactly when the kind is idle; otherwise `<kind> slot: busy (...)`
    lists the lanes highest first.
  - There is one `<kind> lane <lane>: ...` line per busy lane, and the legacy holder shows as
    `lane all (pre-lane attempt)`.
  - The `lanes: off` line appears only in kind mode.
  - Waiting reasons name the lane and holder.
  - `slots_free` is true only when every kind is idle, including with several lanes busy.
  - The existing `test_deploy_slots.py` expectations for old-format output still pass.
- **Execution:** `tests/integration/test_priority_lanes_status.py`, plus additions to
  `tests/integration/test_deploy_slots.py`.

### INT-006: Deploy rollback guard, restore, and trap

- **Covers:** "Restore the per-kind guard before rolling back past lanes", all scenarios.
- **Boundary:** the real `scripts/deploy.sh` with `scripts/lane-guard.sh`, run under bash in a
  temporary `HOME`. The fake `running` and target executables respond to `lanes supported`,
  `lanes downgrade [--check]`, `lanes enable`, `honored-revisions`, `pause`, `status`, `doctor`,
  and `resume`, and log every invocation. Stub `launchctl`, `PlistBuddy`, `plutil`, `git`, and
  `uv` follow the `tests/integration/test_deploy_fixture_guard.py` harness.
- **Setup:** scenario variants:
  - the target lacks lanes and the live release has two fix lanes busy;
  - the target lacks lanes with at most one attempt per kind;
  - the check passes, but the downgrade after the pause fails (a lane started in between);
  - doctor fails after a successful downgrade;
  - the unload fails: the stub `launchctl print` keeps showing the old lanes resident after
    `bootout`;
  - bootstrap fails, or the resident is not running, after a confirmed unload;
  - the target supports lanes;
  - the live release lacks `lanes supported` (exit 2).
- **Action:** run `deploy.sh` for each variant.
- **Assertions:**
  - **Refusal before the pause:** exits non-zero with the claims and the rollback procedure, never
    calls `pause`, and leaves the plist, `shared_config`, and `releases/current` unchanged.
  - **Success:** the downgrade runs after `pause` and before the plist changes.
  - **Downgrade failure after the pause:** stops paused with the live release unchanged.
  - **Doctor failure:** the revert is followed by exactly one `lanes enable` on the live
    executable, and the original failure exit status is kept.
  - **Unload failure:** the plist and `shared_config` point back at the live release, exactly one
    `lanes enable` runs on the live executable, and the deploy exits paused with failure.
  - **Failure after a confirmed unload:** `lanes enable` is not called, and the per-kind guard is
    kept.
  - **Target supports lanes, or live release lacks them:** the run makes no `lanes downgrade` call
    and proceeds.
- **Execution:** `tests/integration/test_deploy_lane_guard.py`.

## End-to-End Tests

### E2E-001: Several priorities of one kind through `agent-factory tick`

- **Covers:** the issue's journey end to end:
  - a Medium card starts beside a running Low one;
  - a High card starts beside both;
  - lower cards and a second High card wait;
  - the next work starts when the High lane frees;
  - another kind is unaffected;
  - `status` shows it all throughout.
- **Surface:** the `agent-factory tick` and `status` CLIs, run as subprocesses by the fake-`gh`
  board harness in `tests/e2e/test_factory_cycle.py` (`_setup`, `_cli`). Real Git clones, SQLite,
  and supervisor processes are used.
- **Setup:**
  - a hermetic stub `agent-runner` on `PATH` that writes progress, then blocks until a per-issue
    release file appears, then writes a `pull-request` outcome;
  - fix targets with Bug cards whose Priority the harness can set and change;
  - one Task card.
- **Journey:**
  1. A Low Bug goes Ready. Tick.
  2. A Medium Bug goes Ready. Tick.
  3. A High Bug goes Ready. Tick.
  4. A second High Bug and another Low Bug go Ready, plus a Low Task. Tick, then status.
  5. Release the first High run. Tick until it settles, then tick again.
  6. Release everything. Tick until all settle, then status.
- **Assertions:**
  - After steps 1 to 3, the claims run in the Low, Medium, and High fix lanes at the same time.
  - In step 4, neither new Bug starts, and the Low Task starts.
  - Status shows `fix slot: busy (high, medium, low)` and the lane lines, with the waiting Bugs
    named and their causes given.
  - In step 5 the second High Bug starts, and the waiting Low Bug still does not.
  - Finally, every card reaches Review with a PR link, and status shows `fix slot: free` and
    `task slot: free`.
- **Execution:** `tests/e2e/test_priority_lanes_cycle.py` under `uv run pytest` (not
  `docker`-marked, so it runs in CI).

No further E2E test is planned. Upgrade, rollback, deploy, and review-round ordering are covered
more reliably and cheaply by INT-002, INT-003, and INT-006 than by a public journey.

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - this Mac, with the change's worktree and its `uv` environment;
  - a temporary storage root and temporary local configuration;
  - the fake-`gh` board harness and hermetic stub `agent-runner` from the E2E suite;
  - a temporary `HOME` with stub `launchctl`, `PlistBuddy`, and `plutil` for exercising
    `scripts/deploy.sh`;
  - building throwaway release worktrees of this branch and of `origin/main` under that temporary
    `HOME`, to check rollback and upgrade against the real previous release code.
- **Credentials and secrets:** none needed. Do not read or print tokens, `~/.agent-factory/private/`,
  or the factory bot's credentials.
- **Authorized effects:** temporary files, worktrees, and processes under the temporary root and
  `HOME`, all removed afterwards. No model, Fly, Docker, or GitHub calls.
- **Off limits:**
  - the live factory service, its LaunchAgent, `~/.agent-factory/state.sqlite3`, `releases/`,
    `config.toml`, and any real deploy or `launchctl` call against the real user domain;
  - the real Codagent board and any real issue, comment, or pull request;
  - `config/codagent.toml` pins.
- **Permitted substitutes:**
  - the fake `gh` board for GitHub;
  - the stub `agent-runner` for the Runner and models;
  - stub launchd tools for deploy;
  - when building an `origin/main` release worktree is not possible, the verbatim previous-release
    SQL fixture from INT-002, reported as a substitute.
- **Known risk areas:**
  - The admission-pass refactor that moves review rounds and unblocks out of the first loop: watch
    for regressions in `waiting_review` bookkeeping, unblock reconciliation, re-entry gestures, and
    reporting after launch.
  - The boundary between pre-suite failure and "actually started" in continuation classification.
  - Legacy holders after upgrade, and kinds left single-slot longer than expected.
  - The EXIT trap's interaction with `set -euo pipefail` and existing `die` paths in
    `deploy.sh`.
  - Lane and index state after an interrupted deploy (an accepted limitation: safe single-slot
    mode until the resident restarts or `lanes enable` runs).
  - One reservation per kind per cycle, and memory re-sampling with several host attempts in one
    cycle.
  - Skills and `factory-assign` parsing the new status lines.
  - Fly eval concurrency across several lanes is not exercised live (an accepted limitation; the
    per-claim image tags and Machines are already independent).

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| claim-lifecycle: Prevent overlapping execution | INT-001, INT-003 | E2E-001 | — |
| claim-lifecycle: Assign each attempt a Priority lane | INT-003 | — | — |
| claim-lifecycle: Classify new starts and continuations | INT-001, INT-003 | — | — |
| claim-lifecycle: Hold every lane for attempts that predate lanes | INT-002, INT-004 | — | — |
| eval-intake: Fill eval Priority lanes | INT-003 | — | — |
| bug-intake: Select eligible bugs by Priority (lanes) | INT-003 | E2E-001 | — |
| feature-intake: Select eligible features by Priority (lanes) | INT-003 | — | — |
| task-intake: Select eligible tasks by Priority (lanes) | INT-003 | E2E-001 | — |
| task-execution: Treat task claims as pull-request claims (lanes) | INT-003 | E2E-001 | — |
| pull-request-lifecycle: Re-admit a review round through the claim's kind slot | INT-003 | — | — |
| operations: Expose current operational status | INT-005 | E2E-001 | — |
| operations: Run an immediate normal cycle with tick | INT-004 | E2E-001 | — |
| operations: Restore the per-kind guard before rolling back past lanes | INT-002, INT-004, INT-006 | — | — |
| operations: Notify when the job cap holds work (busy lane) | INT-003 | — | — |
