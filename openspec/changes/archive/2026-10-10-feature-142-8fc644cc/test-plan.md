## Coverage Strategy

The specifications are the source of unit-test requirements. That includes the `repair-block.py`
audit parsing, causal-chain and supersession rules, explanation stripping, resume-order arithmetic,
outcome validation in `outcome.py` and `verify-feature-outcome.py`, and stop-message text. This plan
records only:
- the additional integration and end-to-end obligations;
- the acceptance testing envelope;
- human-only obligations (none).

The main risks sit at real boundaries:
- the host wrapper and the recorder subprocess;
- git state (checkpoints, remote-tracking refs, pushes) in real repositories;
- the supervisor reading the outcome after process exit;
- the handler choosing the next attempt and posting the stop comment;
- the Runner's audit-log format.

Those boundaries are tested with real components: temporary git repositories with a bare `origin`,
the real wrapper script, the real claim store, and the supervisor. A fake `agent-runner` writes the
audit log, because a real model-driven `REPAIR_BLOCKED` is not deterministic. A committed fixture
and a runtime contract test against the installed Runner (with a stub agent CLI) pin the fake logs to
the real audit shape. The same stub agent harness drives the resumed definition workflow end to end
through its plan publication.

## Integration Tests

### INT-001: Wrapper records an implementation block as needs-input
- **Covers:** Record a repair-blocked step as needs-input ("Implementation repair is blocked"); Invoke the versioned feature workflow ("Finish after a blocked repair").
- **Boundary:** the real host wrapper from `launch.host_script` (feature kind), the staged `repair-block.py` run by the factory interpreter, git in a real clone with a bare `origin`, and both outcome validators.
- **Setup:** use the `Built` host-launch fixture from `tests/integration/test_host_launch.py`.
  - The clone has `attempt-start.json` written by the real `prepare-branch.sh` and a `planned` checkpoint pushed by the real `checkpoint.sh`.
  - The fake Runner writes an audit log in which `implement, sub:implement-task, verify-task-commit` emits `repair_blocked` with an explanation followed by `REPAIR_BLOCKED`. The log continues with the check's failed `step_end` (`repair_blocked: true`), the failed ancestor ends, and a failed `run_end` (`failure_kind: step`). The fake Runner then exits 1.
- **Action:** run the wrapper.
- **Assertions:**
  - the wrapper exits 1;
  - `feature-outcome.json` exists and has `outcome: needs-input`, `blocked_step` set to that exact path, `stopped_step: implement`, the explanation as reason and question with no `REPAIR_BLOCKED` text, and the claim's `branch`;
  - the direction summary names the blocked step and `implement`;
  - `read_interpreted_outcome` and `verify-feature-outcome.py` both accept the outcome;
  - the bare origin's claim branch still ends at the `planned` checkpoint.
- **Execution:** `tests/integration/`, run by `uv run pytest`.

### INT-002: Wrapper records a first-attempt definition block with no resume point, and the next attempt starts fresh
- **Covers:** Record a repair-blocked step as needs-input ("First-attempt definition repair is blocked", "Resume after a first-attempt definition block"); Comment on feature activity ("Report a blocked repair with nothing published").
- **Boundary:** the wrapper, the recorder, git (`ls-remote` against a bare origin that has no claim branch), the claim store and handler, and the real `prepare-branch.sh`.
- **Setup:** a fresh attempt with empty `resume_from`. The fake Runner's audit blocks in a check nested under `define, sub:factory-define, …` and exits non-zero. Nothing is pushed.
- **Action:**
  1. Run the wrapper.
  2. Finish the run in the store with the supervisor's result reading.
  3. Settle the claim.
  4. Simulate a writer comment.
  5. Ask the handler for the next attempt's launch parameters.
  6. Run `prepare-branch.sh` with them.
- **Assertions:**
  - the outcome omits `stopped_step` and `branch`, and passes both validators;
  - its summary says the next attempt starts a fresh definition;
  - no recovery run is created;
  - the stop comment carries the explanation and says that no branch was published and that the next attempt starts fresh;
  - the next attempt's `resume_from` is empty;
  - `prepare-branch.sh` writes no `resume.json` fallback;
  - the admission comment says the attempt starts fresh and does not report an unavailable resume point.
- **Execution:** `tests/integration/`.

### INT-003: The resume point follows published progress
- **Covers:** Record a repair-blocked step as needs-input (the resume-point rule; "Definition repair is blocked after resuming a pushed partial plan"; "Step without its own resume point is blocked after archive").
- **Boundary:** the recorder's git reads against real repositories: the first-parent log of the remote-tracking ref, excluding the starting head, plus `attempt-start.json` from the real `prepare-branch.sh`.
- **Setup:** table-driven, with temporary repositories and a bare origin:
  - an attempt that resumed at `design` from a pushed definition stop, then blocks later in definition without pushing;
  - an attempt that pushes `archived`, then blocks in `verify-classification`;
  - an attempt whose `prepare-branch.sh` merged target history containing squash commits that carry `Factory-Checkpoint` trailers;
  - an attempt with no `attempt-start.json`.
- **Action:** run the recorder's run context with matching synthetic audit logs.
- **Assertions:**
  - the resume points are `design` and `verify`;
  - the merged target trailers do not advance the resume point;
  - a missing `attempt-start.json` yields no resume point;
  - for the design case, the next attempt's `prepare-branch.sh` resumes at `design`, and `factory-resume-skip.sh` does not skip `design` or `test-plan`.
- **Execution:** `tests/integration/`.

### INT-004: Continuation with an unpushed own branch
- **Covers:** Record a repair-blocked step as needs-input ("Continuation repair is blocked before its own branch was pushed", and the push-failure clause).
- **Boundary:** the recorder pushing to a real bare origin.
- **Setup:** a prior branch with a `planned` checkpoint on origin, and a claim branch that does not exist. `prepare-branch.sh` runs as a continuation (`prior_branch` set) and records `prior_head`. The audit blocks inside `implement`. A second case makes origin reject the push, for example with a pre-receive hook that exits 1.
- **Action:** run the recorder.
- **Assertions:**
  - **Push accepted:** the claim branch on origin equals `prior_head`. The outcome has `stopped_step: implement` and the claim's `branch`. The next attempt's `prepare-branch.sh` (needs-input resume, no `prior_branch`) checks out that branch without a fallback.
  - **Push rejected:** no `feature-outcome.json` is written.
- **Execution:** `tests/integration/`.

### INT-005: Supervisor consumes a recorded block without recovery, and plain failures still recover
- **Covers:** Invoke the versioned feature workflow ("Finish without an outcome", "Finish after a blocked repair"); Record a repair-blocked step as needs-input ("Step fails without a repair block", "Superseded repair block", "Repair block without an explanation").
- **Boundary:** the real supervisor monitoring a host process (the wrapper with a fake Runner) until it exits; result reading; `ClaimStore.finish_run`; the handler's `_needs_recovery` and `next_unit`.
- **Setup:** four fake-Runner audits:
  - a valid implementation block;
  - a plain failure with no `repair_blocked`;
  - an earlier block superseded by a later successful run of the same check, followed by an unrelated plain failure;
  - a block with an empty explanation.
- **Action:** let the supervisor observe each process to completion.
- **Assertions:**
  - for the block, the run finishes with the needs-input result, `_needs_recovery` is false, and `next_unit` creates no recovery run;
  - for the other three, no outcome exists, the run finishes `interrupted` with "owned process exited without durable result", and the handler schedules the recovery retry.
- **Execution:** `tests/integration/`.

### INT-006: Archive hook on the shared core
- **Covers:** Record a repair-blocked step as needs-input ("Archive block is unchanged"); Archive the change before verification (existing "Archive repair is blocked").
- **Boundary:** the real `record-archive-block.sh` delegating to `repair-block.py archive`, invoked the way the workflow does, through a JSON payload on stdin and through positional arguments.
- **Setup:** an audit log ending at the failed archive check, the failed `archive, sub:archive-change` step, and `sub_workflow_end` events, with no `run_end`. Variants:
  - a stale block superseded by a later attempt;
  - a block from a different step;
  - a missing log;
  - the archive-wide stale case: a block in `archive-transition`, then `verify-archive-commit` starts and fails without a block. This is the existing `test_record_archive_block_rejects_stale_declaration_after_later_failure` sequence.
- **Action:** run the script.
- **Assertions:**
  - the outcome is byte-for-byte the existing archive shape (stopped step `archive`, the existing direction summary, the branch) plus `blocked_step`;
  - the stale-block, wrong-step, missing-log, and archive-wide stale variants keep today's error messages and write no outcome;
  - the existing archive tests in `tests/integration/test_feature_workflow_scripts.py` and `test_feature_gestures.py` pass unchanged.
- **Execution:** `tests/integration/`.

### INT-007: The recorder reads the installed Runner's audit output
- **Covers:** the design's dependency on Runner event names and fields (`repair_blocked`, `step_end.outcome` and `repair_blocked`, `sub_workflow_end`, `run_end.outcome` and `failure_kind`, prefix format). Host attempts use the installed Runner, rebuilt from Runner `main`, so this must hold for the Runner that actually runs.
- **Boundary:** the recorder against audit output that a real Agent Runner produces during a run.
- **Setup:** two parts.
  - **Portable:** a committed fixture in `tests/fixtures/`, a sanitized real audit log of a run that ended through a blocked repair in a nested sub-workflow check, with the Runner commit it came from recorded beside it, as `eval-request.source-revision` does.
  - **Runtime:** the stub agent harness from the design. The installed `agent-runner` runs, with a temporary `HOME` and repository, a minimal workflow whose nested sub-workflow holds a check that fails, with inline repair. The stub agent CLI answers the repair with an explanation followed by `REPAIR_BLOCKED`. The runtime part skips only when no Runner is installed (`FEATURE_TEST_RUNNER`, or `agent-runner` on `PATH`), and it must execute on Paul's Mac.
- **Action:** run the recorder's run context on the fixture and on the freshly produced audit log.
- **Assertions:**
  - both identify the nested check as the causal blocked step and extract the stub's explanation;
  - on the fresh log, the recorder writes a valid `needs-input` outcome;
  - the synthetic logs used by INT-001 through INT-006 use the same event and field names as the fixture, through a shared builder that asserts this.
- **Execution:** `tests/integration/`.

### INT-008: Resumed definition regenerates and publishes the plan
- **Covers:** Record a repair-blocked step as needs-input ("Resume after a first-attempt definition block", "Definition repair is blocked after resuming a pushed partial plan").
- **Boundary:** the packaged `factory-feature` and `factory-define` workflows, run by the installed Runner with `--until define`: `prepare-branch.sh`, the resume-skip predicates, the definition steps, the plan commit, and `push-plan` against a temporary bare origin.
- **Setup:** the stub agent harness from the design. Stub agents write each definition artifact with a marker that names the producing step and the artifacts it read. The runtime test skips only when no Runner is installed. Two cases:
  - **Fresh:** no claim branch on origin and empty `resume_from`, as INT-002's next attempt computes it.
  - **Partial plan:** origin holds a pushed definition stop with proposal and specifications, and `resume_from=design`, as INT-003's recorded outcome yields it.
- **Action:** run the workflow through `define`.
- **Assertions:**
  - **Fresh case:** every definition step runs in order, and the `planned` checkpoint is published to origin with all planning artifacts.
  - **Partial-plan case:** proposal and specs steps are skipped. Design and test-plan are regenerated before `write-tasks`, and the `write-tasks` stub's marker shows it read the regenerated design and test plan. The `planned` checkpoint containing them is published to origin.
  - Neither case writes a `resume.json` fallback.
- **Execution:** `tests/integration/`.

## End-to-End Tests

### E2E-001: A blocked implementation stops the claim with needs-input, and a writer comment resumes it
- **Covers:** the issue's acceptance journey: a block in `implement` gives a `needs-input` outcome naming the step, no recovery run, the explanation on the card, and a resume at that step.
- **Surface:** the factory cycle (`tick`) through the existing feature cycle harness in `tests/e2e/test_feature_cycle.py`, with its fake GitHub and fake host Runner.
- **Setup:** an admitted feature claim. The fake Runner template writes a `planned` checkpoint to the test origin, then writes the implementation-block audit log and exits 1 without `feature-outcome.json`.
- **Journey:**
  1. Run ticks until the attempt ends.
  2. Assert the card state.
  3. Add a writer comment.
  4. Run a tick.
- **Assertions:**
  - after the first attempt, the claim is blocked with the `needs-input` label, the card stays in Running, and the feature slot is released;
  - exactly one stop comment carries the explanation, names the blocked check and `implement`, and links the branch;
  - no recovery run exists, and the claim did not settle as `infra-error`;
  - after the comment, the next attempt launches with `resume_from=implement` on the claim's branch.
- **Execution:** `tests/e2e/`, run by `uv run pytest` (no Docker marker).

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - a local checkout of this branch with `uv sync`;
  - temporary directories holding git repositories with a bare `origin`;
  - the repository's fake-Runner, fake-GitHub, and claim-store test harnesses;
  - the installed `agent-runner` binary on Paul's Mac, for `-validate` and for real runs of throwaway workflows in temporary repositories (a workflow with a repair-bearing check whose agent is a stub CLI, or a profile configured to answer `REPAIR_BLOCKED`, may be used).
- **Credentials and secrets:** none are needed. The operator's `gh` login and model CLI authentication exist on the Mac but must not be used for writes or paid model calls.
- **Authorized effects:** only local temporary repositories, temporary state databases, and files under temporary directories. Remove them afterwards. No network writes.
- **Off limits:**
  - the live LaunchAgent service and its state under `~/.agent-factory` (config, `state.sqlite3`, releases, clones, artifacts other than reading existing evidence for reference);
  - `scripts/deploy.sh`;
  - real GitHub repositories, issues, the Codagent project board, and pushes to any remote other than a temporary bare repository;
  - Fly.io;
  - paid model invocations.
- **Permitted substitutes:** synthetic or fake-Runner audit logs instead of a real model-driven `REPAIR_BLOCKED`, provided they use the event shapes pinned by INT-007. A fake GitHub in place of real issue comments.
- **Known risk areas:**
  - coupling to the audit format, especially nested prefixes, loop `:N` suffixes, and the truncated `response` field;
  - false positives from `continue_on_failure` blocks (archive, verify, finalize) or from superseded blocks;
  - resume order when a merge commit or merged target history sits between the starting head and the newest checkpoint;
  - the relaxed `needs-input` validation, which must stay strict for outcomes without `blocked_step`;
  - the continuation push;
  - archive regressions;
  - the #141 verify repair reason, which must stay `failed`.
- **Accepted limitations:**
  - unpushed partial work is redone, and may be lost after slimming (D6, D10);
  - a definition-step block is reachable today only through future repair-bearing checks (D20);
  - a fresh start over an already-published draft branch can fail to push, as recovery already can (design risk).

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Invoke the versioned feature workflow (exception for a blocked repair) | INT-001, INT-005 | E2E-001 | — |
| Record a repair-blocked step as needs-input: implementation block | INT-001, INT-005 | E2E-001 | — |
| Record a repair-blocked step as needs-input: definition block with nothing published, and the fresh next attempt | INT-002, INT-008 | — | — |
| Record a repair-blocked step as needs-input: resume point from published progress | INT-003, INT-008 | E2E-001 | — |
| Record a repair-blocked step as needs-input: continuation push and push failure | INT-004 | — | — |
| Record a repair-blocked step as needs-input: plain, superseded, and empty-explanation failures still recover | INT-005 | — | — |
| Record a repair-blocked step as needs-input: archive unchanged (shared core) | INT-006 | — | — |
| Runner audit-format dependency (installed Runner) | INT-007 | — | — |
| Comment on feature activity: blocked repair with and without a published branch | INT-002 | E2E-001 | — |
