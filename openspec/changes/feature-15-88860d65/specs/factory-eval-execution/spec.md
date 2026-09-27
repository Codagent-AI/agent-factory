## MODIFIED Requirements

### Requirement: Preserve suite-owned evidence and candidate outputs

The suite SHALL own its evaluation evidence, results, candidate branches, and draft PRs. The factory SHALL preserve those artifacts and record their locations and candidate links. Factory logs SHALL be kept separately from suite-owned evidence.

Evidence under the factory's artifact root, including each repetition's candidate worktree, SHALL remain available until the evidence retention rule in `factory-operations` removes it. That rule SHALL keep each repetition's result and provenance records and SHALL NOT delete candidate branches or PRs.

The suite worktree and repetition evidence needed for human review SHALL remain available until the first of the following:

- the reviewed item moves to Done, when the worktree cleanup policy in `factory-operations` applies;
- the settled retention period defined in `factory-operations` elapses with the item's card outside Done, when idle release applies.

The factory SHALL NOT delete candidate branches or PRs, merge changes, or perform human review.

#### Scenario: Finish or stop a request

- **WHEN** a request completes, is cancelled, or stops after exhausting recovery
- **THEN** existing evaluation evidence and candidate links remain available for inspection
- **AND** artifacts for repetitions ready for human review remain usable by the retained suite's human-review command until the reviewed item moves to Done or the settled retention period elapses with its card outside Done

#### Scenario: Prune a settled eval's evidence

- **WHEN** an eval claim has been Done for longer than the configured evidence retention and nothing still needs its evidence
- **THEN** its logs, session state, agent output, and candidate worktrees under the artifact root are removed
- **AND** its result and provenance records, candidate branches, and PRs remain

#### Scenario: Release an eval left in Review

- **WHEN** a settled eval claim's card stays in Review past the settled retention period
- **THEN** its suite worktree is released and its evidence becomes eligible for pruning under `factory-operations`
- **AND** its result and provenance records, candidate branches, PRs, and committed results remain
