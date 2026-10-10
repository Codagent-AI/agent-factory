## Why

Each work kind (eval, fix, feature, task) has one execution slot today, enforced by the SQLite
unique index `one_nonterminal_run_per_kind`. Admission ranks Ready cards by Priority, then newest
created, but ranking only decides who gets a slot once it is free. A High or Urgent card waits
behind whatever Low card got there first, and a long attempt can hold it up for hours. The
attempt can run up to its total limit; a feature or eval can take several attempts. The Priority
field already says what Paul wants run first, but the factory only uses it for ordering. It
cannot let urgent work start next to routine work.

Issue #143 asks for a modified priority queue in each kind. Each Priority level gets its own slot,
so higher-priority work starts at once. Lower-priority work does not start while higher-priority
work of the same kind runs. Work that is already running is never interrupted. This matters now
because the factory runs more kinds and longer feature and task attempts, and Paul has to cancel
or wait out low-priority work to get an urgent fix moving.

**Verdict: go with caveats.** The change fits the existing per-kind design. The SQLite guard,
selection order, and status lines already split by kind, and this adds Priority as a second key.
There is no simpler alternative that gives the requested behavior. Raising one global concurrency
number would not give higher priorities a guaranteed slot. The caveats are more concurrent host
attempts, a schema change that must stay safe for rollback, and the status format that
`scripts/deploy.sh` and the skills parse. All three are bounded below.

## What Changes

- **Lanes.** Each work kind has one execution lane per Priority level: Urgent, High, Medium, Low.
  At most one unfinished attempt (`reserved`, `running`, or `observing`) of a kind runs per lane.
  A card with no Priority uses the Low lane, consistent with "unset sorts last". So each kind runs
  at most four attempts at once instead of one.
- **Admission rule.** An attempt of kind K at Priority P may be reserved only when (a) K's lane P
  is free, and (b) no unfinished attempt of K runs in a higher lane. A higher lane being busy
  blocks new starts in every lower lane of the same kind. Kinds stay independent: a running High
  fix does not block a Low eval.
- **No preemption.** Attempts already running continue to completion, whatever starts above them.
  Changing a card's Priority does not interrupt or move a running attempt. This keeps the existing
  rule that reprioritizing never interrupts active work.
- **Which attempts the rule gates.** The issue says no *new issue* of lower priority is
  *admitted*. That is read as follows:
  - New starts are gated: a first admission, a fresh re-admission, a blocked claim resuming
    after `needs-input` is answered, and a review round on a settled claim.
  - A continuation of a running issue needs only its own lane to be free; a busy higher lane does
    not hold it back. Examples are the next eval repetition and a retry after technical recovery.
    Whether a reservation counts as a continuation comes from saved execution history, not from
    the claim's lifecycle. The claim must already have an attempt that actually started (not just
    one that was reserved) in its current execution episode. An episode begins at the claim's
    admission, fresh re-admission, unblock, or review round.
  - A claim that is `active` or `waiting` but never started an attempt is still a new start. That
    covers a newly accepted claim and one held by a pre-launch preparation or readiness failure.
    So does a reserved attempt that failed before launch: it keeps the episode's original
    admission category.
- **Lane assignment.** An attempt's lane is the card's current Priority when the attempt is
  reserved. It is recorded on the run, so status, the guard, and recovery see a stable lane even
  if the card is reprioritized later.
- **Same guard everywhere.** The resident service, `tick`, manual execution commands, review
  rounds, and blocked-claim resumes all reserve through one atomic SQLite check. That check covers
  both the lane's uniqueness and the higher-lane block, so two paths cannot race past either rule.
- **Status and tooling.** `status` reports each kind's busy lanes with holder and progress.
  `<kind> slot: free` stays as the line when the whole kind is idle, so `scripts/slots.sh`
  (`slots_free`) and the skills keep working. Lines that name a waiting claim give the lane and,
  when blocked by a higher lane, that reason. The `factory-status`, `factory-assign`, and
  `factory-watch` skills, `AGENTS.md`, and `docs/operations.md` describe lanes. That includes the
  resource guidance in the operations docs, which today sizes the machine for "one eval and one
  fix concurrently".
- **Upgrade.** A run that is unfinished when lanes arrive has no recorded Priority. Until it
  finishes, it is a legacy holder that occupies every lane of its kind. That kind keeps today's
  one-at-a-time behavior until the run settles, so an Urgent or High run that was already going is
  never undercut by a Medium or High start right after the deploy.
- **Rollback guard.** `scripts/deploy.sh` refuses to roll back to a release without lanes while
  any kind has more than one unfinished run, and names the claims. This follows the existing guard
  against rolling back past fixture support. When a rollback is allowed, the deploy atomically
  restores the per-kind unique index before the older resident starts. It does this while the
  factory is paused, through a command in the current release. The older release's reservation
  code relies on that index for atomicity, so the database contract it expects is back in place.
- No **BREAKING** changes to configuration or to the GitHub-facing contract. The persisted schema
  change is additive. Rollback goes through the guarded downgrade above (see Technical Approach).

## Capabilities

### New Capabilities

None. Lanes extend the existing execution-slot and selection requirements rather than adding a
separate behavioral area.

### Modified Capabilities

- `factory-claim-lifecycle`: "at most one attempt per work kind" becomes at most one per kind and
  Priority lane, with the higher-lane admission block, no preemption, a recorded lane per run,
  atomic enforcement across all reservation paths, continuation decided from saved run history,
  legacy runs holding their whole kind after upgrade, and a migration that keeps rollback possible
  with a guarded index restore.
- `factory-eval-intake`: selection by Priority fills lanes; a higher eval starts beside a running
  lower one; lower evals do not start while a higher one runs.
- `factory-bug-intake`: same for fixes. Replaces "each kind fills only its own execution slot".
- `factory-feature-intake`: same for features, including resumes after `needs-input`.
- `factory-task-intake`: same for tasks.
- `factory-pull-request-lifecycle` / `factory-review-execution`: review rounds on settled claims
  wait for their lane, and for higher lanes of their kind to clear.
- `factory-operations`: `status` lane lines and waiting reasons, `tick` applying the lane guard,
  the job-cap "card waiting for a busy slot" episode becoming lane-aware, deploy's "every slot is
  free" check keeping its meaning (no attempt in any lane), the deploy rollback guard and
  downgrade command, and the operations documentation and skills updates.

## Technical Approach

- **Data.** Add a `lane` column to `run`, filled at reservation. Swap the unique index
  `one_nonterminal_run_per_kind` for one on `(kind, lane)` over unfinished runs. Existing
  unfinished runs get a legacy marker (no lane). The guard treats a legacy run as occupying every
  lane of its kind, so its kind stays single-slot until it settles. Assigning such runs to Low
  would let a higher start undercut a running High or Urgent attempt. Follow the existing pattern
  for the watch and notify schemas: an idempotent change outside the `user_version` bump. The
  previous release, and supervisors still running from it, can then still open the database and
  update their runs. A version bump would make them fail with "factory database is newer than
  this controller" during a deploy. Supervisors only update runs; they never reserve them. So the
  index change does not affect them.
- **Downgrade.** Add a command in this release, for example `agent-factory lanes downgrade`. Under
  one SQLite transaction it checks that each kind has at most one unfinished run, then restores
  `one_nonterminal_run_per_kind`. `deploy.sh` runs it, with the factory paused, before switching
  to a release without lanes, and refuses the rollback if the check fails. A hand rollback that
  skips the script bypasses the guard, as with the fixture guard. The design must document this.
- **Guard.** `ClaimStore.reserve_run` takes the lane. In the same transaction it rejects the
  reservation when a legacy run or a higher lane of the kind has an unfinished run. The exception
  is a continuation, and the store decides that from the claim's saved run history in the same
  transaction, not from a caller flag or the claim's lifecycle. The unique index then enforces
  lane exclusivity. Everything that
  calls `nonterminal_runs(kind=...)` to mean "the slot is free" becomes a lane query: the runtime's
  `slot_free` map, review rounds, blocked resumes, and status.
- **Selection.** `runtime.py` already walks cards in Priority order. Its per-kind `slot_free`
  boolean becomes a per-kind view of lane occupancy and the highest busy lane, evaluated against
  each card's Priority. The "one reservation per tick" loop break becomes one reservation per kind per
  cycle (see design.md), so starting several lanes is not throttled to one start per tick across all kinds.
- **Status compatibility.** Keep the `<kind> slot: ...` summary line format that `slots.sh`
  parses, and add lane detail beneath it. That way a deploy script from either side of the change
  reads the other's status correctly.
- **Resources.** Up to four host attempts per host kind can now run at once. Disk-floor, memory,
  quota, readiness, and job-cap holds are already checked before each admission and keep bounding
  this. Design should confirm that each check is taken per admission and not cached per kind for
  the tick. If they are not enough, add a per-kind lane cap as a follow-up rather than now.

## Out of Scope

- Preempting, pausing, or cancelling a running attempt because higher-priority work arrived.
- Per-kind or per-lane configuration: enabling or disabling lanes, custom lane counts, mapping
  levels to lanes, or a concurrency cap. Lanes apply to every kind unconditionally.
- Cross-kind priority (a High fix blocking a Low eval).
- Changing how cards are ranked (Priority, then newest created) or the Priority field's values.
- Changes to job-cap counting.
- Watch-dispatch concurrency, which has its own cap.

## Impact

- **Code:** `store.py` (schema, `reserve_run`, lane queries), `runtime.py` (admission gating),
  `work_kinds/pull_request/review.py` and `blocked.py` (lane-aware busy checks), `operations.py`
  (status lines, waiting reasons), `job_cap.py` (busy-slot episode), manual execution paths in
  `cli.py`, and tests across them.
- **Scripts and docs:** `scripts/slots.sh` stays compatible and gets checked. Update
  `docs/operations.md`, `AGENTS.md`, and the `factory-status`, `factory-assign`, and
  `factory-watch` skills.
- **Operations:** Higher peak load on the Mac (host fix, feature, and task attempts) and on Fly
  (concurrent eval claims each build an image and run Machines). Spend and capacity can rise when
  several priorities are queued at once. The job cap still bounds attempts per window.
- **Rollback:** Rolling back to a release without lanes requires each kind to have at most one
  unfinished run. To get there: pause, let the extra lanes settle or cancel them, then deploy the
  older release. `deploy.sh` restores the per-kind index first. A release from before this change
  can always open the database, and supervisors still running from it keep working across the
  deploy in either direction.
- **Deploy script:** `scripts/deploy.sh` gains the lane rollback guard and the downgrade step.
- **Users:** Paul sees urgent and high-priority cards start at once beside routine work. Lower
  cards wait while higher work of the same kind runs.
