## MODIFIED Requirements

### Requirement: Fix or decline autonomously

The fix workflow SHALL read the issue and its supplied comments and decide whether the bug is safely fixable without human input. It SHALL return `needs-input` with specific reasons instead of guessing when the problem stems from a CLI, platform, or dependency limitation the agent cannot change, when several viable solutions require a human choice, when the report lacks enough detail to reproduce, when the fix would require changes outside the target repository, or when the fix would require a non-trivial specification change, which belongs in a Feature rather than a bug fix. Trivial inline specification updates are permitted within a fix. Otherwise the lead SHALL write a small definition of the fix outside the repository: a proposal, a specification stating the corrected behavior and requiring a regression test, a design, and a test plan. The workflow SHALL append the factory's implementation constraints to that design and implement the fix through the Runner's `core/implement-change` on the definition: the lead's and the implementor's checklists merged by the crosscheck, the implementation with tests following the repository's conventions, the crosscheck's audit by inspection, the implementor's resolution of its findings, and `core/verify-change`, whose single validator run, with its bounded repair, precedes the draft pull request and bounded acceptance. When the lead rules that no compliant implementation is possible without the user, the workflow SHALL return `needs-input`. It SHALL then reuse the Runner's existing generic finalization workflow to push the branch, open or update the pull request, wait for CI, and address failures within its bounded loop. A validator that remains red after its bounded repair SHALL return `failed` with reasons and open no PR; a failure before verification starts SHALL be a technical failure; CI that remains red after the loop SHALL return `failed` with reasons while leaving that PR open. The PR SHALL be on a deterministic branch named from the issue and claim, SHALL reference the issue without a closing keyword, and SHALL identify the factory claim in a stable marker.

#### Scenario: Fix a reproducible bug

- **WHEN** the issue describes a reproducible defect within the target repository
- **THEN** the implementor produces the code and tests, the crosscheck audits them, the validator runs once, and the workflow returns `pull-request` after CI is addressed

#### Scenario: Decline a bug needing a decision

- **WHEN** the issue admits several viable solutions that change behavior differently
- **THEN** the workflow returns `needs-input` naming the options and what it needs decided
- **AND** it pushes no branch and opens no PR

#### Scenario: Fail CI after the bounded loop

- **WHEN** CI remains red after the workflow's fix cycles
- **THEN** the workflow returns `failed` with the failing checks and leaves the PR open for a human

#### Scenario: Validator stays red

- **WHEN** the validator remains red after its bounded repair inside verification
- **THEN** the workflow returns `failed` naming the failing step, opens no pull request, and reports the validator as failed

#### Scenario: Implementation needs the user

- **WHEN** the lead rules that no compliant implementation is possible without a decision from the user
- **THEN** the workflow returns `needs-input` with the recorded question and opens no pull request
