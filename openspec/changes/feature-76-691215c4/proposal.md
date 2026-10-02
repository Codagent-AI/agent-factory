## Why

The factory takes two kinds of pull-request work today: Bugs, which go through the fix
workflow's triage, and Features, which go through a full OpenSpec definition. Low-risk
maintenance work fits neither. Examples are tightening lint or type-check configuration,
adding a CI job, bumping a toolchain, refactoring without changing behavior, and docs
housekeeping. Filed as a Bug, it is misdescribed: the fix workflow requires a regression test
first and its triage looks for a reproducible defect. Filed as a Feature, it pays for
proposal, specifications, design, test plan, crosscheck reviews, and an archive, none of
which a lint baseline needs. So this work either stays with a human or gets forced into the
wrong workflow.

There is a concrete backlog now: the static-quality-gate tasks filed across the stack
(Codagent-AI/agent-factory#74, Codagent-AI/agent-validator#174, Codagent-AI/agent-evals#53).
GitHub already has a native **Task** issue type, so the intent can be declared where it is
filed. Because the generalized pull-request work kind (`PullRequestKind` in
`work_kinds/pull_request/kinds.py`) already drives admission, slots, recovery, the review
loop, merge sync, cleanup, board presentation, and reporting for both existing kinds,
a third kind costs little.

The risk is scope creep: "chore" can quietly cover behavior changes. So the kind is only
worth adding with a triage gate that is strict about what a task may touch.

**Verdict: go, with caveats.** There are three caveats. First, the triage gate must
decline anything that changes runtime behavior, public interfaces, specs, data,
credentials, or release configuration, and anything too large for one PR. It must do so on
every review round as well as at intake. Second, the gate must still accept bounded,
evidence-based choices that an issue explicitly delegates, or it will turn away the very
backlog that motivates it. Third, the remaining `fix`/`feature` literals in shared code
must be handled so that Bugs and Features behave exactly as before.

### How the motivating Tasks fare under these rules

- **agent-factory#74** (Ruff rule set, vulture, jscpd, CI lint and test job) qualifies.
  "Fix, or baseline with per-file ignores (decide during the work)" and "measure before
  fixing the jscpd threshold" are bounded choices the issue delegates. The PR records the
  measured duplication and the chosen threshold: 1.5% if the repository meets it, otherwise
  the measured value as a baseline that is not loosened further. Complexity or argument
  violations that cannot be fixed without risking behavior are baselined. Adding the CI
  job is a checking job, not release configuration.
- **agent-validator#174** (jscpd, knip, Biome on tests) qualifies, with one boundary:
  knip's unused-export cleanup must not remove anything reachable from the published
  package's entry points or CLI. Such exports are configured as entries or baselined,
  never deleted. "Done when" states a rejection criterion, so the negative gate exercise
  is mandatory.
- **agent-evals#53** (Biome, `engines.node` with a target-API lint, knip, jscpd)
  qualifies if the repository already fixes the Node version, through CI's `setup-node`
  version, `.nvmrc`, or a Docker base image. Declaring `engines.node` with that value is a
  delegated, bounded choice. If no such evidence exists, choosing the supported Node
  version is a compatibility decision, and triage declines with `needs-input` naming it.
  The issue would then need an edit stating the version. None of the three belongs in a
  Feature.

## What Changes

- **New `task` pull-request work kind** next to `fix` and `feature`:
  - Admitted from the `[fix] targets` repositories when the issue's native type matches
    `[routing] task_type` (default `"Task"`), the author has write access, Owner=factory,
    Status=Ready, and there is no `needs-input` label. These are the rules fixes and
    features already use.
  - Its own slot, attempt limits, admission window, readiness checks, roles (lead,
    implementor, tester), and model defaults. It is enabled by a `[task]` section in shared
    configuration (contract `factory-task/1`, `[task.defaults]`) and has optional local
    `[task]` settings for limits, schedule, and execution.
  - Branch prefix `factory/task`, outcome file `task-outcome.json`, and the fix kind's
    reconciliation policy (settle on an open PR).
  - Handoff works like the existing Ready handoff: a writer-authored Task in a fix target
    that a human moves to Ready gets Owner=factory. No automatic routing rule is added.
- **New `factory-task` workflow** (contract `factory-task/1`):
  - Steps: the contract and clean-tree checks, then a task triage step, then (only when
    triage accepts) branch creation, implementation, the validator gates, a conditional
    gate-exercise and `test-flows` step, a lead review, `builtin:core/finalize-pr`, PR
    annotation, and the outcome record.
  - Triage declines with `needs-input` and names the decision, before any branch is created
    or pushed, when the work would change runtime behavior, a public API or CLI, an
    OpenSpec spec, or persisted data. It also declines when the work needs a product,
    design, compatibility, or threshold decision that the issue and repository conventions
    leave open, when it is too large to review as one PR, when it touches credentials,
    release or deploy configuration, or branch protection, or when it requires changes
    outside the target repository.
  - **Bounded, evidence-based choices are allowed.** When the issue explicitly hands a
    choice to the work, triage accepts it and the PR records the evidence and the result.
    Examples are "fix or baseline with per-file ignores (decide during the work)" and
    "measure before fixing the threshold". Two conditions apply: the choice changes no
    runtime behavior, and its range is set by the issue (a stated target or limit) or by
    the repository (an existing CI version, `.nvmrc`, or current configuration). A fix that
    would need a behavior change is baselined instead. Triage declines only when nothing in
    the issue or the repository bounds the choice, or when every option changes behavior or
    compatibility.
  - **Toolchain versus release configuration.** Task scope covers development toolchain
    and dev-dependency bumps, linter, type-checker, and test-runner configuration, and CI
    jobs that only check (lint, type, test, duplication, dead code). Release configuration
    is anything that publishes, versions, signs, or deploys an artifact: release and publish
    workflows, version fields, tags and changelog releases, registry and deploy settings,
    and secrets. That is excluded, as are runtime-dependency bumps and changes to a shipped
    runtime requirement (such as `engines`) that the issue does not explicitly request with
    a value the repository already uses.
  - Implementation does not require a red test first. It runs the repository's tests,
    linters, and validator.
  - **New or tightened gates are developer-visible changes.** For each new or tightened
    check or CI job, the workflow records both a positive and a negative exercise: the gate
    passes on the delivered tree, and it fails on a deliberately introduced violation that
    is then reverted, so the tree stays clean. This is mandatory when the issue states a
    rejection criterion, such as "a deliberately duplicated 70+ token block or an unused
    export fails the check". `test-flows` runs when triage marks other tooling behavior as
    user-visible. Documentation-only and pure refactor tasks with no changed workflow skip
    both.
  - Commits use the `chore:` conventional-commit type, and the workflow makes sure the PR
    title starts with `chore:`.
- **Lifecycle parity with fixes**: the PR-READY review loop (`factory-review/1`), recovery,
  claim reconciliation, needs-input retry gestures, merge sync, cleanup, board presentation,
  issue reporting, and the watch `PR-READY` and `FAILURE` events all apply to task claims.
- **Task scope holds through review rounds.** On a task claim, the review workflow's triage
  applies the same scope exclusions to every requested change and to the resulting diff.
  When feedback asks for something outside task scope, the round implements nothing for it
  and records `needs-input`, naming the request and routing it to a Bug or a Feature. Review
  behavior for fix and feature claims is unchanged.
- **Operations and docs**: `status` and `doctor` cover the new kind (a `task-host` doctor
  group). The `factory-assign` skill accepts `--apply task`, and `factory-status` reports
  task claims. `AGENTS.md` and `docs/operations.md` describe the kind.
- **Configuration**: `config/codagent.toml` gains `[routing] task_type = "Task"` and a
  `[task]` section with a Sonnet-class lead, so the live factory can take Tasks after
  deploy.

There are no breaking changes. Bug and Feature admission and their workflows are unchanged.

## Capabilities

### New Capabilities
- `factory-task-intake`: Task handoff through Ready, eligibility (target, type, writer,
  Owner, Status, `needs-input`), ranking by Priority, per-claim branch resolution, retry
  gestures, and side-effect reconciliation before launch.
- `factory-task-execution`: the `factory-task/1` workflow and its contract. This covers the
  triage gate's decline criteria, the bounded-choice and toolchain-versus-release rules,
  implementation without red-test-first, validator gates, positive and negative gate
  exercises, the conditional `test-flows` step, `chore:` commits and PR title, and the
  outcome file.
- `factory-task-reporting`: comments on task activity, mapping task outcomes to the board,
  and durable, deduplicated delivery of reports.

### Modified Capabilities
- `factory-routing`: the `[routing] task_type` setting and recognizing Task-typed issues for
  handoff. Bug and Feature routing is unchanged.
- `factory-pull-request-lifecycle`: the review loop, recovery, merge sync, and cleanup
  requirements cover the `task` kind.
- `factory-review-execution`: review rounds on task claims enforce task scope and stop with
  `needs-input` (routed to Bug or Feature) instead of implementing out-of-scope feedback.
- `factory-watch-dispatch`: `PR-READY` events are detected for task runs as they are for fix
  and feature runs.
- `factory-operations`: `status`, `doctor` (the `task-host` group), configuration parsing
  for `[task]`, and documentation cover the new kind.

## Technical Approach

The change adds one `PullRequestKind` definition, `TASK`, and registers it. Nearly all
behavior comes from the existing generic handler and its collaborators. The work is:

- **Config**: add `RoutingConfig.task_type` (default `"Task"`), a `TaskConfig` shared
  section (contract, defaults) that is optional like `[feature]` and gates `enabled`, and a
  `TaskLocalConfig` (limits, schedule, execution). The defaults are fix-sized limits: 15
  minutes without progress, two hours of execution, three hours in total.
- **Execution mode**: `host` only, as for features. The live service runs fixes on the host,
  and adding a Docker sandbox for tasks would mean another image and doctor group with no
  current user. Docker support can come later by extending `allowed_modes`.
- **Hard-coded kind literals**: a few places in shared code still name `fix` or `feature`:
  the outcome contract map in `supervisor._load_result`, `watch/detect.py`'s PR-READY kind
  set, the non-fix branch in `readiness.py` and `operations.py`, and the host backend's
  readiness check that only looks at `local.fix`. Each one gets a `task` case, or better,
  derives the answer from the registered kinds, so that Bug and Feature behavior is
  unchanged. The `kind == "feature"` branches (definition resume, checkpoints, archive)
  stay feature-only. Task takes the fix path.
- **Workflow**: `factory-task-v1.0.yaml` is staged like the fix workflow. It is modeled on
  `factory-fix-v1.0.yaml`, with a task-specific triage prompt and output. `fixable` becomes
  `doable`, and triage also returns the delegated choices it will make, the gates to
  exercise, and a `user_visible` flag that gates `test-flows`. The implementation prompt
  has no TDD requirement. A gate-exercise step runs each named gate against the delivered
  tree and against a scratch violation, reverts the violation, and records both results
  under the attempt's logs for the PR description. The final clean-tree gate catches a
  violation that was left behind. It reuses `record-triage.sh`, `record-outcome.sh`, and
  `check-contract.sh`, generalized where they hard-code the fix contract or outcome name.
  `builtin:core/finalize-pr` has no title parameter. So the implementation prompt asks for
  `chore:` commits and a `chore:` PR title, and a factory-side step after finalize retitles
  the PR through the REST API when the title lacks the `chore:` prefix. This is the same
  approach `annotate-pr` uses for the body, and it keeps the change inside this repository.
- **Review loop**: task claims use `factory-review/1` on `reason == "review"`, as fixes do.
  The review file already carries the claim's `kind`. For `task`, the review triage
  prompt adds the task scope exclusions to its criteria: a requested change that falls
  outside them becomes a `needs-input` reason routed to Bug or Feature rather than an item
  to implement. A scope check on the round's diff before finalize stops the round the same
  way. The fix and feature branches of the review workflow are untouched, so the contract
  stays `factory-review/1`.

## Out of Scope

- Automatic routing of Tasks to the factory. A human or `factory-assign` hands a Task off,
  as with Features.
- A Docker sandbox for tasks.
- Having the fix triage redirect a report with "belongs in a Task".
- Changes to Agent Runner's `finalize-pr` or `push-pr` (for example a title parameter), or
  to the Agent Skills repository beyond this repository's own `factory-assign` and
  `factory-status` skills.
- Doing the example tasks themselves (#74 and the others). They become the first Task
  claims once this change ships.
- Eval-style or human-review verdicts for tasks.

## Impact

- **Code**: `config.py`, `work_kinds/pull_request/kinds.py`, `handler.py`, `launch.py`,
  `readiness.py`, `outcome.py`, `supervisor.py`, `operations.py`, `backends/host.py`,
  `watch/detect.py`, and the new workflow and script files under
  `work_kinds/pull_request/workflow/`, plus the task branch of
  `factory-review-v1.0.yaml`. Tests cover config parsing, admission and non-admission
  (including that Bugs and Features are unchanged), the triage decline with no branch
  pushed, the `factory-task/1` outcome contract, review-round scope enforcement, and
  PR-READY detection.
- **Configuration**: there are new shared keys (`[routing] task_type`, `[task]`) and
  optional local `[task]` settings. Existing configurations without `[task]` keep the kind
  disabled.
- **Persisted state**: claims and runs gain a new `kind` value, `task`, and the store schema
  is unchanged. Rolling back to a release without the kind while task claims are open would
  leave them unhandled. Settle or cancel open task claims before rolling back.
- **Operators and skills**: `status`, `doctor`, `factory-assign` (`--apply task`),
  `factory-status`, `AGENTS.md`, and `docs/operations.md`.
- **GitHub**: the target repositories must have the native `Task` issue type enabled. It is
  a default organization issue type.
