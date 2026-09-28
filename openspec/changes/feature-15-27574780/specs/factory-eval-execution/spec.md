## MODIFIED Requirements

### Requirement: Preserve suite-owned evidence and candidate outputs

The suite SHALL own its evaluation evidence, results, candidate branches, and draft PRs. The
factory SHALL preserve those artifacts and record their locations and candidate links.
Factory logs SHALL be kept separately from suite-owned evidence. Evidence under the
factory's artifact root SHALL remain available until the evidence retention rule in
`factory-operations` removes it. That rule SHALL keep each repetition's result and
provenance records and SHALL NOT delete candidate branches or PRs.

The suite worktree needed for human review SHALL remain available until the first of these
events:

- the reviewed item moves to Done, when the worktree cleanup policy in `factory-operations`
  applies;
- the request's human-review commands expire under `factory-eval-reporting`, when the
  terminal release in `factory-operations` applies.

A cancelled or superseded request's suite worktree SHALL be released under that terminal
release. The factory SHALL NOT delete candidate branches or PRs, merge changes, or perform
human review.

#### Scenario: Finish or stop a request

- **WHEN** a request completes, is cancelled, or stops after exhausting recovery
- **THEN** existing evaluation evidence and candidate links remain available for inspection
- **AND** artifacts for repetitions ready for human review remain usable by the retained suite's human-review command until the reviewed item moves to Done or the command expires

#### Scenario: Prune a settled eval's evidence

- **WHEN** an eval claim has been Done for longer than the configured retention and nothing still needs its evidence
- **THEN** its logs, session state, and agent output under the artifact root are removed
- **AND** its result and provenance records, candidate branches, and PRs remain

#### Scenario: Release an expired review worktree

- **WHEN** a settled eval's human-review commands have expired and the expiry comment has been delivered
- **THEN** its suite worktree is removed
- **AND** its captured results, result records, candidate branches, and PRs remain
