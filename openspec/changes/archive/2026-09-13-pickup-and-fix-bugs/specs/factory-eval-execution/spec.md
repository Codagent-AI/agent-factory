## MODIFIED Requirements

### Requirement: Evaluate Runner and Skills using an existing suite

The factory SHALL support evaluation of requested Agent Runner and Agent Skills revisions through the existing `agent-evals` harness. `and-scene` SHALL be the default eval suite. The harness SHALL be taken from a configured branch (default `main`), resolved to a commit at claim admission, recorded, and retained as part of the evaluation environment for that claim; requests SHALL NOT select different `agent-evals` revisions. Suite setup documentation SHALL state which harness behavior (score-failure contract, calibration-gate removal, linked-worktree metadata mounts, persisted sessions) the factory depends on, and `doctor` SHALL verify the resolved harness commit contains the suite entry points the factory invokes.

Suite-specific readiness checks, invocation, evidence interpretation, supported resume behavior, and human-review instructions SHALL remain separate from generic queueing, scheduling, claim tracking, and recovery. Adding another suite to the harness SHALL be able to reuse that generic behavior. Implementing additional suites or a dynamic plugin system is outside this change.

#### Scenario: Evaluate selected component revisions

- **WHEN** an accepted request specifies Runner and Skills refs
- **THEN** the factory evaluates their resolved revisions with the default `and-scene` suite at the harness commit resolved for the claim
- **AND** records the suite identity and that harness commit as part of the test environment

#### Scenario: Add a suite later

- **WHEN** another suite is integrated with `agent-evals` in a later change
- **THEN** its suite-specific behavior can be supplied without replacing the factory's generic queueing, claim tracking, scheduling, and recovery behavior

#### Scenario: Advance the harness branch during a claim

- **WHEN** `agent-evals` `main` receives commits while a claim has unfinished repetitions
- **THEN** the remaining repetitions and any recovery retry use the claim's recorded harness commit
