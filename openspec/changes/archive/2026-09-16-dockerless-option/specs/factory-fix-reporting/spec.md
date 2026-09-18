## MODIFIED Requirements

### Requirement: Comment on fix activity

The factory SHALL comment on the issue when it admits a bug (including the resolved refs and attempt number), when an attempt is declined, fails, is retried, is cancelled, or produces a PR, and when a post-merge sync succeeds or is blocked. Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state. When the attempt being reported ran in host mode, the comment reporting its outcome (`pull-request`, `needs-input`, `failed`, or exhausted recovery) SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The admission comment SHALL be unchanged.

#### Scenario: Admit a bug

- **WHEN** the factory admits a bug
- **THEN** the issue receives one comment naming the target, Runner, and Skills commits and the attempt number

#### Scenario: Report a host-mode PR

- **WHEN** a host-mode attempt returns `pull-request`
- **THEN** the PR comment links the PR and states that the attempt ran on the host with the operator's installed Runner and Skills rather than the recorded commits

#### Scenario: Report a Docker-mode outcome

- **WHEN** a Docker-mode attempt returns any outcome
- **THEN** its outcome comment carries no host note
