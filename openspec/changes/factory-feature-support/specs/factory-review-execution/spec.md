## MODIFIED Requirements

### Requirement: Run the versioned review workflow

The factory SHALL ship a packaged review workflow declaring contract `factory-review/1`, stage it beside the fix workflow and the shared implementation sub-workflow, and launch it through the execution path of the claim's kind, a fix claim's round as a fix attempt and a feature claim's round on the host as a feature attempt, on clones where the target is checked out on the PR branch at its recorded head. The factory SHALL write `review.json` into the attempt's artifact directory containing the repository, issue number, claim identifier, the claim's work kind, attempt number, the pull request number, URL, branch, base branch and head commit, the original issue title and body, and the eligible comments grouped by source (review summaries, unresolved inline threads with path, line, thread identifier and every comment in the thread, and conversation comments), each with author, identifier, body, and creation time. The workflow SHALL return exactly one structured outcome in `review-outcome.json` declaring its contract: `pull-request` with the PR reference and the identifiers it answered and changed, `needs-input` with reasons, `failed` with reasons, or a technical failure when the file is absent or invalid.

#### Scenario: Launch a review round

- **WHEN** a review round is admitted with a compatible Runner commit
- **THEN** the attempt starts under its own supervisor with the review contract and `review.json` describes the PR and the eligible comments

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `review-outcome.json`
- **THEN** the factory records a technical failure and applies the recovery policy of the claim's kind

### Requirement: Triage each comment and decide autonomously

The review workflow SHALL read `review.json` and produce one decision per eligible comment or thread: `change` with a concrete plan when the comment asks for a code change the agent should make, or `answer` with the reply text when a reply suffices. When a comment is ambiguous, the agent SHALL decide itself which applies and explain its reading in the reply. It SHALL return `needs-input` only when reviewer requests conflict with each other, when a requested change on a fix pull request requires a non-trivial specification change, when a requested change requires changes outside the target repository, or when a genuinely open product decision must be made by a human; it SHALL name what needs deciding. A requested change that widens the original issue's scope SHALL still be made. On a feature pull request, a requested change that alters specified behavior SHALL be made rather than declined.

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

When any decision is `change`, the workflow SHALL implement the changes on the existing PR branch through the shared implementation sub-workflow: implement with tests following repository conventions, run the validator with its repair cycles, exercise the changed flows, repair regressions once with a verification-only validator pass, push to the existing branch, and wait for CI with one fix cycle. It SHALL NOT create a branch or a pull request. On a feature pull request it SHALL keep the repository's specifications under `openspec/specs/` consistent with the changed behavior, and SHALL NOT re-run acceptance. A validator that remains red SHALL return `failed` before any push; CI that remains red after the loop SHALL return `failed` while leaving the PR open. When no decision is `change`, the implementation sub-workflow SHALL be skipped and the outcome SHALL be `pull-request` after the replies are posted.

#### Scenario: Change pushed and green

- **WHEN** the implementation passes the validator and CI
- **THEN** the PR branch has new commits, the PR is unchanged in identity, and the outcome is `pull-request`

#### Scenario: Validator stays red

- **WHEN** the validator fails after its repair cycles
- **THEN** nothing is pushed, the reply on each `change` thread says so, that thread stays unresolved, and the outcome is `failed`
