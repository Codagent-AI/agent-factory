# factory-watch-dispatch Specification

## Purpose
TBD - created by archiving change feature-61-51640878. Update Purpose after archive.
## Requirements
### Requirement: Detect watch events in every cycle

When watching is enabled, every factory cycle, whether started by the resident or by `tick`, SHALL detect watch events after it consumes attempt results. It SHALL detect them whether the factory is paused or not, and whether the admission window is open or not. The watcher exists to make sure the factory itself works, so the factory SHALL detect only two event kinds:

- `FAILURE`: an attempt of any kind whose status is `failed`, `interrupted`, `cancelled`, or `timed_out`, or a fix, feature, or task attempt recorded `completed` with the `failed` outcome, once the failure grace period has passed since it finished. A `needs-input` outcome is never a failure event;
- `PR-READY`: a fix, feature, or task attempt that completed with the `pull-request` outcome. This includes initial, recovery, and review-round attempts.

A newly admitted claim and a finished eval SHALL NOT be detected as events. The factory SHALL keep a durable watch cursor holding the time watching was enabled and the time of the last detection pass, and each cycle SHALL record its detection time there. `PR-READY` detection SHALL scan eligible fix, feature, and task attempts completed with the `pull-request` outcome, finished after watching was enabled and within the last 7 days, and not previously detected.

`FAILURE` detection SHALL NOT depend on that point. Each cycle SHALL detect every attempt that meets all of these conditions:

- it has a failure status, or it is a completed fix or feature attempt with the `failed` outcome;
- it finished after watching was enabled and within the last 7 days;
- it finished at least the currently configured grace period ago;
- its result has been consumed (`consumed-results` exists for the attempt);
- it has not yet been detected.

So a change to the grace period can neither skip a failure nor detect it twice. When watching is first enabled, and whenever it is enabled again after being disabled, the enablement time SHALL be the current time, so events from before that time are not detected. When watching is disabled, no event SHALL be detected. An error during detection SHALL be logged, SHALL leave the cursor unchanged, and SHALL NOT stop the rest of the cycle.

#### Scenario: A quiet day starts no session

- **WHEN** watching is enabled and a day of cycles passes with no failed or pull-request work
- **THEN** no event is detected and no agent session is started

#### Scenario: Claims and finished evals are not events

- **WHEN** a claim is admitted and an eval attempt finishes successfully
- **THEN** no event is detected and no dispatch is recorded for either

#### Scenario: A host fix passes through interrupted

- **WHEN** a host fix attempt is recorded `interrupted` when its process exits, and a cycle within the grace period reads its outcome and records it `completed` with a pull request
- **THEN** no `FAILURE` event is detected for it, and one `PR-READY` event is detected

#### Scenario: A cycle fails before consuming a host result

- **WHEN** a host fix is recorded `interrupted`, GitHub work fails before result consumption while the watch cursor advances, and a later cycle consumes the result and normalizes it to `completed` with a pull request more than two minutes after its `finished_at`
- **THEN** exactly one `PR-READY` event is queued and one PR-READY check session starts, with no `FAILURE` event

#### Scenario: A failed result has not been consumed

- **WHEN** an attempt is recorded failed beyond the grace period but its result has not been consumed
- **THEN** no `FAILURE` event is queued until a cycle consumes the result

#### Scenario: A failure outlasts the grace period

- **WHEN** a Fly eval attempt is recorded `failed` and is still `failed` when the grace period has passed
- **THEN** the first cycle after the grace period detects one `FAILURE` event for that attempt

#### Scenario: A fix attempt completes with outcome failed

- **WHEN** a fix attempt is recorded `completed` with outcome `failed` and its result is consumed
- **THEN** the first cycle after the grace period detects exactly one `FAILURE` event, and no `PR-READY`

#### Scenario: A fix attempt completes with needs-input

- **WHEN** a fix attempt is recorded `completed` with outcome `needs-input` and its result is consumed
- **THEN** no event is detected after the grace period

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

#### Scenario: A task attempt opens a pull request

- **WHEN** watching is enabled and a task attempt completes with the `pull-request` outcome
- **THEN** the next cycle detects one `PR-READY` event for it

#### Scenario: A task attempt fails

- **WHEN** a task attempt is recorded `failed` and its result has been consumed past the grace period
- **THEN** one `FAILURE` event is detected for it

### Requirement: Queue each event exactly once

Every event that a cycle detects SHALL be recorded durably as a `pending` dispatch, under a key made of the event kind and the id of its attempt. It SHALL be recorded in the same transaction that advances the watch cursor, so an event can never be passed over without a dispatch record. An event detected again SHALL NOT create a second dispatch. Each dispatch SHALL record its event kind, claim, attempt when there is one, issue, pull request when there is one, event time, and state. The states are:

- `pending`;
- `logged`;
- `launched`;
- `budget-exhausted`;
- `completed`;
- `interrupted`;
- `timed-out`;
- `launch-failed`.

Each cycle SHALL process `pending` dispatches oldest first, by event time and then key. Only a `pending` `PR-READY` or `FAILURE` dispatch SHALL start a session. A `pending` dispatch of any other kind, left by an earlier release, SHALL be recorded `logged` without a session, a comment, or a budget count. The factory SHALL record the change to `launched` before it starts the session, and only when the dispatch is still `pending`. So no cycle, concurrent `tick`, or restarted resident starts a second session for the same dispatch.

#### Scenario: Exactly one triage session across a restart

- **WHEN** a `FAILURE` event is detected and its triage session is launched, and the resident restarts while the session runs
- **THEN** the replacement resident starts no second session for that event, and exactly one triage comment is posted on the claim's issue

#### Scenario: Restart before launch

- **WHEN** a cycle records a `pending` `FAILURE` dispatch and the resident stops before it launches the session
- **THEN** the next cycle launches exactly one session for that dispatch

#### Scenario: A manual tick overlaps the resident

- **WHEN** an operator runs `tick` while the resident's cycle processes the same `pending` dispatch
- **THEN** exactly one of them launches the session

#### Scenario: A claim event left by an earlier release

- **WHEN** a `pending` `CLAIM` or `EVAL-DONE` dispatch recorded by an earlier release is processed
- **THEN** it is recorded `logged`, and no session starts and no comment is posted for it

### Requirement: Triage a failure in a dispatched session

A `FAILURE` dispatch SHALL start one headless triage session. Its brief SHALL hold the event, the claim and attempt records, and the attempt's evidence path. The session SHALL follow the factory failure-handling procedure's diagnosis steps:

- rule out the known non-failures;
- diagnose from the evidence and code, reproducing when it can;
- decide who owns the cause: factory code, Agent Runner, Agent Evals, Agent Validator, Skills, the environment, or a transient condition.

It MAY pause the factory to protect later retries and other claims when the cause persists. It MAY resume the factory when it paused it and the cause is gone. It SHALL NOT fix anything: it SHALL NOT create branches, commit, push, open pull requests, or apply environment fixes. For a defect in the factory stack it SHALL file an issue as defined in "File factory defects as issues". For a transient or environment cause it SHALL file no issue unless the cause exposed a real defect, and its next step SHALL say what the operator must do. It SHALL NOT hold, cancel, or relaunch the failed claim's own automatic retry. That retry starts as it does today, before triage, and triage SHALL report the retry's state or result.

The session SHALL return a result, and the factory SHALL post it as one factory-bot comment on the claim's issue. The comment SHALL state:

- the cause and its evidence;
- the owner;
- what the session did, including whether it paused or resumed the factory;
- the issues it filed and the existing issues it added evidence to;
- the factory's pause state when the session ended;
- the recommended next step.

The comment SHALL NOT be treated as a writer gesture on the claim.

#### Scenario: A factory defect

- **WHEN** a triage session finds that a factory code defect caused the failure
- **THEN** it files a Bug issue in the factory repository with the evidence, assigned to the factory, and one factory-bot comment on the claim's issue gives the cause, the evidence, the owner "factory code", the issue link, and the next step
- **AND** the session creates no branch, commit, push, or pull request

#### Scenario: A transient failure whose retry succeeded

- **WHEN** a Fly eval fails on a manifest that was not yet available, and its automatic retry has already succeeded when triage runs
- **THEN** the triage comment calls the cause transient and reports the retry's success, no issue is filed, and the factory is not paused

#### Scenario: Another repository owns the cause

- **WHEN** triage finds the cause in Agent Runner and an open Agent Runner issue already describes it
- **THEN** the session adds its evidence to that issue instead of filing another, the comment gives the owner "Agent Runner" and the issue link, and the session changes nothing in Agent Runner

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
- bypass the validator's retry limit;
- create branches, commit, push, or open pull requests in any repository.

A session that runs past the configured session timeout SHALL be terminated no later than the first cycle after the timeout. If it has a valid result, it SHALL be recorded `completed` and its result delivered; otherwise it SHALL be recorded `timed-out`. No more sessions than the configured concurrency cap SHALL be `launched` at once. A dispatch that finds the cap reached SHALL stay `pending` until a later cycle has room. A dispatch SHALL also stay `pending` while the watch readiness checks that doctor defines fail. It SHALL start in the first cycle after they pass. A session that exits with a valid result SHALL be recorded `completed`. One that exits without a valid result SHALL be recorded `interrupted`. One whose process could not be started SHALL be recorded `launch-failed`. When the factory stops between recording a dispatch as `launched` and starting its process, a later cycle SHALL record that dispatch `launch-failed` and alert on it. This SHALL happen once no process has appeared for it within a short launch lease. The dispatch SHALL release its concurrency slot, and no second session SHALL start for it.

After a resident restart, the factory SHALL reconcile each `launched` dispatch:

- a live process that the dispatch owns is supervised again;
- a process that ended and left a valid result is recorded `completed` and its result posted;
- any other ended process is recorded `interrupted`.

A dispatch SHALL NOT be relaunched automatically. Pausing the factory SHALL NOT stop detection, dispatch, or supervision of sessions. A dispatched session SHALL NOT be recorded as a claim or an attempt, so detection never sees the session itself. Work the session creates enters the factory only through the normal path: a filed Bug issue assigned to the factory is routed and admitted.

#### Scenario: Escalate failures to a stronger model

- **WHEN** the configured default profile is a Sonnet profile and an Opus profile is configured for `FAILURE`
- **THEN** triage sessions use the Opus profile and PR-READY check sessions use the Sonnet profile

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

- **WHEN** the resident restarts and a `launched` PR-READY check session's process has exited without a result
- **THEN** the dispatch is recorded `interrupted` and alerted, and no second check session starts

#### Scenario: The factory stops during a launch

- **WHEN** a dispatch was recorded `launched` and the resident stopped before its session process started
- **THEN** a cycle after the launch lease records it `launch-failed` with an alert, frees its concurrency slot, and starts no session for it

#### Scenario: The factory stops just after the spawn

- **WHEN** a session process started but the resident stopped before recording its identity
- **THEN** the next cycle adopts and supervises the running session, and does not record it as failed

#### Scenario: A triage pull request does not trigger review

- **WHEN** a triage session ends
- **THEN** it has opened no pull request, so no `PR-READY` event can be detected for its work

#### Scenario: The throwaway checkout is removed

- **WHEN** a dispatched session ends in any state
- **THEN** its checkout no longer exists, and the release, the service clone, and the operator's checkout are unchanged by it

### Requirement: Bound sessions with a daily budget

The factory SHALL count the dispatched sessions it starts in each local day of the configured schedule timezone. A redispatched attempt counts like any other. When the count has reached the configured per-day budget, a `pending` `PR-READY` or `FAILURE` dispatch SHALL NOT start a session. It SHALL be recorded `budget-exhausted`, and the factory SHALL post one factory-bot comment on the claim's issue. That comment names the event, states that no agent ran because the day's budget was spent, and gives the command that redispatches it. A budget of zero SHALL post every such event this way. The budget SHALL be applied before the watch readiness checks, the one-session-per-pull-request rule, and the concurrency cap, and it SHALL need none of the session's prerequisites. So a spent budget is reported even while sessions could not start anyway. The count SHALL survive restarts.

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
- **THEN** a PR-READY check session starts

### Requirement: Alert on a dispatch that did not finish

When a dispatch is recorded `interrupted`, `timed-out`, or `launch-failed`, the factory SHALL post one factory-bot comment on the claim's issue. The comment SHALL name the event, the pull request when there is one, what happened, the dispatch's evidence path, and the command that redispatches it.

#### Scenario: A review session fails to launch

- **WHEN** the check session for a ready pull request cannot be started because the configured CLI is not executable
- **THEN** the dispatch is recorded `launch-failed`, and one comment on the claim's issue names the pull request, the reason, the evidence path, and the redispatch command

### Requirement: Deliver dispatch comments exactly once

Every comment the factory posts for a dispatch SHALL carry a marker unique to that dispatch and that comment's purpose. Before posting, the factory SHALL look for a comment by the factory bot on the same issue that already carries the marker, and adopt it instead of posting again. A delivery that fails SHALL be recorded with its reason and retried in a later cycle. Every new dispatch comment SHALL go to the claim's issue; the factory SHALL NOT queue a comment on a pull request. Across restarts and retries, each such comment SHALL be delivered at most once, and it SHALL be retried until delivered.

#### Scenario: A restart after posting

- **WHEN** the factory posts a triage comment and restarts before recording the delivery
- **THEN** the next cycle finds the comment by its marker and records it, and no second comment is posted

#### Scenario: GitHub is unavailable

- **WHEN** posting a triage comment on the claim's issue fails
- **THEN** the failure and its reason are recorded, and a later cycle posts the comment once

### Requirement: Record each dispatched session's usage

Each dispatch that started a session SHALL record:

- its model profile;
- its start and end times and duration;
- its input and output tokens;
- its estimated cost.

Tokens and cost SHALL come from the session's Agent Runner metrics. When a value cannot be read, or the Runner reports it as incomplete, it SHALL be recorded as unavailable, with the Runner's coverage, rather than as zero. The factory SHALL record each session's usage metrics in its dispatch record and status. The watch-session post-run audit SHALL run only when the factory audit switch that governs all post-run audits is on. That switch is off pending Codagent-AI/agent-factory#60. When disabled, no metrics are sent to the development-audit destination and the audit outcome is recorded as missing. When enabled, audit delivery and its outcome SHALL be recorded. A session terminated at its timeout skips that audit, and its delivery SHALL be recorded as missing.

#### Scenario: A completed review records its usage

- **WHEN** a PR-READY check session completes
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

### Requirement: Check a ready pull request for factory defects

A `PR-READY` dispatch with a parseable pull request URL SHALL start one headless session that checks what the run exposed about the factory itself. It SHALL NOT review the pull request's code. The session's brief SHALL name the pull request, repository, issue, attempt kind, attempt reason, attempt id, and the attempt's evidence path, and SHALL list the configured fix-target repositories. The session SHALL mine the pull request description's red and orange attention items, and the attempt's evidence as needed, for defects in the factory stack: Agent Factory, the workflows and Agent Runner it runs, Agent Skills, and Agent Validator as the factory uses it. A defect in the product code the pull request changes is not a factory defect. For each factory defect it SHALL file an issue as defined in "File factory defects as issues".

The session SHALL NOT post a review or any comment on the pull request, SHALL NOT approve, request changes, merge, or close it, and SHALL NOT create branches, commit, push, or open pull requests. It SHALL NOT ask questions. It SHALL return a result holding a short summary, the issues it filed, and the existing issues it added evidence to. The factory SHALL post no comment for a completed check; status SHALL show the issues it filed or updated. A `PR-READY` dispatch for a pull request SHALL stay `pending` while another dispatch for the same pull request is `launched`.

#### Scenario: A ready pull request has no parseable URL

- **WHEN** a `PR-READY` dispatch has no parseable pull request URL
- **THEN** the event is logged and recorded `logged`, and no session starts or counts against the budget

#### Scenario: One check session per ready pull request

- **WHEN** a fix attempt completes with a pull request
- **THEN** exactly one PR-READY check session starts for that pull request

#### Scenario: An orange item exposes a factory defect

- **WHEN** a pull request's description has an orange item showing that a Runner workflow step misfired, and no open issue describes it
- **THEN** the session files a Bug issue in Agent Runner with the pull request link, the item, and a log excerpt, assigned to the factory; the dispatch completes; no comment or review is posted on the pull request; and status shows the pull request with the issue link

#### Scenario: Only product findings

- **WHEN** every red and orange item concerns the product change or is a false alarm
- **THEN** the session files no issue, and nothing is posted on the pull request or the claim's issue

#### Scenario: A review round finishes while its pull request is being checked

- **WHEN** a review-round attempt on a pull request completes while that pull request's earlier check session is still running
- **THEN** the new `PR-READY` dispatch waits and starts after the earlier session ends

### Requirement: File factory defects as issues

A dispatched session that finds a defect in the factory stack SHALL first search the owning repository's open issues. When an open issue already describes the defect, it SHALL add its new evidence to that issue as a comment and SHALL NOT file a duplicate. Otherwise it SHALL file a Bug issue in the owning repository whose body gives concrete evidence: the pull request or attempt, the attention item or failure it came from, and a file, line, or log excerpt. When the owning repository is a configured fix target, the session SHALL assign the new issue to the factory: board Owner factory, Status Ready, and Priority Low unless the defect blocks work. When it is not a fix target, the session SHALL file the issue without assigning it and say so in its result. The session SHALL use the operator's `gh` login, never the factory bot, so the factory admits the issue.

#### Scenario: A duplicate defect

- **WHEN** a session finds a factory defect that an open issue already describes
- **THEN** it comments its evidence on that issue, files no new issue, and reports the issue as updated

#### Scenario: A new defect in a fix target

- **WHEN** a session finds a new Agent Validator defect and Agent Validator is a fix target
- **THEN** it files a Bug issue in Agent Validator with the evidence, sets Owner factory, Status Ready, and Priority Low, and reports the issue as filed
