## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration, end-to-end, agent-acceptance, and exceptional human-only obligations.

The plan follows the test pyramid. Unit tests (written test-first during implementation) cover
isolated logic: backend resolution for every hint shape, the adoption decision table, tier
classification rules, `resume_from` computation, the resume-skip ordering, configuration parsing
and rejection, per-kind definitions, retention target selection, eligibility and gesture rules,
and outcome validation per contract. Integration tests cover real boundaries: processes, git
repositories, packaged scripts, the installed Runner, and configuration-to-runtime wiring. The
end-to-end layer is deliberately thin: one happy-path feature journey through the CLI. Every
other journey (stops, resumes, continuations, restarts, review rounds, failures) is proved at
the integration layer. Agent acceptance exercises the real workflow with real models.

The existing suites are regression gates for parts A and B and must pass unchanged in intent
(fixture edits for moved module paths and renamed classes are expected):
`tests/e2e/test_fix_cycle.py`, `tests/e2e/test_factory_cycle.py`,
`tests/e2e/test_independent_execution.py`, `tests/integration/test_supervision_hardening.py`,
`tests/integration/test_supervision_recovery.py`, `tests/integration/test_fly_*.py`,
`tests/integration/test_fix_*.py`, `tests/integration/test_host_launch.py`, and
`tests/integration/test_post_run_audit.py`.

The Agent Runner prerequisite (`core/verify-change`) carries its own tests in its own pull
request (workflow validation, composition with `core/implement-change`, no `call_agent` and no
prompt-driven validator in the tail, acceptance-loop gate and push scripts, and loop behavior).
This plan does not repeat them; AT-001 exercises the merged builtin for real.

## Integration Tests

### INT-001: Adoption after a restart settles unsupervised host runs
- Covers: factory-execution-backends "Adopt unsupervised execution after a restart"; factory-fix-execution "Supervise a host attempt by process"
- Boundary: supervisor watcher + real child processes + SQLite store (darwin process-session semantics)
- Setup: temporary storage root; host plans whose argv is a small script that writes a kind outcome file (or `result.json`) and exits, or sleeps; watcher killed before the child exits
- Action: start a replacement watcher (`resume_supervisor`) after the child (a) exited with an outcome, (b) exited without one, (c) is still alive, (d) was replaced by an unrelated process reusing the pid
- Assertions: (a) run finishes with the outcome's status and the slot is released; (b) run is `interrupted` with the unsupervised reason and the recovery policy schedules one retry; (c) supervision continues with no new attempt, no consumed retry, and preserved elapsed time; (d) nothing is terminated and the run is held for operator attention
- Execution: `tests/integration/test_backend_adoption.py`, `@pytest.mark.darwin`

### INT-002: Adoption through the Docker backend
- Covers: factory-execution-backends "Adopt unsupervised execution after a restart" (container outlives launcher, ambiguous container)
- Boundary: supervisor + Docker backend + real Docker daemon with test-owned containers
- Setup: a test-owned container mounting the attempt's artifact path; the launcher process exits while no watcher runs; a decoy container with a different mount
- Action: start a replacement watcher; then stop the container; separately, alter the recorded container identity
- Assertions: a matching running container is adopted and supervised until it exits, then the result is recorded; a container that no longer matches is neither stopped nor adopted and the run is held; the decoy is never touched
- Execution: `tests/integration/test_backend_adoption_docker.py`, `@pytest.mark.docker` (deselected by default; run with `pytest -m docker` on a Docker host)

### INT-003: Plans recorded before the deploy keep working
- Covers: factory-execution-backends "Resolve the backend of plans recorded before backends were named" and "Own every execution through a named backend"
- Boundary: SQLite run records → resolver → supervisor, runtime disposal, doctor, status
- Setup: store fixtures with every legacy plan shape (eval Docker with `sandbox=docker`; eval with only `suite=and-scene`; eval Fly; fix Docker; fix and review host) plus new plans
- Action: adopt, dispose, and report each run through the refactored code; load new plans with the previous release's hint checks
- Assertions: each legacy plan resolves to the expected backend and is supervised, disposed (Fly decision table unchanged), and shown in status as before; an unresolvable plan is held without termination and blocks only its kind; every new plan carries `backend` and its legacy `sandbox`/`suite` hints
- Execution: `tests/integration/test_backend_resolution.py`

### INT-004: Feature configuration reaches runtime and doctor
- Covers: factory-operations "Configure deployment…" and "Diagnose readiness with doctor"; factory-feature-execution "Execute features on the host only"
- Boundary: TOML loading → `LocalConfig`/`SharedConfig` → handler registration → `doctor` output
- Setup: configuration files with and without `[feature]`, with `execution = "docker"` and `"fly"`; a stub `agent-runner` on PATH with and without `core/verify-change`; fix targets with and without `openspec/`
- Action: load configuration; run `doctor`; run one `tick` against a stub board; then remove `[feature]` while one feature attempt is running and another feature claim is settled with an open pull request, and run further ticks
- Assertions: Docker or Fly for features fails loading with the unsupported mode named; without `[feature]` no feature kind is registered and bug and eval behavior is unchanged; doctor shows a `feature-host` group that fails naming `core/verify-change` when the stub Runner lacks it and passes otherwise; targets without `openspec/` are informational; other kinds' readiness is reported independently; after `[feature]` is removed, the running attempt is still supervised and its result reported, the settled claim still gets review-round admission, merge sync, and cleanup, and no new Feature card is handed off or admitted
- Execution: `tests/integration/test_feature_config.py`

### INT-005: Feature handoff, eligibility, and routing retype
- Covers: factory-feature-intake "Hand off features…" and "Select eligible features by Priority"; factory-routing "Never route features to the factory"
- Boundary: runtime Ready handoff + router + stub GitHub client
- Setup: stub board with Feature cards by writer and non-writer authors, in configured and unconfigured repositories, with Priority values; a maintainer's bug auto-routed to Ready, untouched, then retyped to Feature; a human-edited card retyped to Feature
- Action: route events and run handoff and admission for one cycle
- Assertions: only writer-authored Features in fix targets get `Owner=factory`; non-writers get one explanation and no ownership; admission ranks by Priority then newest created; the retyped untouched card returns to Backlog and is not admitted; the human-edited card keeps its values; a Feature card never receives `Owner=factory` from routing
- Execution: `tests/integration/test_feature_intake.py`

### INT-006: Stops, resumes, continuations, and review rounds at admission
- Covers: factory-feature-intake "Recognize feature retry gestures" and "Reconcile feature side effects before launching"; factory-feature-execution "Resume and continue feature work"; factory-pull-request-lifecycle "Re-admit a review round through the claim's kind slot"; factory-feature-reporting "Map feature outcomes to the board"
- Boundary: controller + pull-request handler + blocked/review processing + stub GitHub, with stubbed launch capture (no workflow execution)
- Setup: feature claims whose recorded outcomes are `needs-input` with `stopped_step=design`, `needs-input` with `stopped_step=preflight`, `failed` after the plan checkpoint, `failed` after CI with its pull request left open, a technical failure whose pushed branch (in a real bare remote) carries a `Factory-Checkpoint: archived` trailer, a technical failure whose branch carries only `planned`, and `pull-request` with an open pull request; a fix attempt holding the fix slot
- Action: post a writer comment, a non-writer comment, and a factory comment; drag cards Running→Ready and Review→Ready; add a writer pull-request comment; run cycles
- Assertions: the blocked claim resumes on the same claim and branch with `resume_from=design` and the writer comment in its input; non-writer and factory comments start nothing; the failed claim yields a new claim with re-resolved commits, `prior_branch` set, and `resume_from=implement`; the preflight stop is re-admitted as a fresh definition; the recovery of the `archived` branch launches with `resume_from=verify`, the prior attempt's session reports copied into the new session directory, and no artifact reconciliation; the recovery of the `planned`-only branch launches with `resume_from=implement`; dragging the failed claim whose pull request is open to Ready creates no claim, restores Review, and posts one explanation; the review round is admitted through the feature slot under feature limits ahead of a new Ready feature while the fix slot is busy; a stopped feature stays in Running with `needs-input` and releases the feature slot; an open non-draft factory pull request settles a claim as handed off, while the claim's own draft is treated as the resume point
- Execution: `tests/integration/test_feature_gestures.py`

### INT-007: Feature workflow scripts against real git repositories
- Covers: factory-feature-execution "Define the change autonomously" (checkpoint pushes), "Stop definition for a direction-level decision", "Resume and continue feature work"; factory-feature-reporting "Annotate the feature pull request by review attention"
- Boundary: packaged scripts (`prepare-branch.sh`, `factory-resume-skip.sh`, `record-stop.sh`, `annotate-pr.sh`) + real git repositories with bare remotes + stub `gh`
- Setup: target repository with `openspec/`; a claim branch with drafted artifacts; a prior branch with a committed plan; a prior branch that conflicts with the recorded target commit; `review-attention.json` fixtures including empty tiers
- Action: run each script with representative inputs; interrupt the checkpoint sequence before and after each push; run annotation after adding a commit past the accepted head
- Assertions: `prepare-branch.sh` creates, resumes, continues (merging the recorded commit), and falls back to fresh on a missing branch or conflict, recording the fallback in `resume.json`; `factory-resume-skip.sh` skips exactly the steps before `resume_from` and nothing when it is empty; each checkpoint commit carries its `Factory-Checkpoint` trailer and an interruption before a push leaves the previous checkpoint as the newest on the remote; `record-stop.sh` commits drafts, pushes, and writes a `needs-input` outcome with `stopped_step`, questions, direction summary, and branch; `annotate-pr.sh` renders "Review first" with red, orange, and yellow in order, states empty tiers, collapses the ledger and white evidence, lists commits after the accepted head as an orange item and rewrites `review-attention.json` with counts that match the rendered description, and includes `Refs #N` and the claim marker without a closing keyword
- Execution: `tests/integration/test_feature_workflow_scripts.py`

### INT-008: Shared outcome and contract scripts keep fix behavior
- Covers: factory-fix-execution "Invoke the versioned fix workflow" (unchanged `factory-fix/1` contract)
- Boundary: parameterized `record-outcome.sh` and `check-contract.sh` as invoked by `factory-fix-v1.0.yaml` and `factory-feature-v1.0.yaml`
- Setup: the fix workflow's existing script inputs; feature inputs with tier counts
- Action: run both scripts for each contract
- Assertions: fix outcomes are byte-for-byte equivalent to the previous scripts' output for the same inputs; feature outcomes carry the feature contract and extra fields; a contract mismatch fails the check
- Execution: extend `tests/integration/test_fix_workflow.py`; add feature cases in `tests/integration/test_feature_workflow_scripts.py`

### INT-009: Packaged feature workflows against the installed Runner
- Covers: factory-feature-execution "Invoke the versioned feature workflow"; design D4 (pre-populated session directory)
- Boundary: staged workflow catalog + the installed `agent-runner` (with `core/verify-change`)
- Setup: a temporary clone with the packaged workflows staged as the host plan stages them; gated like `tests/e2e/test_host_fix_launch.py` on an installed Runner
- Action: `agent-runner -validate` each packaged workflow; statically inspect `factory-feature` and `factory-define`; run a model-free stand-in workflow with a `--session-dir` whose `output/` already holds session-report files
- Assertions: every packaged workflow validates; every resumable step carries the resume `skip_if`; every step after a possible stop carries the stop `skip_if`; no step declares `tools: [call_agent]`; no prompt invokes Agent Validator or a validator-running skill; the Runner accepts the pre-populated session directory and preserves the files (if it does not, the test fails and the design's fallback to `resume_from=implement` must be implemented and asserted instead)
- Execution: `tests/integration/test_feature_workflow_catalog.py`, `@pytest.mark.darwin`, skipped with an explicit reason when no suitable Runner is installed

### INT-010: Retention for feature claims
- Covers: factory-operations "Retain evidence for a bounded period"; design B7
- Boundary: retention sweep + store + evidence directories
- Setup: a Done feature claim past the retention period with attempt evidence, a post-run audit directory, and an outcome file; a feature claim with a pending merge sync
- Action: run the retention sweep
- Assertions: the feature claim is pruned with the pull-request rules (logs, session state, audit directory, agent output removed; `feature-outcome.json` and `input/` kept); the claim with a pending sync is not pruned
- Execution: extend `tests/integration/test_retention.py`

## End-to-End Tests

### E2E-001: A feature goes from Ready to a merged, cleaned-up pull request
- Covers: the feature happy path across factory-feature-intake, factory-feature-execution, factory-feature-reporting, and factory-pull-request-lifecycle
- Surface: the `agent-factory` CLI (`tick`, `status`) with the resident's normal cycle
- Setup: the `tests/e2e/test_fix_cycle.py` harness (stub `gh`, stub board, stub host `agent-runner` that records its argv and writes `feature-outcome.json` = `pull-request` with tier counts and a pull request reference); configuration with `[feature]`; one writer-authored Feature card in Ready in a fix target
- Journey: tick → handoff and admission → host launch → attempt completes → tick → merge the stub pull request → tick → move card to Done → tick
- Assertions: `Owner=factory` set; the attempt runs in the feature slot with every packaged workflow staged in the clone and the feature params passed; the card moves to Review with `pending-human-review` and one comment states the flag counts; status shows the feature slot; after merge the working clone is synced and the issue closed; after Done the clones and credential copy are removed and evidence remains
- Execution: `tests/e2e/test_feature_cycle.py`

## Agent Acceptance Tests

Real feature runs execute the packaged `factory-feature` workflow directly with the installed
Agent Runner in a disposable scratch repository initialized with OpenSpec, with real models and
a real GitHub pull request, without the factory service or the live Project board. Board and
service behavior is covered by INT-004 to INT-006 and E2E-001.

### AT-001: A well-described feature becomes a finalized pull request
- Classification: Required
- Covers: factory-feature-execution definition, implementation, archive, verification (through the merged `core/verify-change`), classification, finalization; factory-feature-reporting annotation
- Actor and surface: an operator running `agent-runner run factory-feature --profile factory` on the host, and the resulting GitHub pull request
- Setup: a new private scratch repository with a tiny program, a test command, `openspec/` initialized, and CI that runs the tests; the factory profile and staged workflows as a host plan would stage them; a small, unambiguous feature issue file (for example, add a `--version` flag with a specified output)
- Steps: run the workflow; inspect the branch, the archived change, `decisions.md`, `feature-outcome.json`, `review-attention.json`, and the pull request
- Expected: the outcome is `pull-request`; the branch holds the archived OpenSpec change with specs applied, `decisions.md`, and the implementation with tests; the pull request is ready (not draft), references the issue without a closing keyword, carries the claim marker, and opens with "Review first" showing red, orange, and yellow (empty tiers stated); acceptance evidence names the accepted commit
- Evidence: the pull request URL and description, `feature-outcome.json`, `review-attention.json`, and the Runner session's output file list
- Effects and cleanup: creates a scratch repository, branches, and one pull request, and consumes model time (about an hour); delete the scratch repository after HT-001
- Permitted substitutes: None

### AT-002: An ambiguous feature stops during definition and resumes
- Classification: Required
- Covers: factory-feature-execution "Stop definition for a direction-level decision" and "Resume and continue feature work"; factory-feature-reporting stop content
- Actor and surface: the same operator and scratch repository as AT-001
- Setup: an issue file whose request has two materially different readings
- Steps: run the workflow; inspect the outcome and the pushed branch; add an answering comment to the issue file; rerun with `resume_from` set to the recorded `stopped_step` on the same branch
- Expected: the first run returns `needs-input` naming both readings, with drafted artifacts committed and the branch pushed and no pull request; the rerun skips completed steps, revises drafted artifacts to match the answer (recorded in `decisions.md`), and continues past the stopped step
- Evidence: both outcome files, the branch history showing the stop commit and the resumed commits, and `decisions.md`
- Effects and cleanup: as AT-001; the rerun may be stopped after definition completes to limit cost
- Permitted substitutes: None

### AT-004: A review round on a feature pull request changes specified behavior
- Classification: Required
- Covers: factory-review-execution "Change specified behavior on a feature pull request" and "Implement, verify, and push on the existing branch"; factory-feature-reporting "Complete a review round on a feature"
- Actor and surface: a reviewer commenting on the AT-001 pull request, and an operator running `agent-runner run factory-review --profile factory` with the review input the factory would write
- Setup: the open AT-001 pull request; a writer review comment asking for a behavior change that contradicts one of the change's specified scenarios
- Steps: run the review workflow; inspect the pull request branch, `openspec/specs/`, the replies, and `review-outcome.json`
- Expected: the code and the affected living specification under `openspec/specs/` are both updated on the existing branch; the thread is answered and resolved; the outcome is `pull-request`; acceptance is not re-run and the acceptance evidence still names its original commit
- Evidence: the review commits, the specification diff, the thread reply, and `review-outcome.json`
- Effects and cleanup: adds commits and replies to the scratch pull request; cleaned up with the scratch repository
- Permitted substitutes: None

### AT-005: Incomplete acceptance still yields a finalized pull request with red flags
- Classification: Required
- Covers: factory-feature-execution "Classify review attention without blocking" and "Finalize the feature pull request"; factory-feature-reporting "Annotate the feature pull request by review attention"
- Actor and surface: the same operator and scratch repository as AT-001
- Setup: a small feature issue whose expected test plan includes a criterion the tester cannot verify in this environment (for example, behavior that depends on an external service with no credentials available)
- Steps: run the workflow; inspect the outcome, `review-attention.json`, and the pull request
- Expected: acceptance ends `ACCEPTANCE_FAILED` after its bounded rounds; the pull request is nevertheless marked ready; "Review first" lists the unverified criterion and the incomplete acceptance as red above the orange and yellow items; the outcome is `pull-request` with a red count of at least two
- Evidence: `acceptance-preparation-status.txt`, `review-attention.json`, `feature-outcome.json`, and the pull request description
- Effects and cleanup: as AT-001
- Permitted substitutes: None

### AT-003: Operator surface with features configured
- Classification: Required
- Covers: factory-operations doctor and status for the feature kind
- Actor and surface: an operator using `agent-factory doctor` and `agent-factory status`
- Setup: an isolated storage root and a copy of the configuration with `[feature]`; never the live service's configuration or database
- Steps: run `doctor`; run `status`
- Expected: doctor shows a `feature-host` group with the installed Runner's `core/verify-change` check passing and targets without `openspec/` listed as informational; status lists a feature slot
- Evidence: captured command output
- Effects and cleanup: none beyond the temporary storage root, which is removed
- Permitted substitutes: None

## Human-Only Testing

### HT-001: The "Review first" section lets the reviewer find what matters
- Reason: whether the tiered summary is useful enough that the reviewer need not read the full ledger is the reviewer's subjective judgment
- Prerequisites: AT-001 passed and its pull request is available
- Instructions: open the AT-001 pull request and read only the "Review first" section; then skim the collapsed ledger
- Required decision or observation: whether each red and orange item was worth attention, whether anything in the ledger should have been red or orange, and whether the section is short enough to read first

## Coverage Map

| Requirement or journey | INT | E2E | AT | HT |
| --- | --- | --- | --- | --- |
| Adopt unsupervised execution after a restart | INT-001, INT-002 | — | — | — |
| Resolve legacy plans / named backends | INT-003 | — | — | — |
| Feature configuration, host only, doctor, disabling with claims in flight | INT-004 | — | AT-003 | — |
| Feature handoff, Priority selection, routing retype | INT-005 | E2E-001 | — | — |
| Retry gestures, resume, continuation, reconciliation, preflight, failed with open pull request | INT-006, INT-007 | — | AT-002 | — |
| Checkpoints on the branch and recovery | INT-006, INT-007 | — | — | — |
| Review round through the feature slot | INT-006 | — | — | — |
| Review round changes specified behavior | — | — | AT-004 | — |
| Define autonomously, checkpoints, stops | INT-007, INT-009 | — | AT-001, AT-002 | — |
| Invoke the versioned feature workflow | INT-009 | E2E-001 | AT-001 | — |
| Archive, verify, classify, finalize | — | — | AT-001, AT-005 | — |
| Incomplete acceptance finalized with red flags | — | — | AT-005 | — |
| Annotate the pull request by review attention, later commits | INT-007 | E2E-001 | AT-001, AT-005 | HT-001 |
| Map feature outcomes to the board | INT-006 | E2E-001 | — | — |
| Merge sync and cleanup for feature claims | — | E2E-001 | — | — |
| Shared scripts keep fix behavior | INT-008 | — | — | — |
| Retention for feature claims | INT-010 | — | — | — |
