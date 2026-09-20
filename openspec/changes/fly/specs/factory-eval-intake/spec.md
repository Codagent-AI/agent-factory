## MODIFIED Requirements

### Requirement: Interpret one evaluation configuration per request

The factory SHALL read TOML execution overrides from a fenced `eval` block in the issue body and ignore surrounding prose for execution settings. Supported keys SHALL be `agent_runner_ref`, `agent_skills_ref`, `lead`, `implementor`, `tester`, `skip_validator`, and `repetitions`. Other keys, including the legacy `lead_profile`, `implementor_profile`, `reviewer`, `reviewer_profile`, and `tester_profile` aliases, SHALL be rejected. Each supplied role override SHALL contain a complete `cli / model / effort` triple as a TOML string; `skip_validator` SHALL be a boolean. Omitted settings SHALL use configured defaults. A request SHALL describe one configuration with a repetition count, without automatic matrix expansion.

When the eval kind is configured for Fly execution, a request whose effective lead, implementor, or tester profile uses the `cursor` CLI, whether supplied in the block or inherited from the configured defaults, SHALL be an invalid request and SHALL follow the correction behavior below with a comment naming the affected role and stating that Cursor is unavailable on Fly. Under Docker execution Cursor profiles SHALL remain valid.

Request-level revision selection SHALL apply to Agent Runner and Agent Skills. The `agent-evals` harness SHALL follow the configured harness branch (default `main`); request-level selection of harness revisions remains unsupported. Recording the resolved harness commit SHALL identify the test environment used for the result.

Repetitions SHALL be a positive integer. An optional configured maximum SHALL reject excessive requests rather than reduce them silently. A repetition ceiling is not required.

#### Scenario: Override selected defaults

- **WHEN** a valid block supplies a Runner ref and a complete lead profile while omitting other settings
- **THEN** those supplied settings replace their respective defaults
- **AND** all omitted settings retain their configured defaults

#### Scenario: Supply invalid execution settings

- **WHEN** a block is malformed, a supplied role profile is incomplete, an unsupported key is supplied, or repetitions is not a positive integer
- **THEN** the request is invalid and no evaluation starts

#### Scenario: Exceed an optional repetition limit

- **WHEN** a repetition maximum is configured and a request exceeds it
- **THEN** the factory rejects the request settings and explains the limit
- **AND** it does not silently run fewer repetitions

#### Scenario: Select Cursor under Fly execution

- **WHEN** the eval kind runs under Fly execution and a request's effective role profiles include a `cursor` CLI
- **THEN** the request is invalid, stays in Ready with `needs-input`, and the comment names the role and that Cursor is unavailable on Fly
- **AND** no claim is created

#### Scenario: Replace Cursor with a supported CLI

- **WHEN** the user changes the flagged role to a complete Codex or Claude profile
- **THEN** the factory removes `needs-input` and the request can become eligible for admission

### Requirement: Freeze accepted evaluation inputs

A new claim SHALL record the effective evaluation settings, including the selected eval suite, and resolve the requested Runner and Skills refs and the configured `agent-evals` harness branch to immutable commits from the remote at admission. `agent-evals` is the evaluation harness and may contain multiple suites; `and-scene` is the default suite. Those accepted inputs SHALL remain fixed for the claim, including its repetitions and automatic recovery. Later changes to branches, defaults, or the issue SHALL NOT mutate an existing claim's frozen inputs. Configuration SHALL name the harness branch, not a commit.

#### Scenario: Continue after refs or defaults change

- **WHEN** an unfinished claim resumes after its requested branches, the harness branch, or configured defaults have changed
- **THEN** it uses the same accepted settings and immutable revisions
- **AND** its completed repetitions remain completed

#### Scenario: Admit two evals on different days

- **WHEN** the harness branch advances between two admissions
- **THEN** each claim records the harness commit it resolved at its own admission and the difference is visible in its Refs and results

#### Scenario: Switch to Fly with a frozen Cursor claim

- **WHEN** a claim admitted with a Cursor role profile has unfinished repetitions and the eval execution mode changes to Fly
- **THEN** its frozen inputs are not mutated and it is held under the eval readiness behavior in `factory-claim-lifecycle` with an explanation naming the Cursor role
