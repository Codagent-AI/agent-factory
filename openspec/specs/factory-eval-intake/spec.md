# factory-eval-intake Specification

## Purpose
TBD - created by archiving change iteration-1. Update Purpose after archive.
## Requirements
### Requirement: Create explicitly assigned evaluation requests

The change SHALL provide a regular Markdown issue template in `Codagent-AI/agent-evals`. The template SHALL set the native organizational issue type to `Eval`, apply the `eval-request` label, and pre-fill a description and fenced `eval` configuration block. Creating the request by an author with write, maintain, or admin access to the source repository SHALL be sufficient to assign it to the factory and queue it; no manual assignment or move to Ready SHALL be required. The Project SHALL display the native issue Type rather than a duplicate custom Type field.

#### Scenario: Create an evaluation request

- **WHEN** a user with write, maintain, or admin access to the source repository creates an issue using the evaluation request template
- **THEN** the issue has native Type `Eval` and the `eval-request` label
- **AND** routing adds it to the configured Project with `Owner=factory` and `Status=Ready` without another human action

### Requirement: Restrict automatic execution to repository writers

Execution admission SHALL verify that the issue author has effective write, maintain, or admin permission on its source repository, independently of the check routing performed. Organization membership or the presence of the request label alone SHALL NOT satisfy this check. Failure to establish the author's permission SHALL NOT be treated as authorization. Routing-time enforcement is specified in `factory-routing`.

Execution admission SHALL identify an evaluation card by its native issue type, its source repository, and its board Owner and Status. The routing label SHALL be the router's entry signal only and SHALL NOT be a further condition of admission, so a card carrying the evaluation issue type is admitted whether or not the label is present.

#### Scenario: Receive an outside contributor's request

- **WHEN** a public-repository contributor without write access creates an issue from the eval template
- **THEN** routing leaves the request in Backlog without factory ownership, as specified in `factory-routing`
- **AND** execution admission never accepts it even though the template applied the request label

#### Scenario: Recheck permission at execution admission

- **WHEN** a Ready card has the eval marker but its author lacks the required repository access
- **THEN** the factory does not accept it for execution based only on its label or board fields

#### Scenario: Admit an evaluation card created without the routing label

- **WHEN** a Ready card owned by the factory in the eval source repository carries the evaluation issue type but not the routing label
- **THEN** the factory admits it for execution on the strength of its issue type
- **AND** the operator is not required to restate the issue type as a label

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

### Requirement: Flag invalid requests for correction

An invalid request SHALL remain in Ready and receive the red `needs-input` label and a comment explaining how to correct it. Validation failures SHALL start no evaluation, consume no execution retry, and SHALL NOT be classified as `infra-error`. Unchanged invalid input SHALL NOT produce the same corrective comment on every poll. The factory SHALL revalidate corrected input and remove `needs-input` automatically when it becomes valid.

#### Scenario: Encounter unchanged invalid input repeatedly

- **WHEN** successive polls encounter the same invalid request settings
- **THEN** the request remains flagged in Ready without execution or repeated copies of the same corrective comment
- **AND** the factory can consider other eligible requests

#### Scenario: Correct the request

- **WHEN** the user corrects the eval block so its settings are valid
- **THEN** the factory removes `needs-input` and the request can become eligible for admission

#### Scenario: Remove only the attention label

- **WHEN** a user removes `needs-input` while the settings remain invalid
- **THEN** the factory does not admit the request and restores the attention label

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

### Requirement: Distinguish continuation from an explicit fresh request

A Ready card with `Verdict=quota-deferred` or `infra-error` SHALL continue its existing unfinished claim when eligible, preserving completed repetitions and retry history. On an otherwise eligible Ready card, a human clearing Verdict or changing the parsed eval settings SHALL request a new claim using current inputs while retaining prior claim history. Formatting changes and edits to surrounding prose SHALL NOT request a new claim.

The factory SHALL reconcile local claim and reporting state with board observations so that a Verdict missing because the factory has not finished reporting is not interpreted as a human reset. Edits during execution SHALL NOT alter the running claim or launch overlapping work. Interpreting a completed card merely dragged to Ready with its old verdict is outside iteration 1.

#### Scenario: Resume an automatically deferred claim

- **WHEN** an eligible Ready card still has its deferral verdict and unchanged parsed request settings
- **THEN** the factory continues the existing unfinished claim with its frozen inputs and remaining retry budget

#### Scenario: Request a fresh evaluation explicitly

- **WHEN** a human clears a previously reported Verdict or changes the parsed eval settings on an otherwise eligible Ready card
- **THEN** the factory creates a new claim, resolves current inputs, and retains the prior claim's history

#### Scenario: Recover incomplete factory reporting

- **WHEN** a claim exists but its expected Verdict update has not been delivered
- **THEN** the missing Verdict does not by itself create a new claim
- **AND** the factory reconciles the existing claim and pending reporting

#### Scenario: Edit text without changing execution settings

- **WHEN** a user changes surrounding prose or formatting without changing the parsed eval settings
- **THEN** the edit does not request a fresh claim

#### Scenario: Edit a running request

- **WHEN** a user changes request settings during execution
- **THEN** the active claim keeps its frozen inputs and no overlapping evaluation starts

### Requirement: Select eligible work by Priority

The factory SHALL select open issues from configured source repositories whose authors have write, maintain, or admin access, with native `Type=Eval`, `Owner=factory`, and `Status=Ready`, valid execution settings, and no applicable admission hold. Selection SHALL rank eligible requests by the Project Priority field, highest first with unset values last, then by newest creation time. Invalid or otherwise ineligible requests SHALL NOT prevent selection of a later eligible request. Reordering or reprioritizing SHALL NOT interrupt active work.

#### Scenario: Reorder queued evaluations

- **WHEN** a user gives one eligible eval a higher Priority than another before the next selection
- **THEN** the higher-priority eval is selected first, regardless of issue age or board position

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible evals share a Priority value
- **THEN** the more recently created eval is selected first

#### Scenario: Skip an ineligible request

- **WHEN** the highest queued request is invalid or cannot yet resume and a lower request is eligible under current admission controls
- **THEN** the factory selects the lower eligible request

#### Scenario: Reorder while an evaluation is running

- **WHEN** a user changes Priority values during active execution
- **THEN** the active evaluation continues and the new ranking governs subsequent selection

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

### Requirement: Keep frozen evaluation inputs stable across deploys

A deploy or rollback SHALL NOT change a claim's frozen inputs or the reports rendered from them.

#### Scenario: Continue a claim across a deploy or rollback

- **WHEN** a claim with unfinished repetitions is continued after the factory is deployed or
  rolled back
- **THEN** its recorded settings, revisions, sources, and request fingerprint are unchanged, and
  its `Refs` field, frozen-inputs comment, and repetition reports are the same as before
