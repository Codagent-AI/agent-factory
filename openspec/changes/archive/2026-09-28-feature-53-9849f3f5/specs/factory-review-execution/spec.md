## MODIFIED Requirements

### Requirement: Implement, verify, and push on the existing branch

When any decision is `change`, the workflow SHALL implement the changes on the existing PR branch through the shared implementation sub-workflow: implement with tests following repository conventions, run the validator with its repair cycles, exercise the changed flows, repair regressions once with a verification-only validator pass, push to the existing branch, and wait for CI with one fix cycle. It SHALL NOT create a branch or a pull request. On a feature pull request it SHALL keep the repository's specifications under `openspec/specs/` consistent with the changed behavior, and SHALL NOT re-run acceptance. A validator that remains red SHALL return `failed` before any push; CI that remains red after the loop SHALL return `failed` while leaving the PR open. On a feature pull request whose round merged new commits from the target branch, the workflow SHALL run the validator, push, and CI wait on the merged branch even when no decision is `change`, and the same `failed` rules SHALL apply. When no decision is `change` and the round merged no new commits, the implementation sub-workflow SHALL be skipped and the outcome SHALL be `pull-request` after the replies are posted.

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

## ADDED Requirements

### Requirement: Merge the target branch into a feature pull request before triage

For a review round on a feature pull request, the factory SHALL fetch the target repository and resolve the current head of the pull request's base branch when it prepares the round. The workflow SHALL merge that head into the PR branch before triage. The round's evidence SHALL record the merged head. A merge without conflicts SHALL be committed as a merge commit that is not a phase checkpoint, and a base head that the PR branch already contains SHALL add no commit. A conflicting merge SHALL be resolved by an agent before triage, preserving the intent of both sides, and committed. When the agent cannot resolve the conflicts confidently, the workflow SHALL skip triage and return `needs-input` naming the conflicting files and the decision needed. It SHALL push nothing and post no replies to the eligible comments, and it SHALL leave the PR branch unchanged. The claim is then blocked by that round's `needs-input`, and a writer's next eligible comment SHALL admit a new round that merges again with the answer in `review.json`. The round SHALL NOT re-run acceptance. Its completion comment SHALL name each merge commit it pushed, whether the merge was clean or resolved by the agent, as added after acceptance and not covered by the acceptance evidence, linking the commit and the acceptance evidence. When the merge was not pushed, the comment SHALL say so rather than name it. A conflict resolution SHALL satisfy the same history and conflicting-files checks as a feature attempt's resolution. Review rounds on fix pull requests SHALL NOT merge the base branch.

#### Scenario: Merge before triage on a feature pull request

- **WHEN** a review round is admitted on a feature pull request whose base branch gained commits since the PR head was pushed
- **THEN** the workflow merges the base branch's current head into the PR branch before triage
- **AND** the round's evidence records the merged head

#### Scenario: Resolve a conflicting merge in a feature round

- **WHEN** a feature pull request's branch conflicts with its base branch's current head and the agent can reconcile both sides
- **THEN** the workflow commits a merge that resolves every conflict, triages the comments on the merged branch, runs the validator before pushing the result, and waits for CI after the push

#### Scenario: Stop on an unresolvable conflict in a feature round

- **WHEN** a feature pull request's branch conflicts with its base branch's current head and the agent cannot resolve it confidently
- **THEN** the workflow returns `needs-input` naming the conflicting files and the decision needed, pushes nothing, and leaves the PR branch unchanged
- **AND** a writer's next eligible comment admits a new round that merges the base branch again

#### Scenario: Disclose a merged commit in the completion comment

- **WHEN** a feature review round pushes a merge commit from the base branch, either a clean merge or a conflict resolved by the agent
- **THEN** the completion comment names that exact commit, linked, as added after acceptance and not covered by the acceptance evidence, and links the acceptance evidence
- **AND** the description is the one the round started with, and the acceptance evidence still names the commit it describes

#### Scenario: Fix pull request round is unchanged

- **WHEN** a review round is admitted on a fix pull request whose base branch gained commits
- **THEN** the round runs on the PR branch at its recorded head without merging the base branch
