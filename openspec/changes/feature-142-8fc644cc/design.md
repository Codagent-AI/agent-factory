## Context

**Where `REPAIR_BLOCKED` comes from.** Agent Runner emits it only from a check that declares
`repair:`, meaning a shell or script step (`internal/exec/repair.go`). The guarded agent or the inline
repair agent ends its response with the line `REPAIR_BLOCKED`. The Runner then writes two audit events
on the owning check's prefix:
- `repair_blocked {"attempt": N, "response": "<agent response>"}` (the response is truncated for
  audit);
- the check's own terminal `step_end` with `outcome: "failed"` and `repair_blocked: true`.

Every enclosing step and sub-workflow that does not continue on failure then ends `failed`, and the
run ends with `run_end {"outcome": "failed", "failure_kind": "step", ...}`. An infrastructure crash
ends instead with `failure_kind: "infrastructure"`.

**Checks that can block a feature run.** Today these are:
- inside `implement` (`builtin:core/implement-task`): `verify-task-commit`;
- in the feature workflow: `verify-classification`;
- in archive, verify, and finalize. These either already write an outcome (archive, through
  `record-archive-block.sh`) or continue on failure and record `failed` (verify, finalize).

The feature's own definition steps declare no `repair:` today. Builtin sub-workflows come from the
installed Runner, which is rebuilt from Runner `main` on every deploy, so they can gain repair-bearing
checks without a factory change. The mechanism therefore keys on the Runner's events wherever they
occur, not on a list of factory step ids.

**What happens today without an outcome.** The host wrapper (`launch.host_script`) exits with the
Runner's status. The supervisor finds the process gone and no `feature-outcome.json`, and records
`interrupted`. `_needs_recovery` then launches the claim's single recovery retry.

**Outcome validation constraints.** `outcome.read_interpreted_outcome` and
`workflow/verify-feature-outcome.py` validate `factory-feature/1`. A `needs-input` outcome must
currently have a non-empty `stopped_step` and, unless the step is `preflight`, a `branch`.

**How a needs-input stop resumes.** `handler.feature_resume_point()` resumes at `stopped_step`. The
handler passes no `prior_branch` for that resume, so `prepare-branch.sh` fetches the claim's own
branch. If that branch is missing, `prepare-branch.sh` writes `resume.json` with a fallback and starts
fresh, which is reported as an unavailable resume point.

**Checkpoints.** Phase checkpoints are commits with a `Factory-Checkpoint: planned|implemented|archived`
trailer, pushed by `checkpoint.sh` (steps `push-plan`, `complete-task`, `push-archive`). Resume order
is the list in `factory-resume-skip.sh`: proposal, proposal-review, specs, design, test-plan,
approach-review, write-tasks, implement, archive, verify, finalize.

## Goals / Non-Goals

**Goals:**
- One shared recorder turns a run-ending `REPAIR_BLOCKED` into a valid `needs-input` outcome before
  the attempt process exits, so the supervisor never records it as interrupted.
- The resume point is computed from published progress, as the spec requires.
- Archive's in-workflow stop uses the same parser and outcome builder.
- The outcome contract and stop comment accept the new shape: a blocked step, an optional resume
  point, and an optional branch.

**Non-Goals:**
- verify and finalize semantics, fix, task, and review workflows.
- Agent Runner changes.
- Preserving unpushed work.
- Adding `repair:` to definition steps.

## Approach

### Components

```
host wrapper (launch.host_script, feature kind, non-review contract)
  run_status = agent-runner run ...
  if run_status != 0:  <python> -I <clone>/.agent-runner/workflows/repair-block.py run \
                         --session-dir S --artifact-dir A --branch B || true
  [post-run audit]
  exit run_status

record-archive-block.sh (unchanged step in factory-feature-v1.0.yaml)
  python3 .agent-runner/workflows/repair-block.py archive --session-dir S --artifact-dir A --branch B

repair-block.py (new packaged workflow script, stdlib only)
  parse_audit(lines) -> events
  blocked_response(events, endpoint, scope) -> (step_path, explanation) | None  # shared core
  causal_endpoint(events) -> step_path | None                              # run context only
  resume_point(artifact_dir, branch) -> str                                # run context only
  build_outcome(...) -> dict ; write feature-outcome.json                  # shared core
```

`repair-block.py` lives in `src/agent_factory/work_kinds/pull_request/workflow/` and is added to the
packaged file list in `kinds.py`, so it is staged with the other workflow scripts. The wrapper runs the
staged copy with the factory's interpreter (`sys.executable -I`), never the clone's `python3`. The
attempt's credentials stay in the environment, because the recorder may need `git ls-remote` and
`git push` (see below). The recorder's own failure never changes the wrapper's exit status. If it
writes nothing, the attempt behaves exactly as it does today.

### Audit parsing (shared core)

Each audit line is `<timestamp> [<prefix>] <event> <json>`, or `<timestamp> <event> <json>` for run
events. A step path is the bracket content with its `, ` separators, for example
`implement, sub:implement-task, verify-task-commit`. This is the Runner's own name, and it is stored
verbatim as `blocked_step`.

Path B is a descendant of A when B starts with `A, ` or `A:` (loop iterations carry `:N`).

`blocked_response(events, endpoint, scope)` takes a supersession scope from its caller:

- **`path` scope (run context).** The function keeps, per step path, the latest `repair_blocked`
  response. A later `step_start` or successful `step_end` of that same path supersedes it, as
  `verify-failure.py` does. It returns the surviving response for exactly the endpoint path. The causal
  chain (below) already guarantees that nothing unrelated ran after the endpoint failed.
- **`subtree` scope (archive context).** The function keeps a single candidate for the whole endpoint
  subtree. Any later event in the subtree other than `step_end` or `sub_workflow_end` clears the
  candidate, and a later `repair_blocked` replaces it. This is exactly the current
  `record-archive-block.sh` rule. A block in `archive-transition` followed by a plain failure in
  `verify-archive-commit` is therefore stale and rejected. The function returns the surviving
  candidate and the path it came from.

In both scopes, a malformed `repair_blocked` payload counts as no declaration. The explanation is the
response with a trailing `REPAIR_BLOCKED` line removed and whitespace stripped, and an empty
explanation yields `None`.

### Run context (`repair-block.py run`)

1. If `feature-outcome.json` exists, exit 0 and write nothing.
2. Read `audit.log` from the session directory. Require that its last run event is a `run_end` with
   `outcome: "failed"` and a `failure_kind` other than `"infrastructure"`. Otherwise write nothing.
3. Find the causal step with `causal_endpoint`. Walk backwards from `run_end` over events of type
   `step_start`, `step_end`, `sub_workflow_start`, `sub_workflow_end`, and `repair_blocked`; other
   event types are skipped. Collect the trailing chain of failed ends (`step_end` or
   `sub_workflow_end` with `outcome: "failed"`) in which each earlier end is a descendant of, or the
   same path as, the later one. Any `step_start` or non-failed end breaks the chain. The deepest path
   in the chain is the causal step.
4. Require that the causal step's failed `step_end` has `repair_blocked: true` and that
   `blocked_response(events, causal_step, "path")` returns an explanation for exactly that path. Otherwise
   write nothing, and the failure stays a technical failure.
5. Compute the resume point and the branch state (below), then write the outcome.

### Resume point and branch state

`prepare-branch.sh` gains one write at its end: `<artifact_dir>/attempt-start.json`, containing:
- `resume_from`: the effective resume point it prints;
- `head`: `git rev-parse HEAD` after preparation;
- `prior_head`: the fetched prior-branch commit, when the attempt continues a prior branch.

The workflow's behavior is otherwise unchanged.

`resume_point` computes:
- `start` = `attempt-start.json.resume_from` (empty if the file is missing);
- `pushed` = the newest `Factory-Checkpoint` trailer in
  `git log --first-parent refs/remotes/origin/<branch> ^<head>`. This covers only commits this attempt
  pushed. `checkpoint.sh` pushes with `git push origin HEAD:refs/heads/<branch>`, which also updates the
  remote-tracking ref. With no `head`, or no tracking ref, there is no pushed checkpoint;
- `next(pushed)`: planned → implement, implemented → archive, archived → verify;
- the resume point is the later of `start` and `next(pushed)` in the resume order, or empty when both
  are empty.

Branch state comes from `git ls-remote --exit-code origin refs/heads/<branch>`, as
`record-merge-stop.sh` does. Exit 2 means unpublished. Any other failure falls back to whether
`refs/remotes/origin/<branch>` exists. Then:
- **Resume point set, branch unpublished.** This happens only for a continuation of a prior branch
  whose own branch was never pushed. The recorder pushes `prior_head` to `refs/heads/<branch>`. That
  is the prior claim's work unchanged, as merge stops do, so the next attempt resumes from it. If the
  push fails, the recorder writes nothing, and the run goes to recovery, which handles continuations.
- **Resume point empty.** No push. `branch` is included only if the branch is already published.

### Outcome

```json
{"contract": "factory-feature/1", "outcome": "needs-input",
 "blocked_step": "implement, sub:implement-task, verify-task-commit",
 "stopped_step": "implement",          // omitted when the resume point is empty
 "reasons": ["<explanation>"], "questions": ["<explanation>"],
 "direction_summary": "...",
 "branch": "factory/feature-N-xxxx"}   // omitted when unpublished
```

The direction summary names `blocked_step` and the resume step. It tells the operator to fix the
cause on the target branch, or commit the fix to the claim's branch, and to comment on the issue, so
the next attempt merges the target branch and resumes at the resume step. With no resume point, it
says the next attempt starts a fresh definition from the target branch. The archive context keeps its
existing summary and `stopped_step: "archive"`, and adds `blocked_step`.

### Archive context (`repair-block.py archive`)

`record-archive-block.sh` keeps its step, inputs, and error messages, and delegates to the shared
core with endpoint `archive, sub:archive-change` and the `subtree` scope. The archive stop's causality
rule is therefore unchanged: only a declaration that nothing else in the archive subtree followed,
apart from terminal ends, counts. There is no `run_end` requirement and no resume computation. The two
contexts share response extraction, explanation stripping, and outcome construction. The script's
current inline parser is removed, and its existing regression tests stay as they are, including the
stale declaration followed by a later failure of a different check.

### Contract and reporting changes

- `outcome.py` and `verify-feature-outcome.py`:
  - `blocked_step`, when present, must be a non-empty string;
  - a `needs-input` outcome with `blocked_step` may omit `stopped_step` and `branch`;
  - all other `needs-input` outcomes keep today's rules.
- `handler.feature_resume_point()` already returns `""` for a missing `stopped_step`. The next attempt
  passes no resume point, so `prepare-branch.sh` starts fresh without writing a fallback, and the
  admission comment says it starts fresh. No change.
- `handler._feature_stop_message()`: when there is no `branch` and the stop is not `preflight`, add
  "No branch was published; the next attempt starts fresh." The questions and summary already carry
  the explanation and the step names.

## Decisions

- **Recorder in the host wrapper, not in the workflow or the supervisor.** The Runner aborts at the
  first failing step that is not `continue_on_failure`, so a workflow step cannot run afterwards. The
  supervisor sees only process exit, so it would have to re-parse evidence and synthesize a workflow
  outcome. The wrapper runs inside the attempt's process lifetime, with the clone, the credentials, and
  the session directory, so the supervisor's existing "result present" path applies unchanged.
- **One stdlib script staged with the workflow.** It serves both contexts, so archive and the
  wrapper share parsing and outcome building. The wrapper runs it with the factory interpreter in
  isolated mode, so nothing in the clone can shadow an import.
- **The causal chain, not just the last `repair_blocked`.** An earlier `continue_on_failure` block, or
  one superseded by a rerun, must not turn a later unrelated failure into `needs-input`. Requiring the
  failed `run_end`, the unbroken chain of failed ancestors, and the check's own `repair_blocked: true`
  ties the block to the failure that actually ended the run.
- **`attempt-start.json` from `prepare-branch.sh`.** The effective resume point and starting head are
  only known inside the workflow. Reading them from audit captures would couple the recorder to step
  ids and capture formatting. A small file in the attempt's own artifact directory is deterministic.
- **Checkpoints read from git, limited to this attempt.** Excluding everything reachable from the
  starting head counts only checkpoints this attempt pushed. Commits from the target branch that a
  merge brought in sit outside the first-parent chain, or behind the starting head.
- **Push prior work for unpushed continuations.** Without this, a needs-input resume finds no claim
  branch and falls back to a fresh start, so the continuation is lost. This mirrors
  `record-merge-stop.sh` and the merge-stop requirement, and it pushes no partial work.
- **Optional `stopped_step` and `branch` only alongside `blocked_step`.** This keeps every existing
  outcome's validation strict and makes the relaxation additive.

## Risks / Trade-offs

- **Audit format coupling.** The recorder relies on Runner event names and fields (`repair_blocked`,
  `step_end.outcome`, `step_end.repair_blocked`, `run_end.failure_kind`). If they change, the recorder
  writes nothing and behavior degrades to today's recovery, never to a wrong `needs-input`. Host
  attempts use the installed Runner, which is rebuilt from Runner `main` on every deploy, so a
  committed fixture alone cannot catch drift. A runtime contract test runs the installed Runner on a
  throwaway nested repair-bearing workflow with a stub agent CLI and feeds the fresh audit to the
  recorder (see the stub agent harness below).
- **Truncated responses.** The audit truncates the response. The explanation can be cut, but it is
  never empty when the agent wrote text.
- **Redone work.** Unpushed definition artifacts and implementation commits are redone, and they may
  be lost after slimming (D6, D10).
- **Fresh start over an existing draft branch.** An empty resume point with an already-published
  branch is possible: an attempt that recovered from an interrupted run resumed from a definition stop
  with no checkpoint. In that case the next attempt starts fresh from the target and can later fail to
  push over the old branch. This is the same pre-existing behavior as recovery in that situation; the
  recorder does not make it worse.
- **Recorder failures.** Network errors in `ls-remote` fall back to the tracking ref. A failed push
  for a continuation writes nothing (recovery). Any exception writes nothing.

## Migration Plan

- Deploy normally. Running attempts keep their release and wrapper. New attempts get the recorder.
  `attempt-start.json` appears only in attempts launched by the new release.
- **Rollback.** An older release reading a stored `needs-input` result without `stopped_step` resumes
  fresh (`feature_resume_point` returns `""`), and it ignores `blocked_step`. An older supervisor never
  sees new outcome files from an attempt it did not launch. No data migration is needed.

## Test Strategy (for the test plan)

- **Unit tests (`repair-block.py`)** against synthetic audit logs:
  - causal chain through nested sub-workflows and loop suffixes;
  - superseded block (later start, or success, of the same path);
  - earlier `continue_on_failure` block followed by an unrelated plain failure;
  - infrastructure `run_end`;
  - empty response;
  - missing `run_end`;
  - an outcome already present.
- **Resume-point tests** with temporary git repositories and a bare origin:
  - start only;
  - a checkpoint pushed this attempt;
  - target history merged in;
  - no start and no checkpoint;
  - continuation with an unpushed own branch (prior head pushed);
  - push failure.
- **Integration (wrapper):** a fake Runner writes an audit log that blocks in
  `implement, sub:implement-task, verify-task-commit`, and another that blocks in a check nested in
  `define`, then exits non-zero. The wrapper keeps the exit status, `feature-outcome.json` validates,
  and the supervisor and handler record `needs-input` without a recovery run. A fake Runner that fails
  without a block leaves no outcome and goes to recovery.
- **Integration (archive):** the archive hook on an audit that ends at the failed archive step and
  sub-workflow events with no `run_end`; existing archive tests stay green.
- **Second attempts:** after a stop with no resume point, `prepare-branch.sh` starts fresh with no
  `resume.json` fallback. After a definition block that resumed at design, the next attempt resumes at
  design.
- **Reporting:** the stop comment for a missing branch, and validation of the relaxed outcome shapes
  in both `outcome.py` and `verify-feature-outcome.py`.

### Stub agent harness (runtime tests with the installed Runner)

The runtime tests run the real installed `agent-runner` in a temporary repository with a temporary
`HOME`, as `tests/integration/test_feature_workflow_catalog.py` already does. A factory test profile
selects one agent CLI adapter. A stub executable with that CLI's name sits first on `PATH`.
- **How the stub works:** it emulates the adapter's headless invocation and output protocol, choosing
  the adapter with the simplest contract in the installed Runner. Each response is scripted by a
  marker in the prompt: write a named artifact, commit, or answer with an explanation followed by
  `REPAIR_BLOCKED`.
- **When it skips:** only when no Runner is installed (`FEATURE_TEST_RUNNER`, or `agent-runner` on
  `PATH`). On Paul's Mac, where the validator runs, the tests must execute.
- **What it runs:** the contract test, which runs a minimal workflow whose nested sub-workflow holds a
  check with inline repair, and the resumed-definition tests.
- **Resumed-definition tests:** these run the packaged `factory-feature` workflow with `--until define`
  against a temporary bare origin. Stub agents write each definition artifact with a marker naming the
  step and the inputs they read, and the plan commit and `push-plan` publish the result. The `gh`
  calls and validator steps outside the define range are not reached.
