## MODIFIED Requirements

### Requirement: Comment on feature activity

The factory SHALL comment on the issue when it admits a feature attempt, when it admits a review round (naming the pull request and the comments it will address), when an attempt stops with `needs-input`, fails, is retried, is cancelled, or produces a pull request, and when a review round completes (linking the pull request and summarizing what was changed and answered). The admission comment SHALL include the resolved refs and attempt number and SHALL state whether the attempt starts fresh, resumes at a named step, or continues a prior claim's branch, and SHALL state when a resume point was unavailable and the attempt started fresh. The comment reporting a `needs-input` stop SHALL list the specific questions, summarize the direction drafted so far, and link the pushed branch; for a `preflight` stop it SHALL state that no branch was created and that the next attempt starts fresh. For a `needs-input` stop recorded because a step's repair was blocked, the comment SHALL show the agent's explanation as its question and a direction summary that names the blocked step and the step the next attempt resumes at. When that stop published no work on the claim's branch, the comment SHALL state that no branch was published and that the next attempt starts fresh, instead of linking a branch. The comment linking a produced pull request SHALL state the number of red, orange, and yellow items of the final classification defined by `factory-feature-execution`, matching the pull request description. Every comment reporting an attempt outcome SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The acceptance comment posted when a feature claim's inputs are accepted SHALL read `Feature inputs accepted and frozen.` Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

#### Scenario: Admit a resumed attempt

- **WHEN** the factory admits a resumed attempt for a claim that stopped during design
- **THEN** the issue receives one comment naming the refs, the attempt number, and that the attempt resumes at design

#### Scenario: Report a definition stop

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the issue receives one comment listing the questions, summarizing the drafted direction, and linking the pushed branch

#### Scenario: Report a blocked repair

- **WHEN** a feature attempt records `needs-input` because the repair of a check inside `implement` was blocked
- **THEN** the issue receives one comment with the agent's explanation, a direction summary naming the blocked check inside `implement` and the step the next attempt resumes at, and a link to the pushed branch

#### Scenario: Report a blocked repair with nothing published

- **WHEN** a feature attempt records `needs-input` with no resume point because the repair of a check inside definition was blocked before anything was pushed
- **THEN** the issue receives one comment with the agent's explanation, stating that no branch was published and that the next attempt starts fresh

#### Scenario: Report flag counts with the pull request

- **WHEN** a feature attempt returns `pull-request` with two red items, three orange items, and twelve yellow items
- **THEN** the comment linking the pull request states two red flags, three orange flags, and twelve yellow items

#### Scenario: Report a fresh start after an unavailable resume point

- **WHEN** a continuing claim's prior branch no longer exists
- **THEN** the outcome comment states that the prior branch was unavailable and the attempt started fresh

#### Scenario: Accept a feature claim's inputs

- **WHEN** the factory accepts and freezes a feature claim's inputs
- **THEN** the acceptance comment reads `Feature inputs accepted and frozen.`
