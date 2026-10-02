# factory-review-execution Specification

## Purpose
TBD - created by archiving change code-review. Update Purpose after archive.
## Requirements
### Requirement: Run the versioned review workflow

The factory SHALL ship a packaged review workflow declaring contract `factory-review/1`, stage it beside the fix workflow and the shared implementation sub-workflow, and launch it through the execution path of the claim's kind, a fix claim's round as a fix attempt, a feature claim's round on the host as a feature attempt, and a task claim's round on the host as a task attempt, on clones where the target is checked out on the PR branch at its recorded head. The factory SHALL write `review.json` into the attempt's artifact directory containing the repository, issue number, claim identifier, the claim's work kind, attempt number, the pull request number, URL, branch, base branch and head commit, the original issue title and body, and the eligible comments grouped by source (review summaries, unresolved inline threads with path, line, thread identifier and every comment in the thread, and conversation comments), each with author, identifier, body, and creation time. The workflow SHALL return exactly one structured outcome in `review-outcome.json` declaring its contract: `pull-request` with the PR reference and the identifiers it answered and changed, `needs-input` with reasons, `failed` with reasons, or a technical failure when the file is absent or invalid.

#### Scenario: Launch a review round

- **WHEN** a review round is admitted with a compatible Runner commit
- **THEN** the attempt starts under its own supervisor with the review contract and `review.json` describes the PR and the eligible comments

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `review-outcome.json`
- **THEN** the factory records a technical failure and applies the recovery policy of the claim's kind

### Requirement: Triage each comment and decide autonomously

The review workflow SHALL read `review.json` and produce one decision per eligible comment or thread: `change` with a concrete plan when the comment asks for a code change the agent should make, or `answer` with the reply text when a reply suffices. When a comment is ambiguous, the agent SHALL decide itself which applies and explain its reading in the reply. It SHALL return `needs-input` only when reviewer requests conflict with each other, when a requested change on a fix pull request requires a non-trivial specification change, when a requested change requires changes outside the target repository, when a requested change on a task pull request falls outside task scope as defined by "Hold task scope through review rounds", or when a genuinely open product decision must be made by a human; it SHALL name what needs deciding. A requested change that widens the original issue's scope SHALL still be made, except on a task pull request, where it SHALL be made only while it stays within task scope. On a feature pull request, a requested change that alters specified behavior SHALL be made rather than declined.

#### Scenario: Requested change

- **WHEN** an inline comment asks to rename a function and handle an edge case
- **THEN** triage records a `change` with a plan for both, and the implementation step carries them out

#### Scenario: Question only

- **WHEN** a comment asks why an approach was chosen
- **THEN** triage records an `answer` and no code change is made for that comment

#### Scenario: Ambiguous remark

- **WHEN** a comment says "this looks fragile" without asking for anything
- **THEN** the agent decides whether to change the code or explain, and the reply states which it chose and why

#### Scenario: Conflicting requests

- **WHEN** two writers ask for incompatible changes to the same behaviour
- **THEN** the workflow returns `needs-input` naming both requests and what must be decided, and pushes nothing

#### Scenario: Change specified behavior on a feature pull request

- **WHEN** a writer asks on a feature pull request for behavior that differs from the change's specifications
- **THEN** triage records a `change` and the implementation updates both the code and the repository's specifications

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

### Requirement: Reply and resolve

After implementation, the workflow SHALL reply once to each eligible thread or comment: in the thread for inline comments, as a PR conversation comment for review summaries and conversation comments. A `change` reply SHALL say what changed and where and reference the commit; an `answer` reply SHALL contain the answer. It SHALL then resolve each inline thread it handled successfully. Threads whose change was not pushed SHALL be answered but left unresolved. Replies SHALL be posted with the fix credential so they appear as the factory's PR identity.

#### Scenario: Answered thread

- **WHEN** the agent answers an inline question
- **THEN** the thread carries the reply and is resolved, and a reviewer's later reply in that thread unresolves it and becomes eligible for the next round

#### Scenario: Summary review

- **WHEN** the eligible comment is a review summary body
- **THEN** the reply is a PR conversation comment referencing that review

### Requirement: Merge the target branch into a feature pull request before triage

For a review round on a feature pull request, the factory SHALL fetch the target repository and resolve the current head of the pull request's base branch when it prepares the round. The workflow SHALL merge that head into the PR branch before triage. The round's evidence SHALL record the merged head. A merge without conflicts SHALL be committed as a merge commit that is not a phase checkpoint, and a base head that the PR branch already contains SHALL add no commit. A conflicting merge SHALL be resolved by an agent before triage, preserving the intent of both sides, and committed. When the agent cannot resolve the conflicts confidently, the workflow SHALL skip triage and return `needs-input` naming the conflicting files and the decision needed. It SHALL push nothing and post no replies to the eligible comments, and it SHALL leave the PR branch unchanged. The claim is then blocked by that round's `needs-input`, and a writer's next eligible comment SHALL admit a new round that merges again with the answer in `review.json`. The round SHALL NOT re-run acceptance. Its completion comment SHALL name each merge commit it pushed, whether the merge was clean or resolved by the agent, as added after acceptance and not covered by the acceptance evidence, linking the commit and the acceptance evidence. When the merge was not pushed, the comment SHALL say so rather than name it. A conflict resolution SHALL satisfy the same history and conflicting-files checks as a feature attempt's resolution. Review rounds on fix and task pull requests SHALL NOT merge the base branch.

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

#### Scenario: Task pull request round does not merge

- **WHEN** a review round is admitted on a task pull request whose base branch gained commits
- **THEN** the round runs on the PR branch at its recorded head without merging the base branch

### Requirement: Hold task scope through review rounds

On a task pull request, review triage SHALL judge every requested change against the task scope defined by `factory-task-execution`. Out of scope are changes to runtime behavior, a public API or CLI, an OpenSpec specification, or persisted data, and changes touching credentials, release or deploy configuration, or branch protection. Also out of scope are changes needing an unbounded product, design, compatibility, or threshold decision, changes outside the target repository, and changes that would make the pull request too large to review as one. When any requested change falls outside task scope, the round SHALL implement nothing, push nothing, and post no replies to the eligible comments. It SHALL return `needs-input` naming each out-of-scope request and routing it to a Bug or a Feature. After implementing in-scope changes and before pushing, the workflow SHALL check the round's diff against the same boundary. A diff that crosses it SHALL return `needs-input` naming what crossed, and SHALL push nothing. When finalization's CI repair adds commits to a task pull request, the round SHALL repeat the boundary check on the round's complete diff after finalization. A crossing SHALL return `failed` with the reasons, leaving the pull request open. A writer's next eligible comment, for example withdrawing or narrowing the request, SHALL admit a new round that triages again. Review rounds on fix and feature pull requests SHALL be unchanged.

#### Scenario: Reviewer asks a task pull request for a behavior change

- **WHEN** a writer asks on a task pull request to also change a CLI flag's default
- **THEN** the round returns `needs-input` naming the request and stating it belongs in a Feature
- **AND** nothing is pushed and the pull request is unchanged

#### Scenario: Reviewer asks for an in-scope adjustment

- **WHEN** a writer asks on a task pull request to baseline one more file instead of fixing it
- **THEN** triage records a `change`, the round implements it, and the outcome is `pull-request` when the validator and CI pass

#### Scenario: Diff crosses the boundary

- **WHEN** a round's in-scope change ends up editing the repository's release workflow
- **THEN** the round returns `needs-input` naming the release configuration change and pushes nothing

#### Scenario: CI repair crosses the boundary in a task round

- **WHEN** a task round's in-scope change passes the pre-push check, but finalization's CI repair pushes a commit that edits a release workflow
- **THEN** the round returns `failed` naming the crossing and the pull request stays open

#### Scenario: Narrow the request after a scope stop

- **WHEN** a task round stopped for an out-of-scope request and the writer replies withdrawing it
- **THEN** the next poll admits a new round that triages the remaining comments and implements the in-scope ones

#### Scenario: Fix pull request round keeps widening rule

- **WHEN** a writer asks on a fix pull request for a change that widens the original bug's scope
- **THEN** triage records a `change` as before

