# Task: Package the autonomous `factory-feature` and `factory-define` workflows

## Goal

Ship, in the factory package, the Runner workflows and scripts that turn a feature issue
into a finalized pull request carrying an archived OpenSpec change: autonomous definition
with crosscheck reviews and direction-level stops, single-task implementation, archival,
verification through Runner's `core/verify-change`, review-attention classification, pull
request finalization and annotation, and exactly one structured outcome. The workflows
resume from a `resume_from` step and a `prior_branch` supplied by the factory, and push
checkpoint commits so the branch alone records the last completed phase. Also teach the
shared review workflow what a behavior change on a feature pull request means.

## Background

Read `openspec/changes/factory-feature-support/design.md`, section "D. The feature
workflows" (the step outline and D1 to D7) and Decisions 4 to 8 and 10; the proposal's
"What Changes" part 3 describes the workflow in prose.

Where things are (paths relative to `src/agent_factory/`):

- Packaged workflows live in `work_kinds/pull_request/workflow/` beside
  `factory-fix-v1.0.yaml`, `factory-review-v1.0.yaml`, `factory-implement-v1.0.yaml`, and
  the shared scripts `record-outcome.sh` and `check-contract.sh`, which take the contract
  and outcome path as inputs and accept optional extra fields (`stopped_step`,
  `review_attention_counts`, `resume`) that `work_kinds/pull_request/outcome.py` validates
  per contract. `work_kinds/pull_request/kinds.py` defines `PullRequestKind` (its
  `staged_files` lists a kind's workflows, rules, and scripts) and the `FIX` instance.
  Staging copies every registered kind's `staged_files` plus the review and implementation
  workflows into the clone's `.agent-runner/workflows/`, where the Runner resolves workflows
  by name and scripts by relative path.
- `factory-fix-v1.0.yaml` shows the house patterns: the header comment
  `# factory-contract: <contract>`, `hidden: true`, `artifact_dir` param, builtins
  referenced as `workflow: builtin:core/<file>-v1.0.yaml`, `skip_if: 'sh: ...'` gates, a
  declined triage that ends the run successfully by skipping later steps, and recording a
  failure after a failed sub-step (`mark-ci-failed` with `skip_if: previous_success`).
- Runner facts: sub-workflows share `{{session_dir}}` and named sessions, receive only the
  params passed to them, and return results only through files. `skip_if: 'sh: <cmd>'`
  skips when the command exits 0, with params interpolated shell-safely. There is no
  start-from-step flag and `-resume` cannot be combined with `--session-dir`. The autonomy
  preamble is prepended only when a session starts, not when it resumes. The factory
  profile set defines `lead`, `implementor`, `tester`, and (with this change) `crosscheck`.
- Runner builtins referenced: `core/check-planning-artifacts`, `core/commit-change-plan`,
  `core/implement-task`, `core/verify-change` (params `change_name`, `change_dir`,
  `change_label`, `artifact_validation_instruction`, `skip_validator`,
  `acceptance_rounds`; writes `acceptance-assumptions.md`,
  `acceptance-preparation-status.txt`, acceptance evidence, and opens the draft pull
  request), `openspec/archive-change`, and `core/finalize-pr`. `core/verify-change` is
  provided by an Agent Runner change landing separately; build a Runner from a branch that
  contains it to run the catalog test locally.
- Skills used by prompts: `codagent:propose`, `codagent:spec`, `codagent:design`,
  `codagent:test-plan` (these contain "get user approval" gates and use
  `codagent:ask-questions`, whose headless rule is to report the unresolved decision or
  follow the caller's fallback), `codagent:proposal-review`, `codagent:review-approach`,
  `codagent:prepare-acceptance`, `codagent:session-report`.

Workflow outline to implement (from design D):

```
factory-feature-v1.0.yaml                          (# factory-contract: factory-feature/1)
  params: issue_file, branch_name, change_name, contract_version, artifact_dir,
          resume_from ("" | a define step id | implement | archive | verify | finalize),
          prior_branch ("" or the prior claim's branch)
  sessions: lead-agent (lead), proposal-reviewer (crosscheck), approach-reviewer (crosscheck)
  check-contract              script check-contract.sh           (shared with factory-fix)
  check-openspec              script → needs-input outcome when openspec/ is absent
  prepare-branch              script prepare-branch.sh           fresh | resume | continue | fallback
  create-change               script (skip when the change directory exists)
  reconcile-artifacts         lead; skip unless resume_from or prior_branch is set
  define ─► factory-define-v1.0.yaml   (group skip when resume_from is past define)
  implement ─► builtin:core/implement-task   task_file=<change>/tasks.md, run_session_report=true
  complete-task               script: tick tasks.md, commit, push (checkpoint "implemented")
  archive ─► builtin:openspec/archive-change (skip when the change directory is gone)
  push-archive                script: push (checkpoint "archived")
  verify ─► builtin:core/verify-change       change_dir=<archived dir>,
                                             artifact_validation_instruction="run `openspec validate --specs --strict`"
  classify                    lead → {{artifact_dir}}/review-attention.json
  finalize ─► builtin:core/finalize-pr
  annotate-pr                 script annotate-pr.sh (description + flag counts)
  record-outcome              script record-outcome.sh           (shared, parameterized)
  verify-outcome              command                            (shared shape)

factory-define-v1.0.yaml
  proposal → proposal-review (proposal-reviewer) → apply-proposal-review (lead)
  → specs → design → test-plan → approach-review (approach-reviewer)
  → apply-approach-review (lead) → write-tasks → validate-openspec (script + repair)
  → check-planning-artifacts ─► builtin:core/check-planning-artifacts
        (required_files: proposal.md,design.md,test-plan.md,tasks.md; require_specs: true)
  → commit-plan ─► builtin:core/commit-change-plan → push-plan (checkpoint "planned")
```

Decisions to implement:

- **Autonomy rules (D1).** Stage `factory-define-rules.md` beside the workflows. Every lead
  prompt in both workflows says "follow `.agent-runner/workflows/factory-define-rules.md`".
  The rules file states: no approval gates (write the artifact to the named path); the
  decide-or-stop rule and the four direction-level triggers (contradicts the issue; the
  issue admits materially different readings; breaks a public interface or persisted data
  format; requires a decision or change outside the target repository); how to record a
  decision (append to `<change>/decisions.md` with the step, the decision, the
  alternatives considered, and whether it is decision-bearing); how to stop (write
  `{{artifact_dir}}/define-stop.json` with `step`, `questions`, `direction_summary`, then
  end the step); that a proposal no-go maps to a stop; and that ask-questions' headless
  rule means "stop or decide", never "wait".
- **Stops (D2).** `check-openspec` writes the `needs-input` outcome itself with
  `stopped_step: preflight` and no branch. After each define step that can stop, a
  `record-stop` script step runs with `skip_if: 'sh: test ! -s {{artifact_dir}}/define-stop.json'`;
  `record-stop.sh` commits the drafted artifacts (`[define-stop]` prefix), pushes the
  branch, and writes the `needs-input` outcome with `stopped_step`, `questions`,
  `direction_summary`, and `branch`. Every later step, including the rest of
  `factory-define`, carries `skip_if` on the same file's presence, so the run ends
  successfully after a stop.
- **Reviews (D3).** `proposal-review` and `approach-review` run in their own named reviewer
  sessions (each name used once per run) with `codagent:proposal-review` and
  `codagent:review-approach`, write `{{artifact_dir}}/<step>-findings.json` (each finding:
  id, severity, problem, recommendation), and never edit files. The following `apply-*`
  lead step records a decision on every finding in `decisions.md`: applied, rejected with a
  reason, or direction-level (which triggers a stop). A rejected finding of high or medium
  severity is classified orange.
- **Resume and checkpoints (D4).** `factory-resume-skip.sh <resume_from> <step>` exits 0
  (skip) when `<step>` precedes `resume_from` in the fixed order `proposal,
  proposal-review, specs, design, test-plan, approach-review, write-tasks, implement,
  archive, verify, finalize`, and exits non-zero when `resume_from` is empty. Each resumable
  step carries `skip_if: 'sh: .agent-runner/workflows/factory-resume-skip.sh "{{resume_from}}" <step>'`.
  The commits ending the `planned`, `implemented`, and `archived` phases carry a
  `Factory-Checkpoint: <phase>` git trailer, and the branch is pushed immediately after
  each. `prepare-branch.sh` handles fresh (new branch from the recorded target commit),
  resume (fetch and check out the claim's own branch), continue (create the claim branch
  from `prior_branch` and merge the recorded target commit; on conflict abort and fall back
  to fresh), and missing-branch fallback, recording any fallback in
  `{{artifact_dir}}/resume.json`, which the outcome reports. `reconcile-artifacts` runs
  only when `resume_from` names a define step (a resume after a definition stop) or
  `prior_branch` is non-empty (a new claim continuing a prior claim, which resumes at
  `implement`). A technical recovery never runs it: the factory passes it an empty
  `prior_branch` and a `resume_from` of `implement` or later, because a definition-phase
  crash leaves no pushed checkpoint and restarts fresh. It compares artifacts with the current issue file and
  eligible comments, revises them where warranted, and appends each revision to
  `decisions.md` (classified orange).
- **Classification and annotation (D5).** `classify` (lead) reads `decisions.md`, the
  `*-findings.json` files, `acceptance-assumptions.md`, `acceptance-preparation-status.txt`,
  `acceptance-flow-evidence.md`, `acceptance-findings.md`, `resume.json`, and the commits
  after the accepted head, and writes `review-attention.json`:
  `{"red": [{"title", "detail", "link"}], "orange": [], "yellow": [], "white": [],
  "accepted_head": "<sha>", "later_commits": ["<sha>"]}`. A verify step checks that it
  parses and every tier is a list. `annotate-pr.sh` runs after `finalize`, recomputes
  `later_commits` from git, adds one orange item listing commits after the accepted head
  that the classification did not cover, rewrites `review-attention.json` with the final
  tiers, renders "Review first" (red, orange, yellow; each tier shown even when empty,
  visually distinct), the summary with links to the archived proposal, specifications,
  design, and test plan, the collapsed ledger and white evidence, `Refs #N` (no closing
  keyword), and the claim marker, and replaces the description with `gh pr edit`. The
  outcome carries the final tier counts (`review_attention_counts`).
- **Outcomes.** `feature-outcome.json` in `{{artifact_dir}}` declares `factory-feature/1`
  and is exactly one of `pull-request` (pull request reference and tier counts),
  `needs-input` (reasons, `stopped_step`, questions, direction summary, branch), or
  `failed` (reasons, pushed branch, and the pull request when one is open). A validator that
  stays red inside `verify` yields `failed` with the branch pushed and no pull request; CI
  that stays red after `finalize` yields `failed` with the pull request left open; OpenSpec
  validation that still fails after bounded repair yields `failed` with the errors. Extend
  `outcome.py` validation for the feature contract.
- **Shared scripts (D6).** `factory-feature-v1.0.yaml` calls the shared `check-contract.sh`
  and `record-outcome.sh` with its contract and `feature-outcome.json`.
- **Review rounds (D7).** `factory-review-v1.0.yaml` is unchanged in structure; its triage
  prompt reads the work kind from `review.json` and, for a feature, instructs that a
  requested behavior change is made and `openspec/specs/` updated to match, without
  re-running acceptance.
- Add a `FEATURE_STAGED_FILES` tuple in `work_kinds/pull_request/kinds.py` listing
  `factory-feature-v1.0.yaml`, `factory-define-v1.0.yaml`, `factory-define-rules.md`, and
  the new scripts, for the feature kind definition to use; the packaging (`pyproject.toml`
  package data, if files are enumerated) must include them.

## Spec

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Invoke the versioned feature workflow

The factory SHALL run the packaged `factory-feature` workflow, with its `factory-define` sub-workflow, through the operator's installed Agent Runner, passing the configured feature role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, the attempt's artifact directory, and the attempt's resume point when one exists. The resume point SHALL be the claim's own pushed branch, or, for a new claim continuing a settled prior claim, the prior claim's branch supplied at admission. The factory SHALL ship the feature workflows beside the fix, review, and shared implementation workflows and stage all of them into the Runner catalog the attempt uses. The workflow contract SHALL be versioned as `factory-feature/1`. The factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the installed Runner does not provide what the workflow requires, including the `core/verify-change` builtin workflow, and SHALL hold the claim and report the problem in status and doctor. The workflow SHALL write exactly one structured outcome to `feature-outcome.json` in the attempt's artifact directory, declaring its contract version: `pull-request` with the pull request reference; `needs-input` with reasons; or `failed` with reasons. Absence of a structured outcome SHALL be treated as a technical failure. A feature attempt SHALL work on a deterministic branch named from the issue and claim.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged feature workflow declares a compatible contract version and the installed Runner provides `core/verify-change`
- **THEN** the attempt starts under its own supervisor on the host with the configured feature roles
- **AND** the staged workflow directory holds the feature, define, fix, review, and implementation workflow files

#### Scenario: Launch against a Runner without verify-change

- **WHEN** the installed Runner lacks the `core/verify-change` builtin workflow
- **THEN** no attempt is recorded, the feature is held, and status and doctor name the missing workflow

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `feature-outcome.json`
- **THEN** the factory records a technical failure and applies the recovery policy

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Require an OpenSpec repository

Before defining a change, the feature workflow SHALL check that the target clone contains an `openspec/` directory. When it does not, the workflow SHALL return `needs-input` stating that the repository is not initialized for OpenSpec, with the stopped step recorded as `preflight`, and SHALL push no branch and open no pull request. An attempt re-admitted after a `preflight` stop SHALL start a fresh definition.

#### Scenario: Hand off a feature in a repository without OpenSpec

- **WHEN** a feature is admitted for a configured target that has no `openspec/` directory
- **THEN** the workflow returns `needs-input` naming the missing OpenSpec initialization with stopped step `preflight`
- **AND** it pushes no branch and opens no pull request

#### Scenario: Re-admit after OpenSpec is initialized

- **WHEN** a feature stopped at `preflight` and, after the repository was initialized for OpenSpec, a writer comments on the issue
- **THEN** the re-admitted attempt starts a fresh definition

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Define the change autonomously

The feature workflow SHALL create an OpenSpec change for the issue and define it without human interaction, in this order: proposal; an adversarial proposal review by a fresh crosscheck agent; specifications; design; test plan; an approach review of all definition artifacts together by a fresh crosscheck agent; a `tasks.md` with a single task covering the whole change; OpenSpec validation with bounded mechanical repair; the planning-artifact check requiring the proposal, specifications, design, test plan, and tasks; and a commit of the plan. There SHALL be no task planning and no task review. When a definition step reaches a decision the issue and eligible comments do not answer, the workflow SHALL decide it and record the decision as an explicit assumption in the relevant artifact, unless the decision is direction-level. A decision is direction-level when it contradicts the issue, when the issue admits materially different readings, when it would break a public interface or persisted data format, or when it requires a decision or change outside the target repository. The lead SHALL apply review findings it judges correct without human confirmation, unless a finding raises a direction-level decision. Each review SHALL run as its own workflow step in a fresh reviewer session using the configured crosscheck role, in a single pass. Every recorded decision, review-finding disposition, and plan revision SHALL be appended to a decision log inside the change directory, which is archived with the change. After the plan commit the workflow SHALL push the branch without opening a pull request. The workflow SHALL also push the branch after implementation and after archival, so that each completed phase survives a technical failure. Each checkpoint commit SHALL identify its phase (`planned`, `implemented`, or `archived`) in the commit itself, so the last completed phase is determined from the pushed branch rather than from local attempt evidence.

#### Scenario: Define a well-described feature

- **WHEN** the issue describes a feature whose open decisions are all below direction level
- **THEN** the workflow produces and commits the proposal, specifications, design, test plan, and single-task `tasks.md`, each open decision recorded as an assumption
- **AND** it pushes the branch and continues to implementation without opening a pull request

#### Scenario: Apply a review finding

- **WHEN** the approach review reports a consequential gap that does not raise a direction-level decision
- **THEN** the lead revises the affected artifacts and continues without stopping

#### Scenario: Fail validation after repair

- **WHEN** OpenSpec validation still fails after its bounded mechanical repair
- **THEN** the workflow returns `failed` with the validation errors

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Stop definition for a direction-level decision

Any definition step, from the proposal through the approach review, SHALL stop the run with `needs-input` when it reaches a direction-level decision. The proposal step SHALL also stop the run with `needs-input` when it judges the feature should not be built as described, stating why. On a stop the workflow SHALL commit the artifacts drafted so far, push the branch, and open no pull request. The outcome SHALL name the specific questions or objections, summarize the direction drafted so far, and identify the pushed branch.

#### Scenario: Stop at the proposal

- **WHEN** the proposal step finds that the issue admits two materially different features
- **THEN** the workflow commits the draft proposal, pushes the branch, and returns `needs-input` naming both readings and what must be decided
- **AND** it opens no pull request

#### Scenario: Stop during design

- **WHEN** the design step finds that the specified behavior requires breaking a public interface
- **THEN** the workflow commits the proposal, specifications, and draft design, pushes the branch, and returns `needs-input` naming the interface and the decision required

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Resume and continue feature work

A resumed attempt on a blocked claim SHALL check out the claim's branch and continue at the definition step that stopped. A new claim supplied with a prior claim's branch SHALL start its branch from the prior branch, merge in the target's recorded commit, and continue at implementation with the prior committed plan. An attempt resumed after a definition stop, or a new claim continuing a prior claim, SHALL first check the existing artifacts against the current issue and the eligible comments and revise them where that input warrants, recording each revision; because such an attempt resumes at or before implementation, every revision is implemented, archived, and verified. A technical recovery retry SHALL NOT revise existing artifacts. When the resume point is missing or cannot be used, including an unresolvable merge, the workflow SHALL start a fresh definition and state in its outcome that it did so. A technical failure SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation, which continues after the last completed phase found on the claim's pushed branch, and from its draft pull request when one is open; a phase whose checkpoint commit was not pushed SHALL be redone. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

#### Scenario: Resume after answering a definition question

- **WHEN** a claim stopped during design and a writer answers the question on the issue
- **THEN** the resumed attempt checks out the claim's branch, revises the proposal or specifications where the answer conflicts with them, and continues at design

#### Scenario: Continue a failed feature on a new claim

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after its plan commit
- **THEN** the workflow merges the target's recorded commit into that branch and starts at implementation with the prior plan
- **AND** it revises the plan first when the current issue or eligible comments warrant it

#### Scenario: Continue when the prior branch is gone

- **WHEN** a new claim is supplied with a prior branch that no longer exists
- **THEN** the workflow starts a fresh definition and states in its outcome that the prior branch was unavailable

#### Scenario: Recover after verification began

- **WHEN** an attempt fails technically after pushing its `archived` checkpoint
- **THEN** the recovery retry continues at verification from the pushed branch and does not revise the plan or its artifacts

#### Scenario: Crash before a checkpoint is pushed

- **WHEN** an attempt fails technically after committing its implementation but before pushing the `implemented` checkpoint
- **THEN** the recovery retry finds `planned` as the last pushed phase and redoes implementation

#### Scenario: Recover after a crash during implementation

- **WHEN** an attempt fails technically after pushing its plan commit
- **THEN** the recovery retry starts from fresh clones, checks out the pushed branch, and continues at implementation

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Implement the change as one task

After the plan commit the feature workflow SHALL implement the whole change as a single task through the Runner's `core/implement-task` builtin workflow with a session report, following the repository's conventions for tests.

#### Scenario: Implement the planned change

- **WHEN** the plan is committed
- **THEN** the workflow implements the single task with tests and records the implementation session report under the Runner session directory

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Archive the change before verification

After implementation the feature workflow SHALL archive the OpenSpec change, applying its specification deltas to the repository's specifications, and commit the result before any verification, draft pull request, or acceptance runs, so that verification and acceptance evidence describe the tree the pull request carries.

#### Scenario: Verify against the archived tree

- **WHEN** implementation completes
- **THEN** the change is archived and committed
- **AND** assumption review, the validator, and acceptance run against the archived tree

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Verify the change and open a draft pull request

After archiving, the feature workflow SHALL run the Runner's `core/verify-change` builtin workflow given the archived change directory: assumption review, simplify, the validator, the clean-tree check, a draft pull request, and acceptance preparation against the test plan. A validator that remains red after its bounded repair SHALL return `failed` with reasons; the branch SHALL be pushed and no pull request opened.

#### Scenario: Open a draft pull request

- **WHEN** the validator passes after simplify
- **THEN** the workflow pushes the branch, opens a draft pull request, and prepares acceptance evidence against the test plan

#### Scenario: Stay red after validator repair

- **WHEN** the validator remains red after its bounded repair
- **THEN** the workflow pushes the branch, opens no pull request, and returns `failed` with the failing checks

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Classify review attention without blocking

After the plan commit the feature workflow SHALL NOT stop for human input. Whether or not acceptance completed, and whether or not assumption review left decision-bearing assumptions, the workflow SHALL continue to finalization. Before finalizing, it SHALL classify every item a reviewer may need to examine into exactly one tier:

- red: an acceptance criterion that failed or could not be verified, acceptance that did not complete, any known deviation from the specifications, and a resume or continuation that fell back to a fresh start;
- orange: decision-bearing assumptions, whose alternative a reasonable reviewer might choose and which shape behavior; plan revisions made in response to human comments; and commits added after acceptance ran, including those the finalization loop adds after classification, which acceptance evidence does not cover;
- yellow: every other recorded assumption;
- white: acceptance criteria that passed, with their evidence.

The classification SHALL be recorded in the attempt's evidence and used by `factory-feature-reporting`. After finalization, the workflow SHALL add an orange item for any commits made after acceptance that the classification did not cover, and the tier counts in the outcome SHALL be those of the final classification.

#### Scenario: Acceptance does not converge

- **WHEN** acceptance preparation ends without completing
- **THEN** the workflow continues to finalization and classifies the unmet criteria and the incomplete acceptance as red

#### Scenario: A decision-bearing assumption remains

- **WHEN** assumption review leaves a decision-bearing assumption unresolved
- **THEN** the workflow continues to finalization and classifies the assumption as orange

#### Scenario: Everything passes

- **WHEN** every acceptance criterion passes and no assumption is decision-bearing
- **THEN** the red and orange tiers are empty, the assumptions are yellow, and the criteria are white

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Finalize the feature pull request

After classification, the feature workflow SHALL reuse the Runner's generic finalization workflow to mark the pull request ready, wait for CI, and address failures within its bounded loop. CI that remains red after the loop SHALL return `failed` with reasons while leaving the pull request open. The pull request SHALL reference the issue without a closing keyword and SHALL identify the factory claim in a stable marker. The workflow SHALL return `pull-request` with the pull request reference when CI passes.

#### Scenario: Finalize a passing pull request

- **WHEN** classification completes and CI passes
- **THEN** the pull request is ready for review and the workflow returns `pull-request`, whatever the red and orange tiers contain

#### Scenario: Fail CI after the bounded loop

- **WHEN** CI remains red after the finalization loop
- **THEN** the workflow returns `failed` with the failing checks and leaves the pull request open

_From `specs/factory-feature-reporting/spec.md` (ADDED Requirements)_

### Requirement: Annotate the feature pull request by review attention

The feature pull request's description SHALL be maintained in place rather than as comments and SHALL open with a "Review first" section that lists every red item, then every orange item, then every yellow item, one line each, from the classification defined by `factory-feature-execution`, each linking to its detail. Red, orange, and yellow SHALL be visually distinct and SHALL each be shown even when empty, stating that the tier has no items. Below that section the description SHALL carry the issue reference without a closing keyword, the claim marker, a short summary of the change with links to the archived proposal, specifications, design, and test plan, the full assumptions ledger with each assumption's resolution from assumption review, and the acceptance evidence, with white items and the full ledger collapsed by default. The acceptance evidence SHALL name the commit acceptance ran against and, when later commits from the finalization loop exist, SHALL list them. A review round SHALL NOT re-run acceptance; the evidence SHALL keep naming the commit it describes, and the round's completion comment SHALL state that acceptance was not re-run.

#### Scenario: Review a pull request with red flags

- **WHEN** a feature attempt returns `pull-request` after an acceptance criterion could not be verified
- **THEN** the "Review first" section lists that criterion as red above every orange and yellow item

#### Scenario: Review a clean pull request

- **WHEN** a feature attempt returns `pull-request` with every criterion passed and no decision-bearing assumption
- **THEN** the "Review first" section states that the red and orange tiers are empty and lists the yellow assumptions
- **AND** the passed criteria and their evidence are collapsed below it

#### Scenario: Repair CI after acceptance

- **WHEN** the finalization loop pushes commits to fix CI after acceptance ran
- **THEN** those commits appear as an orange item and the evidence names the accepted commit and lists the later commits
- **AND** the orange count in the description and in the issue comment includes that item

#### Scenario: Complete a review round on a feature

- **WHEN** a review round on a feature pull request completes
- **THEN** the acceptance evidence still names the commit it describes and the completion comment states that acceptance was not re-run

_From `specs/factory-review-execution/spec.md` (MODIFIED Requirements)_

### Requirement: Triage each comment and decide autonomously

The review workflow SHALL read `review.json` and produce one decision per eligible comment or thread: `change` with a concrete plan when the comment asks for a code change the agent should make, or `answer` with the reply text when a reply suffices. When a comment is ambiguous, the agent SHALL decide itself which applies and explain its reading in the reply. It SHALL return `needs-input` only when reviewer requests conflict with each other, when a requested change on a fix pull request requires a non-trivial specification change, when a requested change requires changes outside the target repository, or when a genuinely open product decision must be made by a human; it SHALL name what needs deciding. A requested change that widens the original issue's scope SHALL still be made. On a feature pull request, a requested change that alters specified behavior SHALL be made rather than declined.

#### Scenario: Requested change

- **WHEN** an inline comment asks to rename a function and handle an edge case
- **THEN** triage records a `change` with a plan for both, and the implementation step carries them out

#### Scenario: Question only

- **WHEN** a comment asks why an approach was chosen
- **THEN** triage records an `answer` and no code change is made for that comment

#### Scenario: Ambiguous remark

- **WHEN** a comment says "this looks fragile" without asking for anything
- **THEN** the agent decides whether to change the code or explain, and the reply states which it chose and why

#### Scenario: Conflicting requests

- **WHEN** two writers ask for incompatible changes to the same behaviour
- **THEN** the workflow returns `needs-input` naming both requests and what must be decided, and pushes nothing

#### Scenario: Change specified behavior on a feature pull request

- **WHEN** a writer asks on a feature pull request for behavior that differs from the change's specifications
- **THEN** triage records a `change` and the implementation updates both the code and the repository's specifications

_From `specs/factory-review-execution/spec.md` (MODIFIED Requirements)_

### Requirement: Implement, verify, and push on the existing branch

When any decision is `change`, the workflow SHALL implement the changes on the existing PR branch through the shared implementation sub-workflow: implement with tests following repository conventions, run the validator with its repair cycles, exercise the changed flows, repair regressions once with a verification-only validator pass, push to the existing branch, and wait for CI with one fix cycle. It SHALL NOT create a branch or a pull request. On a feature pull request it SHALL keep the repository's specifications under `openspec/specs/` consistent with the changed behavior, and SHALL NOT re-run acceptance. A validator that remains red SHALL return `failed` before any push; CI that remains red after the loop SHALL return `failed` while leaving the PR open. When no decision is `change`, the implementation sub-workflow SHALL be skipped and the outcome SHALL be `pull-request` after the replies are posted.

#### Scenario: Change pushed and green

- **WHEN** the implementation passes the validator and CI
- **THEN** the PR branch has new commits, the PR is unchanged in identity, and the outcome is `pull-request`

#### Scenario: Validator stays red

- **WHEN** the validator fails after its repair cycles
- **THEN** nothing is pushed, the reply on each `change` thread says so, that thread stays unresolved, and the outcome is `failed`


Portion for this task: the workflow side of each requirement — the workflow files, scripts,
checkpoint commits and pushes, outcome contents, and pull request description. Selecting a
resume point at admission, launching attempts, holding claims, doctor and status reporting,
and issue comments are the factory's side and are not implemented here. In "Invoke the
versioned feature workflow", this task provides the packaged files, their contract header,
the outcome, and the deterministic branch name taken from the `branch_name` param.

## Test Plan

- `INT-007` — feature workflow scripts against real git repositories. Boundary: packaged
  `prepare-branch.sh`, `factory-resume-skip.sh`, `record-stop.sh`, `annotate-pr.sh` with
  real repositories, bare remotes, and a stub `gh`. Setup: a target repository with
  `openspec/`; a claim branch with drafted artifacts; a prior branch with a committed plan;
  a prior branch that conflicts with the recorded target commit; `review-attention.json`
  fixtures including empty tiers. Action: run each script with representative inputs;
  interrupt the checkpoint sequence before and after each push; run annotation after adding
  a commit past the accepted head. Assertions: `prepare-branch.sh` creates, resumes,
  continues (merging the recorded commit), and falls back to fresh on a missing branch or
  conflict, recording the fallback in `resume.json`; `factory-resume-skip.sh` skips exactly
  the steps before `resume_from` and nothing when it is empty; each checkpoint commit
  carries its `Factory-Checkpoint` trailer and an interruption before a push leaves the
  previous checkpoint as the newest on the remote; `record-stop.sh` commits drafts, pushes,
  and writes a `needs-input` outcome with `stopped_step`, questions, direction summary, and
  branch; `annotate-pr.sh` renders "Review first" with red, orange, and yellow in order,
  states empty tiers, collapses the ledger and white evidence, lists commits after the
  accepted head as an orange item and rewrites `review-attention.json` with counts that
  match the rendered description, and includes `Refs #N` and the claim marker without a
  closing keyword. File: `tests/integration/test_feature_workflow_scripts.py`.
- `INT-008` (feature portion) — shared `record-outcome.sh` and `check-contract.sh` as
  invoked by `factory-feature-v1.0.yaml`. Setup: feature inputs with tier counts, a stop,
  and a resume fallback. Assertions: feature outcomes carry the feature contract and the
  extra fields and pass `outcome.py` validation; a contract mismatch fails the check; the
  existing fix cases in `tests/integration/test_fix_workflow.py` still pass. File: feature
  cases in `tests/integration/test_feature_workflow_scripts.py`.
- `INT-009` — packaged feature workflows against the installed Runner. Boundary: the
  staged workflow catalog and an installed `agent-runner` that provides `core/verify-change`.
  Setup: a temporary clone whose `.agent-runner/workflows/` holds `FEATURE_STAGED_FILES`
  plus the fix, review, and implementation workflows and their scripts, copied by the test
  itself from `work_kinds/pull_request/workflow/` (no feature kind is registered for the
  factory's own staging to pick them up, so the fixture must not rely on registered-kind
  staging); gated like
  `tests/e2e/test_host_fix_launch.py` on an installed Runner. Action: `agent-runner
  -validate` each packaged workflow; statically inspect `factory-feature` and
  `factory-define`; run a model-free stand-in workflow with a `--session-dir` whose
  `output/` already holds session-report files. Assertions: every packaged workflow
  validates; every resumable step carries the resume `skip_if`; every step after a possible
  stop carries the stop `skip_if`; no step declares `tools: [call_agent]`; no prompt invokes
  Agent Validator or a validator-running skill; the Runner accepts the pre-populated
  session directory and preserves the files. If the Runner refuses a pre-populated session
  directory, do not change the workflows to work around it: make that test case assert the
  observed refusal, name it `test_runner_refuses_prepopulated_session_dir` with a docstring
  stating that the factory must resume at `implement` instead of `archive` or later, and
  state the refusal in your completion report. In that case INT-009 is not complete:
  the test plan requires the factory's admission fallback to `resume_from=implement` to be
  implemented and asserted instead, which this task does not implement. Report INT-009 as
  open on that condition, not as passed. File: `tests/integration/test_feature_workflow_catalog.py`, `@pytest.mark.darwin`,
  skipped with an explicit reason when no suitable Runner is installed.

## Done When

- `factory-feature-v1.0.yaml`, `factory-define-v1.0.yaml`, `factory-define-rules.md`,
  `prepare-branch.sh`, `factory-resume-skip.sh`, `record-stop.sh`, `annotate-pr.sh`, and
  any other scripts the outline needs exist in `work_kinds/pull_request/workflow/`, are
  listed in `FEATURE_STAGED_FILES`, and ship in the built package (`uv build`).
- `outcome.py` validates `factory-feature/1` outcomes; the review triage prompt carries the
  feature instruction.
- INT-007, the feature portion of INT-008, and INT-009 pass (INT-009 run against a Runner
  built with `core/verify-change`, or reported as skipped with the reason and the Runner
  commit used). The completion report states whether the Runner accepted the pre-populated
  session directory; if it refused, INT-009 is reported as open on the admission fallback
  rather than as passed, and every other INT-009 assertion passes.
- `uv run pytest`, `uv run ruff format --check . && uv run ruff check .`, and `uv run pyright`
  pass.
