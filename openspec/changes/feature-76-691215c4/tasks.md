- [ ] Add the `task` pull-request work kind: Task issues become guarded, low-risk `chore:` pull requests

## Task: Support Task issues as low-risk chore work

Implement the whole change described in these files, in the change directory:

- `proposal.md`;
- the delta specs under `specs/`:
  - new: `factory-task-intake`, `factory-task-execution`, `factory-task-reporting`;
  - modified: `factory-routing`, `factory-pull-request-lifecycle`,
    `factory-review-execution`, `factory-watch-dispatch`, `factory-operations`;
- `design.md`;
- the decision log `decisions.md`. Where its rows differ, later rows override earlier ones.
  In particular, the `approach-review` rows (AR-001 to AR-004) override the earlier design
  rows about the advisory scope floor, exit-code-only gate checks, and prompt-only commit
  subjects;
- the automated obligations in `test-plan.md`: INT-001 to INT-008 and E2E-001 to E2E-003.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or `/Users/paul/codagent/*`. Never deploy, and never change a real
GitHub issue, board item, or branch. Bug and Feature behavior must stay exactly as it is.

### Scope

1. **Configuration** (`src/agent_factory/config.py`, `config/codagent.toml`; design §1):
   - Add `RoutingConfig.task_type`, default `"Task"`.
   - Add `TaskConfig` (defaults and contract, default `factory-task/1`) as
     `SharedConfig.task`, optional like `[feature]`.
   - Add `TaskLocalConfig` as `LocalConfig.task`: limits 900/7200/10800, a schedule, host
     execution only, and an optional `minimum_free_gib`.
   - Generalize the feature parsers into `_optional_kind_shared_config` and
     `_host_kind_local_config`, so feature and task share one implementation. Feature
     defaults must not change.
   - Add `task_type = "Task"` and the `[task]` / `[task.defaults]` block from the design to
     `config/codagent.toml`.
2. **Kind definition** (`work_kinds/pull_request/kinds.py`; design §2):
   - Add `TASK` with the fields given in the design, and return `(FIX, FEATURE, TASK)` from
     `registered()`.
   - Add `TASK_STAGED_FILES`.
   - Add the guard workflow and its scripts to `launch.REVIEW_WORKFLOW_SCRIPTS`, so they are
     staged for every kind.
3. **Remaining kind literals** (design "Context" table):
   - `supervisor._load_result` contract map;
   - `watch/detect.py` PR-READY kinds, taken from `registered()`;
   - the `readiness.check_readiness` no-target diagnostic, for every non-fix kind;
   - the handler's non-writer handoff comment, for every non-fix kind, using marker
     `agent-factory-<kind>-handoff:v1`. The feature marker string must not change;
   - the handler's task `needs-input` body, which adds "No branch was pushed.";
   - the `launch.py` host banner, which uses `definition.workflow_name`.

   Keep every feature-only branch feature-only.
4. **Packaged task workflow** (`work_kinds/pull_request/workflow/`; design §3, as amended by
   AR-001 to AR-003):
   - `factory-task-v1.0.yaml` with contract `factory-task/1`, following the step diagram in
     design §3:
     - triage with the `doable` decision shape, its prompt covering the decline criteria,
       bounded choices, the toolchain-versus-release boundary, routing, and the three worked
       examples;
     - implement without TDD, writing `task-choices.json`;
     - validator gates with repair and recheck;
     - gate exercise, then the lead's verification, then `check-gate-exercises.py`, with one
       repair;
     - conditional `test-flows`;
     - lead review, then address-findings, then the final gate;
     - the pre-push `factory-task-guard`;
     - `normalize-chore-commits.py`;
     - `builtin:core/finalize-pr` with 3 cycles;
     - the post-finalize guard, when HEAD moved;
     - `annotate-chore-pr.sh`;
     - `record-outcome.sh`, then `verify-outcome`.

     Seed every capture that a later `skip_if` reads.
   - New `factory-task-guard-v1.0.yaml`, in `prepush` and `postfinalize` modes.
   - New scripts:
     - `task-scope-floor.py`;
     - `record-scope.sh`;
     - `check-gate-exercises.py`, which checks the command match, exit codes, the planted
       patch, a confirmed verdict whose diagnostic appears only in the negative log, and a
       clean tree;
     - `normalize-chore-commits.py`, which keeps trees, authors, and dates, and refuses on a
       merge commit or an upstream;
     - `check-chore-subjects.py`;
     - `annotate-chore-pr.sh`, which writes Refs and the claim marker, retitles to
       `chore:`, appends the task-evidence section, is idempotent, and tolerates a failed
       PATCH.
   - Generalize `record-triage.sh` with optional `contract`, `accept_field`, and
     `decision_path` inputs. With the defaults, its fix output must stay byte-identical.
   - Extend `record-outcome.sh` with an optional `scope_path`: a pre-push crossing records
     `needs-input`, and a post-finalize crossing records `failed` keeping the PR.
5. **Review rounds** (design §4):
   - `factory-review-v1.0.yaml`:
     - a `read-kind` step;
     - the task paragraph in triage;
     - `task_scope` and `scope_base` passed to `factory-implement`;
     - `respond` skipped on a pre-push crossing.
   - `factory-implement-v1.0.yaml`:
     - the `task_scope` and `scope_base` params, default off;
     - `implement-task-plan` with no TDD;
     - the pre-push guard, then normalize, before finalize;
     - the post-finalize guard after finalize;
     - `scope` in `write-result`.

     With default params, fix and feature behavior must not change.
   - `record-review-outcome.sh`: a pre-push crossing maps to `needs-input`, and a
     post-finalize crossing maps to `failed`.
6. **Operations, skills, docs** (design §6):
   - Check that doctor shows the `task-host` group and status shows the task slot and task
     claims.
   - `.claude/skills/factory-assign/assign.py` and its `SKILL.md`: the task kind and
     `--apply task`, refusing a mismatched kind.
   - `.claude/skills/factory-status/SKILL.md`: the task slot line.
   - `AGENTS.md` and `docs/operations.md`: everything the "Document the task kind"
     requirement lists, including the rollback caveat and the pre-enablement audit of Ready
     Tasks.
7. **Tests**: the unit tests the specs imply, plus INT-001 to INT-008 and E2E-001 to E2E-003
   at the locations `test-plan.md` names. The existing fix, feature, review-contract, watch,
   and catalog suites must pass unmodified.

### Done when

- An open, writer-authored Task in a fix target with Owner=factory and Status=Ready is
  admitted as a `task` claim in its own slot. It runs `factory-task` on the host and, through
  the stand-in runner, reaches Review with a `chore:` pull request (E2E-001).
- A Task whose triage declines is blocked with `needs-input`, its comment names the decision
  and says no branch was pushed, and no `factory/task-*` branch exists (E2E-002). A writer's
  comment relaunches it.
- Bugs and Features are admitted with unchanged launches and catalogs when `[task]` is
  present (E2E-003, INT-002).
- Admission, triage decline, the `factory-task/1` outcome contract, config parsing, the
  guards, gate-proof verification, commit normalization, review-round scope stops, watch
  events, doctor, status, and the assign skill are covered by passing tests.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and
  `uv run pytest` pass, and `agent-validator run` is green.
- `openspec validate feature-76-691215c4 --strict` passes.
