## ADDED Requirements

### Requirement: Govern post-run audits with one switch

One factory audit switch SHALL govern every post-run development audit the factory starts or settles. This covers the host attempt audit, the resident's settlement of an attempt's audit outcome (including delivery of an eval's collected reports), the post-run audit lines in `status`, and the post-run audit check in `doctor`. The switch SHALL be off. That is a temporary disable pending a decision on whether audits are worth their cost (Codagent-AI/agent-factory#60). Turning the switch back on SHALL restore the factory-owned audit behavior that applied before it was turned off, unchanged.

The switch governs only what the factory itself starts or settles. Audits that Agent Runner starts by its own automatic hook are outside it, including the audits inside an eval's sandbox or a Fly guest whose reports the resident delivers. Agent Runner turns that hook off separately (Codagent-AI/agent-runner#191). So:

- An attempt that runs a Runner revision which still has the hook, such as a claim admitted with an older pinned Runner, may still produce Runner-side audit reports while the switch is off. The resident SHALL NOT deliver or report them.
- Restoring eval audits requires both the switch and the Runner's automatic hook to be on.

While the switch is off:

- A host fix, feature, or review attempt SHALL run no post-run audit replay, and its outcome SHALL NOT depend on audits.
- When the resident consumes an attempt's result, it SHALL record no post-run audit outcome for that attempt, deliver no collected eval reports to the development-audit destination, and post no `post-run-audit` issue event.
- `status` SHALL NOT list an attempt as missing its post-run audit only because it has no recorded audit outcome. It SHALL still list a recorded undelivered outcome from within the last seven days.
- The `doctor` post-run audit check SHALL pass and state that post-run audits are disabled. It SHALL NOT probe the installed Agent Runner for audit support or require the development-audit reporting connection. So neither a missing connection nor a Runner built without development audits fails `doctor` or blocks admission.

Agent Runner's own execution log in an attempt's session evidence is not a post-run audit outcome, and this requirement SHALL NOT remove it.

#### Scenario: A host attempt finishes with audits off

- **WHEN** the switch is off and a host fix, feature, or review attempt's workflow ends, whether it succeeded or failed
- **THEN** no `agent-runner audit replay` runs for the attempt, the attempt's exit status is the workflow's own, and its evidence holds no post-run audit outcome

#### Scenario: The resident consumes an attempt with audits off

- **WHEN** the switch is off and the resident consumes the result of any attempt, including one whose Agent Runner metrics were recorded and an eval whose sandbox collected reports
- **THEN** no `post-run-audit` issue event is posted, no audit outcome is recorded, no reports are delivered to the development-audit destination, and the rest of result consumption and reporting proceeds as before

#### Scenario: Status after a run with audits off

- **WHEN** the switch is off and a host attempt finished within the last seven days with Agent Runner metrics but no recorded audit outcome
- **THEN** `status` shows no post-run audit line for that attempt

#### Scenario: Status keeps earlier recorded outcomes

- **WHEN** the switch is off and an attempt that finished within the last seven days recorded an undelivered audit outcome before the switch was turned off
- **THEN** `status` still lists that attempt's post-run audit outcome and reason

#### Scenario: Doctor without a reporting connection

- **WHEN** the switch is off and the Mac has no development-audit reporting connection, or the installed Agent Runner lacks development audits
- **THEN** `doctor` reports the post-run audit check as passing, with a detail saying post-run audits are disabled, and admission is not held because of audits

#### Scenario: Re-enabling audits

- **WHEN** the switch is turned on
- **THEN** host attempts replay their audit, the resident settles and reports undelivered audits as `post-run-audit` events, `status` lists missing and undelivered audits, and `doctor` checks Runner audit support and the reporting connection, exactly as before the switch was turned off
- **AND** the resident again delivers an eval's reports only when the attempt's Agent Runner collected them, which requires the Runner's automatic audit hook to be on
