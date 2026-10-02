## MODIFIED Requirements

### Requirement: Record each dispatched session's usage

Each dispatch that started a session SHALL record:

- its model profile;
- its start and end times and duration;
- its input and output tokens;
- its estimated cost.

Tokens and cost SHALL come from the session's Agent Runner metrics. When a value cannot be read, or the Runner reports it as incomplete, it SHALL be recorded as unavailable, with the Runner's coverage, rather than as zero. The factory SHALL record each session's usage metrics in its dispatch record and status. The watch-session post-run audit SHALL run only when the factory audit switch that governs all post-run audits is on. That switch is off pending Codagent-AI/agent-factory#60. When disabled, no metrics are sent to the development-audit destination and the audit outcome is recorded as missing. When enabled, audit delivery and its outcome SHALL be recorded. A session terminated at its timeout skips that audit, and its delivery SHALL be recorded as missing.

#### Scenario: A completed review records its usage

- **WHEN** a PR-READY check session completes
- **THEN** its dispatch record holds the model profile, duration, input and output tokens, and estimated cost, and, when the audit switch is enabled, its metrics reach the development-audit destination

#### Scenario: An interrupted session

- **WHEN** a session is interrupted before it reports its usage
- **THEN** its tokens and cost are recorded as unavailable

#### Scenario: A timed-out session

- **WHEN** a session is terminated at its timeout
- **THEN** its usage holds whatever the Runner recorded, with its coverage, and its audit delivery is recorded as missing
