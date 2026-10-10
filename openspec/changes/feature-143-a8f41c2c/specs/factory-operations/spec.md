## MODIFIED Requirements

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind:

- each busy Priority lane with its holder and progress, and a summary line that starts with `<kind> slot: ` and reads exactly `<kind> slot: free` when no attempt of the kind is unfinished in any lane;
- waiting work and why it waits, including work waiting for its own lane and work held because a higher lane of its kind is busy, naming that lane and its holder;
- blocked fix, feature, and task claims;
- settled fix, feature, and task claims with eligible review comments waiting for their lane or for a higher lane of their kind;
- pending merge syncs and their last failure reason;
- pause state and blocking conditions;
- the next permitted start time, when it can be determined;
- the factory job cap: the attempts counted in its window against the cap and the window length;
  while the cap is reached, also that it is reached, its earliest clear time, and the reset
  command;
- the unclaimed Ready cards that wait only for the job cap in the current cap episode.

For an eval attempt under Fly execution it SHALL show the Machine identity, the Machine's
state including whether it is stopped for a quota hold, and the attempt's recorded deadline.
It SHALL also list Machines that reconciliation reported as unknown to the store or as failed
cleanup.

It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a
merge sync, or holding a recorded cleanup failure. A recorded cleanup failure includes a
failed terminal release, an undelivered human-review expiry report, and a failed,
refused, or skipped registry image deletion. For each such failure it SHALL show the
claim, the failed item, and the last reason, whether the claim is settled, cancelled, or
superseded. Claims whose card is Done with nothing pending, and superseded claims with
nothing pending, SHALL be omitted unless `--all` is given, which lists every saved claim.

It SHALL expose enough saved state to distinguish active execution, an admission-window
wait, a usage hold, a memory or disk hold, a job-cap hold, an unavailable prerequisite, a blocked claim, a
waiting review round, and unfinished reporting. Status SHALL remain usable while execution
is active and SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite, memory, or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

#### Scenario: Inspect both slots

- **WHEN** an eval is running and a fix is blocked awaiting input with the `needs-input` label
- **THEN** status shows the eval lane's holder, `fix slot: free`, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but its lane, or a higher lane of its kind, is busy
- **THEN** status names the claim, the PR, and the busy lane it waits for

#### Scenario: Inspect an installation with history

- **WHEN** the database holds many Done and superseded claims and one running claim
- **THEN** status lists the running claim and none of the settled ones
- **AND** `status --all` lists every saved claim

#### Scenario: Inspect a Fly attempt

- **WHEN** the operator requests status while an eval repetition runs in, or is stopped in, a Fly Machine
- **THEN** status shows the Machine identity, whether it is running or stopped for a quota hold, and the attempt's deadline

#### Scenario: Inspect a reconciliation finding

- **WHEN** reconciliation has reported a Machine unknown to the store or a cleanup that could not be verified
- **THEN** status lists that Machine and the reported reason until it is resolved

#### Scenario: Inspect a blocked feature

- **WHEN** a feature claim stopped during definition
- **THEN** status shows `feature slot: free` and the blocked feature with its stop reason and pushed branch

#### Scenario: Inspect a failed registry image deletion

- **WHEN** a superseded eval claim's registry image deletion failed on the last poll
- **THEN** plain `status` lists that claim with the image tag and the registry's reason until a later poll deletes the image

#### Scenario: Inspect a declined task

- **WHEN** a task claim was declined by triage
- **THEN** status shows `task slot: free` and the blocked task with its decline reason

#### Scenario: Inspect the task slot

- **WHEN** a task attempt is running
- **THEN** status shows the task lane's holder and progress beside the eval, fix, and feature slot lines

#### Scenario: Inspect the job cap below its limit

- **WHEN** 42 attempts have started in the last 24 hours and the cap is 100 attempts per 24 hours
- **THEN** status shows 42 of 100 attempts in the last 24 hours and no job-cap hold

#### Scenario: Inspect a reached job cap

- **WHEN** the job cap is reached, a fix claim's retry is held by it, and a Ready feature card waits only for it
- **THEN** status shows that the cap is reached, its earliest clear time, and the reset command; it shows the fix claim waiting on the job cap; and it lists the feature card's issue as waiting for the job cap

#### Scenario: Inspect several busy lanes of one kind

- **WHEN** a Low and a High fix attempt are running
- **THEN** status shows a `fix slot: ` summary line that does not read `free`, and each busy fix lane with its Priority, holder, and progress

#### Scenario: Inspect work held by a higher lane

- **WHEN** a High fix attempt is running and an eligible Medium bug waits in Ready with the Medium fix lane free
- **THEN** status shows the Medium bug waiting because the High fix lane is busy, and names that lane's holder

#### Scenario: Inspect an attempt that predates lanes

- **WHEN** a fix attempt with no recorded lane is unfinished
- **THEN** status shows that the attempt holds every fix lane until it finishes

### Requirement: Run an immediate normal cycle with tick

`agent-factory tick` SHALL perform one normal polling cycle immediately, including reconciliation, merge syncs, and pending reporting, and MAY start eligible work in any free lane. It SHALL apply the same admission windows, pause state, factory job cap, prerequisite, memory, and quota holds, and Priority lane guard as the resident service. It SHALL NOT act as a preview or force work past those controls. Execution started through tick SHALL receive the same supervision, persistence, and recovery guarantees as service-started execution.

#### Scenario: Tick with eligible queued work

- **WHEN** the operator runs tick with eligible work whose lane is free, no higher lane of its kind busy, and all admission conditions satisfied
- **THEN** the factory performs the normal cycle and can start the selected request under normal supervision

#### Scenario: Tick while execution is disallowed

- **WHEN** the operator runs tick outside a kind's window, while paused, while the factory job cap is reached, or while the work's lane or a higher lane of its kind is occupied
- **THEN** tick does not bypass the blocking condition or start overlapping execution
- **AND** its cycle can still reconcile existing work, syncs, and reporting

## ADDED Requirements

### Requirement: Restore the per-kind guard before rolling back past lanes

`scripts/deploy.sh` SHALL NOT make a release live that does not support Priority lanes while any work kind has more than one unfinished attempt. Making a release live means pointing the LaunchAgent or `shared_config` at it, or moving `releases/current` to it. Before such a release is made live, the deploy SHALL restore the database's guard of one unfinished attempt per kind, on which a release without lanes relies for atomic reservation.

The target release's own executable SHALL answer whether it supports lanes, through a read-only `agent-factory lanes supported` command that prints `priority-lanes` and needs no configuration. A target release that lacks the command, or does not print `priority-lanes`, does not support them. The live release's executable SHALL provide `agent-factory --config <local> lanes downgrade`, which in one atomic step verifies that no kind has more than one unfinished attempt and restores the per-kind guard. With `--check` it SHALL only verify, and change nothing. When the verification fails, the command SHALL change nothing, exit with failure, and name each kind with more than one unfinished attempt, with its claims and issues. It SHALL also provide `agent-factory --config <local> lanes enable`, which re-establishes lane enforcement.

The deploy SHALL check before it pauses the factory. On refusal at that point it SHALL stop with a failing exit status, deploy nothing, and leave the factory's pause state unchanged. It SHALL name each affected kind with its claims and issues. The Agent Runner checkout fast-forward and building the target release's immutable worktree are the only steps that may still have run. The deploy SHALL restore the guard after pausing and before it changes the LaunchAgent, `shared_config`, or `releases/current`. If restoring fails at that point, it SHALL stop, name the affected claims, leave the live release unchanged, and leave the factory paused, as other post-pause deploy failures do.

The refusal message SHALL give the rollback procedure:

1. pause the factory;
2. let attempts settle, or cancel claims, until each kind has at most one unfinished attempt;
3. deploy the older release.

A release that supports lanes SHALL establish lane enforcement when its resident starts, and when `lanes enable` runs. No other command, and no supervisor, SHALL switch between lane enforcement and the per-kind guard. If the deploy stops after restoring the guard and before it has confirmed that the live lanes release's resident is gone, it SHALL point the LaunchAgent and `shared_config` back at the live release and run `lanes enable` with it before it exits. That covers a failed `doctor` and a resident that did not unload. Once the resident's removal is confirmed, a later failure SHALL keep the per-kind guard, for the older release. While the per-kind guard is in place, a release that supports lanes SHALL start at most one attempt per kind, and `status` SHALL show that lanes are off and that the resident re-enables them when it starts. If the live release does not support lanes, the deploy SHALL proceed as before this change. A deploy of a release that supports lanes SHALL be unaffected. The deploy SHALL offer no option that bypasses the check.

#### Scenario: Roll back while two lanes of a kind are busy

- **WHEN** the operator deploys a release without lanes while a Low and a High fix attempt are both unfinished
- **THEN** the deploy stops before pausing the factory, names the fix kind with both claims and their issues, gives the rollback procedure, exits with failure, and leaves the live release, its plist, `shared_config`, and `releases/current` unchanged

#### Scenario: Roll back with at most one attempt per kind

- **WHEN** the operator deploys a release without lanes while one fix attempt and one eval attempt are unfinished and no kind has more than one
- **THEN** the deploy pauses, restores the per-kind guard, makes the older release live, and the older release reserves attempts atomically one per kind

#### Scenario: A second lane starts during the deploy

- **WHEN** a second fix attempt starts after the deploy's first check and before the factory is paused, and the target release has no lanes
- **THEN** restoring the guard after the pause fails, the deploy names the claims, and it leaves the live release unchanged with the factory paused

#### Scenario: Deploy to a release with lanes

- **WHEN** the operator deploys a release that supports lanes while several lanes of a kind are busy
- **THEN** the deploy proceeds as before this change, and the running attempts keep their release and lanes

#### Scenario: A failed rollback keeps the lanes release

- **WHEN** a deploy restored the per-kind guard, then failed `doctor` and pointed the service back at the release with lanes
- **THEN** the deploy runs `lanes enable` with that release before exiting, and lane admission resumes once the factory is resumed

#### Scenario: Lanes stay off after an interrupted rollback

- **WHEN** a deploy restored the per-kind guard and was killed before it could re-enable lanes, leaving the lanes release live
- **THEN** that release starts at most one attempt per kind, status shows that lanes are off, and lanes return when its resident next starts or `lanes enable` runs

#### Scenario: The live resident does not unload during a rollback

- **WHEN** a deploy restored the per-kind guard for a rollback and the live lanes resident is still running after the unload wait
- **THEN** the deploy points the LaunchAgent and `shared_config` back at the live release, runs `lanes enable` with it, and stops with the factory paused

#### Scenario: The older resident fails to start after unload

- **WHEN** a rollback deploy confirmed the lanes resident was gone and the older release's resident then failed to start
- **THEN** the deploy keeps the per-kind guard and stops with the factory paused, and does not run `lanes enable`

### Requirement: Document Priority lanes

The operations documentation, `AGENTS.md`, and the repository's `factory-status`, `factory-assign`, and `factory-watch` skills SHALL describe Priority lanes. They SHALL explain:

- that each work kind runs at most one attempt per Priority level, Urgent, High, Medium, and Low, and that unprioritized work uses the Low lane;
- that a higher-priority start never waits for lower-priority work, and that new lower-priority work of the same kind does not start while higher-priority work runs;
- that running work is never preempted or moved by a Priority change;
- that a claim already under way continues its repetitions and retries in its lane, while admissions, resumes, and review rounds are new starts;
- how status shows each kind's lanes and why work waits;
- that attempts reserved before the upgrade hold their whole kind until they finish;
- the disk and memory the machine needs when several lanes of the host kinds run together, and that the existing disk, memory, quota, readiness, and job-cap holds still bound admission;
- the deploy's refusal to roll back past lanes and its rollback procedure, and that a rollback done by hand, or with a deploy script without this check, bypasses the refusal and the guard restoration.

#### Scenario: Learn why a card waits

- **WHEN** an operator reads the documentation because a Ready Medium bug has not started while a High fix runs
- **THEN** it tells them that the High fix holds lower fix lanes until it finishes, and how status names the holder

#### Scenario: Roll back past lanes

- **WHEN** an operator reads the documentation before rolling back to a release without lanes
- **THEN** it tells them to pause and let attempts settle, or cancel claims, until each kind has at most one unfinished attempt, and that the deploy refuses the rollback otherwise
