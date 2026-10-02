## MODIFIED Requirements

### Requirement: Interpret one evaluation configuration per request

The factory SHALL read TOML execution overrides from a fenced `eval` block in the issue body and ignore surrounding prose for execution settings. Supported keys SHALL be `agent_runner_ref`, `agent_skills_ref`, `fixture_ref`, `lead`, `implementor`, `tester`, `skip_validator`, and `repetitions`. Other keys, including the legacy `lead_profile`, `implementor_profile`, `reviewer`, `reviewer_profile`, and `tester_profile` aliases, SHALL be rejected. Each supplied role override SHALL contain a complete `cli / model / effort` triple as a TOML string; `skip_validator` SHALL be a boolean; `fixture_ref`, when supplied, SHALL be a non-empty TOML string. Omitted settings SHALL use configured defaults. A request SHALL describe one configuration with a repetition count, without automatic matrix expansion.

When the eval kind is configured for Fly execution, a request whose effective lead, implementor, or tester profile uses the `cursor` CLI, whether supplied in the block or inherited from the configured defaults, SHALL be an invalid request and SHALL follow the correction behavior below with a comment naming the affected role and stating that Cursor is unavailable on Fly. Under Docker execution Cursor profiles SHALL remain valid.

Request-level revision selection SHALL apply to Agent Runner, Agent Skills, and the and-scene fixture. `fixture_ref` names an and-scene branch, tag, or commit for the suite's fixture. When it is omitted, the suite SHALL use the fixture its pinned harness selects, as before this change; configuration SHALL supply no default for it. The `agent-evals` harness SHALL follow the configured harness branch (default `main`); request-level selection of harness revisions remains unsupported. Recording the resolved harness commit SHALL identify the test environment used for the result.

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

#### Scenario: Select a fixture ref

- **WHEN** a valid block supplies `fixture_ref = "eval/fixture-sonnet-validator"` and omits every other setting
- **THEN** the request is valid, its fixture selection is `eval/fixture-sonnet-validator`, and all other settings retain their configured defaults

#### Scenario: Omit the fixture ref

- **WHEN** a valid block does not supply `fixture_ref`
- **THEN** the request is valid and selects no fixture revision, so the suite uses the fixture its pinned harness selects

#### Scenario: Supply an invalid fixture ref

- **WHEN** a block supplies `fixture_ref` as an empty string or as a non-string TOML value
- **THEN** the request is invalid, stays in Ready with `needs-input`, the comment states that `fixture_ref` must be a non-empty string, and no evaluation starts

## ADDED Requirements

### Requirement: Freeze a requested fixture revision

When an accepted request supplies `fixture_ref`, admission SHALL resolve it to a full 40-character commit SHA of the and-scene repository and record both the requested ref and that SHA with the claim's other frozen inputs. Resolution SHALL use the operator's and-scene checkout (see `factory-operations`), fetching it from its origin first. A branch name SHALL resolve to the origin's branch, not a local branch. Each repetition, retry, and resume of the claim SHALL use the recorded SHA even after the requested ref moves, under both Docker and Fly execution.

Admission SHALL accept only a fixture commit that the suite's own clone can fetch:

- the checkout's origin, normalized to `https://github.com/<owner>/<repo>.git` without credentials, SHALL be the repository the and-scene suite clones its fixture from (`https://github.com/Codagent-AI/and-scene.git`);
- after the fetch, which prunes deleted remote branches, the commit SHALL be reachable from a branch on that origin or from a tag that the origin advertises.

These rules apply equally to a branch, a tag, and a full or abbreviated SHA. A commit present only locally, or reachable only from a local branch, a local-only tag, or a pruned remote branch, SHALL NOT be accepted.

When the fixture revision cannot be resolved or accepted, the factory SHALL NOT admit the eval. It SHALL handle the failure exactly as it handles a Runner or Skills ref that cannot be resolved: a single "waiting for revision readiness" comment per distinct reason, the attention label, and a recheck on later cycles. The reason SHALL name the and-scene checkout and say which of these applies, without credentials:

- the checkout is missing;
- the fetch failed;
- the origin is not the suite's fixture repository;
- the ref is unknown or ambiguous;
- the commit is not published on the origin.

A request that does not supply `fixture_ref` SHALL NOT consult the and-scene checkout. Its claim SHALL record no fixture ref or fixture revision. A claim admitted before this change SHALL remain valid without one.

#### Scenario: Freeze a fixture branch

- **WHEN** a request supplies `fixture_ref = "eval/fixture-sonnet-validator"` and that branch is at `b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a` on the and-scene origin
- **THEN** the claim records the requested ref `eval/fixture-sonnet-validator` and the fixture revision `b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a`

#### Scenario: Freeze a published commit

- **WHEN** a request supplies `fixture_ref = "b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a"` and that commit is reachable from a branch on the and-scene origin
- **THEN** the claim records that full SHA as its fixture revision

#### Scenario: Keep the frozen fixture after the branch advances

- **WHEN** a claim froze fixture revision `F` from a branch and the branch later receives commits before the claim's remaining repetitions run
- **THEN** every remaining repetition and recovery attempt of the claim uses `F`

#### Scenario: Refuse an unpublished commit

- **WHEN** a request supplies a SHA that exists in the local and-scene checkout but is not reachable from any branch or advertised tag on its origin after the fetch
- **THEN** no claim is created, and the issue receives one "waiting for revision readiness" comment stating that the fixture commit is not published on the and-scene origin, and the attention label
- **AND** a later cycle admits the eval once the commit is pushed to a branch on the origin

#### Scenario: Refuse a commit kept only by a deleted remote branch

- **WHEN** a request supplies a SHA whose only remote branch was deleted from the origin, and the local checkout still holds the commit
- **THEN** no claim is created, and the issue receives one "waiting for revision readiness" comment stating that the fixture commit is not published on the and-scene origin

#### Scenario: Refuse an unknown fixture ref

- **WHEN** a request supplies a `fixture_ref` that names no branch, tag, or commit on the and-scene origin
- **THEN** no claim is created, and the issue receives one "waiting for revision readiness" comment naming the and-scene checkout and the unknown ref, and the attention label

#### Scenario: Refuse a checkout whose origin is another repository

- **WHEN** a request supplies `fixture_ref` and the and-scene checkout's origin is a fork, another host, a local path, or a `file://` URL
- **THEN** no claim is created, and the issue receives one "waiting for revision readiness" comment stating that the checkout's origin is not the suite's fixture repository

#### Scenario: Fixture checkout missing

- **WHEN** a request supplies `fixture_ref` and no and-scene checkout exists at the configured or default path
- **THEN** no claim is created, and the issue receives one "waiting for revision readiness" comment naming the missing and-scene checkout
- **AND** requests that do not supply `fixture_ref` are admitted as before

#### Scenario: Admit a default request without the checkout

- **WHEN** a request omits `fixture_ref` and no and-scene checkout exists
- **THEN** the eval is admitted as before this change, and its claim records no fixture ref or fixture revision

#### Scenario: Continue a claim admitted before fixture selection

- **WHEN** an unfinished claim recorded before this change, without a fixture revision, launches its next repetition
- **THEN** the claim is not rejected, its frozen inputs are not changed, and the suite uses the fixture its pinned harness selects
