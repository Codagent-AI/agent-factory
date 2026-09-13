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

#### Scenario: Receive an outside contributor's request

- **WHEN** a public-repository contributor without write access creates an issue from the eval template
- **THEN** routing leaves the request in Backlog without factory ownership, as specified in `factory-routing`
- **AND** execution admission never accepts it even though the template applied the request label

#### Scenario: Recheck permission at execution admission

- **WHEN** a Ready card has the eval marker but its author lacks the required repository access
- **THEN** the factory does not accept it for execution based only on its label or board fields

### Requirement: Interpret one evaluation configuration per request

The factory SHALL read TOML execution overrides from a fenced `eval` block in the issue body and ignore surrounding prose for execution settings. Supported keys SHALL be `agent_runner_ref`, `agent_skills_ref`, `lead`, `implementor`, `tester`, `skip_validator`, and `repetitions`. Other keys, including the legacy `lead_profile`, `implementor_profile`, `reviewer`, `reviewer_profile`, and `tester_profile` aliases, SHALL be rejected. Each supplied role override SHALL contain a complete `cli / model / effort` triple as a TOML string; `skip_validator` SHALL be a boolean. Omitted settings SHALL use configured defaults. A request SHALL describe one configuration with a repetition count, without automatic matrix expansion.

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

### Requirement: Select eligible work in manual Project order

The factory SHALL select open issues from configured source repositories whose authors have write, maintain, or admin access, with native `Type=Eval`, `Owner=factory`, and `Status=Ready`, valid execution settings, and no applicable admission hold. Selection SHALL follow manual Project order, matching the unsorted Ready column within the Eval horizontal group. Priority values and issue age SHALL NOT override that order. Invalid or otherwise ineligible requests SHALL NOT prevent selection of a later eligible request. Reordering SHALL NOT interrupt active work.

#### Scenario: Reorder queued evaluations

- **WHEN** a user drags one eligible eval above another before the next selection
- **THEN** the higher eval is selected first, regardless of issue age or Priority values

#### Scenario: Skip an ineligible request

- **WHEN** the highest queued request is invalid or cannot yet resume and a lower request is eligible under current admission controls
- **THEN** the factory selects the lower eligible request

#### Scenario: Reorder while an evaluation is running

- **WHEN** a user changes queue order during active execution
- **THEN** the active evaluation continues and the new order governs subsequent selection

### Requirement: Freeze accepted evaluation inputs

A new claim SHALL record the effective evaluation settings, including the selected eval suite, and resolve the requested Runner and Skills refs and the configured `agent-evals` harness branch to immutable commits from the remote at admission. `agent-evals` is the evaluation harness and may contain multiple suites; `and-scene` is the default suite. Those accepted inputs SHALL remain fixed for the claim, including its repetitions and automatic recovery. Later changes to branches, defaults, or the issue SHALL NOT mutate an existing claim's frozen inputs. Configuration SHALL name the harness branch, not a commit.

#### Scenario: Continue after refs or defaults change

- **WHEN** an unfinished claim resumes after its requested branches, the harness branch, or configured defaults have changed
- **THEN** it uses the same accepted settings and immutable revisions
- **AND** its completed repetitions remain completed

#### Scenario: Admit two evals on different days

- **WHEN** the harness branch advances between two admissions
- **THEN** each claim records the harness commit it resolved at its own admission and the difference is visible in its Refs and results

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

