## Context

Pull-request work is already generic. `PullRequestKind` (`work_kinds/pull_request/kinds.py`)
is a frozen description of one kind, and `PullRequestHandler` and its collaborators (launch,
outcome, readiness, review, sync, cleanup, blocked) are driven by it. `registered()` returns
`(FIX, FEATURE)`. Several things already derive from `registered()`:

- the handler map in `work_kinds/__init__.py`;
- the doctor group list and per-kind readiness loop in `operations.py`;
- status slot lines (`_slot_lines`) and pending-sync lookups;
- the staged-file set in `launch.STAGED_FILES`;
- retention, terminal release, and cleanup lookups.

The store enforces one nonterminal run per kind with the partial unique index
`one_nonterminal_run_per_kind ON run(kind)`, so a new kind value gets its own slot with no
schema change. `scripts/slots.sh` already treats any `<kind> slot:` line generically, and the
deploy's host-attempt check counts every nonterminal non-eval run.

A small number of places still name `fix` or `feature` and need attention:

| Site | Current behaviour | Change |
| --- | --- | --- |
| `supervisor._load_result` | maps `fix` and `feature` to outcome contracts when `result.json` is missing | add `task` → `factory-task/1` (or `factory-review/1` for `reason == "review"`), and include `factory-task/1` in the `kind is None` list |
| `watch/detect.py` | `run["kind"] in {"fix", "feature"}` for PR-READY | use `{d.kind for d in registered()}` |
| `readiness.check_readiness` | "no targets" diagnostic only for `feature` | emit it for every non-`FIX` kind |
| `handler.py` Ready handoff | non-writer explanation comment only for `feature` | emit it for every non-`fix` kind, with marker `agent-factory-<kind>-handoff:v1` (the feature marker string is unchanged) |
| `handler.py` decline presentation | fix decline posts reasons only | for `task` (non-review) append "No branch was pushed." |
| `launch.py` host script banner | literal `factory-fix:` | use `definition.workflow_name` |
| `.claude/skills/factory-assign/assign.py` | kinds `fix`, `feature`, `eval` | add `task` |

All other `kind == "feature"` branches stay feature-only: definition resume, checkpoints,
the archive, `base_head`, review description restore, attention ids, and `verify-change`
validation. A task takes the same `else` path a fix takes.

The fix workflow (`factory-fix-v1.0.yaml`) inlines its implement, validate, finalize, and
annotate steps. The review workflow delegates implementation to `factory-implement-v1.0.yaml`,
which runs implement, validator gate, repair, recheck, `builtin:core/finalize-pr`, and
write-result. `builtin:core/finalize-pr` (Agent Runner) has no title parameter.
`record-triage.sh` hard-codes the `fixable` field and contract `factory-fix/1`.

## Goals / Non-Goals

**Goals:**
- Add the `task` kind by registering one more `PullRequestKind`, plus a new packaged
  workflow, without changing Bug or Feature behaviour.
- Enforce the task risk boundary at triage, through deterministic gate-exercise checks, and on
  every review round.
- Make `chore:` commits and titles deterministic rather than prompt-dependent where that is
  cheap.
- Keep duplication low, because the motivating tasks add duplication gates to this
  repository. Generalize the config parsing and scripts the new kind needs instead of
  copying them.

**Non-Goals:**
- Docker or Fly execution for tasks.
- Automatic routing of Task-typed issues. Routing already treats an untyped or Task-typed
  issue the same way, so `routing.py` needs no code change.
- Changes to Agent Runner builtins.
- Refactoring the fix workflow onto `factory-implement`.

## Approach

### 1. Configuration (`config.py`)

- `RoutingConfig.task_type: str = "Task"`, parsed with `_optional_string(routing, "task_type",
  "routing", "Task")`.
- `TaskConfig(defaults: Mapping[str, str], contract: str = "factory-task/1")` and
  `SharedConfig.task: TaskConfig | None = None`. `_feature_shared_config` becomes
  `_optional_kind_shared_config(raw, section, default_contract, cls)`, used by both `[feature]`
  and `[task]`. Wrong value types raise `ConfigurationError` naming `task.*`.
- `TaskLocalConfig(limits: FixLimitsConfig, schedule, execution: Literal["host"] = "host",
  minimum_free_gib)` and `LocalConfig.task`. `_feature_local_config` is refactored into
  `_host_kind_local_config(raw, section, default_limits)`, which returns the limits, schedule,
  floor, and host-only execution check. The feature and task parsers become thin wrappers
  with their own defaults: feature 1800/21600/28800, task 900/7200/10800. A non-`host`
  execution value raises `task.execution does not support '<mode>'; use 'host'`.
- `config/codagent.toml` gains `task_type = "Task"` under `[routing]`, and:

  ```toml
  [task]
  contract = "factory-task/1"

  [task.defaults]
  lead = "claude:claude-sonnet-5-5:high"
  implementor = "codex:gpt-6-sol:medium"
  tester = "claude:claude-sonnet-5:medium"
  ```

### 2. Kind definition (`kinds.py`)

```python
TASK = PullRequestKind(
    kind="task",
    unit_key="task",
    noun="Task",
    item_noun="task",
    issue_type=lambda shared: shared.routing.task_type,
    workflow_name="factory-task",
    workflow_file="factory-task-v1.0.yaml",
    staged_files=TASK_STAGED_FILES,
    contract=lambda shared: shared.task.contract if shared.task is not None else "factory-task/1",
    outcome_file="task-outcome.json",
    branch_prefix="factory/task",
    sync_marker="task-sync",
    allowed_modes=("host",),
    roles=("lead", "implementor", "tester"),
    doctor_groups={"host": "task-host"},
    reconcile=ReconcilePolicy.SETTLE_ON_OPEN_PR,
    local=lambda local: local.task,
    defaults=lambda shared: shared.task.defaults if shared.task is not None else {},
    targets=lambda shared: shared.fix.targets,
    enabled=lambda shared: shared is not None and shared.task is not None,
)


def registered() -> tuple[PullRequestKind, ...]:
    return (FIX, FEATURE, TASK)
```

`TASK_STAGED_FILES` lists `factory-task-v1.0.yaml`, `check-contract.sh`, `record-triage.sh`,
`record-outcome.sh`, `check-gate-exercises.py`, and `annotate-chore-pr.sh`.
`launch.STAGED_FILES` picks these up automatically.

`factory-implement` now calls the guard. So `factory-task-guard-v1.0.yaml`,
`task-scope-floor.py`, `record-scope.sh`, `check-chore-subjects.py`, and
`normalize-chore-commits.py` join `launch.REVIEW_WORKFLOW_SCRIPTS`, which is staged for every
kind, as the review and implement files already are.

`TASK` goes last so the existing status line order and slot output for eval, fix, and
feature are unchanged.

`enabled` gates only handoff and new admission, as it does for `feature` today. Settled or
running task claims continue through review, sync, and cleanup when `[task]` is removed.

### 3. Packaged workflow `factory-task-v1.0.yaml` (contract `factory-task/1`)

Its parameters match the fix workflow: `issue_file`, `branch_name`, `contract_version`, and
`artifact_dir`. It has three sessions: lead, implementor, and tester.

```
check-contract ─ check-clean-tree ─ triage(lead) ─ record-triage ─ [repair/recheck]
  └─ implement (skip unless doable)
       create-branch
       implement-task(implementor)          no TDD; writes task-choices.json
       initial validator gate ─ repair ─ recheck
       exercise-gates(tester) ─ verify-gate-exercises(lead) ─ check-gate-exercises
         └─ [repair-gates(implementor) ─ re-exercise ─ re-verify ─ recheck]
       test-flows(tester)                   only if triage.user_visible
       review-task(lead)                    diff, evidence, task-scope-floor report
       address-findings(implementor)
       final validator + clean-tree gate ─ repair ─ recheck
       prepush-guard (factory-task-guard, mode=prepush, base=recorded target commit)
                                            binding: crossing → needs-input, nothing pushed
       normalize-chore-commits              rewrite unpushed subjects to chore:
       record-prefinalize-head
       finalize-pr (builtin, 3 CI cycles) ─ mark-ci-failed
       postfinalize-guard (factory-task-guard, mode=postfinalize)
                                            only if HEAD moved during finalize; crossing or
                                            failed re-exercise → failed, PR left open
       record-pr-details
       annotate-chore-pr                    Refs + claim marker, chore: title, evidence section
  record-outcome ─ verify-outcome
```

`builtin:core/finalize-pr` cannot be stopped from pushing a CI repair: its `fix-pr` step
commits and pushes inside the loop, and the Runner rejects `ci_fix_cycles` below 1
(`resolveMaxParam` requires a positive integer). The workflow therefore keeps the builtin,
as the issue specifies, and guards it from both sides. Every commit made before
finalization passes the binding pre-push guard. Every commit finalization adds is checked
afterwards by the post-finalize guard, and a crossing there turns the outcome into `failed`
with the PR open for a human, the same treatment CI that stays red gets. No task attempt
can therefore report `pull-request` for a head that skipped a guard.

- **Triage output.** Triage returns one JSON object:

  ```json
  {
    "doable": true,
    "reasons": [],
    "plan": "...",
    "choices": ["..."],
    "gates": [{"name": "...", "command": "...", "violation": "..."}],
    "user_visible": false
  }
  ```

  The prompt lists the decline criteria, the bounded-choice rule, the
  toolchain-versus-release boundary, the "belongs in a Bug or Feature" routing, and how to
  read re-attempt comments, all from `factory-task-execution`. The prompt also states the
  agent-factory#74, agent-validator#174, and agent-evals#53 cases from the proposal as
  worked examples.
- **`record-triage.sh` generalization.** It gains three optional inputs:
  - `contract`, default `factory-fix/1`;
  - `accept_field`, default `fixable`;
  - `decision_path`, which, when set, writes the parsed decision object there.

  The task workflow passes `factory-task/1`, `doable`, and
  `{{artifact_dir}}/task-triage.json`. With the defaults, the fix workflow's behaviour and
  output are byte-for-byte unchanged. A decline writes `task-outcome.json` with
  `needs-input` before `create-branch` runs, so no branch exists.
- **Implementation prompt.** It does not ask for implement-with-tdd. It tells the
  implementor to run the repository's tests, linters, and `agent-validator`, to follow the
  bounded-choice rules (meet the target if measured within it, otherwise set the measured
  baseline and never loosen it), and to write `{{artifact_dir}}/task-choices.json` as
  `[{choice, evidence, result}]`. Commit subjects are `[<step>] chore: …`.
- **Gate exercises.** This step is skipped when `task-triage.json` lists no gates.
  1. The tester runs each gate's triage-named command on the delivered tree, plants the
     triage-described violation, saves `git diff` of the plant to
     `{{artifact_dir}}/gates/<name>/planted.patch`, reruns the same command, and reverts.
  2. Both outputs are kept as `positive.log` and `negative.log`. The tester records
     `[{name, command, positive: {exit, log}, negative: {exit, log, patch}}]` in
     `{{artifact_dir}}/gate-exercises.json`.
  3. The lead, a different session from the tester who planted the violation, then reads
     each patch and negative log. It writes `gate-verdicts.json` as
     `[{name, confirmed, diagnostic, criterion_met, reason}]`. `diagnostic` is a verbatim
     line from the negative output that identifies the planted violation, and
     `criterion_met` says whether the plant satisfies any issue-stated rejection criterion.
  4. `check-gate-exercises.py` does the deterministic part. It prints `passed` only when all
     of these hold:
     - every gate in `task-triage.json` has an entry whose positive and negative `command`
       both equal the triage-named command;
     - the positive exit is 0 and the negative exit is non-zero;
     - `planted.patch` is non-empty;
     - the verdict is `confirmed` and `criterion_met`, and its `diagnostic` occurs verbatim
       in `negative.log` and not in `positive.log`;
     - `git status --porcelain` is empty.

  A non-zero exit caused by an unrelated error therefore cannot count as proof: it has no
  confirmed diagnostic that is present only in the negative output. On failure the
  implementor repairs the gate once, and the exercise, verification, and check run again.
  A second failure sets `validator_status=failed` with the reason "gate <name> does not
  reject violations". The outcome is then `failed` before any push, through the same
  `record-outcome.sh` path as a red validator.
- **Scope floor.** `task-scope-floor.py <base>` lists changed paths that definitely cross the
  boundary:
  - `openspec/specs/**`;
  - `.github/workflows/*` files whose name or `on:` triggers mention release, publish,
    deploy, or tag pushes;
  - `CODEOWNERS`.

  Its output is evidence for `review-task`, so the lead can raise a crossing early as a
  finding. It is also the deterministic half of the binding guard below.
- **`factory-task-guard-v1.0.yaml` (shared sub-workflow).** Its parameters are `base`,
  `mode` (`prepush` or `postfinalize`), `artifact_dir`, and `gates_file` (optional). It runs:
  1. `task-scope-floor.py {{base}}`;
  2. `task-scope-review` (lead), which judges the complete `git diff {{base}}..HEAD`
     against the task boundary, treating the floor's paths as definite crossings, and
     returns `{crossed, reasons}`;
  3. `record-scope.sh`, which writes `{{artifact_dir}}/scope-<mode>.json` and prints
     `clean` or `crossed`.

  In `postfinalize` mode with a `gates_file`, the guard also reruns the gate exercise,
  verification, and check for the gates whose files the post-finalize commits touched. It
  then runs `check-chore-subjects.py`, which lists any added commit subject without
  `chore:` into `{{artifact_dir}}/nonchore-commits.json`. Those commits are already
  pushed, so they are recorded and not rewritten.
  - The **initial attempt** calls the guard with `mode=prepush` and `base` set to the
    recorded target commit. This runs after address-findings and the final validator gate,
    so a crossing that the implementation or the finding repair introduced is caught. A
    crossing records `needs-input` naming it, through `record-outcome.sh`'s new
    `scope_path` input, and skips normalize and finalize, so nothing is pushed.
  - After finalize, if `git rev-parse HEAD` differs from the recorded pre-finalize head, the
    initial attempt calls the guard with `mode=postfinalize`, `base` set to the recorded
    target commit, and the triage gates file. A crossing or a failed re-exercise makes
    `record-outcome.sh` record `failed` with those reasons and the PR reference, leaving the
    PR open.
- **`normalize-chore-commits.py <base>`.** This runs after the final gate, while nothing is
  pushed. It rebuilds the linear commits in `base..HEAD` with `git commit-tree`, reusing
  each commit's tree, author, and dates. Each subject keeps its optional `[step]` marker. A
  leading conventional type (`type(scope)!:`) is replaced with `chore:`, and `chore: ` is
  inserted when there is none. The branch ref then moves to the new head. Trees are
  identical, so the working tree and validator result are unaffected. The script refuses,
  changing nothing, when the range contains a merge commit or the branch has an upstream.
- **`annotate-chore-pr.sh`.** It replaces the fix workflow's inline annotate block for this
  workflow and does three things through the REST API, which `gh pr edit` cannot do with a
  fix token:
  - prepends `Refs #N` and the claim marker, as the fix workflow does;
  - sets the title to `chore: <rest>`, stripping any other conventional prefix, when it
    does not start with `chore:`;
  - appends a section marked `<!-- agent-factory:task-evidence -->` listing
    `task-choices.json` and the gate-exercise results.

  A title PATCH failure writes `{{artifact_dir}}/retitle-failed` with the error and exits 0,
  so the outcome is unchanged. It is idempotent on the markers.
- **Outcome.** `record-outcome.sh` is reused with `contract: factory-task/1` and
  `outcome_path: {{artifact_dir}}/task-outcome.json`. `outcome.py` already derives
  `task-outcome.json` from the contract name. Task outcomes use the fix shape, with no
  feature extras.

### 4. Review rounds on task pull requests

The contract stays `factory-review/1`. The review file already carries `kind`.

- `factory-review-v1.0.yaml`:
  - A `read-kind` step captures `review_kind` from `review.json`, and `is_task` is `true`
    or `false`.
  - The triage prompt gains a task paragraph. On a task pull request, judge every requested
    change against the task boundary. Any out-of-scope request goes into `needs_input`,
    naming the request and "belongs in a Bug" or "belongs in a Feature". A widening change
    is made only while it stays in scope. The existing rule "when needs_input is non-empty,
    make no code changes" and the existing `respond` skip on `needs-input` give "implement
    nothing, push nothing, post no replies".
  - It passes `task_scope: "{{is_task}}"` and `scope_base` (the PR head from `review.json`)
    to `factory-implement`.
  - `respond` is also skipped when `{{artifact_dir}}/scope-prepush.json` records a
    crossing.
- `factory-implement-v1.0.yaml` gains two params: `task_scope`, default `"false"`, and
  `scope_base`, default `""`. With the defaults, every existing step behaves as today.
  - `implement-plan` is skipped when `task_scope` is `true`. A sibling
    `implement-task-plan` step runs instead, with the no-TDD prompt and `chore:` commit
    subjects.
  - After the validation gate passes, `factory-task-guard` runs with `mode=prepush` and
    `base={{scope_base}}`. A crossing writes `scope-prepush.json`, which `respond` also
    skips on, and sets `scope_status=crossed`.
  - `normalize-chore-commits.py {{scope_base}}` runs when `task_scope` is `true`, before
    finalize. It only touches the round's unpushed commits.
  - `finalize-pr` is skipped when `scope_status=crossed`.
  - When finalize moved HEAD, `factory-task-guard` runs with `mode=postfinalize` and no
    gates file.
  - `write-result` adds `"scope": {"prepush": ..., "postfinalize": ..., "reasons": [...]}`.
- `record-review-outcome.sh` maps a pre-push crossing to `needs-input` and a post-finalize
  crossing to `failed`, each with the reasons, before its existing validator and CI
  mapping.
- The task round goes through the task slot, window, and limits via the existing
  kind-generic review admission. Task pull requests are not base-merged: the handler
  writes `base_head` into `review.json` only for `feature`, and `review-merge-base.sh` prints
  `none` without merging when `base_head` is absent.

### 5. Reporting and board

Board mapping and durable delivery are already generic. Task-specific presentation:

- The acceptance comment reads "Task inputs accepted and frozen." through the existing
  `noun` template.
- A task triage decline appends "No branch was pushed." in the handler's decline body,
  next to the existing feature `preflight` line.
- The host note is already appended to every host outcome.

### 6. Operations, skills, and docs

- **Doctor.** The `task-host` group is already listed through `registered()`. Readiness
  runs through `PullRequestHandler.readiness` → `check_readiness(definition=TASK)`, which
  covers the host diagnostics (including the Validator build), free space, the credential,
  the task contract marker, and the role profiles. `_validate_diagnostic` runs
  `agent-runner -validate` on `factory-task-v1.0.yaml` and `factory-review-v1.0.yaml`.
- **Status.** Slot lines, blocked lines, and waiting-review lines are generic over
  `registered()` and claim kind. Tests confirm task lines appear.
- **`assign.py`.** Add `task_targets` (the fix targets when `shared.task` is set), classify
  by `routing.task_type`, add `task` to `--apply` choices and to the `kind in {...}` sets,
  and keep refusing a mismatched `--apply`. Update `SKILL.md` (Task rules, `--apply task`)
  and the factory-status skill's slot grep to include `task slot`.
- **Docs.** `AGENTS.md` and `docs/operations.md` cover the topics listed in the
  `factory-operations` "Document the task kind" requirement.

### 7. Failure handling summary

| Failure | Result |
| --- | --- |
| Triage output unparseable twice | `recheck-triage` fails, which is a technical failure, then the recovery retry |
| Triage declines | `needs-input` before the branch exists; card stays in Running with the label |
| Validator, clean-tree, or gate-exercise red after repair | `failed` before push |
| CI red after 3 cycles | `failed`, PR left open |
| Retitle PATCH fails | `retitle-failed` evidence; outcome unchanged |
| `normalize-chore-commits` refuses (merge or upstream) | logs the reason and leaves commits unchanged; finalize continues |
| Review round out of scope at triage | `needs-input`, nothing pushed, no replies |
| Initial attempt's full diff crosses the boundary (implementation or finding repair) | `needs-input` from the pre-push guard, nothing pushed, card in Running with the label |
| Finalize's CI repair adds a crossing or breaks a gate exercise | `failed` from the post-finalize guard, PR left open, card in Review |
| Finalize's CI repair adds a non-`chore:` commit | recorded in `nonchore-commits.json` and the PR's task-evidence section; outcome unchanged |
| Review round diff crosses the boundary before push | `needs-input` with `scope-prepush.json` reasons, nothing pushed, no replies |
| Review round's CI repair adds a crossing | `failed`, PR left open |
| `task-outcome.json` missing, invalid, or wrong contract | technical failure through `verify-outcome` and the outcome reader |

## Decisions

- **One more `PullRequestKind`, not a new handler.** The generic handler already supplies
  every lifecycle behaviour the specs require. A task differs from a fix only in its
  workflow, contract, type, config section, and host-only mode.
- **A new workflow file rather than parameterizing `factory-fix`.** The triage criteria,
  the absence of TDD, the gate exercises, and the chore enforcement differ enough that a
  shared file would be riddled with `skip_if` branches and put the fix contract at risk.
  The shared mechanics live in scripts: generalized `record-triage.sh` and reused
  `record-outcome.sh` and `check-contract.sh`.
- **Deterministic chore enforcement.** Rewriting unpushed commit subjects and retitling
  after finalize makes the `chore:` requirement hold regardless of agent compliance,
  without changing Agent Runner. Rewriting is limited to unpushed linear history, so no
  force-push is ever needed.
- **Scope floor plus agent judgment, binding before every push.** Paths alone cannot detect
  behaviour changes, and agents alone can miss a release-workflow edit. The floor catches
  unambiguous crossings cheaply, and the lead judges the rest. The same guard sub-workflow
  runs on the complete diff before any push, in both the initial attempt and review rounds,
  and again after finalize whenever CI repair moved the head.
- **Keep `builtin:core/finalize-pr` and guard it, rather than replacing it.** The issue
  names the builtin, and the Runner cannot run it with zero repair cycles. A factory-owned
  push and CI loop would duplicate the builtin's CI semantics. The cost is that a CI repair
  can push an out-of-scope or non-`chore:` commit before it is detected. The post-finalize
  guard makes that a `failed` outcome with the PR open, so it is never reported as a good
  pull request, and human merge remains the control.
- **The gate-exercise check is a script over agent-recorded runs, with an independent
  verdict.** The tester plants and reverts violations, since only an agent can construct a
  meaningful one. The lead independently confirms that the negative output identifies the
  plant and meets the issue's criterion. The script binds both to the triage-named command,
  the saved patch, and a diagnostic that appears only in the negative output, so an
  unrelated failure or a different command cannot pass.
- **Keep `factory-review/1` and branch on `review.json` `kind`.** Running review rounds and
  admitted claims need no contract migration. Fix and feature prompts and steps are
  unchanged apart from added text that applies only to tasks.

## Risks / Trade-offs

- **Prompt-judged boundaries.** "Changes runtime behaviour" is a judgment. The risk is
  mitigated by the explicit criteria and worked examples in the triage prompt, the lead
  review with floor evidence, review-round scope enforcement, and human merge. Residual
  risk is accepted.
- **Gate commands vary by repository.** The exercise depends on triage naming a runnable
  command. If triage names none for a new gate, the check fails rather than passing
  silently, which costs at most one repair cycle.
- **CI repair inside the builtin.** A `fix-pr` push can land an out-of-scope or
  non-`chore:` commit before the post-finalize guard sees it. This is accepted because the
  builtin is the issue's specified finalizer and cannot run with zero cycles. The outcome
  becomes `failed` with the PR open, and non-`chore:` subjects are disclosed in the PR's
  evidence section.
- **Commit rewriting.** A bug in `normalize-chore-commits.py` could corrupt the history
  before push. It reuses trees verbatim and refuses on merges or upstreams. Tests compare
  trees, authors, and dates before and after.
- **Widening `factory-implement` and `factory-review`.** A mistake could change fix or
  feature rounds. All new steps are gated on `task_scope`/`is_task`, the defaults keep
  existing behaviour, and the existing review contract tests run unchanged.
- **Rollback with open task claims.** An older release has no `task` handler, so open task
  claims would be ignored. This is documented, and operators settle or cancel them before
  rolling back. The store needs no schema change in either direction.

## Migration Plan

1. Ship the code and the `config/codagent.toml` changes together in one PR. The release's
   own shared config enables `[task]` once it is deployed by `scripts/deploy.sh`.
2. No database migration: the `run.kind` and `claim.kind` columns accept `task`, and the
   per-kind unique index gives it a slot.
3. **Before merging,** audit the queue the new kind would take. List the open issues of
   type Task in every `[fix] targets` repository whose board Status is Ready, for example
   with `gh project item-list` filtered on type and status. Move each one, other than
   agent-factory#74, to Backlog, or record Paul's approval to let it run. The Ready handoff
   assigns any writer-authored Task in Ready once `[task]` is live, so this audit is the
   rollout guard.
4. After deploy, `doctor` shows the `task-host` group. Assign agent-factory#74 with
   `factory-assign --apply task` as the first live task.
5. **Rollback.** Settle or cancel open task claims, then deploy the previous ref. Its shared
   config has no `[task]`, so no new Tasks are handed off.

## Testing Strategy

- **Unit:**
  - the `TASK` definition fields and `registered()` order;
  - config parsing, covering defaults, a wrong-type `task.defaults` or limits value, a
    non-host execution value, absence of `[task]`, and a custom `task_type`;
  - `assign.py` task classification and `--apply` refusals.
- **Integration (pytest, existing fakes):**
  - task handoff and admission, including a non-writer comment once, the unconfigured
    repository, and the disabled kind;
  - ranking, and independence from the fix and feature slots;
  - Bugs and Features admitted unchanged when `[task]` is present;
  - retry gestures;
  - side-effect reconciliation;
  - the `task-outcome.json` contract, valid and invalid, through `read_interpreted_outcome`
    and `supervisor._load_result`;
  - decline presentation ("No branch was pushed.") and board mapping;
  - review admission through the task slot;
  - PR-READY and FAILURE detection for task runs;
  - doctor `task-host` group present or absent;
  - status task slot and blocked lines;
  - merge sync for a task claim.
- **Workflow scripts:**
  - `record-triage.sh` with the defaults (fix output unchanged) and with task inputs: a
    decline writes `task-outcome.json` and `decision_path`;
  - `check-gate-exercises.py`: a missing gate, a command different from triage's, a
    negative exit of 0, an unrelated non-zero failure whose verdict diagnostic is absent
    from the negative log or also present in the positive log, an unconfirmed verdict or
    one with `criterion_met` false, an empty patch, a dirty tree, and the
    pass case;
  - `task-scope-floor.py` path classification;
  - `normalize-chore-commits.py` on a temporary repository: subjects rewritten, trees,
    authors, and dates unchanged, and refusal on a merge or upstream;
  - `annotate-chore-pr.sh` with a fake `gh`: title fixed, idempotent, and PATCH failure
    tolerated;
  - `record-review-outcome.sh` mapping a pre-push crossing to `needs-input` and a
    post-finalize crossing to `failed`;
  - `record-scope.sh` and `task-scope-floor.py` on a diff that edits a release workflow,
    `record-outcome.sh` with `scope_path` (pre-push crossing → `needs-input`, post-finalize
    crossing → `failed` keeping the PR), and `check-chore-subjects.py`;
- **Catalog:**
  - the staged file set includes the task files;
  - every packaged workflow's contract marker matches;
  - `agent-runner -validate` accepts `factory-task-v1.0.yaml`,
    `factory-task-guard-v1.0.yaml`, and the modified review and implement workflows, in the existing e2e catalog test where the Runner is
    available.
- **Regression:** the existing fix, feature, review-contract, and watch suites pass
  unmodified.

## Open Questions

None.
