## MODIFIED Requirements

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind:

- the slot holder and progress;
- waiting work and why it waits;
- blocked fix, feature, and task claims;
- settled fix, feature, and task claims with eligible review comments waiting for their kind's slot;
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
- **THEN** status shows the eval slot's holder, the fix slot as free, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but its kind's slot is busy
- **THEN** status names the claim, the PR, and that it waits for the slot

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
- **THEN** status shows the feature slot as free and the blocked feature with its stop reason and pushed branch

#### Scenario: Inspect a failed registry image deletion

- **WHEN** a superseded eval claim's registry image deletion failed on the last poll
- **THEN** plain `status` lists that claim with the image tag and the registry's reason until a later poll deletes the image

#### Scenario: Inspect a declined task

- **WHEN** a task claim was declined by triage
- **THEN** status shows the task slot as free and the blocked task with its decline reason

#### Scenario: Inspect the task slot

- **WHEN** a task attempt is running
- **THEN** status shows the task slot's holder and progress beside the eval, fix, and feature slots

#### Scenario: Inspect the job cap below its limit

- **WHEN** 42 attempts have started in the last 24 hours and the cap is 100 attempts per 24 hours
- **THEN** status shows 42 of 100 attempts in the last 24 hours and no job-cap hold

#### Scenario: Inspect a reached job cap

- **WHEN** the job cap is reached, a fix claim's retry is held by it, and a Ready feature card waits only for it
- **THEN** status shows that the cap is reached, its earliest clear time, and the reset command; it shows the fix claim waiting on the job cap; and it lists the feature card's issue as waiting for the job cap

### Requirement: Run an immediate normal cycle with tick

`agent-factory tick` SHALL perform one normal polling cycle immediately, including reconciliation, merge syncs, and pending reporting, and MAY start eligible work in any free slot. It SHALL apply the same admission windows, pause state, factory job cap, prerequisite, memory, and quota holds, and per-kind slot guard as the resident service. It SHALL NOT act as a preview or force work past those controls. Execution started through tick SHALL receive the same supervision, persistence, and recovery guarantees as service-started execution.

#### Scenario: Tick with eligible queued work

- **WHEN** the operator runs tick with eligible work, a free slot for its kind, and all admission conditions satisfied
- **THEN** the factory performs the normal cycle and can start the selected request under normal supervision

#### Scenario: Tick while execution is disallowed

- **WHEN** the operator runs tick outside a kind's window, while paused, while the factory job cap is reached, or while that kind's slot is occupied
- **THEN** tick does not bypass the blocking condition or start overlapping execution
- **AND** its cycle can still reconcile existing work, syncs, and reporting

### Requirement: Configure service-driven watching

The shared configuration SHALL accept an optional `[watch]` section with these settings:

- `enabled` (default false);
- the factory `repository` (`owner/name`) that dispatched sessions check out, required when watching is enabled;
- the default dispatch `agent` profile in `cli:model:effort` form, required when watching is enabled;
- optional per-event profiles for `PR-READY` and `FAILURE`;
- the concurrency cap (default 2, at least 1);
- the failure grace period in minutes (default 7, zero or more);
- the session timeout in minutes (default 90, at least 1).

When watching is enabled, configuration loading SHALL fail on a missing repository or default profile, a profile that is not in `cli:model:effort` form, an unknown event name, or a value out of range, and the failure SHALL name the setting. A missing section, or `enabled = false`, SHALL keep today's behavior. The Codagent example configuration SHALL enable watching with the default profile `claude:claude-sonnet-5-5:medium`. Each cycle SHALL read the watch settings from the configuration it loads, so a changed profile, concurrency cap, grace period, or timeout applies to dispatches that start after the change. A session that is already running SHALL keep its profile and timeout.

The section SHALL have no per-day session budget. A `daily_sessions` key left in the section SHALL NOT fail configuration loading and SHALL have no effect.

#### Scenario: Configure the dispatch model and budget

- **WHEN** the shared configuration enables watching with the agent `claude:claude-sonnet-5-5:medium` and a concurrency cap of 3, and sets no session budget because none exists
- **THEN** dispatched sessions run with that profile, no more than 3 run at once, and no event is skipped because of how many sessions started that day

#### Scenario: A leftover budget setting

- **WHEN** watching is enabled and the `[watch]` section still sets `daily_sessions = 5`
- **THEN** configuration loads, and a sixth event in a local day starts a session like any other

#### Scenario: Reject an invalid profile

- **WHEN** watching is enabled with the agent `sonnet`
- **THEN** configuration loading fails and names the `[watch]` agent setting

#### Scenario: Leave watching unconfigured

- **WHEN** the shared configuration has no `[watch]` section
- **THEN** the factory detects no watch event, starts no session, and shows watching as disabled in status

### Requirement: Report watch dispatches in status

When watching is enabled, `agent-factory status` SHALL show a watch section with:

- whether watching is enabled;
- the time of the last detection pass;
- each `launched` dispatch with its event, claim, pull request when there is one, model profile, and elapsed time;
- the number of `pending` dispatches, and why they wait: the concurrency cap, a failing watch doctor group, or a running check of the same pull request;
- each ended dispatch whose usage delivery to the development-audit destination did not succeed;
- the number of sessions started today and today's known estimated cost, with no budget;
- every dispatch recorded `interrupted`, `timed-out`, `launch-failed`, or `budget-exhausted` whose claim is not yet observed Done, cancelled, or superseded;
- each undelivered dispatch comment with its last failure reason;
- for each completed dispatch whose claim is not yet observed Done, cancelled, or superseded, the factory issues its session filed or updated, with the pull request, or the claim's issue for a triage.

When watching is disabled, status SHALL show one line saying so, and it SHALL still list `launched` and `pending` dispatches. Status SHALL NOT start or change any dispatch.

#### Scenario: Inspect a running triage

- **WHEN** a triage session is running and one PR-READY check dispatch waits for the cap
- **THEN** status shows the triage session's event, claim, profile, and elapsed time, and one pending dispatch waiting for the concurrency cap

#### Scenario: Inspect the day's spend

- **WHEN** seven sessions have started today with known costs
- **THEN** status shows seven sessions started today and the sum of their estimated costs, and shows no session budget

#### Scenario: Inspect a dispatch an earlier release skipped for budget

- **WHEN** a `FAILURE` dispatch that an earlier release recorded `budget-exhausted` belongs to a claim not yet observed Done, cancelled, or superseded
- **THEN** status lists that dispatch as `budget-exhausted`

#### Scenario: Inspect a failed dispatch

- **WHEN** a PR-READY check dispatch timed out for a claim that is still in Review
- **THEN** status lists that dispatch as `timed-out` with its pull request and evidence path

#### Scenario: Inspect the issues a check filed

- **WHEN** a PR-READY check completed and filed one factory issue for a claim still in Review
- **THEN** status shows the pull request and the filed issue's URL on one line

### Requirement: Redispatch a watch event

`agent-factory watch redispatch <dispatch>` SHALL queue a new `pending` attempt for the same event as the named dispatch, under a new attempt key. It SHALL accept only a `PR-READY` or `FAILURE` dispatch whose state is `completed`, `interrupted`, `timed-out`, `launch-failed`, or `budget-exhausted`. It SHALL refuse any other dispatch, naming its state, and change nothing. The new attempt SHALL be processed like any `pending` dispatch, under the concurrency cap, the watch readiness checks, and one session per pull request at a time. The command SHALL print the new dispatch's id. When watching is disabled, it SHALL say that the attempt waits until watching is enabled. The command SHALL NOT start the session itself.

#### Scenario: Redispatch an interrupted review

- **WHEN** the operator redispatches an `interrupted` `PR-READY` dispatch
- **THEN** a new pending attempt is queued, and the next cycle starts one PR-READY check session for that pull request

#### Scenario: Refuse a running dispatch

- **WHEN** the operator redispatches a `launched` dispatch
- **THEN** the command names the `launched` state, queues nothing, and exits with an error

#### Scenario: Redispatch an event skipped for budget

- **WHEN** the operator redispatches a `FAILURE` dispatch that an earlier release recorded `budget-exhausted`
- **THEN** a new pending attempt is queued, and a cycle starts one triage session for it once the concurrency cap and readiness checks allow

### Requirement: Document the service-driven watcher

The operations documentation SHALL describe service-driven watching as the normal mode, and SHALL state that the watcher's job is to make sure the factory itself works, not to review the code the factory builds:

- the two events and what each one does;
- the `[watch]` settings and their defaults, and how to escalate a failure to a stronger model;
- the concurrency behavior; that no event is skipped because of session volume, which the factory job cap bounds instead; and that `budget-exhausted` dispatches from earlier releases remain in history and can be redispatched;
- the actions a dispatched session may and may not take, including that it files issues for factory defects and never fixes anything;
- that triage runs after a failed claim's automatic retry and does not hold it;
- the watch doctor group, including the `gh` login that files issues;
- the watch section of status;
- `watch redispatch`;
- how to find a dispatch's evidence and usage.

They SHALL state that no interactive watcher session is used. An on-demand `factory-status` skill SHALL report the factory's state once when asked, without watching or polling. A `factory-triage` skill SHALL hold the failure-handling procedure and both headless procedures that the watch workflow's sessions follow: the PR-READY check and the failure triage. The factory PR review skill and its reviewer agent SHALL have no headless mode; they review a pull request only when the operator asks.

#### Scenario: Operate the service watcher

- **WHEN** an operator follows the documentation to enable watching
- **THEN** they can set the profile and concurrency cap, pass the watch doctor group, find running and failed dispatches and the issues they filed in status, and redispatch a failed one

#### Scenario: Ask for a factory update

- **WHEN** the operator asks an agent for a factory update
- **THEN** the agent follows `factory-status`, reports once what waits on the operator, what is running, and what failed, and starts no watcher

#### Scenario: Ask for a PR review

- **WHEN** the operator asks an agent to review a factory pull request
- **THEN** the agent follows `factory-pr-review` interactively; no watch session reviews it

## ADDED Requirements

### Requirement: Cap attempts started across the factory

The shared configuration SHALL accept an optional `[job_cap]` section with these settings:

- `attempts`: the most attempts that may start in the window, across all work kinds. It defaults
  to 100 and SHALL be an integer of at least 1.
- `window_hours`: the length of the rolling window. It defaults to 24 and SHALL be an integer of
  at least 1.

The cap SHALL always apply. When the section is missing, the defaults apply. Configuration loading
SHALL fail on a value that is not an integer or is out of range, and the failure SHALL name the
setting. Each cycle SHALL read the cap from the configuration it loads. The Codagent example
configuration SHALL set the section explicitly.

The factory SHALL count every attempt it reserves whose reservation time is within the last
`window_hours` hours and not earlier than the latest job-cap reset:

- an initial attempt;
- a retry;
- a recovery;
- an unblock;
- a review round;
- an eval repetition.

Watch sessions, merge syncs, and post-run audits SHALL NOT count. The cap is reached while the
count is at or above `attempts`.

While the cap is reached, the factory SHALL NOT reserve any new attempt of any kind:

- it SHALL NOT claim a new Ready card;
- it SHALL NOT start a retry, recovery, unblock, review round, or eval repetition.

The check and the reservation SHALL be one atomic step for every reservation path. So concurrent
cycles, a `tick` overlapping the resident, or several paths in one cycle cannot together start
more attempts than the cap allows.

An attempt held by the cap SHALL NOT consume an execution or recovery retry. Its claim SHALL keep
its lifecycle, frozen inputs, completed work, and card status. Attempts already running SHALL
continue under their own limits. The hold SHALL clear without operator action once enough counted
attempts leave the window for the count to fall below `attempts`. Held work then starts in a later
cycle under the normal admission controls.

The earliest clear time is when the (N − `attempts` + 1)th oldest counted attempt leaves the
window, where N is the count. This assumes no reset and no configuration change. `pause` and
`resume` SHALL NOT change the count or clear the hold. The resident SHALL log one line when it
finds the cap reached, and one when it finds the cap clear again.

#### Scenario: Reach the cap

- **WHEN** the cap is 100 attempts per 24 hours, 100 attempts have started in the last 24 hours, and a fix claim's automatic retry is due
- **THEN** no attempt is reserved, the claim stays active with its retry unconsumed, and a running eval repetition continues

#### Scenario: Every reservation path is held

- **WHEN** the cap is reached while a blocked claim has a writer's answer, a claim in Review has eligible review comments, and an eval claim has a repetition left
- **THEN** no unblock, review round, or repetition starts, and each claim keeps its lifecycle and card status

#### Scenario: The cap clears with the window

- **WHEN** the cap is reached and the oldest counted attempt leaves the 24-hour window
- **THEN** the count falls below the cap, and a later cycle starts held work under the normal admission controls

#### Scenario: Concurrent reservations at the edge

- **WHEN** 99 of 100 attempts have started in the window, and the resident's cycle and an operator's `tick` each try to reserve an attempt at the same time
- **THEN** exactly one attempt is reserved

#### Scenario: Work that does not count

- **WHEN** a watch session, a merge sync, and a post-run audit run in the window
- **THEN** the job cap count does not change

#### Scenario: A lowered cap

- **WHEN** 120 attempts count, and a configuration change lowers `attempts` from 150 to 100
- **THEN** the cap is reached, and its earliest clear time is when the 21st-oldest counted attempt leaves the window

#### Scenario: A raised cap

- **WHEN** the cap is reached at 100, and a configuration change raises `attempts` to 150
- **THEN** the next cycle finds the cap clear and can start held work

#### Scenario: Resume does not clear the cap

- **WHEN** the factory is paused while the cap is reached and the operator resumes it
- **THEN** the pause clears, and no attempt starts until the cap clears

#### Scenario: Reject an invalid cap

- **WHEN** the shared configuration sets `[job_cap] attempts = 0`
- **THEN** configuration loading fails and names the `job_cap.attempts` setting

#### Scenario: Leave the cap unconfigured

- **WHEN** the shared configuration has no `[job_cap]` section
- **THEN** the factory caps attempts at 100 per 24 hours

### Requirement: Notify when the job cap holds work

A cap episode SHALL begin when the factory finds the job cap reached while no episode is open. It
SHALL end when the factory finds the count below the cap. The open episode SHALL survive restarts.
In each episode, the factory SHALL post at most one factory-bot comment:

- on the issue of each claim that has an attempt held by the cap. This includes a Ready card
  whose existing claim admission would reuse, for example a fix awaiting its retry or an eval
  awaiting its next repetition. Such a card is a held claim, not an unclaimed card;
- on the issue of each unclaimed Ready card that the factory would admit now except for the cap.
  This applies only when all of these hold:
  - the factory is not paused;
  - the card's kind's window is open;
  - its slot is free;
  - the kind's readiness checks pass;
  - the request's revisions resolve;
  - the request is valid for admission;
  - no provider quota hold applies to the roles it would freeze.

  A card that fails one of these checks gets the notice it gets today, such as revision
  readiness or invalid-request feedback, or no notice. It does not get the job-cap comment.
  Checking a card SHALL NOT create, change, or supersede a claim.

Each comment SHALL state that the factory job cap is reached. It SHALL give:

- the cap and the window;
- the number of attempts counted when the comment was posted;
- the earliest clear time at that moment, noting that `status` shows the current value;
- the reset command.

Comments SHALL be delivered at most once per issue in each episode, across restarts and retries,
and SHALL be retried until delivered. An unclaimed card SHALL stay Ready and unclaimed, and the
factory SHALL record which cards it notified so that status can list them. A new episode SHALL
post new comments.

#### Scenario: A held retry is announced once

- **WHEN** the cap holds a fix claim's retry over several cycles and a resident restart
- **THEN** exactly one job-cap comment is posted on that claim's issue for the episode

#### Scenario: A new request arrives while the cap is reached

- **WHEN** the cap is reached, the factory is not paused, and a Feature card moves to Ready while the feature window is open, its slot is free, and its readiness checks pass
- **THEN** the card is not claimed, stays Ready, receives one job-cap comment, and status lists it as waiting for the job cap

#### Scenario: A card waiting for a busy slot

- **WHEN** the cap is reached and a Ready fix card waits while the fix slot is occupied
- **THEN** no job-cap comment is posted on it

#### Scenario: A previously claimed card awaiting a retry

- **WHEN** the cap is reached and a Ready fix card's existing claim has an automatic retry due, with no attempt running
- **THEN** the retry does not start, the claim's issue receives one job-cap comment, and status shows the claim held by the job cap and does not list the card among unclaimed waiting cards

#### Scenario: A new request whose revisions do not resolve

- **WHEN** the cap is reached and a new Ready card names a revision that cannot be resolved
- **THEN** no claim is created, the issue receives the existing revision-readiness comment, and no job-cap comment is posted on it

#### Scenario: A new request held by a provider quota

- **WHEN** the cap is reached and a new Ready card's roles would use a provider under an active quota hold
- **THEN** no claim is created and no job-cap comment is posted on it

#### Scenario: A later episode

- **WHEN** an episode ends because the count falls below the cap, and the cap is reached again the next day while the same claim's review round is held
- **THEN** that claim's issue receives one new job-cap comment for the new episode

### Requirement: Reset the job cap

`agent-factory job-cap reset` SHALL record the current time as the job-cap reset time in the
store. Attempts reserved before that time SHALL no longer count toward the cap. The reset SHALL
survive restarts. The command SHALL print the reset time and the count after the reset. When the
cap was reached, it SHALL also say that held work can start in the next cycle. The command SHALL
NOT start work itself, SHALL NOT clear a pause or other holds, and SHALL NOT change running
attempts. It SHALL be accepted whether or not the cap is reached.

#### Scenario: Reset a reached cap

- **WHEN** the cap is reached with a held unblock and the operator runs `agent-factory job-cap reset`
- **THEN** the command reports a count of zero, and the next cycle can start the unblock under the normal admission controls

#### Scenario: Reset while paused

- **WHEN** the factory is paused and the cap is reached, and the operator resets the cap
- **THEN** the count is reset, the factory stays paused, and no attempt starts until resume

### Requirement: Document the factory job cap

The operations documentation SHALL describe:

- the `[job_cap]` settings and their defaults;
- which attempts count and which work does not;
- what a reached cap holds and what continues;
- how the cap clears;
- the claim and card comments;
- the job cap in status;
- `agent-factory job-cap reset`;
- that raising `attempts` needs a committed configuration change.

The `AGENTS.md` "Service-driven watcher" section SHALL state that every PR-READY and FAILURE
event gets a session, limited only by the concurrency settings. It SHALL state that the watch
status shows no session budget, and it SHALL point to the factory job cap as the volume bound.
The on-demand `factory-status` skill SHALL report a job cap that is reached or near its limit,
instead of a watch session budget.

#### Scenario: Relieve a reached cap

- **WHEN** an operator sees a job-cap comment on an issue and follows the documentation
- **THEN** they can find the count and earliest clear time in status, and either wait, reset the cap, or raise it through a configuration change

#### Scenario: Ask for a factory update while the cap is reached

- **WHEN** the operator asks an agent for a factory update while the cap is reached
- **THEN** the agent following `factory-status` reports that the job cap holds work, when it clears, and the reset command
