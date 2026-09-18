# factory-review-execution Specification

## Purpose
TBD - created by archiving change code-review. Update Purpose after archive.
## Requirements
### Requirement: Run the versioned review workflow

The factory SHALL ship a packaged review workflow declaring contract `factory-review/1`, stage it beside the fix workflow and the shared implementation sub-workflow, and launch it through the same sandbox path as a fix attempt, on clones where the target is checked out on the PR branch at its recorded head. The factory SHALL write `review.json` into the attempt's artifact directory containing the repository, issue number, claim identifier, attempt number, the pull request number, URL, branch, base branch and head commit, the original issue title and body, and the eligible comments grouped by source (review summaries, unresolved inline threads with path, line, thread identifier and every comment in the thread, and conversation comments), each with author, identifier, body, and creation time. The workflow SHALL return exactly one structured outcome in `review-outcome.json` declaring its contract: `pull-request` with the PR reference and the identifiers it answered and changed, `needs-input` with reasons, `failed` with reasons, or a technical failure when the file is absent or invalid.

#### Scenario: Launch a review round

- **WHEN** a review round is admitted with a compatible Runner commit
- **THEN** the attempt starts under its own supervisor with the review contract and `review.json` describes the PR and the eligible comments

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `review-outcome.json`
- **THEN** the factory records a technical failure and applies the fix recovery policy

### Requirement: Triage each comment and decide autonomously

The review workflow SHALL read `review.json` and produce one decision per eligible comment or thread: `change` with a concrete plan when the comment asks for a code change the agent should make, or `answer` with the reply text when a reply suffices. When a comment is ambiguous, the agent SHALL decide itself which applies and explain its reading in the reply. It SHALL return `needs-input` only when reviewer requests conflict with each other, when a requested change requires a non-trivial specification change or changes outside the target repository, or when a genuinely open product decision must be made by a human; it SHALL name what needs deciding. A requested change that widens the original bug's scope SHALL still be made.

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

### Requirement: Implement, verify, and push on the existing branch

When any decision is `change`, the workflow SHALL implement the changes on the existing PR branch through the shared implementation sub-workflow: implement with tests following repository conventions, run the validator with its repair cycles, exercise the changed flows, repair regressions once with a verification-only validator pass, push to the existing branch, and wait for CI with one fix cycle. It SHALL NOT create a branch or a pull request. A validator that remains red SHALL return `failed` before any push; CI that remains red after the loop SHALL return `failed` while leaving the PR open. When no decision is `change`, the implementation sub-workflow SHALL be skipped and the outcome SHALL be `pull-request` after the replies are posted.

#### Scenario: Change pushed and green

- **WHEN** the implementation passes the validator and CI
- **THEN** the PR branch has new commits, the PR is unchanged in identity, and the outcome is `pull-request`

#### Scenario: Validator stays red

- **WHEN** the validator fails after its repair cycles
- **THEN** nothing is pushed, the reply on each `change` thread says so, that thread stays unresolved, and the outcome is `failed`

### Requirement: Reply and resolve

After implementation, the workflow SHALL reply once to each eligible thread or comment: in the thread for inline comments, as a PR conversation comment for review summaries and conversation comments. A `change` reply SHALL say what changed and where and reference the commit; an `answer` reply SHALL contain the answer. It SHALL then resolve each inline thread it handled successfully. Threads whose change was not pushed SHALL be answered but left unresolved. Replies SHALL be posted with the fix credential so they appear as the factory's PR identity.

#### Scenario: Answered thread

- **WHEN** the agent answers an inline question
- **THEN** the thread carries the reply and is resolved, and a reviewer's later reply in that thread unresolves it and becomes eligible for the next round

#### Scenario: Summary review

- **WHEN** the eligible comment is a review summary body
- **THEN** the reply is a PR conversation comment referencing that review

