## MODIFIED Requirements

### Requirement: Queue each event exactly once

Every event that a cycle detects SHALL be recorded durably as a `pending` dispatch, under a key made of the event kind and the id of its attempt. It SHALL be recorded in the same transaction that advances the watch cursor, so an event can never be passed over without a dispatch record. An event detected again SHALL NOT create a second dispatch. Each dispatch SHALL record its event kind, claim, attempt when there is one, issue, pull request when there is one, event time, and state. The states are:

- `pending`;
- `logged`;
- `launched`;
- `budget-exhausted`, a historical state only;
- `completed`;
- `interrupted`;
- `timed-out`;
- `launch-failed`.

No dispatch SHALL enter `budget-exhausted`. A dispatch that an earlier release recorded `budget-exhausted` SHALL stay readable, as an ended dispatch, in the store, status, and its evidence. It SHALL remain eligible for redispatch.

Each cycle SHALL process `pending` dispatches oldest first, by event time and then key. Only a `pending` `PR-READY` or `FAILURE` dispatch SHALL start a session. A `pending` dispatch of any other kind, left by an earlier release, SHALL be recorded `logged` without a session or a comment. A `pending` `PR-READY` or `FAILURE` dispatch SHALL wait only for:

- the concurrency cap;
- the one-session-per-pull-request rule;
- the watch readiness checks.

The factory SHALL NOT skip or end it because of how many sessions have started in a day or any other period. The factory SHALL record the change to `launched` before it starts the session, and only when the dispatch is still `pending`. So no cycle, concurrent `tick`, or restarted resident starts a second session for the same dispatch.

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

#### Scenario: A busy day

- **WHEN** 30 dispatched sessions have already started in the local day, no session is running, the watch readiness checks pass, and a `FAILURE` event is detected
- **THEN** a triage session starts for it, and no dispatch is recorded `budget-exhausted`

#### Scenario: A busy day while the concurrency cap is full

- **WHEN** many sessions have started today, running sessions fill the concurrency cap, and a `PR-READY` event is detected
- **THEN** the dispatch stays `pending`, no notice is posted for it, and a PR-READY check session starts for it once a session ends

#### Scenario: A dispatch an earlier release skipped for budget

- **WHEN** the store holds a `FAILURE` dispatch that an earlier release recorded `budget-exhausted`
- **THEN** the dispatch keeps that state, no cycle starts a session for it on its own, and the operator can redispatch it

### Requirement: Deliver dispatch comments exactly once

Every comment the factory posts for a dispatch SHALL carry a marker unique to that dispatch and that comment's purpose. Before posting, the factory SHALL look for a comment by the factory bot on the same issue that already carries the marker, and adopt it instead of posting again. A delivery that fails SHALL be recorded with its reason and retried in a later cycle. Every new dispatch comment SHALL go to the claim's issue; the factory SHALL NOT queue a comment on a pull request. Across restarts and retries, each such comment SHALL be delivered at most once, and it SHALL be retried until delivered. A comment that an earlier release queued and did not deliver SHALL be delivered the same way. That includes a budget notice for a `budget-exhausted` dispatch. The factory SHALL NOT queue any new budget notice.

#### Scenario: A restart after posting

- **WHEN** the factory posts a triage comment and restarts before recording the delivery
- **THEN** the next cycle finds the comment by its marker and records it, and no second comment is posted

#### Scenario: GitHub is unavailable

- **WHEN** posting a triage comment on the claim's issue fails
- **THEN** the failure and its reason are recorded, and a later cycle posts the comment once

#### Scenario: A budget notice queued before the upgrade

- **WHEN** the release that removes the watch budget starts with a `budget-exhausted` dispatch whose budget notice was queued and not yet posted
- **THEN** a cycle posts that notice once on the claim's issue, and no later cycle posts it again

### Requirement: Check a ready pull request for factory defects

A `PR-READY` dispatch with a parseable pull request URL SHALL start one headless session that checks what the run exposed about the factory itself. It SHALL NOT review the pull request's code. The session's brief SHALL name the pull request, repository, issue, attempt kind, attempt reason, attempt id, and the attempt's evidence path, and SHALL list the configured fix-target repositories. The session SHALL mine the pull request description's red and orange attention items, and the attempt's evidence as needed, for defects in the factory stack: Agent Factory, the workflows and Agent Runner it runs, Agent Skills, and Agent Validator as the factory uses it. A defect in the product code the pull request changes is not a factory defect. For each factory defect it SHALL file an issue as defined in "File factory defects as issues".

The session SHALL NOT post a review or any comment on the pull request, SHALL NOT approve, request changes, merge, or close it, and SHALL NOT create branches, commit, push, or open pull requests. It SHALL NOT ask questions. It SHALL return a result holding a short summary, the issues it filed, and the existing issues it added evidence to. The factory SHALL post no comment for a completed check; status SHALL show the issues it filed or updated. A `PR-READY` dispatch for a pull request SHALL stay `pending` while another dispatch for the same pull request is `launched`.

#### Scenario: A ready pull request has no parseable URL

- **WHEN** a `PR-READY` dispatch has no parseable pull request URL
- **THEN** the event is logged and recorded `logged`, and no session starts

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

## REMOVED Requirements

### Requirement: Bound sessions with a daily budget

**Reason**: The budget limited the watcher rather than the factory. The watcher reacts to factory
runs, so it skipped checks exactly when factory volume was high. The FAILURE event for
agent-evals#57 was skipped while the controller was crashing on every cycle. Factory volume is now
bounded by the factory job cap ("Cap attempts started across the factory" in
`factory-operations`), and watch volume follows it.

**Migration**: Remove `daily_sessions` from `[watch]`. A configuration that still sets it loads,
and the setting has no effect. Existing `budget-exhausted` dispatches stay readable and can be
redispatched with `agent-factory watch redispatch <id>`. A budget notice that was already queued
is still delivered once. Use `max_sessions` to limit concurrent sessions, and `[job_cap]` to bound
the work that produces watch events.
