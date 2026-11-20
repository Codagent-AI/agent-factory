## MODIFIED Requirements

### Requirement: Implement, verify, and push on the existing branch

When any decision is `change`, the workflow SHALL implement the changes on the existing PR branch through the shared implementation sub-workflow: implement with tests following repository conventions, have the crosscheck audit the round's changes against the plan and the reviewer's comments by inspection, have the implementor resolve its findings, run the validator once through the Runner's `core/run-validator` with its bounded repair and require a clean tree, push to the existing branch, and wait for CI with one fix cycle. It SHALL NOT create a branch or a pull request. On a feature pull request it SHALL keep the repository's specifications under `openspec/specs/` consistent with the changed behavior, and SHALL NOT re-run acceptance. A validator that remains red after its bounded repair SHALL return `failed` before any push; CI that remains red after the loop SHALL return `failed` while leaving the PR open. On a feature pull request whose round merged new commits from the target branch, the workflow SHALL run the validator, push, and CI wait on the merged branch even when no decision is `change`, and the same `failed` rules SHALL apply. When no decision is `change` and the round merged no new commits, the implementation sub-workflow SHALL be skipped and the outcome SHALL be `pull-request` after the replies are posted.

#### Scenario: Change pushed and green

- **WHEN** the implementation passes the validator and CI
- **THEN** the PR branch has new commits, the PR is unchanged in identity, and the outcome is `pull-request`

#### Scenario: Validator stays red

- **WHEN** the validator fails after its repair cycles
- **THEN** nothing is pushed, the reply on each `change` thread says so, that thread stays unresolved, and the outcome is `failed`

#### Scenario: Answer-only feature round after the target branch moved

- **WHEN** every decision in a feature pull request's round is `answer` and the round merged new commits from the target branch
- **THEN** the workflow runs the validator on the merged branch, pushes it, and waits for CI
- **AND** the outcome is `pull-request` only when the validator and CI pass, and otherwise `failed` with the PR left open

#### Scenario: Answer-only round with nothing to merge

- **WHEN** every decision in a round is `answer` and the PR branch already contains the target branch's current head
- **THEN** the implementation sub-workflow is skipped, nothing is pushed, and the outcome is `pull-request` after the replies are posted

#### Scenario: The crosscheck finds a gap in the round's change

- **WHEN** the crosscheck's audit reports that a requested change was not fully implemented
- **THEN** the implementor resolves it before the validator runs, and the validator runs once on the result
