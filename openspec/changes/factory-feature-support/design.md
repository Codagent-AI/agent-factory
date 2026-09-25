## Context

Agent Factory is a local Python service (`src/agent_factory`) that admits GitHub Project cards
as claims, runs one attempt per work kind at a time, supervises each attempt from a detached
watcher process, and reports on the issue. It has two work kinds, `eval` and `fix`. This
change adds `feature` and, before it does, unifies execution ownership behind
`ExecutionBackend` and turns the fix kind into a shared pull-request kind. The approved
behavior is specified in `specs/`; this document describes how to build it.

Current state that shapes the design (file references are relative to `src/agent_factory/`
unless they name another repository):

- **Supervision.** `supervisor.py` runs every attempt. `_launch_and_observe` (180-241)
  spawns the plan's argv, captures a process identity (`_process_identity`: pid, `ps`
  start time, argv, artifact path), and records it with `store.begin_run` under a nonce
  compare-and-set. `_observe` (596-757) watches progress sources, enforces limits, and
  records the result. Docker ownership is inline: `discover_container`, `inspect_container`,
  `container_matches_recorded_ownership`, `stop_owned_container`, `_terminate_execution`.
  Fly is selected by `ownership_hints["backend"] == "fly-machine"` branches at 146, 190,
  and 238 and runs `_supervise_fly` / `_observe_fly`.
- **Restart gap.** `supervise()` (151-169) adopts a run only through a live launcher pid.
  When the launcher exited while no watcher was running (a Mac restart, or the attempt
  simply finished), the replacement watcher reports "recorded execution identity is no
  longer verifiable" and the run stays `observing` indefinitely, holding its kind's slot,
  even when a result file exists or the Docker container is still running.
- **Backend protocol.** `backends/__init__.py` defines `ExecutionBackend` (`readiness`,
  `identity_from_plan`, `probe`, `terminate`, `dispose`, `attach_argv`, `reconcile`,
  `provenance`). Only `fly/backend.py` implements it, and runtime and doctor reach it
  through configuration-gated branches (`runtime.py:77-84`, `runtime.py:640-673`
  `_dispose_fly_result`, `operations.py:169-172`).
- **Plans.** `ExecutionPlan` (`controller.py:47-55`) persists `ownership_hints` as a
  string map. Today's hint shapes: eval Docker `{artifact_path, suite: and-scene,
  sandbox: docker, image_tag}`; eval Fly `{artifact_path, suite: and-scene, backend:
  fly-machine}`; fix and review Docker `{artifact_path, image_tag, sandbox: docker,
  branch_name}`; fix and review host `{artifact_path, sandbox: host, branch_name,
  runner_executable, runner_version, session_dir}`; eval plans recorded before
  `sandbox=docker` existed carry only `suite: and-scene`.
- **Fix coupling.** `runtime.py` checks `isinstance(handler, FixHandler)` at 65-68, 148,
  192, 344-348, and 559, and `claim.kind == "fix"` at 182; `blocked.py`, `review.py`,
  `sync.py`, and `retention.py` hard-code the `fix` kind or unit; `handler.py` hard-codes
  the `"Bug"` issue type in `handles()` and `snapshot()` while the Ready handoff
  (`runtime._assign_ready_bug`, 464-512) uses `shared.routing.bug_type`; `launch.py` and
  `outcome.py` hard-code the workflow, contract, branch prefix, and outcome filename.
  `retention.py:121-128` treats every non-fix claim as an eval.
- **Host attempts** run `host-run.sh` (`launch.host_script`), which runs the Runner with
  `--session-dir <evidence>/agent-runner-session`, then the post-run audit
  (`python -m agent_factory.audit host`) with the GitHub credential removed, then exits
  with the workflow's status.
- **Agent Runner** (`/Users/paul/codagent/agent-runner`). Project workflows reference
  builtins as `workflow: builtin:core/<file>`. Sub-workflows share the run's
  `{{session_dir}}` and its named sessions (`internal/model/context.go:175-183`), receive
  only the params passed to them, and cannot return captures, so results come back through
  files. `skip_if` supports `previous_success` and `sh: <command>` (skips when the command
  exits 0; params and captures are interpolated shell-safely). There is no start-from-step
  flag on `run`, and `-resume` cannot be combined with `--session-dir`. The autonomy
  preamble is prepended only when a session starts, not when it is resumed
  (`internal/exec/agent.go:680-682`). The factory's profile set defines `lead`,
  `implementor`, and `tester`; `agent: crosscheck` currently falls back to Runner's
  built-in profile.
- **`core/implement-change`** (Runner, 217 lines) ends with the autonomous tail this change
  carves out: `review-assumptions` (writes `output/acceptance-assumptions.md`),
  `verify-assumptions-handoff`, `simplify`, `run-validator`, `verify-clean-for-pr`,
  `open-draft-pr`, `verify-draft-pr` (captures `pr_url`), `prepare-acceptance` (the lead
  calls the `acceptance-tester` session through `call_agent` up to three times and writes
  `output/acceptance-preparation-status.txt` as `ACCEPTANCE_COMPLETE` or
  `ACCEPTANCE_FAILED`), and `verify-acceptance-handoff`, which accepts either marker. It
  is used by `openspec:change` v2, `spec-driven:change` v2, `openspec:implement-change` v2,
  and `spec-driven:implement-change` v2. The v1 change workflows use their own older files.
- **Define skills.** `codagent:propose`, `spec`, `design`, and `test-plan` contain explicit
  "get user approval before writing" gates and use `codagent:ask-questions`, whose headless
  rule is to report the unresolved decision or follow the caller's fallback.
  `codagent:prepare-acceptance`, `proposal-review`, `review-approach`, and `session-report`
  need no override. `core/define-change` is interactive in every step and cannot be reused.

## Goals / Non-Goals

**Goals:**

- Every execution is owned through a named backend, selected from its recorded plan, and
  every plan recorded before the deploy keeps working.
- Adoption after a restart settles runs whose launcher exited while the factory was down,
  instead of holding them forever.
- One pull-request work-kind implementation serves fix and feature, with bug behavior
  unchanged.
- A fully autonomous `factory-feature` workflow that defines, implements, verifies, and
  finalizes an OpenSpec change, stops only for direction-level decisions during
  definition, resumes without redoing finished work, and flags what a reviewer must look
  at by severity.
- One Agent Runner pull request, landed first, that carves out `core/verify-change` and
  removes `call_agent` from acceptance preparation.

**Non-Goals:**

- Docker or Fly execution for features, OpenSpec initialization in target repositories,
  multi-task features, re-running acceptance in review rounds.
- A generic resume-from-step feature in Agent Runner.
- Changing the fix workflow's steps, beyond sharing its outcome and contract scripts.
- Orphan-container reconciliation for Docker (nothing reconciles them today; the Docker
  backend's `reconcile` stays a no-op).
- Restructuring `core/finalize-pr`, whose `push-pr` step and CI-loop `fix-pr` step still run
  Agent Validator through skills; it is shared by the fix workflows and every Runner change
  workflow and is tracked as a follow-up issue.

## Approach

The work lands in five parts, in order. Parts A and B are refactors whose test suites must
stay green (with the one intended behavior change in A3) before part D begins.

### A. Execution backends

**A1. Resolution.** A new `backends/resolve.py` exposes `backend_for(plan) -> Backend` and
`backend_name(hints) -> str | None`:

1. `hints["backend"]` when present (`docker`, `host`, `fly-machine`);
2. else `sandbox == "host"` → `host`; `sandbox == "docker"` → `docker`;
3. else `suite == "and-scene"` with an empty identity or an argv whose program is `run.sh`
   → `docker` (eval plans recorded before `sandbox=docker`);
4. else a plan whose hints carry only `artifact_path` (used by tests and generic process
   plans) → `host`;
5. anything else, or conflicting hints → `None`, which the caller treats as ambiguous
   ownership (report uncertainty, terminate nothing, hold new launches of that kind).

The `backend` key must win because Fly plans also carry `suite=and-scene`. Every new plan
writes `backend` (the eval adapter, `launch.build_plan`, `launch.build_host_plan`) and keeps
writing today's `sandbox` and `suite` hints beside it, so a rollback to the previous release
still resolves the plan. The
run's backend name is also copied into `progress["backend"]` at launch so status can show
it without re-resolving.

**A2. Protocol and implementations.** `ExecutionBackend` keeps its methods. `attach_argv`
becomes optional (a `supports_attach` attribute; only Fly sets it). A new `adopt(plan, run,
store) -> Probe` method is added (A3). Implementations:

- `HostProcessBackend` (`backends/host.py`): identity is `run.process`; `probe` maps
  `_identity_status` (alive, missing → `gone`, unknown); `terminate` is today's killpg
  `_terminate`; `dispose` and `reconcile` are no-ops; `readiness` returns the host
  executable and Runner-settings checks that doctor already runs for `fix-host`, parameterized
  by the kinds configured for host execution; `provenance` returns runner path and version
  from the hints.
- `DockerContainerBackend` (`backends/docker.py`): identity is the composite of
  `run.process` (launcher) and `progress["container"]` (`{id, image, artifact_path}`);
  `probe` returns `alive` when the launcher is alive, or when the launcher is gone and the
  recorded container inspects as running and matches recorded ownership; `mismatch` when
  the container no longer matches; `gone` when both are gone; `unknown` on Docker CLI
  failure or multiple matches. Container discovery moves here unchanged
  (`discover_container`, `inspect_container`, `container_matches_recorded_ownership`,
  `_mount_matches`), as do `stop_owned_container` and `_terminate_execution` (stop the
  container, then killpg the launcher). `dispose` does nothing at result time (image
  removal stays in cleanup, `work_kinds/images.py`). `readiness` holds the `docker info`,
  memory-allowance, and reclaimable-space checks now in `operations.py` and `runtime.py`.
- `FlyMachineBackend` stays in `fly/backend.py`, unchanged apart from implementing `adopt`
  through its existing reattach path.

**A3. Supervisor.** `supervisor.py` keeps the generic launcher and watcher: spawn
(`_launch_and_observe` and `_spawn_plan_process` merge into one `launch(plan, argv)`),
process identity, the watcher lease and nonce, `_run_lock`, timers, progress sources,
limits, quota pause, result loading, and the exit-code wrapper. Mechanism-specific code
calls the resolved backend. `_observe` and `_observe_fly` keep their loops for now but take
liveness, termination, and disposal from the backend; merging them is optional follow-up.

Adoption changes (the intended behavior change). When a replacement watcher starts on a
`running` or `observing` run whose recorded launcher is not alive, it calls
`backend.adopt(...)` instead of reporting uncertainty immediately:

| Backend probe of the whole execution | Result file present | Action |
|---|---|---|
| alive (e.g. Docker container still running, Fly Machine running) | — | continue observing (Fly: reattach a launcher) |
| gone | yes | record the result as the watcher would have (`finish_run` with the result's status) |
| gone | no | `finish_run(interrupted, "execution ended while unsupervised")`, which the recovery policy treats as a technical failure |
| mismatch or unknown | — | report uncertainty as today (held, operator attention) |

"Result file present" means `result.json`, or the kind's structured outcome file
(`fix-outcome.json`, `review-outcome.json`, `feature-outcome.json`) in the attempt's
evidence, which the handler's `read_result` interprets exactly as for a normally observed
exit. A run whose recorded identity is empty (the child exited before identity capture)
follows the same table. A `reserved` run is still never resumed by `tick`.

**A4. Runtime and doctor.** `runtime.py` replaces the startup Fly reconcile with a loop over
the backends named by nonterminal and undisposed runs plus the backends configured for
new work, and replaces `_dispose_fly_result` with `_dispose_result`, which resolves the
run's backend and applies today's decision table (only Fly acts on it). `_consume_results`
stops copying `progress["container"]` itself; the Docker backend's `provenance` supplies
it. `operations.doctor` asks each configured backend for `readiness` and keeps today's
group names; `needs_docker` becomes "the Docker backend is configured for some kind".

### B. The shared pull-request work kind

**B1. Package.** `work_kinds/fix/` moves to `work_kinds/pull_request/` (modules keep their
names: `handler.py`, `launch.py`, `outcome.py`, `blocked.py`, `review.py`, `sync.py`,
`cleanup.py`, `readiness.py`, `workspace.py`, `workflow/`). `FixHandler` becomes
`PullRequestHandler(definition, shared, local)`; `FixCleanup` and `FixWorkspace` drop the
prefix. Tests that patch module paths (`test_fix_readiness.py`, `test_fix_gestures.py`)
move with it.

**B2. Definition.** `work_kinds/pull_request/kinds.py` holds a frozen dataclass and the two
instances:

```python
@dataclass(frozen=True)
class PullRequestKind:
    kind: str                     # "fix" | "feature": slot, fingerprint prefix, claim.kind
    unit_key: str                 # "fix" | "feature": evidence dir and run filters
    noun: str                     # "Fix" | "Feature"  (messages, diagnostic names)
    item_noun: str                # "bug" | "feature"
    issue_type: Callable[[SharedConfig], str]         # routing.bug_type | routing.feature_type
    workflow_name: str            # "factory-fix" | "factory-feature"
    workflow_file: str            # "factory-fix-v1.0.yaml" | "factory-feature-v1.0.yaml"
    staged_files: tuple[str, ...] # this kind's workflows, rules, and scripts
    contract: Callable[[SharedConfig], str]           # fix.contract | feature.contract
    outcome_file: str             # "fix-outcome.json" | "feature-outcome.json"
    branch_prefix: str            # "factory/fix" | "factory/feature"
    sync_marker: str              # "fix-sync" | "feature-sync" (fix keeps its value)
    allowed_modes: tuple[str, ...]# ("docker", "host") | ("host",)
    roles: tuple[str, ...]        # ("lead","implementor","tester") | + "crosscheck"
    doctor_groups: Mapping[str, str]                  # {"docker":"fix-sandbox","host":"fix-host"} | {"host":"feature-host"}
    reconcile: ReconcilePolicy    # SETTLE_ON_OPEN_PR | RESUME_FROM_OWN_BRANCH
    local: Callable[[LocalConfig], KindLocalConfig]   # limits, schedule, execution, disk floor
    defaults: Callable[[SharedConfig], Mapping[str, str]]  # role profiles
```

Staging copies the union of every registered kind's `staged_files` plus the review and
implementation workflows (`factory-review-v1.0.yaml`, `factory-implement-v1.0.yaml`), so
a clone always holds every packaged workflow. The shadow check in `launch.py` covers every
staged workflow name.

**B3. Handler capabilities.** Runtime stops checking types. `WorkKindHandler` gains optional
capabilities, and the eval handler implements the ones that apply to it as no-ops:
`attach_github(client, token_provider)`, `resolve_request(request)`,
`execution_mode(local)`, `needs_sandbox_memory(local)`, `ready_handoff(card, shared)` (the
generalized `_assign_ready_bug`), `unblock(...)` (today's `process_blocked_claim`),
`review_round(...)` (today's `process_review_claim`, with `prior_pull_request` public),
`merge_sync(...)` and `pending_sync(claim)`, `retention_targets(run)`, and
`blocked_reason(claim)`. Runtime calls them for every registered handler. Status lines,
provider maps, `DiagnosticGroup`, `_GROUP_ORDER`, `host_executables`, and the LaunchAgent
PATH gate iterate the registered kinds instead of the literals `("eval", "fix")`.

**B4. Ready handoff.** `ready_handoff` runs for each pull-request kind: the card's
repository is a fix target, it is an open issue, its native type equals the kind's issue
type, its status is Ready, its owner is not factory, and its author has write, maintain, or
admin access (cached per repository and author per tick). The feature kind's handoff and
admission additionally require its configuration to be present (B5).

**B5. Configuration.** Shared: `routing.feature_type` (default `Feature`), `[feature]`
with `contract` (default `factory-feature/1`) and `[feature.defaults]` roles (`lead`,
`implementor`, `tester`, `crosscheck`). Local: `[feature]` with `limits` (defaults 1800 /
21600 / 28800 seconds), `schedule`, `execution` (only `"host"` accepted; anything else
raises `ConfigurationError` in a new `_feature_local_config`, following the eval precedent
at `config.py:257-262`), and `minimum_free_gib`. The feature handler is always registered, so
claims already recorded keep being supervised, reported, synced, cleaned up, and pruned;
the shared `[feature]` section only enables handoff and admission of new feature work
(`ready_handoff` and admission return nothing without it). A claim's roles are frozen at
admission; without `[feature]`, limits and window fall back to their defaults. Targets, branches, and `credentials.fix_environment`
are shared with fix. `config/codagent.toml` gains the feature section with roles matching
`[fix.defaults]` and a `crosscheck` role (recommended: a different model family from
`lead`).

**B6. Reconciliation policies.** `SETTLE_ON_OPEN_PR` is today's fix behavior (any open
factory pull request settles the claim). `RESUME_FROM_OWN_BRANCH` (feature): the claim's own
branch, or its own draft pull request, is the resume point; a non-draft open factory pull
request for the issue settles the claim as handed off; an ambiguous lookup holds. For a
feature, the fresh-claim gesture (Review → Ready) is not honored while the settled claim's
factory pull request is open: the handler returns no gesture, the status correction restores
Review, and a one-time `open-pr-retry` event explains that commenting on the pull request
starts a review round.

**B7. Retention.** `retention_targets` returns the pull-request removal list
(`logs`, `factory-suite.log`, `agent-runner`, `agent-runner-session`, `.runtime`) for both
kinds, keeping the kind's outcome file and `input/`. The post-run audit's run directory
already sits beside the session directory inside the attempt's evidence and needs nothing
new.

### C. Agent Runner prerequisite (separate pull request, landed and deployed first)

1. New `workflows/core/verify-change-v1.0.yaml`, params `change_name`, `change_dir`,
   `change_label`, `artifact_validation_instruction` (required), `skip_validator`
   (default `"false"`), `acceptance_rounds` (default `"3"`); sessions `lead-agent: lead`,
   `acceptance-tester: tester` (named sessions are shared across the run, so continuity with
   the caller is preserved). Steps: `validate-change-name`, `validate-skip-validator`, then
   `review-assumptions`, `verify-assumptions-handoff`, `simplify`, `run-validator`,
   `verify-clean-for-pr`, `open-draft-pr`, `verify-draft-pr` moved verbatim from
   `implement-change`, then the restructured acceptance, then `verify-acceptance-handoff`
   verbatim.
2. Acceptance without `call_agent`, and with the validator as a step: a `loop` with
   `max_param: acceptance_rounds`, whose body is (a) `acceptance-test` in session
   `acceptance-tester`, autonomous, using `codagent:prepare-acceptance` with the same inputs
   and evidence directory as today and, from the second round, `acceptance-impact-scope.md`
   naming the affected flows; (b) `acceptance-gate`, a script that exits 0 when the tester's
   evidence shows convergence, with `break_if: success`; (c) `acceptance-fix` in session
   `lead-agent`, which fixes reported defects with `codagent:implement-with-tdd`, runs the
   artifact validation instruction and the narrowest relevant checks, commits
   `[{{step_id}}]`, and writes the impact scope for the next round, but does not run Agent
   Validator or push; (d) `acceptance-validator`, `workflow: run-validator-v1.0.yaml`, skipped
   when `skip_validator` is `true`; (e) `acceptance-push`, a script that pushes and verifies
   local `HEAD` equals the draft pull request head. Steps (c) to (e) are skipped on the last
   round, so no unverified fix is made after the final test. After the loop,
   `write-acceptance-status` writes `ACCEPTANCE_COMPLETE` or `ACCEPTANCE_FAILED` and ensures
   `acceptance-handoff.md` exists (on failure it lists the open defects and evidence).
   `open-draft-pr` no longer invokes `codagent:push-pr` (which runs validator detection and
   the `validator-run` skill): its prompt pushes the branch and opens the draft pull request
   directly, and the existing `verify-draft-pr` script checks the result. Rule for the whole
   carve-out: no step prompt runs Agent Validator; validation is always the `run-validator`
   workflow as its own step.
3. `core/implement-change` keeps its head (through `verify-task-index`) and calls
   `verify-change-v1.0.yaml` with its params, `acceptance_rounds` left at the default.
   Behavior of the four v2 callers is unchanged apart from the tester running as a workflow
   step.
4. The Runner build on the factory host must contain this change before the feature kind is
   enabled; `scripts/deploy.sh` builds the host Runner from `dev`, so the pull request must
   reach `dev`.

### D. The feature workflows

Packaged in `work_kinds/pull_request/workflow/` and staged into the target clone's
`.agent-runner/workflows/`:

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

**D1. Autonomy rules.** `factory-define-rules.md` is staged beside the workflows. Every lead
prompt in both workflows says "follow `.agent-runner/workflows/factory-define-rules.md`",
because the Runner's autonomy preamble reaches only a session's first step. The rules
file states: no approval gates (write the artifact to the named path); the decide-or-stop
rule and the four direction-level triggers from the specification; how to record a
decision (append to `<change>/decisions.md` with the step, the decision, the alternatives
considered, and whether it is decision-bearing); how to stop (write
`{{artifact_dir}}/define-stop.json` with `step`, `questions`, `direction_summary`, then end
the step); how a proposal no-go maps to a stop; and that ask-questions' headless rule
means "stop or decide", never "wait".

**D2. Stops.** `check-openspec` writes the `needs-input` outcome itself with
`stopped_step: preflight` and no branch; the factory maps a `preflight` stop to a fresh
definition on re-admission. After each define step that can stop, a `record-stop` script step runs with
`skip_if: 'sh: test ! -s {{artifact_dir}}/define-stop.json'`. It commits the drafted
artifacts (`[define-stop]` prefix), pushes the branch, and writes the `needs-input`
outcome with `stopped_step`, `questions`, `direction_summary`, and `branch`. Every later
step, including the rest of `factory-define`, carries `skip_if` on the same file's
presence, so the run ends successfully after the stop, which is the pattern factory-fix
uses for a declined triage.

**D3. Reviews.** `proposal-review` and `approach-review` run in their own named reviewer
sessions (fresh: each name is used once per run) with `codagent:proposal-review` and
`codagent:review-approach`. They write `{{artifact_dir}}/<step>-findings.json` (each
finding: id, severity, problem, recommendation) and never edit files. The following
`apply-*` lead step records a decision on every finding in `decisions.md`: applied,
rejected with a reason, or direction-level (which triggers a stop). A rejected finding of
high or medium severity is classified orange.

**D4. Resume and checkpoints.** A shared helper, `factory-resume-skip.sh <resume_from>
<step>`, exits 0 (skip) when `<step>` precedes `resume_from` in the fixed order
`proposal, proposal-review, specs, design, test-plan, approach-review, write-tasks,
implement, archive, verify, finalize`. Each resumable step carries
`skip_if: 'sh: .agent-runner/workflows/factory-resume-skip.sh "{{resume_from}}" <step>'`.

Checkpoints live on the branch, never only in local evidence. The commits that end the
`planned`, `implemented`, and `archived` phases carry a `Factory-Checkpoint: <phase>` git
trailer, and the workflow pushes the branch immediately after each. Because the phase marker
and the pushed tree are the same commit, there is no window where one exists without the
other: a crash before a push simply leaves the previous checkpoint as the last one, and that
phase is redone. The draft pull request opened by `verify` is the fourth checkpoint and is
found through GitHub. At admission the factory fetches the claim's branch into its mirror
(`git fetch <mirror> refs/heads/<branch>`) and reads the newest `Factory-Checkpoint`
trailer on it (`git log --format=%(trailers:key=Factory-Checkpoint,valueonly)`), then
computes `resume_from`:

- resumed after a definition stop → the outcome's `stopped_step`, or a fresh definition when
  it was `preflight`;
- technical recovery → the step after the newest checkpoint on the pushed branch
  (`planned` → `implement`, `implemented` → `archive`, `archived` → `verify`, an open draft
  pull request for the claim → `verify`); no branch or no checkpoint → fresh;
- new claim continuing a failed prior claim → `implement`, with `prior_branch` set, when the
  prior branch carries at least `planned`; otherwise fresh.

When `resume_from` is `archive` or later, the factory copies the prior attempt's
`agent-runner-session/output/*session-report*.out` into the new attempt's session
directory before launch, so `review-assumptions` has its input. If the Runner refuses a
pre-populated session directory, the factory falls back to `resume_from=implement`.
`prepare-branch.sh` handles the branch: fresh (new branch from the recorded target
commit), resume (fetch and check out the claim's own branch), continue (create the claim
branch from `prior_branch`, merge the recorded target commit; on conflict abort and fall
back to fresh), and records any fallback in `{{artifact_dir}}/resume.json`, which the
outcome and admission comment report.

`reconcile-artifacts` runs only for an attempt resumed after a definition stop or a new
claim continuing a prior claim, which always resume at or before `implement`, so every
revision is implemented, archived, and verified afterwards. It compares existing artifacts
with the current issue and eligible comments, revises them where warranted, and appends each
revision to `decisions.md` (classified orange). A technical recovery never runs it: no human
input arrives during an attempt, and a recovery may resume past implementation, where a
revision would no longer be implemented or verified.

**D5. Classification and annotation.** `classify` (lead) reads `decisions.md`,
the `*-findings.json` files, `acceptance-assumptions.md`, `acceptance-preparation-status.txt`,
`acceptance-flow-evidence.md`, `acceptance-findings.md`, `resume.json`, and the commits
after the accepted head, and writes `review-attention.json`:

```json
{"red": [{"title": "...", "detail": "...", "link": "..."}], "orange": [], "yellow": [],
 "white": [], "accepted_head": "<sha>", "later_commits": ["<sha>"]}
```

A verify step checks that the file parses and every tier is a list. `annotate-pr.sh` runs
after `finalize` (which may add commits), recomputes `later_commits` from git, adds one
orange item listing any commits after the accepted head that the classification did not
cover, rewrites `review-attention.json` with the final tiers, renders the
"Review first" section (red, orange, yellow; each tier shown even when empty), the summary
with links to the archived artifacts, the collapsed ledger and white evidence, `Refs #N`,
and the claim marker, and replaces the pull request description with `gh pr edit`. The
outcome carries the counts of the final tiers written by annotation, which the handler uses
in the pull-request comment, so the description and the comment always agree.

**D6. Shared scripts.** `record-outcome.sh` and `check-contract.sh` take the contract and
outcome path as inputs instead of hard-coding `factory-fix/1` and `fix-outcome.json`, and
`factory-fix-v1.0.yaml` passes them; its behavior is unchanged. `record-outcome.sh` accepts
optional extra fields (`stopped_step`, `review_attention_counts`, `resume`) that
`outcome.py` validates per contract. `record-triage.sh` and the orphaned
`read-regression-marker.sh` (not staged; only a test uses it) stay fix-only; the latter is
removed.

**D7. Review rounds on feature pull requests.** `factory-review` is unchanged in structure;
its triage prompt gains the kind from `review.json` and, for a feature, the instruction that
a behavior change is made and `openspec/specs/` updated to match, without re-running
acceptance.

### E. Specification corrections applied with this design

- `factory-routing`: a type change to Feature follows the untyped-issue rule (an untouched,
  routing-initialized card returns to Backlog; human edits are preserved), matching
  `routing.py`, so an auto-routed bug retyped to Feature is not picked up as a feature.
- `factory-execution-backends`: adoption after restart settles unsupervised runs (A3).
- `factory-feature-execution`: checkpoint pushes and the resume points replace the
  deferred-to-design marker.

## Decisions

1. **Launcher and watcher stay generic; backends own only what outlives a process.**
   Forcing spawn and identity capture into the protocol would give host and Docker
   meaningless `attach_argv` implementations and duplicate spawn code three times.
2. **Fix the restart gap during the refactor.** Adoption is rewritten anyway, and feature
   runs are long and host-only, so they are the most exposed. The fix only settles cases
   the evidence decides (result present, or nothing alive and no result); ambiguity is still
   held.
3. **One handler class and a frozen definition, not a subclass per kind.** The kinds differ
   in data, not behavior, except reconciliation, which is a policy value.
4. **Crosscheck as ordinary steps in named reviewer sessions, one pass.** `call_agent` has
   been unreliable, and a reviewer that always finds something would loop. Rejected
   consequential findings become orange so the lead's overrides stay visible.
5. **Resume with `skip_if` and pushed checkpoints, not Runner resume.** `-resume` cannot be
   used with `--session-dir`, needs the same clone, and does not survive a fresh clone; a
   `--from` flag would be a larger Runner change and still would not restore sessions.
   Pushing each checkpoint makes the branch the source of truth, which fresh-clone recovery
   needs anyway.
6. **`decisions.md` inside the change directory.** It travels with the branch across
   resumes, is archived with the change, and is readable in the pull request.
7. **A staged rules file for autonomy.** It avoids repeating the same overrides in eight
   prompts and survives the Runner adding the preamble only to a session's first step.
8. **Verify against the archived directory.** Archival before verification keeps evidence
   about the final tree (approved in the proposal); `verify-change` receives the archived
   path, and artifact validation uses `openspec validate --specs --strict` because the
   change no longer exists as an active change.
9. **`acceptance_rounds` parameter, default 3.** Keeps today's behavior for the interactive
   workflows; the last round never makes an unverified fix.
10. **Validator only as a workflow step.** Inside `verify-change`, no prompt runs Agent
    Validator, directly or through a skill; the `run-validator` workflow runs as its own step,
    so its retry loop, metrics, and failure handling are the Runner's rather than an agent's.
    `core/finalize-pr` keeps its skill-based validation until the follow-up.

## Risks / Trade-offs

- **Supervisor refactor on live code.** Parts A and B touch the supervisor and the eval
  path while bug and eval work runs. Mitigation: land A and B with the existing suite green
  (`test_supervision_hardening.py`, `test_supervision_recovery.py`,
  `test_independent_execution.py`, `test_fly_*`, `test_fix_*`, `test_host_launch.py`) and a
  new resolver matrix test over every legacy hint shape before any feature code; deploying
  while jobs run is safe because each process keeps its release.
- **`tests/e2e/test_docker_worktrees.py` may already be broken** (its plan carries only
  `suite=and-scene` with a `python -c sleep` argv, which today's discovery rule skips).
  Verify it when part A touches container discovery rather than assume it passes.
- **Autonomous define quality.** Skills written for a human may still try to ask. The rules
  file and the ask-questions headless rule convert that into a stop or a recorded decision;
  a define stop costs one round trip, not a bad pull request.
- **Session reports across attempts.** Resuming at `archive` or later depends on the Runner
  accepting a pre-populated session directory; the fallback (resume at `implement`) is safe
  but repeats implementation.
- **Unreviewed artifacts on a pushed branch.** Stops and checkpoints push factory branches
  before any pull request exists; they are named `factory/feature-*` and carry the claim.
- **The audit runs inside the attempt** for up to 45 minutes, counting against the
  eight-hour total and holding the feature slot.
- **Crosscheck model.** If the `crosscheck` role is left unset, Runner silently uses its
  built-in Claude profile; doctor's feature-host group checks that the configured role's CLI
  is authenticated.

## Migration Plan

1. Land the Agent Runner pull request (part C) on `main` and `dev`; deploy so the host
   Runner provides `core/verify-change`.
2. Land part A (backends, resolver, adoption fix) with tests; deploy. In-flight runs resolve
   through legacy hints.
3. Land part B (shared pull-request kind, capabilities, configuration schema without a
   `[feature]` section); deploy. Bug behavior is unchanged.
4. Land part D (feature workflows, scripts, handler registration, doctor group, docs) with
   the `[feature]` section absent from `config/codagent.toml`.
5. Enable features by committing the `[feature]` section through a pull request and
   deploying; doctor must pass the feature-host group.

Rollback: removing the `[feature]` section disables handoff and admission of features;
the feature handler stays registered, so existing feature claims keep their recorded plans,
finish supervision and reporting, and receive review rounds, merge sync, cleanup, and
pruning. Parts A and B roll
back by deploying the previous release; plans written by the new code carry a `backend` hint
that the old code ignores and resolves through its existing `sandbox` and `backend` checks.

## Open Questions

None.
