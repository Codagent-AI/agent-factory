## MODIFIED Requirements

### Requirement: Interpret one evaluation configuration per request

The factory SHALL read TOML execution overrides from a fenced `eval` block in the issue body and ignore surrounding prose for execution settings. Supported keys SHALL be `agent_runner_ref`, `agent_skills_ref`, `fixture_ref`, `lead`, `crosscheck`, `implementor`, `tester`, `skip_validator`, and `repetitions`. Other keys, including the legacy `lead_profile`, `implementor_profile`, `reviewer`, `reviewer_profile`, and `tester_profile` aliases, SHALL be rejected. Each supplied role override SHALL contain a complete `cli / model / effort` triple as a TOML string; `skip_validator` SHALL be a boolean; `fixture_ref`, when supplied, SHALL be a non-empty TOML string. Omitted settings SHALL use configured defaults; the configured eval defaults SHALL include a complete `crosscheck` profile, which the `and-scene` suite requires for the agent that reviews planned tasks. A request SHALL describe one configuration with a repetition count, without automatic matrix expansion.

When the eval kind is configured for Fly execution, a request whose effective lead, crosscheck, implementor, or tester profile uses the `cursor` CLI, whether supplied in the block or inherited from the configured defaults, SHALL be an invalid request and SHALL follow the correction behavior below with a comment naming the affected role and stating that Cursor is unavailable on Fly. Under Docker execution Cursor profiles SHALL remain valid.

Request-level revision selection SHALL apply to Agent Runner, Agent Skills, and the and-scene fixture. `fixture_ref` names an and-scene branch, tag, or commit for the suite's fixture. When it is omitted, the suite SHALL use the fixture its pinned harness selects, as before this change; configuration SHALL supply no default for it. The `agent-evals` harness SHALL follow the configured harness branch (default `main`); request-level selection of harness revisions remains unsupported. Recording the resolved harness commit SHALL identify the test environment used for the result.

Repetitions SHALL be a positive integer. An optional configured maximum SHALL reject excessive requests rather than reduce them silently. A repetition ceiling is not required.

#### Scenario: Override selected defaults

- **WHEN** a valid block supplies a Runner ref and a complete lead profile while omitting other settings
- **THEN** those supplied settings replace their respective defaults
- **AND** all omitted settings retain their configured defaults

#### Scenario: Override the crosscheck profile

- **WHEN** a valid block supplies `crosscheck = "claude:claude-opus-5-5:high"` and omits every other setting
- **THEN** the request is valid, its effective crosscheck profile is the supplied triple, and all other settings retain their configured defaults

#### Scenario: Omit the crosscheck profile

- **WHEN** a valid block does not supply `crosscheck`
- **THEN** the request's effective crosscheck profile is the configured eval default

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

#### Scenario: Select a fixture ref

- **WHEN** a valid block supplies `fixture_ref = "eval/fixture-sonnet-validator"` and omits every other setting
- **THEN** the request is valid, its fixture selection is `eval/fixture-sonnet-validator`, and all other settings retain their configured defaults

#### Scenario: Omit the fixture ref

- **WHEN** a valid block does not supply `fixture_ref`
- **THEN** the request is valid and selects no fixture revision, so the suite uses the fixture its pinned harness selects

#### Scenario: Supply an invalid fixture ref

- **WHEN** a block supplies `fixture_ref` as an empty string or as a non-string TOML value
- **THEN** the request is invalid, stays in Ready with `needs-input`, the comment states that `fixture_ref` must be a non-empty string, and no evaluation starts

