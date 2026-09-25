## Why

The factory picks up Bug-typed issues and fixes them autonomously, but it has no path for
work that changes specified behavior. The fix workflow's triage already declines such work
with the reason "belongs in a Feature", and today that is a dead end: a human has to run
`openspec:change` interactively for every feature, however small and well described.

This change lets a maintainer hand a Feature-typed issue to the factory and receive a
reviewed pull request that carries the OpenSpec change (proposal, specifications, design,
test plan), the implementation, acceptance evidence, and the archived spec updates.

Two structural problems stand in the way, and adding a third work kind on top of them
would multiply the cost of fixing them later:

- Everything that makes a bug claim work after launch (the needs-input loop, the pull
  request review loop, merge sync, cleanup) is keyed to the `fix` kind through
  `isinstance(handler, FixHandler)` checks in `runtime.py`, `kind == "fix"` guards in
  `sync.py`, `blocked.py`, and `review.py`, and literals in `handler.py`, `launch.py`, and
  `outcome.py`.
- The `ExecutionBackend` protocol introduced by the `fly` change has one implementation.
  Docker container and host process ownership still live inline in the supervisor, and the
  supervisor, runtime, and doctor select Fly through backend-specific branches rather than
  through the protocol.

The workflow places no limit on the size of a feature. Scope control is the assigning
human's responsibility, which is why features are never assigned to the factory
automatically.

## What Changes

The change has four parts. Parts 1 and 2 preserve behavior, apart from one deliberate fix in
part 1; the existing test suite stays green before part 3 begins.

1. **Unify execution backends.** Add Docker container and host process implementations of
   `ExecutionBackend`, resolve the backend from each execution plan, and route the
   supervisor, result disposal, reconciliation, and doctor through the protocol for every
   work kind. Plans recorded before this change, which carry no backend name, resolve from
   their existing ownership hints so in-flight runs survive the deploy. Adoption after a restart
   probes the whole execution through its backend, so a run whose launcher exited while the
   factory was down is settled from its result, recovered, or kept under supervision instead
   of being held indefinitely as unverifiable.
2. **Generalize the pull-request work kind.** Turn the fix handler and its collaborators
   (launch, outcome, blocked, review, sync, cleanup, readiness) into one implementation
   parameterized by a per-kind definition: issue type, workflow and contract, outcome file,
   branch prefix, slot, limits, window, targets, and user-facing wording. Bug behavior is
   unchanged.
3. **Add the `feature` work kind.**
   - **Intake.** No routing rule is added: a Feature-typed issue is routed like any other
     issue and is never assigned to the factory automatically. The only entry point is the
     existing Ready handoff, generalized so a Feature-typed issue in a configured fix
     target that a human moves to Ready gets `Owner=factory` and is admitted as `feature`
     work. Authorization is the rule the bug handoff already applies: the issue author must
     hold write access to the repository, checked at handoff and again at admission. The
     person who moved the card is not identified; that gesture is trusted through Project
     write access. A feature filed by an outside contributor therefore cannot be handed off
     by moving its card. Feature work has its own slot, attempt limits (30 minutes without
     progress, six hours of execution, eight hours in total by default), and admission
     window, and is ranked by Priority, then newest created, as the factory now ranks every
     queue. Features share the fix kind's targets, branch settings, and credential.
   - **Workflow `factory-feature` (contract `factory-feature/1`)**, fully autonomous and
     modeled on `openspec:change` v2:
     - preamble: contract and clean-tree checks, decline with `needs-input` when the target
       has no `openspec/` directory, create the branch, create the OpenSpec change;
     - define (new `factory-define` sub-workflow): proposal, adversarial proposal review by
       a fresh crosscheck agent (a workflow step in its own reviewer session, one pass, as is
       the approach review), then specifications, design, test plan, approach review by
       a fresh crosscheck agent, a single-entry `tasks.md`, OpenSpec validation with repair,
       the planning-artifact check, and the plan commit, after which the branch is pushed
       without a pull request. The proposal step replaces triage. Each definition step
       decides open questions itself and records them as assumptions, except direction-level
       ones (the issue is contradicted or has materially different readings, a public
       interface or data format would break, or the decision lies outside the repository).
       Any definition step may stop with `needs-input` for such a decision, as may a no-go
       verdict from the proposal step; the drafted artifacts are committed and the branch
       pushed so the questions can be answered against them. There is no task planning and
       no task review;
     - implement: the whole change as one task through `builtin:core/implement-task`, with a
       session report;
     - archive the OpenSpec change, so that everything after this point runs against the
       tree the pull request will carry;
     - verify: assumption review, simplify, validator, draft pull request, and
       `prepare-acceptance` against the test plan, through a new `builtin:core/verify-change`
       given the archived change directory;
     - classify, without stopping: after the plan commit nothing waits for a human. Runner's
       verification tail succeeds whether acceptance converged or not, so the run sorts
       everything a reviewer may need into red (failed or unverified criteria, incomplete
       acceptance, known deviations from the specifications, a resume that fell back to a
       fresh start), orange (decision-bearing assumptions, plan revisions made from human
       comments, commits added after acceptance), yellow (the remaining assumptions), and
       white (passed criteria with evidence);
     - finalize the pull request (marking it ready, whatever the red and orange tiers hold),
       open its description with a "Review first" section listing red, then orange, then
       yellow items, keep the full ledger and white evidence collapsed below, state the flag
       counts in the issue comment, and record exactly one outcome.
   - **Resume.** A re-attempt keeps finished work. A claim stopped during definition resumes
     at the step that stopped. A feature that ended `failed` and is dragged back to Ready gets
     a new claim that continues from the prior claim's pushed plan at implementation. A
     technical recovery retry continues after the newest checkpoint on the pushed branch
     (checkpoint commits carry their phase, so the branch alone decides) and never revises
     artifacts. A resume after a definition stop, and a continuation from a failed claim,
     first check the artifacts against the issue and the new comments and revise them where
     that input warrants; both resume at or before implementation, so every revision is
     implemented and verified. A missing or unmergeable resume point falls back to a fresh
     start, which is reported. Removing the feature configuration stops new handoffs but
     leaves existing feature claims fully handled.
   - **After the pull request.** The needs-input loop, the `factory-review` review loop,
     merge sync with issue closure, and cleanup apply to feature claims as they do to bugs.
   - **Execution.** Host only. Configuring Docker or Fly execution for features is rejected
     when configuration loads.
   - **Audit.** Feature attempts are audited by the existing host launch wrapper, as every
     host attempt already is; this change adds nothing for it.
4. **Agent Runner prerequisites** (separate pull request in `agent-runner`, landed first):
   carve the autonomous tail of `core/implement-change` (review-assumptions through
   verify-acceptance-handoff) out into `core/verify-change`, which `implement-change` then
   calls. Acceptance preparation stops using `call_agent`: the tester runs as a workflow
   step in a bounded loop of `acceptance_rounds` (default 3, today's limit), and the last
   round makes no unverified fix. No step prompt in `verify-change` runs Agent Validator,
   directly or through a skill such as `codagent:push-pr`: validation after acceptance fixes
   is the `run-validator` workflow as its own step, and `open-draft-pr` opens the draft
   directly. The v2 `openspec` and `spec-driven` change and implement-change workflows keep
   their behavior otherwise. Restructuring `core/finalize-pr` the same way is a follow-up.

## Capabilities

### New Capabilities
- `factory-feature-intake`: the Ready handoff for Feature-typed issues, eligibility and
  authorization at admission, Priority ranking, the separate feature slot, branch
  resolution, the retry gestures that resume or continue a feature, and side-effect
  reconciliation before launch.
- `factory-feature-execution`: the versioned `factory-feature` workflow contract, the
  OpenSpec-repository requirement, autonomous definition with its decide-or-stop rule,
  resume and continuation, single-task implementation, archival before verification,
  verification and the draft pull request, review-attention classification without
  blocking, finalization, feature limits and window, and host-only execution.
- `factory-feature-reporting`: feature issue comments, board mapping for feature outcomes,
  the pull request's "Review first" annotation by red, orange, yellow, and white tiers,
  and durable delivery.
- `factory-pull-request-lifecycle`: the contracts shared by every settled pull-request
  claim, fix or feature: detecting eligible review comments, re-admitting a review round
  through the claim's own kind slot, and syncing the working clone and closing the issue
  after merge. These requirements move here from the fix-specific capabilities below.
- `factory-execution-backends`: every plan names the backend that owns its execution, which
  provides readiness, ownership, probing, termination, disposal, reconciliation,
  reattachment, and provenance; plans recorded before this change resolve from their
  existing ownership hints; adoption after a restart settles unsupervised runs.

### Modified Capabilities
- `factory-routing`: one added rule: Feature-typed issues are never assigned to the factory
  by routing.
- `factory-claim-lifecycle`: slots, blocked-card corrections, pre-suite recovery, handler
  ownership, and the closure exemption cover feature claims.
- `factory-bug-intake` and `factory-eval-intake`: selection is restated as Priority, then
  newest created, matching the ranking the factory already applies; bug retry gestures point
  to `factory-pull-request-lifecycle`.
- `factory-fix-execution`: the staged workflow directory holds the feature workflows too.
- `factory-review-intake`: its fix-only requirements are replaced by the shared
  `factory-pull-request-lifecycle` contract.
- `factory-review-execution`: review rounds run for any pull-request claim; on a feature
  pull request a requested behavior change is made and the living specifications follow it.
- `factory-fix-reporting`: merge sync moves to `factory-pull-request-lifecycle`; the
  remaining fix comment and board requirements are unchanged.
- `factory-operations`: feature configuration (host only, sharing the fix targets and
  credential), doctor's feature-host group, status for feature claims, and cleanup and
  retention for feature claims.

`factory-fly-execution` is not modified: the backend refactor preserves its behavior, which
`factory-execution-backends` places behind the protocol.

## Technical Approach

**Backends.** `ExecutionPlan.ownership_hints` gains a backend name on every plan. A resolver
maps the name to `FlyMachineBackend`, a new `DockerContainerBackend` (wrapping today's
`inspect_container`, `discover_container`, and `stop_owned_container`), or a new
`HostProcessBackend` (wrapping the pid-and-start-time identity and `_terminate`).
`runtime.py` and `operations.py` iterate the backends in use instead of importing Fly.

The protocol as it stands covers a durable resource after it exists; it has no operation
for launching or for capturing identity after spawn, and `attach_argv` means nothing for a
host process. The refactor therefore separates two responsibilities rather than forcing
both into the protocol: a generic local launcher and process watcher that every plan uses
(environment, spawn, launcher identity, stale-child cleanup), and the durable-resource
backend the watcher consults to probe, terminate, and dispose what the launcher created.
Docker is the composite case, a launcher process plus a discovered container, and its
combined state is defined explicitly. Attachment becomes optional. Backends whose resources
die with the process implement disposal and reconciliation as no-ops. The exact methods are
a design decision.

Every plan shape in a deployed store resolves to a backend: a `fly-machine` backend name,
`sandbox=host`, `sandbox=docker`, and the legacy eval `suite=and-scene` hint.

**Work kinds.** A frozen per-kind definition replaces the constants block in `launch.py`,
the contract-to-filename ternary in `outcome.py`, the `"Bug"` literals in `handler.py`, and
the kind guards in `blocked.py`, `review.py`, and `sync.py`. `runtime.py` dispatches the
blocked, review, and sync phases on a handler capability rather than on `FixHandler`.
`work_kinds.handlers()` registers `fix` and `feature` from the same class, so per-kind
slots come from the existing `slot_free` map.

**Workflow packaging.** `factory-feature` and `factory-define` ship in the factory package
and are staged into the target clone's project-scope catalog exactly as `factory-fix` is.
They reference Runner builtins (`core/check-planning-artifacts`, `core/commit-change-plan`,
`core/implement-task`, `core/verify-change`, `openspec/archive-change`, `core/finalize-pr`)
rather than copying their steps. The factory derives the OpenSpec change name from the
issue and passes it as a parameter. `factory-define` is factory-owned because Runner's
`define-change` is interactive by construction, in step mode and in prompt wording; how the
autonomous definition decides, records assumptions, and applies review findings is settled
in the specifications and design.

**Known trade-offs.**
- Archival happens before verification and before any human has approved the change, so
  that acceptance evidence describes the final tree. A pull request whose acceptance did not
  converge is still finalized, flagged red, with its change already archived, and a review
  round that changes behavior edits `openspec/specs/` directly. Review rounds do not re-run
  acceptance.
- The factory depends on a Runner build that contains `core/verify-change`. Doctor checks
  the installed Runner, so a stale Runner fails readiness rather than a run.
- The post-run audit runs inside the attempt's launch wrapper for up to 45 minutes, so the
  feature slot stays occupied until it finishes.
- Resuming requires pushing unreviewed definition artifacts to a factory branch before any
  pull request exists.

## Out of Scope

- Automatic assignment of features to the factory, and any limit on feature size.
- Handing off a feature whose author lacks write access, including a comment- or
  command-based handoff attributable to a maintainer.
- Docker or Fly execution for features, including adding `openspec` to the sandbox image.
- Features in repositories without OpenSpec: no `openspec init` inside a feature pull
  request and no fallback to the `spec-driven` workflows.
- Multi-task features, task planning, and task review.
- Re-running acceptance during review rounds, and deferring archival until approval.
- A human checkpoint between definition and implementation.
- Enabling features for only some fix targets.
- Extracting Runner's definition-validation loop into its own builtin.
- Changes to the fix workflow's steps beyond what the shared refactor requires.

## Impact

- **agent-factory code:** `backends/`, `supervisor.py`, `runtime.py`, `operations.py`,
  `controller.py` (plan hints), `config.py`, `work_kinds/__init__.py`, all of
  `work_kinds/fix/` (restructured into a shared pull-request kind), the eval handler and
  `suites/and_scene` (backend name on plans), plus new packaged workflows and scripts.
- **Configuration:** a `feature_type` value and a `[feature]` table in
  `config/codagent.toml` (contract, roles) and in machine-local configuration (limits,
  window); features use the fix targets, branches, and credential. Existing configuration
  keeps working unchanged.
- **agent-runner:** one prerequisite pull request (the `core/verify-change` carve-out) and a
  Runner build containing it on the factory host.
- **Documentation:** `docs/installation.md`, `docs/operations.md`, and
  `docs/github-setup.md` for feature setup and the handoff gesture.
- **Risk:** parts 1 and 2 touch the supervisor and eval, the factory's most delicate code,
  while bug and eval work is live. Deployment must tolerate runs started before the change.
- **Users:** maintainers gain a Ready-to-pull-request path for features; bug and eval
  behavior is unchanged.
