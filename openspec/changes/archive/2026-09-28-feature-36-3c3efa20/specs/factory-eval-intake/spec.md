## MODIFIED Requirements

### Requirement: Freeze accepted evaluation inputs

A new claim SHALL record the effective evaluation settings, including the selected eval suite, and resolve the requested Runner and Skills refs and the configured `agent-evals` harness branch to immutable commits from the remote at admission. When the eval kind is configured for Fly execution, the claim SHALL also resolve the configured Agent Validator branch (`agent_validator_ref`, default `main`) to an immutable commit through the operator's Agent Validator checkout, fetching it from its remote first, and record that commit with the Runner, Skills, and harness commits. The Validator branch SHALL come only from configuration; an eval request SHALL NOT select it, and an `agent_validator_ref` key in the eval block SHALL be rejected as an unsupported key. When the Validator commit cannot be resolved, the factory SHALL NOT admit the eval and SHALL handle it exactly as it handles a Runner or Skills ref that cannot be resolved: a single "waiting for revision readiness" comment per distinct reason, the attention label, and a recheck on later cycles. A claim admitted under Docker execution SHALL record no Validator commit. `agent-evals` is the evaluation harness and may contain multiple suites; `and-scene` is the default suite. Those accepted inputs SHALL remain fixed for the claim, including its repetitions and automatic recovery. Later changes to branches, defaults, or the issue SHALL NOT mutate an existing claim's frozen inputs. Configuration SHALL name the harness branch and the Validator branch, not commits. A claim admitted before the factory recorded Validator commits SHALL remain valid without one.

With the Validator commit, the claim SHALL also record the Validator source it will be built
from: the Validator checkout's GitHub origin, normalized to
`https://github.com/<owner>/<repo>.git` without credentials. HTTPS, `git@github.com:`, and
`ssh://git@github.com/` origins are accepted. An origin the Fly builder cannot fetch anonymously
over HTTPS, such as a local path, a `file://` URL, or another host, SHALL NOT be admitted; it is
handled as a Validator commit that cannot be resolved, with a reason naming the unusable origin
and no credentials.

A claim that recorded a Validator commit SHALL run only under Fly execution. While the eval kind
is configured for Docker execution, such a claim SHALL be held under the eval readiness behavior
in `factory-claim-lifecycle`, with an explanation naming the pinned Validator and how to resume.
No Docker attempt SHALL launch for it, and its frozen inputs SHALL NOT change.

#### Scenario: Continue after refs or defaults change

- **WHEN** an unfinished claim resumes after its requested branches, the harness branch, the Validator branch, or configured defaults have changed
- **THEN** it uses the same accepted settings and immutable revisions
- **AND** its completed repetitions remain completed

#### Scenario: Admit two evals on different days

- **WHEN** the harness branch advances between two admissions
- **THEN** each claim records the harness commit it resolved at its own admission and the difference is visible in its Refs and results

#### Scenario: Freeze the Validator for a Fly eval

- **WHEN** an eval is admitted under Fly execution while Agent Validator `main` is at commit `C`
- **THEN** the claim records `C` as its Validator revision, and every repetition and recovery attempt of the claim uses `C` even after `main` advances

#### Scenario: Validator cannot be resolved

- **WHEN** an eval would be admitted under Fly execution and the Validator checkout is missing or cannot be fetched
- **THEN** no claim is created, the issue receives one "waiting for revision readiness" comment naming the Validator checkout and the attention label, as for an unresolvable Runner ref
- **AND** a later cycle admits the eval once the Validator commit resolves

#### Scenario: Normalize an SSH origin

- **WHEN** an eval is admitted under Fly execution and the Validator checkout's origin is `git@github.com:Codagent-AI/agent-validator.git`
- **THEN** the claim records the source `https://github.com/Codagent-AI/agent-validator.git` with its Validator commit

#### Scenario: Refuse an origin the builder cannot fetch

- **WHEN** an eval would be admitted under Fly execution and the Validator checkout's origin is a local path or a `file://` URL
- **THEN** no claim is created, and the issue receives one "waiting for revision readiness" comment stating that the origin is not a GitHub repository the Fly builder can fetch

#### Scenario: Switch a pinned claim to Docker execution

- **WHEN** a claim that recorded a Validator commit has unfinished repetitions and eval execution changes from Fly to Docker
- **THEN** its next repetition is held with an explanation naming the pinned Validator, and no Docker attempt launches
- **AND** once eval execution is Fly again, the repetition launches with the claim's original frozen inputs

#### Scenario: Request a Validator ref

- **WHEN** an eval block supplies `agent_validator_ref`
- **THEN** the request is invalid as an unsupported key and no evaluation starts

#### Scenario: Continue a claim admitted before Validator pinning

- **WHEN** an unfinished claim recorded before this change, without a Validator revision, launches its next repetition
- **THEN** the claim is not rejected and its frozen inputs are not changed

#### Scenario: Switch to Fly with a frozen Cursor claim

- **WHEN** a claim admitted with a Cursor role profile has unfinished repetitions and the eval execution mode changes to Fly
- **THEN** its frozen inputs are not mutated and it is held under the eval readiness behavior in `factory-claim-lifecycle` with an explanation naming the Cursor role
