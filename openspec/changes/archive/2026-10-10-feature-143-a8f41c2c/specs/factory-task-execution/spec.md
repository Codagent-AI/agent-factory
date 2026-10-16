## MODIFIED Requirements

### Requirement: Treat task claims as pull-request claims in shared lifecycle rules

Wherever the claim lifecycle, operations, or execution-backend requirements name fix and feature claims as pull-request claims, task claims SHALL be included and follow the fix rules unless a task requirement says otherwise. That includes:

- one attempt per kind and Priority lane, with a blocked task claim occupying no lane;
- correcting contradicting status edits, where a triage-blocked task stays in Running and a review-blocked task stays in Review;
- pre-suite failure handling;
- exemption of settled work from closure cancellation;
- worktree cleanup after review;
- evidence retention, including keeping the task outcome;
- the deploy's rule that skips the Agent Validator build while a host attempt runs;
- the post-run audit.

Feature-only behavior SHALL NOT apply to task claims: definition, phase checkpoints, resume from a step, OpenSpec archive, acceptance, attention classification, and merging the target branch.

#### Scenario: Leave a declined task in Running

- **WHEN** a task claim declined by triage sits in Running with the `needs-input` label and a human moves its card to Done
- **THEN** the factory restores the card to Running

#### Scenario: Deploy while a task runs on the host

- **WHEN** the operator deploys while a task attempt runs on the host
- **THEN** the deploy skips the Agent Validator fast-forward and build with a warning, as it does for a host fix

#### Scenario: Close the issue of a settled task before sync

- **WHEN** a human closes the issue of a task claim whose pull request merged before the factory synced
- **THEN** the claim is not cancelled and still receives its merge sync
