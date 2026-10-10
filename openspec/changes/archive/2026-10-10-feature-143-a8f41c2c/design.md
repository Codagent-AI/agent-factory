## Context

Each work kind (eval, fix, feature, task) runs at most one attempt today. The guards are:

- **SQLite.** `src/agent_factory/store.py` creates
  `CREATE UNIQUE INDEX one_nonterminal_run_per_kind ON run(kind) WHERE status IN ('reserved','running','observing')`.
  `ClaimStore.reserve_run` inserts inside `BEGIN IMMEDIATE`, and turns the index's `IntegrityError`
  into `NonterminalRunError`.
- **Runtime prefilter.** `runtime.py` computes
  `slot_free = {kind: not store.nonterminal_runs(kind=kind)}` once per cycle. It walks the
  Priority-sorted Ready cards (`github.py` `_PRIORITY_ORDER = ("urgent","high","medium","low")`,
  unset or unknown last) and breaks after the first reservation.
- **Review rounds and unblocks.** `work_kinds/pull_request/review.py` and `blocked.py` reserve
  directly through `store.reserve_run`. They run in the runtime's first per-card loop, before the
  Ready admission loop, and gate on `store.nonterminal_runs(kind=...)`.
- **Status and deploy.** `operations._slot_lines` prints `<kind> slot: free` or
  `<kind> slot: <repo>#<n> <unit> (<status>)`. `scripts/slots.sh` (deploy) greps those lines.

Constraints:

- **Supervisors outlive deploys.** A supervisor keeps running from the release that started it
  and opens the database writable. It never reserves runs; only `controller.reserve_next`,
  `review.py`, and `blocked.py` call `reserve_run`. Raising `user_version` would make an older
  supervisor fail with "factory database is newer than this controller". So schema additions follow
  the watch/notify pattern: idempotent, outside the version number.
- **An older release relies on the per-kind index for atomic reservation.** Its `reserve_run` does
  no busy check of its own.
- **Ordering.** `run.reason` is one of `initial`, `recovery`, `quota`, `review`, `unblock`. A
  pre-suite retry reuses the previous run's reason (`pull_request/handler.py` `next_unit`).
  `started_at` is set only when a supervisor begins the run, never at reservation. A fresh
  re-admission creates a new claim (`supersede_and_create`).

## Goals / Non-Goals

**Goals:**

- Lanes per (kind, Priority), with the higher-lane block for new starts, enforced atomically for
  every reservation path.
- Continuations decided from saved run history inside the reservation transaction.
- An upgrade that keeps running work safe. Attempts from before the upgrade hold their whole kind.
- A rollback that restores the database contract the older release relies on, and is refused while
  it cannot.
- Status that stays parseable by today's `slots.sh` and skills.

**Non-Goals:** preemption; configurable lanes or caps; cross-kind priority; changes to card
ranking or job-cap counting; watch concurrency.

## Approach

### 1. Lane vocabulary: `src/agent_factory/lanes.py` (new)

```python
LANES = ("urgent", "high", "medium", "low")      # highest first; github._PRIORITY_ORDER imports this
def lane_for(priority: str | None) -> str        # case-insensitive; None/unknown -> "low"
def rank(lane: str) -> int                       # 0 = urgent
```

### 2. Schema: no `user_version` change

- **Column.** `ClaimStore._ensure_lane_column()` runs on every writable open, beside
  `_ensure_watch_schema`/`_ensure_notify_schema`. It runs `ALTER TABLE run ADD COLUMN lane TEXT`
  when the column is missing. The column is nullable with no default. Every existing row, and every
  row an older release inserts later, has `lane IS NULL`. An unfinished run with `NULL` lane is a
  **legacy holder**. Older code names its INSERT columns explicitly, so the column does not affect
  it.
- **Lane mode** is decided by which index exists, not by a setting:
  - **lanes mode:** `one_nonterminal_run_per_lane` exists:
    `CREATE UNIQUE INDEX one_nonterminal_run_per_lane ON run(kind, lane) WHERE status IN ('reserved','running','observing')`.
  - **kind mode:** `one_nonterminal_run_per_kind` exists. The lanes release then admits at most one
    attempt per kind, so it stays correct under the old index.
- **`ClaimStore.enable_lanes()`.** In one `BEGIN IMMEDIATE`, it ensures the column, drops the
  per-kind index, and creates the lane index. It is always safe, because kind mode holds at most one
  unfinished run per kind. SQLite unique indexes accept multiple `NULL`s, and the gate (below), not
  the index, handles legacy holders.
- **`ClaimStore.restore_kind_guard(check_only: bool) -> list[Run]`.**
  - In one `BEGIN IMMEDIATE`, it selects the unfinished runs of every kind that has more than one.
    If there are any, it rolls back and returns them.
  - Otherwise, unless `check_only`, it drops the lane index and creates
    `one_nonterminal_run_per_kind`. The `CREATE UNIQUE INDEX` would also fail on a violation, as a
    second safety net.
- **Who switches modes.** Only these switch modes:
  - `resident` at process start calls `enable_lanes()` before its first cycle;
  - `agent-factory lanes enable`;
  - `agent-factory lanes downgrade`.

  `tick`, `doctor`, `status`, supervisors, and an opening store never do. So doctor, run by the new
  release during a forward deploy, cannot remove the per-kind index from under the still-running
  older resident. Lanes take effect when the new resident starts, after the older one has been
  booted out.

### 3. Reservation gate: `store.py`

`reserve_run(claim_id, unit_key, *, reason, evidence_path, lane)` takes a required `lane`. Inside
its existing transaction, before INSERT, it calls `_lane_gate(conn, kind, lane, claim_id, reason)`.
On rejection, the gate raises `LaneBusy(NonterminalRunError)` carrying `cause` (`lane-busy`,
`higher-lane`, `legacy`, or `kind-mode`) and the blocking run. The INSERT writes `lane`. The gate:

```
active = unfinished runs of kind (one query)
if mode == kind:            reject if active                       (kind-mode)
if any r.lane is NULL:      reject                                 (legacy)
if any r.lane == lane:      reject                                 (lane-busy; the index is the backstop)
if classify(...) == start and any rank(r.lane) < rank(lane): reject (higher-lane)
```

`classify_reservation(runs, reason) -> "start" | "continuation"` is a pure function over the
claim's runs, read in the same transaction and ordered by `created_at`. Episode reasons are
`{"review", "unblock"}`.

1. **Is this reservation itself a new episode?** It is when `reason` is an episode reason, and it
   is not a pre-suite retry of the claim's latest run. A pre-suite retry means the latest run has
   the same reason and `result.failure_stage == "pre-suite"`. A new episode is a `start`.
2. **Where did the current episode begin?** At the last run whose reason is an episode reason.
   Walk back over pre-suite retries: while the previous run has the same reason and a pre-suite
   failure, move the start back one run. With no episode run, the episode begins at the claim's
   first run.
3. **Classify.** If any run in the episode has `started_at` set, the reservation is a
   `continuation`; otherwise it is a `start`. A claim with no runs is a `start`.

This gives the behavior the specs require:

- A first admission or fresh claim has no runs, so it is a start.
- A run reserved and then failed during planning has no `started_at`, so the next reservation is
  still a start.
- An eval's next repetition, a recovery, or a quota retry after a started run is a continuation.
- A review round or unblock is a start. Its pre-suite retry after it actually started is a
  continuation.

The caller supplies only `lane` and `reason` (which `next_unit` already computes). It cannot ask
for the exemption.

There are also read-only helpers for prefilters and status, using the same logic:

- `lane_decision(kind, lane, claim_id | None, reason) -> LaneDecision(allowed, cause, holder)`.
  `claim_id=None` means a start.
- `lane_occupancy(kind) -> LaneOccupancy(mode, holders: dict[lane, Run], legacy: list[Run])`.

`nonterminal_runs(kind=...)` stays for reconciliation and the host-attempt count. Every use of it
that means "the slot is free" switches to `lane_decision`. Those are in `runtime.py`, `review.py`,
and `blocked.py`.

### 4. Admission: `runtime.py`

- **Lanes come from cards.** The lane of any reservation is `lane_for(card.priority)` for the card
  being processed. `controller.reserve_next(claim_id, *, lane, readiness)` passes it through, and
  returns `None` without a hold on `LaneBusy`. `handler.unblock(..., lane=...)` and
  `handler.review_round(..., lane=...)` pass it to `reserve_run`, and use `lane_decision` for their
  cheap early gate instead of `nonterminal_runs(kind=...)`. They keep their non-admitting side
  effects: reconciling a blocked claim against an earlier branch or PR, and recording
  `waiting_review`.
- **One ordered admission pass.** Review rounds and unblocks move out of the first per-card loop
  into the admission pass. Everything else in the first loop stays where it is: cancellation, merge
  sync, reporting, and cleanup.
  - **Sort order.** The pass iterates the cycle's cards sorted by
    `(github._priority_rank(card.priority), 0 if the card's current claim is a re-entry candidate else 1, original Priority-then-newest index)`.
    The primary key is the existing Project Priority rank, which keeps unset and unknown values
    after an explicit Low. `lane_for` is used only for occupancy and reservation, so an
    unprioritized review round never sorts ahead of an explicitly Low Ready card, even though they
    share the Low lane.
  - **Re-entry candidates.** A blocked claim is an unblock candidate when it is not blocked by
    review. A settled claim, or one blocked with `blocked_by == "review"`, is a review candidate.
    That matches `process_review_claim`'s existing intake, including its `prior_pull_request`
    fallback for claims settled before the PR was kept on the outcome.
  - **Dispatch** happens separately from whether the claim may reserve:
    1. An unblock candidate calls `unblock`. That keeps its existing reconciliation when it cannot
       start.
    2. A review candidate calls `review_round`, whose `waiting_review` bookkeeping still runs when
       it cannot start. If it reserved nothing, the card then falls through to step 3. That lets a
       settled claim dragged back to Ready, with no eligible review feedback, reach its existing
       fresh re-admission (`handler.gesture` returns `"fresh"`). A review-blocked claim moved to
       Ready likewise reaches its existing fresh-claim gesture.
    3. Every other card, and every fall-through, runs the existing Ready path (snapshot, gestures,
       `accept`, `prepare`, `reserve_next`).

    Each reservation, whichever path makes it, goes through the lane gate.

  The existing `ready` check replaces `slot_free.get(kind)` with
  `lane_decision(kind, lane, existing_claim_id, next_reason).allowed`. For an existing active claim,
  `next_reason` comes from `handler.next_unit`, so a continuation passes the prefilter. A new card
  is checked as a start. The check runs before `prepare()`, so no clones are cut for work that
  cannot reserve.
- **One reservation per kind per cycle.** This replaces "break after the first reservation". The
  pass records each kind that reserved, and skips later cards of that kind for the rest of the
  cycle. Because the lane view is re-read from the store per decision, a reservation earlier in the
  cycle counts as occupying its lane. Sandbox memory is re-sampled before each reservation, not
  once per cycle. Today a review round and a Ready card of another kind could already both start in
  one cycle, so per-kind is the closest equivalent. A global limit of one would slow the lanes down.
- **Job cap.** The job-cap card check ("its slot is free") uses the same `lane_decision`, so a card
  waiting for a busy or higher lane gets no job-cap comment.
- **Lane waits.** Each cycle replaces the `lane-wait` settings namespace. It writes one entry per
  card or re-entry that is otherwise admissible but is rejected by `lane_decision`. The key is
  `<repo>:<number>` and the value is `{lane, cause, holder_run_id}`. An entry is cleared when that
  card is admitted or no longer waits. Status reads these entries.

### 5. CLI: `cli.py`

| Command | Config | Effect |
|---|---|---|
| `agent-factory lanes supported` | none | Prints `priority-lanes`, exits 0. A release without lanes exits 2 (argparse), which means unsupported. |
| `agent-factory --config L lanes downgrade --check` | yes | Read-only. Prints each offending kind with its claims (`<kind>: <claim id> <repo>#<n> <lane>`). Exits 1 if there are any, else 0. |
| `agent-factory --config L lanes downgrade` | yes | `restore_kind_guard(False)`. Same output and exit code. Already in kind mode with no violation: exits 0. |
| `agent-factory --config L lanes enable` | yes | `enable_lanes()`. |

`resident` calls `enable_lanes()` once at start.

### 6. Status: `operations.py`

`_slot_lines` becomes, per kind:

- `<kind> slot: free` when there is no unfinished attempt (unchanged, so `slots_free` keeps
  working);
- otherwise `<kind> slot: busy (<lanes, highest first>)`, followed by one line per busy lane,
  `<kind> lane <lane>: <repo>#<n> <unit> (<status>)`. A legacy holder prints
  `<kind> lane all (pre-lane attempt): ...`.

Lane lines do not match `^[a-z-]+ slot: `, so `slots_free` treats only the summary line. A line
`lanes: off (per-kind guard restored; the resident re-enables lanes when it starts)` appears in kind
mode. Waiting-claim lines and unclaimed lane waits (from `lane-wait`) print
`waits for <kind> lane <lane> (<holder>)` or `held by busy <kind> lane <lane> (<holder>)`. A review
round waiting for a lane uses the same wording.

### 7. Deploy: `scripts/lane-guard.sh` (new, sourced like `fixture-guard.sh`)

- **`lane_guard before`** runs beside `fixture_guard ... before`.
  - It does nothing if the target supports lanes (`"$executable" lanes supported`), or if the live
    release does not (exit 2: proceed as before).
  - Otherwise it runs `"$running" --config "$config" lanes downgrade --check`. On exit 1 it dies
    with the listed claims and the procedure: pause; let attempts settle, or cancel claims, until
    each kind has at most one unfinished attempt; deploy the older release. Nothing is deployed.
- **`lane_guard restore`** runs after `fixture_guard ... after` and before `point_at`.
  - It runs `"$running" ... lanes downgrade`, and dies paused on failure.
  - On success it sets `lanes_downgraded=true`, and installs an `EXIT` trap. If the script exits
    while the live lanes resident may still be running, the trap does two things:
    - it points the LaunchAgent and `shared_config` back at `$running` if `point_at` had already
      moved them (the doctor path already does this);
    - it runs `"$running" --config "$config" lanes enable`, so the live lanes release gets its
      lanes back.

    That covers every `die` before the resident's removal is confirmed: the doctor failure, and
    the "LaunchAgent did not unload" failure after the unload poll. In that failure `launchctl
    print` still shows the old lanes resident.
  - The recovery boundary is **confirmed removal**, not the `bootout` call. The script clears the
    trap only after the unload poll confirms the service is gone. From then on the older release
    is about to be live, and kind mode is what it needs. A later failure, such as bootstrap or the
    resident not running, keeps the per-kind guard and stays paused, as today.
- **The trap must not break `die`.** The trap preserves the exit status, and only warns if
  `lanes enable` fails. Kind mode is safe: it runs at most one attempt per kind.

## Decisions

1. **Lane mode is the presence of an index, switched only by the resident's start and explicit
   commands.**
   - Alternatives: switch on every writable open (rejected: the new release's doctor would drop the
     per-kind index under the older resident during a forward deploy, and the older release's
     supervisors share the database); a settings marker (rejected: a second source of truth that
     can disagree with the indexes).
2. **Re-entries (review, unblock) move into the ordered admission pass.**
   - Alternatives: a "higher Ready card pending" check in the first loop (rejected: it starves a
     Low review round while an ineligible High card, such as one under a quota hold, sits in Ready,
     which the Ready path never does).
3. **One reservation per kind per cycle** replaces one per cycle, which proposal decision 10
   recorded. The pass already re-reads lane state, and per-kind keeps today's ability to start a
   review round and other-kind work in the same cycle.
4. **The continuation classification is a pure function of the run history.** Episode starts are
   runs with reason `review`/`unblock`. Pre-suite retries fold into their episode. No new
   persisted episode column is needed, so older releases see no extra state.
5. **The lane column is called `lane`, and `NULL` means legacy holder.** No backfill is needed, and
   an older release's inserts after a rollback are automatically legacy holders if lanes are
   re-enabled later.
6. **Deploy recovery uses a trap until the old resident's removal is confirmed.** It mirrors the
   deploy's existing "stays paused" failure semantics. A failed rollback, including an unload that
   did not happen, does not leave the live lanes release in kind mode or pointing at the older
   release.

## Risks / Trade-offs

- **Concurrency load.** Up to four host attempts per host kind. The disk floor, sandbox memory
  (now re-sampled per reservation), quota, readiness, and job cap still apply. A cap would be a
  follow-up.
- **The deploy dies hard between restore and bootout**, for example from a kill. Kind mode is then
  left on the live lanes release. That is safe (single slot), and status shows `lanes: off`. The
  next resident start or deploy restores lanes.
- **A hand rollback** without the script skips the guard and the restore. The older release then
  runs with the lane index and loses atomic per-kind reservation. Documentation says so, as for the
  fixture guard.
- **Pre-suite failure semantics.** A pre-suite failure after `begin_run` counts as started (a
  continuation). One during planning, before launch, does not. This is the spec's "actually
  started" line, recorded here so tests pin it.
- **Status wording changes.** The skills are updated with it. `factory-assign`'s example
  `fix slot: <repo>#<n> fix (running)` becomes the summary-plus-lane form.

## Migration Plan

1. **Forward deploy.**
   1. Doctor runs on the new release: the column is added; still kind mode.
   2. Bootout and bootstrap.
   3. The new resident's start runs `enable_lanes()`.
   4. Runs still unfinished from the older release are legacy holders. Their kinds stay single-slot
      until they finish, and older supervisors keep recording results.
2. **Rollback.**
   1. `lane_guard before` refuses while any kind has more than one unfinished attempt.
   2. After the pause, `lanes downgrade` restores `one_nonterminal_run_per_kind` atomically.
   3. The older release becomes live.
   4. Any failure before bootout re-enables lanes on the live release through the trap.
3. **Re-upgrade after a rollback.** The same as a forward deploy. Runs the older release reserved
   are legacy holders.

## Testing

- **Unit.**
  - `lanes.lane_for` and `rank`: every level, case, unset, unknown.
  - `classify_reservation` table tests: no runs; started initial then recovery; planning failure
    (no `started_at`) then retry; review start; review pre-suite retry before and after start;
    unblock after a settled episode; eval repetition 2 after repetition 1.
- **Store integration** (temporary database).
  - Gate causes: lane-busy, higher-lane, legacy, kind-mode.
  - A continuation beside a higher lane.
  - Two connections racing `reserve_run` for the same lane: exactly one succeeds.
  - High plus a Low new start, in both forced transaction orders and as a race:
    - High first: Low fails with `higher-lane`.
    - Low first: both succeed, because High is never blocked by a lower lane.
  - `_ensure_lane_column` on a v4 database leaves it openable by the v4 `_migrate` path. A copy of
    the old store class, or a raw v4 schema check, confirms the per-kind index is untouched until
    `enable_lanes`.
  - `restore_kind_guard` with offenders (no change, offenders listed) and without (index swapped).
  - `enable_lanes` with a legacy holder.
- **Runtime integration** (fake GitHub client, as in `tests/integration/test_per_kind_slots.py`).
  Every scenario in the spec deltas:
  - Medium beside Low, then High beside both;
  - Low and Medium held while High runs;
  - a second High waits;
  - Urgent holds all;
  - other kinds unaffected;
  - an eval repetition continues beside a higher eval;
  - a never-launched claim waits;
  - unblock and review are new starts;
  - a High Ready card beats a Low review round in the same cycle;
  - one reservation per kind per cycle;
  - an unset-Priority review round against an explicit Low Ready card: the Low card goes first;
  - review precedence at equal Priority;
  - writer feedback after a review `needs-input` (`blocked_by == "review"`) reserves a review run,
    for fix, feature, and task;
  - a settled fix or task dragged to Ready with no eligible review feedback reserves a fresh
    initial run;
  - legacy holder after upgrade;
  - job-cap card waiting for a busy lane gets no comment.
- **Status.** Summary and lane lines, legacy line, kind-mode line, and lane-wait reasons.
  `scripts/slots.sh` predicates against both old and new status output (extend
  `tests/integration/test_deploy_slots.py`).
- **Deploy.** Extend the `test_deploy_fixture_guard.py` style with fake executables: refusal before
  pause with offenders; restore after pause; a second lane started between the checks fails the
  restore while paused; the doctor-failure revert runs `lanes enable`; an unload failure (with `launchctl print` still showing the old resident) restores the pointers and runs `lanes enable`, while a bootstrap failure after a confirmed unload does not; a target with lanes is
  unaffected; a live release without `lanes supported` proceeds.
