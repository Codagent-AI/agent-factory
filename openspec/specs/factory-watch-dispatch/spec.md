# factory-watch-dispatch Specification

## Purpose
TBD - created by archiving change feature-61-51640878. Update Purpose after archive.
## Requirements
### Requirement: Detect watch events in every cycle

When watching is enabled, every factory cycle, whether started by the resident or by `tick`, SHALL detect watch events after it consumes attempt results. It SHALL detect them whether the factory is paused or not, and whether the admission window is open or not. The factory SHALL detect four event kinds, with the same definitions as the manual `watch.sh` watcher:

- `FAILURE`: an attempt of any kind whose status is `failed`, `interrupted`, `cancelled`, or `timed_out`, and which still has that status once the failure grace period has passed since it finished;
- `EVAL-DONE`: an eval attempt that finished with any other terminal status;
- `PR-READY`: a fix or feature attempt that completed with the `pull-request` outcome. This includes initial, recovery, and review-round attempts;
- `CLAIM`: a newly admitted claim.

The factory SHALL keep a durable "handled up to" point for `EVAL-DONE` and `CLAIM`. Each cycle SHALL detect those events after that point and up to the cycle's detection time, and SHALL then advance the point. `PR-READY` detection SHALL instead scan eligible fix and feature attempts completed with the `pull-request` outcome, finished after watching was enabled and within the last 7 days, and not previously detected.

`FAILURE` detection SHALL NOT depend on that point. Each cycle SHALL detect every attempt that meets all of these conditions:

- it has a failure status;
- it finished after watching was enabled and within the last 7 days;
- it finished at least the currently configured grace period ago;
- its result has been consumed (`consumed-results` exists for the attempt);
- it has not yet been detected.

So a change to the grace period can neither skip a failure nor detect it twice. When watching is first enabled, and whenever it is enabled again after being disabled, the point SHALL start at the current time, so events from before that time are not detected. When watching is disabled, no event SHALL be detected. An error during detection SHALL be logged, SHALL leave the point unchanged, and SHALL NOT stop the rest of the cycle.

#### Scenario: A quiet day starts no session

- **WHEN** watching is enabled and a day of cycles passes with no failed, finished, pull-request, or newly admitted work
- **THEN** no event is detected and no agent session is started

#### Scenario: A host fix passes through interrupted

- **WHEN** a host fix attempt is recorded `interrupted` when its process exits, and a cycle within the grace period reads its outcome and records it `completed` with a pull request
- **THEN** no `FAILURE` event is detected for it, and one `PR-READY` event is detected

#### Scenario: A cycle fails before consuming a host result

- **WHEN** a host fix is recorded `interrupted`, GitHub work fails before result consumption while the watch cursor advances, and a later cycle consumes the result and normalizes it to `completed` with a pull request more than two minutes after its `finished_at`
- **THEN** exactly one `PR-READY` event is queued and one review session starts, with no `FAILURE` event

#### Scenario: A failed result has not been consumed

- **WHEN** an attempt is recorded failed beyond the grace period but its result has not been consumed
- **THEN** no `FAILURE` event is queued until a cycle consumes the result

#### Scenario: A failure outlasts the grace period

- **WHEN** a Fly eval attempt is recorded `failed` and is still `failed` when the grace period has passed
- **THEN** the first cycle after the grace period detects one `FAILURE` event for that attempt

#### Scenario: The grace period shrinks while a failure waits

- **WHEN** an attempt failed five minutes before a cycle, with a seven-minute grace period, and the grace period is changed to zero before the next cycle
- **THEN** the next cycle detects exactly one `FAILURE` event for that attempt

#### Scenario: The grace period grows while a failure waits

- **WHEN** an attempt failed five minutes before a cycle, with a seven-minute grace period, and the grace period is changed to fifteen minutes
- **THEN** no `FAILURE` event is detected for it until fifteen minutes after it finished, and then exactly one is

#### Scenario: Detection while paused

- **WHEN** the factory is paused and a fix attempt completes with a pull request
- **THEN** the next cycle detects the `PR-READY` event

#### Scenario: Enable watching on an installation with history

- **WHEN** watching is enabled for the first time on an installation with past failed attempts and pull requests
- **THEN** none of those past events is detected, and events after the enablement are

#### Scenario: Re-enable watching

- **WHEN** watching is disabled for a day, a pull request becomes ready during that day, and watching is enabled again
- **THEN** the pull request that became ready while watching was disabled is not detected

### Requirement: Queue each event exactly once

Every event that a cycle detects SHALL be recorded durably as a `pending` dispatch, under a key made of the event kind and the id of its attempt, or of its claim for `CLAIM`. It SHALL be recorded in the same transaction that advances the "handled up to" point, so an event can never be passed over without a dispatch record. An event detected again SHALL NOT create a second dispatch. Each dispatch SHALL record its event kind, claim, attempt when there is one, issue, pull request when there is one, event time, and state. The states are:

- `pending`;
- `logged`;
- `launched`;
- `budget-exhausted`;
- `completed`;
- `interrupted`;
- `timed-out`;
- `launch-failed`.

Each cycle SHALL process `pending` dispatches oldest first, by event time and then key. Only a `pending` dispatch SHALL start a session. The factory SHALL record the change to `launched` before it starts the session, and only when the dispatch is still `pending`. So no cycle, concurrent `tick`, or restarted resident starts a second session for the same dispatch.

#### Scenario: Exactly one triage session across a restart

- **WHEN** a `FAILURE` event is detected and its triage session is launched, and the resident restarts while the session runs
- **THEN** the replacement resident starts no second session for that event, and exactly one triage comment is posted on the claim's issue

#### Scenario: Restart before launch

- **WHEN** a cycle records a `pending` `FAILURE` dispatch and the resident stops before it launches the session
- **THEN** the next cycle launches exactly one session for that dispatch

#### Scenario: A manual tick overlaps the resident

- **WHEN** an operator runs `tick` while the resident's cycle processes the same `pending` dispatch
- **THEN** exactly one of them launches the session

### Requirement: Log claim and eval-completion events without an agent

A `CLAIM` or `EVAL-DONE` dispatch SHALL be written to the service log with its event details and recorded as `logged`. The factory SHALL NOT start a session for it, post a comment for it, or count it against the session budget or concurrency cap. The eval's existing result report SHALL be unchanged.

#### Scenario: A claim is admitted

- **WHEN** a Bug is admitted as a new fix claim
- **THEN** the service log records the `CLAIM` event and no session starts

#### Scenario: An eval finishes

- **WHEN** an eval run finishes with its results
- **THEN** the service log records the `EVAL-DONE` event, the factory posts its usual eval result comment, and no session starts

### Requirement: Review a ready pull request in a dispatched session

A `PR-READY` dispatch with a parseable pull request URL SHALL start one headless session that reviews that pull request by following the factory PR review procedure in headless mode. The session's brief SHALL name the pull request, repository, issue, attempt kind, attempt reason, and attempt id. The session SHALL follow the existing reviewer rules:

- it posts at most one comment-only review, with the operator's writer GitHub login, and only when there is at least one Blocking or Should-fix item. The posted review then starts a factory review round as any writer comment does;
- it may file follow-up Bug issues for the factory;
- it never approves, requests changes, merges, pushes, or closes.

The session SHALL read the pull request's code from a read-only clone in its own scratch directory, made from the factory's mirror of the pull request's repository. It SHALL NOT use the operator's checkout for this. The session SHALL NOT ask questions. It SHALL return the decisions that only the operator can make in its result. When the result holds at least one decision, the factory SHALL post one comment as the factory bot on the pull request. The comment lists each decision with its context, options, and recommendation, and it mentions the configured operator login when one is set. A factory-bot comment SHALL NOT start a review round. When the result holds no decision, the factory SHALL post no decisions comment. A `PR-READY` dispatch for a pull request SHALL stay `pending` while another dispatch for the same pull request is `launched`.

#### Scenario: A ready pull request has no parseable URL

- **WHEN** a `PR-READY` dispatch has no parseable pull request URL
- **THEN** the event is logged and recorded `logged`, no session starts or counts against the budget, and no decisions comment is posted or retried

#### Scenario: One review session per ready pull request

- **WHEN** a fix attempt completes with a pull request
- **THEN** exactly one review session starts for that pull request

#### Scenario: A review with operator decisions

- **WHEN** the review session posts a comment-only review with one Should-fix item and returns two decisions for the operator
- **THEN** the factory starts a review round for the posted review, and posts one factory-bot comment on the pull request that lists the two decisions and mentions the operator login

#### Scenario: The operator's checkout is untouched by a review

- **WHEN** a review session reviews a pull request and files a follow-up Bug issue
- **THEN** the operator's checkout of every repository has the same refs, worktrees, and working tree as before the session

#### Scenario: A clean pull request

- **WHEN** the review session finds nothing Blocking or Should-fix, and no decision
- **THEN** no review and no factory-bot comment is posted on the pull request

#### Scenario: A review round finishes while its pull request is under review

- **WHEN** a review-round attempt on a pull request completes while that pull request's earlier review session is still running
- **THEN** the new `PR-READY` dispatch waits and starts after the earlier session ends

### Requirement: Triage a failure in a dispatched session

A `FAILURE` dispatch SHALL start one headless triage session. Its brief SHALL hold the event, the claim and attempt records, and the attempt's evidence path. The session SHALL follow the factory failure-handling procedure without its deploy step:

- rule out the known non-failures;
- diagnose from the evidence and code, reproducing when it can;
- decide who owns the cause.

It MAY pause the factory to protect later retries and other claims when the cause persists. It MAY resume the factory when it paused it and the cause is gone. It MAY open a fix pull request for a factory-code cause under the existing fix rules. It SHALL NOT hold, cancel, or relaunch the failed claim's own automatic retry. That retry starts as it does today, before triage, and triage SHALL report the retry's state or result.

The session SHALL return a result, and the factory SHALL post it as one factory-bot comment on the claim's issue. The comment SHALL state:

- the cause and its evidence;
- the owner: factory code, Agent Runner, Agent Evals, Skills, the environment, or a transient condition;
- what the session did, including any pull request it opened and whether it paused or resumed the factory;
- the factory's pause state when the session ended;
- the recommended next step, including any handoff for another repository's owner.

The comment SHALL NOT be treated as a writer gesture on the claim.

#### Scenario: A factory defect

- **WHEN** a triage session finds that a factory code defect caused the failure and opens a fix pull request
- **THEN** one factory-bot comment on the claim's issue gives the cause, the evidence, the owner "factory code", the pull request link, and the next step "merge and deploy"
- **AND** the fix pull request is not merged or deployed by the session

#### Scenario: A transient failure whose retry succeeded

- **WHEN** a Fly eval fails on a manifest that was not yet available, and its automatic retry has already succeeded when triage runs
- **THEN** the triage comment calls the cause transient and reports the retry's success, and the factory is not paused

#### Scenario: Another repository owns the cause

- **WHEN** triage finds the cause in Agent Runner
- **THEN** the comment gives the owner "Agent Runner" and the file, log excerpt, and proposed fix to hand off, and the session changes nothing in Agent Runner

#### Scenario: A triage comment on a blocked feature

- **WHEN** a triage comment is posted on the issue of a feature claim that is blocked with `needs-input`
- **THEN** the claim is not resumed by that comment

### Requirement: Run dispatched sessions fresh and bounded

Each dispatched session SHALL be a new session with no history carried over from any earlier session. Its brief SHALL be limited to the event and the records and evidence paths named for that event kind. It SHALL run in its own throwaway checkout of the factory repository at `origin/main`. That checkout SHALL be removed when the session ends, and no session SHALL run in a release, the service clone, or the operator's own checkout. The session SHALL use the model profile configured for its event kind, or the configured default profile otherwise. It SHALL run detached, so no cycle waits for it.

A dispatched session SHALL NOT:

- deploy, including through the hotfix exception;
- merge;
- edit a release or the service clone;
- fetch into, add worktrees to, switch branches in, or run commands from the operator's checkout;
- fetch into the factory's repository mirrors;
- run tools through the live `releases/current` path;
- delete shared data, Keychain items, remote branches, or Docker volumes;
- print credentials;
- bypass the validator's retry limit.

A session that runs past the configured session timeout SHALL be terminated no later than the first cycle after the timeout. If it has a valid result, it SHALL be recorded `completed` and its result delivered; otherwise it SHALL be recorded `timed-out`. No more sessions than the configured concurrency cap SHALL be `launched` at once. A dispatch that finds the cap reached SHALL stay `pending` until a later cycle has room. A dispatch SHALL also stay `pending` while the watch readiness checks that doctor defines fail. It SHALL start in the first cycle after they pass. A session that exits with a valid result SHALL be recorded `completed`. One that exits without a valid result SHALL be recorded `interrupted`. One whose process could not be started SHALL be recorded `launch-failed`. When the factory stops between recording a dispatch as `launched` and starting its process, a later cycle SHALL record that dispatch `launch-failed` and alert on it. This SHALL happen once no process has appeared for it within a short launch lease. The dispatch SHALL release its concurrency slot, and no second session SHALL start for it.

After a resident restart, the factory SHALL reconcile each `launched` dispatch:

- a live process that the dispatch owns is supervised again;
- a process that ended and left a valid result is recorded `completed` and its result posted;
- any other ended process is recorded `interrupted`.

A dispatch SHALL NOT be relaunched automatically. Pausing the factory SHALL NOT stop detection, dispatch, or supervision of sessions. A dispatched session SHALL NOT be recorded as a claim or an attempt, so detection never sees the session itself. Work the session creates enters the factory only through the normal paths: a posted review starts a review round, and a filed Bug issue is routed and admitted.

#### Scenario: Escalate failures to a stronger model

- **WHEN** the configured default profile is a Sonnet profile and an Opus profile is configured for `FAILURE`
- **THEN** triage sessions use the Opus profile and review sessions use the Sonnet profile

#### Scenario: Respect the concurrency cap

- **WHEN** the cap is 2, two sessions are running, and a third event is queued
- **THEN** the third dispatch stays `pending` and starts in the first cycle after a running session ends

#### Scenario: A session times out

- **WHEN** a triage session is still running at the session timeout
- **THEN** it is terminated, recorded `timed-out`, and alerted, and no new session starts for that event

#### Scenario: A session has a result at its deadline

- **WHEN** a live triage session has written a valid result but remains running at its deadline
- **THEN** the factory terminates it, records it `completed` with detail `session deadline exceeded after result`, posts its triage comment once without an alert, and records the audit as missing

#### Scenario: A session dies with the resident

- **WHEN** the resident restarts and a `launched` review session's process has exited without a result
- **THEN** the dispatch is recorded `interrupted` and alerted, and no second review session starts

#### Scenario: The factory stops during a launch

- **WHEN** a dispatch was recorded `launched` and the resident stopped before its session process started
- **THEN** a cycle after the launch lease records it `launch-failed` with an alert, frees its concurrency slot, and starts no session for it

#### Scenario: The factory stops just after the spawn

- **WHEN** a session process started but the resident stopped before recording its identity
- **THEN** the next cycle adopts and supervises the running session, and does not record it as failed

#### Scenario: A triage pull request does not trigger review

- **WHEN** a triage session opens a fix pull request
- **THEN** no `PR-READY` event is detected for that pull request

#### Scenario: The throwaway checkout is removed

- **WHEN** a dispatched session ends in any state
- **THEN** its checkout no longer exists, and the release, the service clone, and the operator's checkout are unchanged by it

### Requirement: Bound sessions with a daily budget

The factory SHALL count the dispatched sessions it starts in each local day of the configured schedule timezone. A redispatched attempt counts like any other. When the count has reached the configured per-day budget, a `pending` `PR-READY` or `FAILURE` dispatch SHALL NOT start a session. It SHALL be recorded `budget-exhausted`, and the factory SHALL post one factory-bot comment on the claim's issue. That comment names the event, states that no agent ran because the day's budget was spent, and gives the command that redispatches it. A budget of zero SHALL post every such event this way. The budget SHALL be applied before the watch readiness checks, the one-review-per-pull-request rule, and the concurrency cap, and it SHALL need none of the session's prerequisites. So a spent budget is reported even while sessions could not start anyway. The count SHALL survive restarts.

#### Scenario: Budget spent

- **WHEN** the budget is 10, ten sessions have started today, and a `FAILURE` event is detected
- **THEN** no session starts, the dispatch is recorded `budget-exhausted`, and one comment on the claim's issue reports the failure and the redispatch command

#### Scenario: Zero budget while readiness fails

- **WHEN** the budget is zero, the watch readiness checks fail, and a `FAILURE` event is detected
- **THEN** the dispatch is recorded `budget-exhausted` and its notice is posted in that cycle

#### Scenario: Budget spent while the cap is full

- **WHEN** the budget is spent, sessions fill the concurrency cap, and a `PR-READY` event is detected
- **THEN** the dispatch is recorded `budget-exhausted` and its notice is posted without waiting for a slot

#### Scenario: A new day

- **WHEN** the budget was spent yesterday and a `PR-READY` event is detected after local midnight
- **THEN** a review session starts

### Requirement: Alert on a dispatch that did not finish

When a dispatch is recorded `interrupted`, `timed-out`, or `launch-failed`, the factory SHALL post one factory-bot comment on the claim's issue. The comment SHALL name the event, the pull request when there is one, what happened, the dispatch's evidence path, and the command that redispatches it.

#### Scenario: A review session fails to launch

- **WHEN** the review session for a ready pull request cannot be started because the configured CLI is not executable
- **THEN** the dispatch is recorded `launch-failed`, and one comment on the claim's issue names the pull request, the reason, the evidence path, and the redispatch command

### Requirement: Deliver dispatch comments exactly once

Every comment the factory posts for a dispatch SHALL carry a marker unique to that dispatch and that comment's purpose. Before posting, the factory SHALL look for a comment by the factory bot on the same issue or pull request that already carries the marker, and adopt it instead of posting again. A delivery that fails SHALL be recorded with its reason and retried in a later cycle. Comments on a claim's issue SHALL go to that issue. The decisions comment SHALL go to the dispatch's pull request. Across restarts and retries, each such comment SHALL be delivered at most once, and it SHALL be retried until delivered.

#### Scenario: A restart after posting

- **WHEN** the factory posts a triage comment and restarts before recording the delivery
- **THEN** the next cycle finds the comment by its marker and records it, and no second comment is posted

#### Scenario: GitHub is unavailable

- **WHEN** posting a decisions comment on a pull request fails
- **THEN** the failure and its reason are recorded, and a later cycle posts the comment once

### Requirement: Record each dispatched session's usage

Each dispatch that started a session SHALL record:

- its model profile;
- its start and end times and duration;
- its input and output tokens;
- its estimated cost.

Tokens and cost SHALL come from the session's Agent Runner metrics. When a value cannot be read, or the Runner reports it as incomplete, it SHALL be recorded as unavailable, with the Runner's coverage, rather than as zero. The factory SHALL record each session's usage metrics in its dispatch record and status. The watch-session post-run audit SHALL run only when the audit switch is enabled; it is disabled by default pending Codagent-AI/agent-factory#60. When disabled, no metrics are sent to the development-audit destination and the audit outcome is recorded as missing. When enabled, audit delivery and its outcome SHALL be recorded. A session terminated at its timeout skips that audit, and its delivery SHALL be recorded as missing.

#### Scenario: A completed review records its usage

- **WHEN** a review session completes
- **THEN** its dispatch record holds the model profile, duration, input and output tokens, and estimated cost, and, when the audit switch is enabled, its metrics reach the development-audit destination

#### Scenario: An interrupted session

- **WHEN** a session is interrupted before it reports its usage
- **THEN** its tokens and cost are recorded as unavailable

#### Scenario: A timed-out session

- **WHEN** a session is terminated at its timeout
- **THEN** its usage holds whatever the Runner recorded, with its coverage, and its audit delivery is recorded as missing

### Requirement: Prune dispatch evidence

The factory SHALL remove a dispatch's evidence directory once the dispatch has been in an end state for longer than the configured evidence retention period. It SHALL keep the dispatch record, with its result, usage, audit outcome, and comment deliveries. It SHALL NOT remove evidence of a `pending` or `launched` dispatch.

#### Scenario: Prune an old dispatch

- **WHEN** a triage dispatch completed longer ago than the retention period
- **THEN** its evidence directory is removed, and status and the dispatch record still show its outcome and usage

### Requirement: Stop watching without losing queued events

When watching is disabled, or the `[watch]` configuration is absent, the factory SHALL detect no events and start no sessions. This keeps today's behavior. Sessions already `launched` SHALL still be supervised, and their results and comments delivered. `pending` dispatches SHALL remain `pending` and SHALL be processed when watching is enabled again.

#### Scenario: Disable watching while a session runs

- **WHEN** watching is disabled while a triage session runs and another dispatch is `pending`
- **THEN** the running session is supervised to its end and its comment posted, the pending dispatch starts no session, and no new event is detected

#### Scenario: No watch configuration

- **WHEN** the shared configuration has no `[watch]` section
- **THEN** cycles detect no watch event and start no session

