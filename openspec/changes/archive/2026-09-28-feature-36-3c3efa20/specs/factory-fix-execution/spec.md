## MODIFIED Requirements

### Requirement: Record host provenance

For a host-mode attempt the run record SHALL store:

- the execution mode;
- the path and reported version of the Runner executable that ran;
- the path of the Agent Validator executable that ran, the version it reports, and the full commit its build came from;
- the Runner session directory.

The Validator commit SHALL be derived from the version the executable reports, expanded to a full SHA against the operator's Validator checkout. It SHALL NOT be taken from the checkout's current `HEAD`. When the executable reports no commit, or the commit cannot be expanded, provenance SHALL record the reported version and say the full commit is unavailable. The attempt's evidence SHALL state that the attempt ran on the host, that the claim's recorded Runner and Skills commits were not the versions that executed, and which Agent Validator commit ran. The claim's recorded commits SHALL be unchanged. Feature attempts SHALL record the same provenance through the host execution rules they follow.

#### Scenario: Inspect a host attempt's provenance

- **WHEN** an operator inspects a host-mode attempt
- **THEN** the run record shows mode `host`, the Runner executable and version used, the Agent Validator executable, its reported version and full commit, and the session directory
- **AND** the evidence states that the recorded Runner and Skills commits were not executed and names the Validator commit that ran

#### Scenario: Record the build, not the checkout

- **WHEN** the Validator checkout's `HEAD` has advanced past the commit its build on PATH reports, because a deploy skipped the build
- **THEN** the attempt records the commit the build reports, not the checkout's `HEAD`

#### Scenario: Validator reports no commit

- **WHEN** `agent-validator --version` on the host reports no commit, for example because it is an npm release
- **THEN** the attempt records the reported version and states that the full Validator commit is unavailable, and the attempt still runs
