## Context

Iteration 1 shipped an eval-only factory: `Controller` takes `EvalDefaults` in its constructor, `_eligible` requires `Type=Eval`, `accept` writes `kind="eval"`, `_next_unit` and `_settle_if_complete` reason about repetitions, and `runtime.cycle` constructs `AndSceneAdapter`, `GitWorktreeManager`, and eval reporting directly. The `run` table has a global partial unique index (`one_nonterminal_run`) that enforces one attempt across the service. The supervisor is already kind-agnostic: it launches an `ExecutionPlan` (argv, cwd, allowed environment, progress sources, ownership hints) in a detached session, discovers containers by their `/artifacts` mount, and records results from files.

Agent Runner has no headless issue-to-fix workflow. It has the pieces: `core/implement-task`, `core/run-validator` (three repair cycles), `core/finalize-pr` (push, ready-for-review, three CI fix cycles), autonomous headless steps with `capture`, and `skip_if: 'sh: …'` against captured values. `scripts/sandbox-run.sh` builds the current Runner checkout into an image (`--image` selects the tag; the `IMAGE` variable is also honored), mounts `/artifacts`, forwards named environment variables and auth files, and accepts `--docker-run-arg` for extra mounts.

The and-scene suite's linked-worktree Git metadata mounts live in its own `run.sh`, not in `sandbox-run.sh`. The Mac mini has 16 GiB RAM with Docker capped near 8 GiB and about 13 GiB free disk. None of the five target repositories protects `main`.

The specifications under `specs/` are approved. This design covers their implementation; `test-plan.md` will define verification.

## Goals / Non-Goals

**Goals:**
- Extract a `WorkKindHandler` seam so the controller and runtime are generic, with eval behavior and its tests unchanged.
- Add the `fix` work kind: routing, admission, checkouts, sandboxed Agent Runner workflow, outcome mapping, blocked loop, post-merge sync.
- One execution slot per kind, enforced in SQLite, with per-run Docker image tags and a Docker memory headroom check.
- Replace the harness commit pin with branch resolution at admission for evals.
- One companion Agent Runner workflow, `core/factory-fix-v1.0.yaml`, with a versioned outcome contract.

**Non-Goals:**
- Running fixes outside Docker. Paul chose to keep the sandbox: the agent runs with permission prompts bypassed, and Docker is what keeps it away from his credentials, other repositories, and the factory state.
- LLM triage of incoming issues before they reach the board, dispatch caps, controller-side diff inspection, auto-merge, branch or PR cleanup, eval of fix PRs.

## Approach

### Components

```
routing.py            Router: eval rule + bug rule (+ factory-hold bypass), receipts unchanged
config.py             SharedConfig: routing.bug_type, routing.hold_label, eval.harness_ref,
                      fix.{targets[], branches, defaults, contract}; LocalConfig: fix limits,
                      fix window, memory_reservation_gib, credentials.fix_environment,
                      repositories.working_clones
work_kinds/base.py    WorkKindHandler protocol + shared dataclasses
work_kinds/eval/      EvalHandler wrapping today's parse/freeze/plan/read/settle code
work_kinds/fix/       FixHandler, mirrors, clones, launcher script, outcome reader,
                      merge sync
controller.py         generic: handlers by kind, per-kind slots, gestures via handler
runtime.py            cycle: consume → reconcile per card/handler → syncs → admit per kind
store.py              schema v2: run.kind, per-kind unique index, lifecycle "blocked"
supervisor.py         unchanged except image tag recorded from plan ownership hints
operations.py         doctor/status per kind; docker memory probe
github.py             + set_label(name, on), get_pull_request(url|number), close_issue,
                      list_comment_records already returns author + created_at
```

### The handler interface

```python
class WorkKindHandler(Protocol):
    kind: str                                   # "eval" | "fix"
    def snapshot(self, card, client, shared) -> RequestSnapshot | None   # eligibility
    def accept(self, snapshot, store, resolve) -> ClaimDraft | Feedback  # parse + freeze
    def readiness(self, local, shared) -> list[Diagnostic]              # kind-specific doctor
    def prepare(self, claim) -> Preparation                              # worktrees | clones
    def next_unit(self, claim, runs) -> tuple[str | None, str]           # rep-N | "fix"
    def plan(self, claim, run, preparation) -> ExecutionPlan
    def read_result(self, run) -> AttemptResult
    def classify(self, run, result) -> Classification   # technical | settled | blocked | quota
    def settle(self, claim, runs) -> Outcome | None
    def presentation(self, claim) -> ClaimPresentation  # status, verdict, labels
    def report_events(self, claim, run, result) -> list[Event]
    def gesture(self, claim, card, comments) -> "fresh" | "unblock" | None
    def limits(self, local) -> SupervisionLimits
    def window(self, local) -> ScheduleConfig
    def providers(self, claim) -> set[str]               # for quota-hold scoping
    def cleanup(self, claim) -> None
```

`Controller` keeps admission serialization, claim and run persistence, recovery accounting, cancellation, presentation delivery, and event delivery. Everything that today mentions repetitions, `Type=Eval`, `and-scene`, scores, or `EvalDefaults` moves into `EvalHandler`. `ClaimPresentation` gains `labels: Mapping[str, bool]` so a handler can request `needs-input` on or off; the runtime applies labels through one `set_label` call per change with a delivery receipt, the same pattern as `needs-input` today.

The extraction is the first implementation task and must leave `tests/` passing without edits, except for test setup that constructs `Controller` directly and now passes `{"eval": EvalHandler(...)}`.

### Persistence

The store is at `SCHEMA_VERSION = 3` today. This change adds a v3→v4 migration inside the existing `user_version` guard, in one transaction:

```sql
ALTER TABLE run ADD COLUMN kind TEXT NOT NULL DEFAULT 'eval';
UPDATE run SET kind = (SELECT kind FROM claim WHERE claim.id = run.claim_id);
DROP INDEX one_nonterminal_run;
CREATE UNIQUE INDEX one_nonterminal_run_per_kind
  ON run(kind) WHERE status IN ('reserved','running','observing');
-- an active global quota hold becomes a provider-scoped hold for codex
INSERT OR REPLACE INTO settings(namespace, key, value_json, updated_at)
  SELECT namespace, 'quota:codex', value_json, updated_at FROM settings
  WHERE namespace = 'admission' AND key = 'quota';
DELETE FROM settings WHERE namespace = 'admission' AND key = 'quota';
PRAGMA user_version = 4;
```

Before migrating, the store copies `state.sqlite3` to `state.sqlite3.v3.bak` beside it (skipped if that file already exists), so rollback to the previous release is a documented copy-back while paused. The prior binary refuses a v4 database, which is the intended fail-closed behavior.

`reserve_run` copies the claim's kind onto the run. `nonterminal_runs(kind=None)` filters when asked. The admission advisory lock stays global; it protects the reserve step only, so it does not serialize execution.

Claim `lifecycle` gains `blocked`. Fix claims use one unit key, `fix`; attempts are numbered as today with `reason` in `{initial, recovery, unblock}`. The frozen spec for a fix is:

```json
{"version": 1, "kind": "fix",
 "target": {"repository": "Codagent-AI/agent-runner", "branch": "main"},
 "revisions": {"target": "<sha>", "runner": "<sha>", "skills": "<sha>"},
 "roles": {"lead": "cursor:…", "implementor": "…", "tester": "…"},
 "contract": "factory-fix/1"}
```

Fix results stored on the run: the parsed `fix-outcome.json` plus `image_tag`, `branch_name`, and `container`.

### Cycle

```
1 consume results        each handler reads results for its terminal-unrecorded runs
2 reconcile per card     claims for the card → cancel on closure → handler.gesture
                         (fresh → supersede; unblock → reserve attempt) → report
                         (status, verdict, refs, labels, events) → cleanup on Done
3 merge syncs            settled fix claims in Review with a recorded PR (see below)
4 admit per kind         for kind in (eval, fix): if slot free and handler.window open
                         and no global hold and no provider hold for handler.providers:
                         walk cards in board order, handler.snapshot → accept → prepare
                         → reserve → plan → launch_supervisor; stop at first launch
```

Holds: `settings("admission","quota")` becomes `settings("admission","quota:<provider>")`; a hold blocks a kind only if `handler.providers(claim)` intersects the held providers. Readiness holds are stored per kind under `settings("runtime","readiness:<kind>")`. Pause, disk, and memory are global.

Memory headroom: `docker info --format '{{.MemTotal}}'` minus the sum of `docker stats --no-stream --format '{{.MemUsage}}'` current values, compared with `limits.memory_reservation_gib` (default 3). A probe failure holds admission with an explanation rather than assuming headroom.

### Routing

`Router.route` gains a second rule after the eval rule:

```
is_bug = repo in general_sources and issue_type == routing.bug_type
         and item is an issue (payload has no pull_request) 
if is_bug and routing.hold_label in labels: initialize owner=human, status=backlog
elif is_bug and permission in writers:      initialize owner=factory, status=ready
```

Receipts and re-delivery behavior are unchanged. The caller workflows already run on issue events; deploying the rule means publishing the factory and bumping `FACTORY_REVISION` in the five callers. Existing issues are untouched until an event fires for them.

### Fix admission and checkouts

`FixHandler.snapshot` mirrors the eval one: `Owner=factory`, `Status=Ready`, native type `Bug`, open, no `needs-input` label, author permission from `get_permission`. Board order is preserved by `list_project_items`, so the first eligible card in the Bug group wins.

`accept`: fetch the target mirror, then resolve `origin/<branch>` in the mirror and in the local Runner and Skills checkouts through the existing `_resolve_revision`, and freeze all three. Mirrors live at `<root>/mirrors/<owner>__<repo>.git` and are created with `git clone --mirror` on first use. Fetching uses HTTPS with the App installation token through a temporary `GIT_ASKPASS` helper, the same trick the suite uses in the container; the token never lands on disk.

`prepare`: `<root>/clones/<claim>/<attempt>/{repo,runner,skills}` via `git clone --local --no-checkout` from the mirror (target) or the local checkouts (Runner, Skills), then `git checkout --detach <sha>`. Recorded in `claim.preparation` for cleanup. A recovery or unblock attempt gets a fresh set at the same commits.

Before reserving any attempt, `reconcile_side_effects`: `gh api repos/<repo>/branches/<branch_name>` and `gh pr list --head <branch_name> --state open`. Branch name is `factory/fix-<issue>-<claim8>`. An open PR settles the claim as `pull-request` without launching. A lookup failure holds the claim for the next poll.

### Launch

`work_kinds/fix/launch.sh` (factory-owned, invoked as the plan's argv, cwd = the Runner clone):

```
scripts/sandbox-run.sh
  --image agent-runner-factory:<run-id>
  --artifact-dir <evidence>
  --no-default-secrets                              # never load the Runner checkout's .sandbox-secrets.env
  --env-file <validated copy of credentials.fix_environment>   # exactly one variable: GH_TOKEN
  --mount-cursor-auth | --mount-claude-auth | --mount-codex-auth   # from fix roles
  --docker-run-arg --mount --docker-run-arg type=bind,source=<repo clone>,target=/workspace/repo
  --docker-run-arg --mount --docker-run-arg type=bind,source=<skills clone>,target=/workspace/skills,readonly
  -- <container script>
```

Container script: install the Skills clone the way the suite's `AGENT_SKILLS_BOOTSTRAP` does, write `/workspace/home/.agent-runner/settings.yaml` (headless autonomous backend) and `/workspace/repo/.agent-runner/config.yaml` (role profiles), configure the Git askpass helper from `GH_TOKEN`, then:

```
cd /workspace/repo
AGENT_RUNNER_NO_TUI=1 agent-runner run core/factory-fix \
   --param issue_file=/artifacts/input/issue.json \
   --param branch_name=factory/fix-<issue>-<claim8> \
   --param contract_version=factory-fix/1
```

Before launch the factory parses `credentials.fix_environment` itself: it must contain exactly one assignment, `GH_TOKEN=<value>`; any other variable name, a missing token, or a token equal to the App installation token fails readiness. The launcher writes a validated single-line copy under the run's private directory and passes that to `--env-file`, so `sandbox-run.sh` cannot forward anything else. The App token is only ever used by the controller for mirror fetches and never appears in the plan's allowed environment.

The factory writes `/artifacts/input/issue.json` before launch: repository, number, title, body, attempt number, prior factory PR if any, and the eligible comments (author permission in writers, created after the last decline; the factory's own comments are excluded). Progress sources for the supervisor: `/artifacts/factory-suite.log`, the Runner session directory under `/artifacts/agent-runner/` including `state.json`, `audit.log`, and `output/*`, and the Cursor and Claude session globs the eval plan already uses. Agent Runner writes step output and audit entries while `wait-ci` polls, so a CI wait counts as progress under the 15-minute inactivity limit; it does count toward the two-hour execution limit, which is why the CI loop is capped (below). Ownership hints carry the artifact path and `image_tag`.

The eval plan gets `IMAGE=agent-runner-factory:<run-id>` in its allowed environment so and-scene's `sandbox-run.sh` call builds under a per-run tag as well. Images are removed with clones on Done.

### The companion workflow

`core/factory-fix-v1.0.yaml` in agent-runner (delivered before the factory can launch):

```
params: issue_file, branch_name, contract_version
 1 check-contract     command   contract_version == "factory-fix/1" else fail
 2 check-clean-tree   command   clean tree at the recorded commit
 3 triage             lead, autonomous, capture: decision   closed JSON
                      {fixable, reasons, plan}
                      decline when: CLI/platform/dependency limitation the agent cannot change;
                      several viable solutions needing a human choice; no reproduction;
                      fix requires another repository; fix requires a non-trivial
                      specification change (belongs in a Feature; trivial inline spec
                      updates are fine)
 4 record-triage      script    if not fixable → write /artifacts/fix-outcome.json
                                {contract, outcome: "needs-input", reasons}; capture fixable
 5 implement          sub-workflow, skip_if: 'sh: test {{fixable}} != true'
    5a create-branch  command   git checkout -b {{branch_name}}
    5b fix            lead, autonomous  codagent:implement-with-tdd on the triage plan;
                                regression test; repo conventions; commit
    5c run-validator  core/run-validator-v1.0
    5d test-flows     tester, autonomous  codagent:test-flows on the changed flow
    5e address        lead, autonomous, skip_if no findings  fix, targeted checks, commit
    5f verify-clean   command
    5g finalize-pr    core/finalize-pr-v1.0 with ci_fix_cycles=1   push, ready for review,
                                one CI wait-and-fix cycle (the companion change adds this
                                parameter to finalize-pr, default 3, so interactive use is
                                unchanged); PR body: "Refs #<n>", claim marker, triage
                                summary, validator result. A validator still red after 5c's
                                repair cycles ends the sub-workflow before 5g with outcome
                                failed and no push.
 6 record-outcome     script    read PR url and final CI marker → fix-outcome.json
                                {contract, outcome: "pull-request" | "failed", pr, ci,
                                validator, reasons}
 7 verify-outcome     command   fix-outcome.json present with a valid outcome
```

`--until triage` runs triage alone for prompt tuning. The contract line `# factory-contract: factory-fix/1` at the top of the YAML is what `doctor` greps at the resolved Runner commit (`git show <sha>:workflows/core/factory-fix-v1.0.yaml`).

### Outcome contract

`/artifacts/fix-outcome.json`:

```json
{"contract": "factory-fix/1",
 "outcome": "pull-request" | "needs-input" | "failed",
 "reasons": ["…"],
 "pr": {"url": "…", "number": 214, "branch": "factory/fix-212-1a2b3c4d", "head_sha": "…"},
 "validator": {"status": "passed" | "failed" | "skipped"},
 "ci": {"status": "passed" | "failed" | "pending"}}
```

`FixHandler.read_result` maps: file absent, unparsable, or with an unexpected contract → technical failure; `needs-input` → `blocked`; `pull-request` → settled `pending-human-review`; `failed` → settled `failed`. Non-zero exit with a valid `pull-request` outcome still settles as a PR (the file is authoritative); non-zero exit with no file is technical.

### Blocked loop

On `needs-input`: lifecycle `blocked`, presentation `Running` + label `needs-input` on, event with the reasons, slot released because the run is terminal. Each poll, for blocked claims: `list_comment_records` → keep comments with `created_at` after the decline comment's timestamp whose author has writer permission (permission lookups cached per login for the cycle); the factory's own comments never count. If any eligible comment exists, or the card has been moved to Ready: label off, `reserve_run(reason="unblock")`, new attempt with the eligible comments in `issue.json`. Non-eligible comments are ignored entirely.

`_report` today forces every idle, unsettled claim to `Ready` regardless of what the controller presents. That fallback moves into `EvalHandler.presentation` (waiting eval claims present `Ready` with their deferral verdict), and the runtime applies the handler's presentation as authoritative for idle states. The generic correction rule then reads: if the card's column differs from the presented status, restore it and comment once, except where the handler's `gesture()` recognizes the drag. For a blocked fix claim that means Review or Done is corrected back to Running, Ready is an unblock, and ordinary queued-idle eval correction is unchanged.

Drag gesture: `gesture()` returns `fresh` when a settled fix claim's card is in Ready and `unblock` when a blocked claim's card is in Ready; the runtime supersedes through the existing `supersede_and_create` path with re-resolved commits. Eval cards keep the current behavior because `EvalHandler.gesture` only recognizes the cleared-Verdict rule.

### Merge sync

For settled fix claims whose stored outcome has a PR and whose `reporting.sync` is not complete, regardless of the card's column: `gh pr view <number> --json state,mergedAt`. When merged, in `repositories.working_clones[<repo>]`:

```
git status --porcelain --untracked-files=no      → non-empty ⇒ block("uncommitted changes")  (before any fetch)
current=$(git branch --show-current)             → empty ⇒ block("detached HEAD")
if current == main: git fetch origin && git merge --ff-only origin/main   → failure ⇒ block("local main diverged")
else: git fetch origin main:main                 → refuses a non-fast-forward or a main checked
                                                   out in any worktree ⇒ block with git's reason
      git merge-tree --write-tree HEAD refs/heads/main   → exit 1 ⇒ block("conflicts")
      git merge --no-edit main                            → failure ⇒ git merge --abort; block
```

`git fetch origin main:main` is deliberate: it is the one Git operation that refuses both a diverged local `main` and a `main` checked out in another of the clone's worktrees (agent-runner has eleven today), which are exactly the cases to block on, and it never rewrites a ref in a way that leaves another worktree with a phantom diff.

Success: close the issue if still open (`gh api … --method PATCH state=closed`) with a marker comment; closure automation moves the card to Done. Block: label `needs-input` on (unless the card is already Done), reason comment with a stable marker per reason, retry every poll; success later removes the label. Sync state is recorded on the claim (`reporting.sync`) so a restart never repeats a completed merge. The guarantee is that the working tree, index, and checked-out branch change only by the merge itself; fetched refs and objects may change. A missing clone blocks with that reason. The dirty check and the merge are not atomic: an edit made in the seconds between them can end up in a merge commit's parent; accepted, and noted in Risks.

### Doctor and status

Doctor adds, under a `fix` heading: each mirror fetchable; each working clone is a Git repository; `credentials.fix_environment` is owner-readable, contains exactly `GH_TOKEN` and nothing else, authenticates (`gh api user`), is not the App identity, is not an organization admin, and can read each target repo; the Runner branch head contains `core/factory-fix-v1.0.yaml` with the expected contract line; Docker memory allowance ≥ one reservation. Shared config with a leftover `harness_sha` key fails configuration loading with a message naming `harness_ref`.

Status prints one block per kind: slot holder or `free`, why waiting, blocked claims with their decline reason, pending syncs with their last failure, plus the global lines it prints today.

### Harness branch

`EvalConfig.harness_sha` → `harness_ref` (a branch name, default `main`; a commit SHA is rejected by configuration loading per the operations spec). `EvalHandler.accept` resolves it in the local evals checkout through `_resolve_revision` and freezes it as `revisions.evals`, which the adapter, the Refs rendering (`evals@<7>`), the frozen-inputs comment, and the suite's own `proof-metadata.json` (`agent_evals_commit`) already record, so the commit each eval ran with is visible in four places without new code. Cross-night comparability is no longer guaranteed by configuration; the recorded commit is the comparability key. `doctor`'s "loaded pinned harness" line becomes "harness branch <ref> → <sha>" using a resolve without fetch.

## Decisions

- **Extract first, then add.** The refactor lands as its own task with the eval tests as the regression net. Otherwise the fix work and the extraction would be one unreviewable diff.
- **One companion workflow.** Triage is a step inside `factory-fix`, gated with `skip_if`. Agent Runner owns the whole fix flow; the factory stays a dispatcher. `--until triage` covers prompt tuning.
- **Keep Docker.** Considered a plain local worktree; rejected because the autonomous agent runs with permission prompts bypassed and would otherwise act as Paul with his `gh` login, keys, sessions, and other repositories. The cost is a cached image build per run and the VM memory cap.
- **Per-run image tags for both kinds.** `sandbox-run.sh` already takes `--image`, and and-scene inherits `IMAGE` from the environment, so no suite change is needed.
- **Mirrors and local clones instead of linked worktrees.** The worktree Git-metadata mounts are suite code; plain clones need nothing special inside the container and match "fresh checkout per attempt".
- **Push credential inside the sandbox.** Reuses `finalize-pr` unchanged. The guarantee that `main` only changes by PR moves to a GitHub ruleset with no bypass for the token's owner, provisioned in this change.
- **Blocked stays in Running, marked `needs-input`.** Paul's choice; `needs-input` already exists in all five repositories and was his original word for the state, while `blocked` is a label he applies by hand in agent-evals. The slot is freed by the run being terminal, so a blocked bug never stalls other fixes.
- **Dry-run merges and fetch-into-branch.** `git merge-tree --write-tree` before `git merge` so a conflict never leaves the working clone mid-merge; `git fetch origin main:main` so a diverged or checked-out-elsewhere `main` blocks instead of being overwritten.
- **Writer comments only unblock.** Factory comments are excluded so a durable status comment can never retry a declined bug; a drag to Ready is the second, explicit gesture.
- **One CI fix cycle for factory runs.** finalize-pr gains a `ci_fix_cycles` parameter (default 3) and the companion workflow passes 1, bounding bot-review churn inside the two-hour limit.
- **Tracking-only template.** The bypass label must exist at the creation event, so a "Bug (tracking only)" template pre-sets it; adding the label afterwards is too late.
- **Docker memory, not host memory.** The VM allowance is the real limit on macOS.
- **Branch, not commit, for everything.** Harness, Runner, Skills, and targets all resolve at admission; every claim records what it resolved.

## Risks / Trade-offs

- **Concurrent Docker builds.** Two builds may run at once; Docker serializes layer cache access safely, but wall-clock startup is slower. Acceptable.
- **Memory.** Two agent containers in a 7.75 GiB allowance is tight. The headroom check refuses the second start rather than letting Docker OOM-kill a running attempt. If it refuses too often, raise Docker's allowance or lower the reservation.
- **Disk.** Clones per attempt plus per-run images consume space quickly on a 13 GiB margin. Cleanup on Done plus the existing floor bound it; the docs will state a recommended free-space target.
- **Prompt input.** Issue text and writer comments reach the agent. Docker, the scoped token, the PR-only ruleset, and human merge are the controls. Non-writer comments never reach the prompt.
- **Companion workflow drift.** Runner `main` may change the workflow incompatibly. The contract line check in `doctor` and at launch fails closed with a clear message.
- **Sync race.** The clean-tree check and the merge are not atomic; an edit in between can be swept into the merge's parent. Accepted for a single-operator clone; the dry-run and fetch-into-branch guards remove the destructive cases.
- **Ruleset exemption.** If the fix credential's owner is an admin, rulesets can be bypassed. The provisioning task must use a non-admin machine user or a ruleset with no bypass actors, and `doctor` warns when the credential's identity is an org admin.
- **Alternative rejected:** controller-side push after diff inspection. Safer boundary but requires Contents write on the App and a second push implementation; deferred until there is evidence it is needed.

## Migration Plan

1. Deliver `core/factory-fix-v1.0.yaml` to agent-runner `main` with the contract line, plus the `ci_fix_cycles` parameter on `core/finalize-pr-v1.0.yaml`.
2. Land the factory change: extraction refactor, schema v3→v4 migration (automatic on first start, after the store writes `state.sqlite3.v3.bak`), fix handler, config changes. Pause the factory, install, run `doctor`, resume.
3. Update `config/codagent.toml`: `harness_ref = "main"` replacing `harness_sha`; `[routing] bug_type = "Bug"`, `hold_label = "factory-hold"`; `[fix]` targets, branches, defaults. Update local TOML: working clones, fix limits and window, memory reservation, `credentials.fix_environment`.
4. Provision: the `factory-hold` label and a "Bug (tracking only)" issue template in the five repos (`needs-input` already exists); machine-user fine-grained PAT (Contents, Pull requests, Issues on the five repos) written to the fix env file; ruleset "require PR for main" with no bypass on each target repo.
5. Bump `FACTORY_REVISION` in the five caller workflows to the published factory SHA.
6. Rollback: pause, reinstall the previous tag, restore `harness_sha` in config, copy `state.sqlite3.v3.bak` back over `state.sqlite3`. Claims created after the upgrade are lost by that copy; the docs say so. Schema v4 is additive but the prior version refuses a newer `user_version` by design.

## Open Questions

None. Test-plan decisions belong to the next step.
