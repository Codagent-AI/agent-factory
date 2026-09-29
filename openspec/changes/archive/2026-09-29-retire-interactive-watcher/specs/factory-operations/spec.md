## MODIFIED Requirements

### Requirement: Document the service-driven watcher

The operations documentation SHALL describe service-driven watching as the normal mode:

- the four events and what each one does;
- the `[watch]` settings and their defaults, and how to escalate a failure to a stronger model;
- the budget and concurrency behavior, and the budget-exhausted comment;
- the actions a dispatched session may and may not take;
- that triage runs after a failed claim's automatic retry and does not hold it;
- the watch doctor group, including the writer `gh` login that reviews need;
- the watch section of status;
- `watch redispatch`;
- how to find a dispatch's evidence and usage.

They SHALL state that no interactive watcher session is used. An on-demand `factory-status` skill SHALL report the factory's state once when asked, without watching or polling. A `factory-triage` skill SHALL hold the failure-handling procedure, including the headless triage procedure that the watch workflow's triage sessions follow. The factory PR review procedure SHALL document its headless mode: decisions are returned in the result instead of asked.

#### Scenario: Operate the service watcher

- **WHEN** an operator follows the documentation to enable watching
- **THEN** they can set the profile and budget, pass the watch doctor group, find running and failed dispatches in status, and redispatch a failed one

#### Scenario: Ask for a factory update

- **WHEN** the operator asks an agent for a factory update
- **THEN** the agent follows `factory-status`, reports once what waits on the operator, what is running, and what failed, and starts no watcher
