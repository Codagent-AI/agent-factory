# Task: Age-based evidence retention, live-only status, and the host-mode fix journey end to end

## Goal

Stop evidence growing without bound and make `status` show only live work. A configurable retention period (default 14 days) prunes logs, session state, and agent output of settled claims, keeping structured outcomes and provenance, under a strict eligibility rule that starts the clock from a durably recorded Done observation and never touches a claim with unsettled work. The rule is applied from the runtime's per-claim loop every tick, so history that predates it is covered without a separate sweep. `agent-factory status` lists only running, waiting, blocked, held, in-Review, or pending-sync claims unless `--all` is given. Finally, prove the whole host-mode fix journey end to end: admission, host launch, outcome comment with the host note, cleanup without Docker, live-only status, and pruning after the retention period, plus recovery across a mode switch in each direction.

## Background

Planning sources in this repository: `openspec/changes/dockerless-option/proposal.md`, `design.md` (sections "Retention", "Status", "Decisions", "Risks / Trade-offs"), the spec deltas under `openspec/changes/dockerless-option/specs/`, and `test-plan.md`. Read all of them before starting.

Repository state this task builds on. The fix kind runs in `docker` or `host` mode per `[fix] execution` (`FixLocalConfig.execution`), host plans carry `sandbox: "host"` hints with `session_dir` under the attempt evidence and no `image_tag`, host runs write `host-provenance.json` and get a host sentence in their outcome comments, fix cleanup skips Docker for host claims, and the runtime gates each kind by its own readiness groups. If any of that is missing when you start, stop and report it rather than reimplementing it here.

Current behaviour to change:

- `src/agent_factory/runtime.py::cycle` walks every board card; for each of its claims it skips `superseded` claims first, then handles cancellation, blocked processing, the settled-claim `sync_claim`, reporting via `_report`, and finally `handler.cleanup(claim, board_status=card_status(shared, card))`. The card's board status comes from `card_status(shared, card)` in `work_kinds/base.py`.
- `src/agent_factory/store.py`: `ClaimStore` schema `user_version` 4; claims carry `cleanup_json` (`claim.cleanup`, written by `set_cleanup(claim_id, mapping)`), `reporting_json` (`claim.reporting`, holding pending report events and the `sync` record with `completed`), and `preparation_json`; `runs_for_claim`, `nonterminal_runs`, `all_claims`, `report_uncertainty` (marks a run `uncertain`), `NONTERMINAL_RUN_STATUSES`. Fix cleanup (`work_kinds/fix/cleanup.py::FixCleanup.reconcile`) sets `cleanup["review_observed"]`, `cleanup["complete"]`, and `cleanup["last_error"]`; eval worktree cleanup keeps an equivalent `complete` flag. No schema migration is wanted; retention state lives in `cleanup_json`.
- `src/agent_factory/operations.py::status(store, config)` prints pause state, slot lines, runtime holds, then one block per claim from `store.all_claims()` (current run or `claim: <repo>#<n> (<lifecycle>)`, blocked, hold, reporting, cleanup, and sync lines), then the admission window. `src/agent_factory/cli.py` wires `status` through `_status(state, config)` with an `argparse` parser in `main()`.
- Evidence layouts written today. Fix attempt `artifacts/<claim>/attempt-N/`: `input/issue.json`, `fix-outcome.json`, `logs/`, `factory-suite.log`, `agent-runner/` (the Docker HOME redirect holding staged workflows and Runner projects), `agent-runner-session/` (host), `host-provenance.json` (host), `.runtime/` (CLI session state). Eval repetition `artifacts/<claim>-rep-N[-rescore-M]/`: `result.json`, `run-state.json`, `phases/`, `evidence/`, `neutral/`, `report.html`, diffs and manifests, `logs/`, `factory-suite.log`, `.runtime/agent-runner-projects/`, `.runtime/agent-session-state/`, `.runtime/judge-workspace/`, `.runtime/judge/`, and `.runtime/candidate-worktree/` (a git worktree owned by the existing worktree cleanup, never touched by retention). Run records name their evidence path (`run.evidence_path`) and host runs name `session_dir` in their result and plan hints.
- `config.py`: `LimitsConfig(minimum_free_gib, inactivity_seconds, execution_seconds, total_seconds, codex_reset_fallback_seconds, memory_reservation_gib)`; `config/local.example.toml` documents `[limits]`.
- `tests/e2e/test_fix_cycle.py` is the fix-journey harness: it builds fixture repositories, a `gh` stub backed by a JSON board, a sandbox stub standing in for `sandbox-run.sh`, and drives `cycle` and the CLI; `_pr_outcome(branch, number)` produces a `pull-request` outcome file. Extend this harness for host mode with a stub `agent-runner` executable rather than writing a new one.
- `docs/operations.md` ("Service management and storage") currently states that the factory never automatically deletes evidence; `docs/installation.md` has a "Configuration" section.

### What to build

**Configuration.** `LimitsConfig.evidence_retention_days: int`, default 14, TOML `[limits] evidence_retention_days`, positive integer. Document it in `config/local.example.toml`.

**Retention module.** New `src/agent_factory/retention.py` owning the rule for both kinds, with one entry point `reconcile(store, local, claim, board_status, now)` called from the runtime per-claim loop for every claim of a card, including superseded ones (place the call before the loop's `superseded` skip). State in `claim.cleanup`:

```json
{"done_observed_at": "<iso>", "retention": {"pruned_at": "<iso>", "removed": ["<path>", "..."], "errors": [{"path": "<path>", "error": "<message>"}, "..."]}}
```

1. Observe: when `board_status == "Done"` and `done_observed_at` is unset, record `now`. When the status is anything else, clear `done_observed_at`. A claim whose card is not on the board is never visited and therefore never pruned.
2. Prune when all hold: `board_status == "Done"` in this poll; `now - done_observed_at >= evidence_retention_days`; no run of the claim is non-terminal or marked `uncertain`; no pending reporting events; the `sync` record is completed or the claim needs no sync (evals, and fix claims that never produced a PR); for a non-superseded claim `cleanup.complete` is true (a superseded claim skips this condition); and `retention.pruned_at` is unset.
3. Removal is an enumerated list under each of the claim's attempt evidence directories, keeping everything else including unknown files. Fix attempt: `logs/`, `factory-suite.log`, `agent-runner/`, `agent-runner-session/` (and the run's recorded `session_dir` if it differs), `.runtime/`. Eval repetition: `logs/`, `factory-suite.log`, `.runtime/agent-runner-projects/`, `.runtime/agent-session-state/`, `.runtime/judge-workspace/`, `.runtime/judge/`. Kept: `input/issue.json`, `fix-outcome.json`, `host-provenance.json`, `result.json`, `run-state.json`, `phases/`, `evidence/`, `neutral/`, `report.html`, `.runtime/candidate-worktree/`, and anything not listed for removal. Locate directories from the claim's run records, not by globbing the artifact root.
4. Record removed paths under `retention.removed` (a list of paths) and failures under `retention.errors` (a list, one entry per failed path with the path and the error message); a failure leaves `pruned_at` unset so the next tick retries the remaining paths; success sets `pruned_at` and logs once. A second reconcile after success removes nothing and records nothing new. Never touch candidate branches, PRs, mirrors, SQLite history, or the operator's working clones.

Also invoke the same rule from Done cleanup paths only if that is simpler than relying on the per-tick call; the per-tick call alone satisfies the spec because every board card is visited each tick.

**Status.** `status(store, config, *, include_all=False)` includes a claim when its lifecycle is non-terminal, or it has a non-terminal run, pending reporting events, a pending sync (PR recorded and `sync.completed` not true), or `cleanup.complete` is not true after it reached Review or Done. Superseded claims are never live. The header reports the number of hidden settled claims. `agent-factory status --all` lists every saved claim as today. Runtime holds, slot lines, and the admission window are unchanged.

**Documentation.** `docs/operations.md`: replace the "never automatically deletes evidence" statement with the retention rule (period, what is kept, what is removed, the eligibility conditions, how to see what was pruned), and document `status` versus `status --all`. `docs/installation.md`: `limits.evidence_retention_days` in the configuration section. The eval-execution documentation, wherever it says artifacts remain until manual cleanup, now says they remain until the retention rule removes them and that candidate branches and PRs are never deleted.

## Spec

From `specs/factory-operations/spec.md`:

> ### Requirement: Retain evidence for a bounded period
>
> The factory SHALL prune attempt evidence of settled claims after a configurable retention period, default 14 days. A claim's evidence SHALL be eligible only when all of the following hold: its card has been observed Done, and the retention period has elapsed since the factory first durably recorded that observation; no run of the claim is non-terminal or of unverified ownership; all of the claim's reporting has been delivered; any post-merge sync the claim requires has completed; and its worktree, clone, image, and credential cleanup has settled. Pruning SHALL remove logs, Runner session state, agent session state, and agent output under the attempt's artifact directory and, for a host attempt, its recorded Runner session directory. Pruning SHALL keep the fix outcome, eval result and provenance records, and the attempt's issue input, and SHALL NOT touch candidate branches, PRs, mirrors, SQLite history, or the operator's working clones. The factory SHALL record what it removed and any failures, retry failed pruning on later polls, and run pruning both at Done cleanup and as a sweep on each tick so evidence that predates this rule is covered. Pruning SHALL NOT touch a claim whose card is not currently Done: the current-Done condition SHALL be established from the board observation of the same poll that prunes, a card observed in any other state SHALL reset the recorded Done observation, and a claim whose card is no longer on the board SHALL NOT be pruned. A superseded claim SHALL be pruned on the same conditions judged on its own runs and reporting; because the factory does not clean up a superseded claim's clones or images, that condition is not applied to it. Pruning SHALL remove only enumerated evidence paths and SHALL keep any file or directory it does not recognise.
>
> #### Scenario: Prune after the retention period
> - **WHEN** a claim's card was observed Done more than 14 days ago under the default retention and nothing still needs its evidence
> - **THEN** the next tick removes its logs, session state, and agent output
> - **AND** its outcome or result records, issue input, candidate branches, and PRs remain
>
> #### Scenario: Skip a Done claim with a pending sync
> - **WHEN** a fix claim's card is Done but its post-merge sync has not completed
> - **THEN** its evidence is not pruned however old the claim is
>
> #### Scenario: Reach Done after a long time
> - **WHEN** a claim that has existed for months is moved to Done today
> - **THEN** its evidence is retained for the full retention period from today's Done observation
>
> #### Scenario: Sweep pre-existing history
> - **WHEN** the factory first runs with this rule against a database whose old Done claims have no recorded Done observation
> - **THEN** it records the observation on that poll and prunes those claims only after the retention period from that observation
>
> #### Scenario: Fail to prune
> - **WHEN** removal of an eligible claim's evidence fails partway
> - **THEN** the factory records the failure and remaining work and retries on later polls without blocking other jobs
>
> ### Requirement: Expose current operational status
>
> `agent-factory status` SHALL show, per work kind, the slot holder and progress, waiting work and why it waits, blocked fix claims, pending merge syncs and their last failure reason, pause state, blocking conditions, and the next permitted start time when it can be determined. It SHALL list only claims that are running, waiting, blocked, held, in Review, or pending a merge sync; claims whose card is Done with nothing pending, and superseded claims, SHALL be omitted unless `--all` is given, which lists every saved claim. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.
>
> #### Scenario: Inspect an active evaluation
> - **WHEN** the operator requests status while a repetition is running
> - **THEN** status identifies the active request and repetition progress without interrupting execution
>
> #### Scenario: Inspect waiting work
> - **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite, memory, or usage limit
> - **THEN** status explains the blocking condition and shows the next permitted start time where known
> - **AND** it does not invent a recovery time for a problem requiring operator action
>
> #### Scenario: Inspect both slots
> - **WHEN** an eval is running and a fix is blocked awaiting input with the `needs-input` label
> - **THEN** status shows the eval slot's holder, the fix slot as free, and the blocked bug with its decline reason
>
> #### Scenario: Inspect an installation with history
> - **WHEN** the database holds many Done and superseded claims and one running claim
> - **THEN** status lists the running claim and none of the settled ones
> - **AND** `status --all` lists every saved claim

From the same file, requirement "Keep local data under a configurable root" (this task's portion): "Evidence and candidate outputs SHALL be retained according to the evidence retention requirement below. Setup documentation SHALL explain how to locate logs and artifacts, inspect storage use, state the disk and memory the machine needs to run one eval and one fix concurrently, explain the retention period and what it keeps, and describe operator-managed cleanup of anything outside that rule while preserving work still needed for execution, recovery, or human review."

> #### Scenario: Retain evidence after handoff
> - **WHEN** an evaluation or fix is handed off for human review
> - **THEN** its artifacts and required suite files remain available until the reviewed item moves to Done; evidence remains retained after worktree or clone cleanup until the retention rule removes it

Requirement "Clean up worktrees after review" (this task's portion): "It SHALL preserve results, logs, SQLite history, candidate branches, PRs, and mirrors, subject to the evidence retention requirement." Requirement "Configure deployment without Codagent-specific controller code" (this task's portion): the configuration includes "evidence retention" and "the execution mode, fix disk floor, and retention period SHALL be local configuration". Requirement "Document installation and service operation" (this task's portion): the documentation covers "the evidence retention period" and "status and `--all`".

From `specs/factory-eval-execution/spec.md`:

> ### Requirement: Preserve suite-owned evidence and candidate outputs
>
> The suite SHALL own its evaluation evidence, results, candidate branches, and draft PRs. The factory SHALL preserve those artifacts and record their locations and candidate links. Factory logs SHALL be kept separately from suite-owned evidence. Evidence under the factory's artifact root SHALL remain available until the evidence retention rule in `factory-operations` removes it; that rule SHALL keep each repetition's result and provenance records and SHALL NOT delete candidate branches or PRs. The suite worktree needed for human review SHALL remain available until the reviewed item moves to Done, when the worktree cleanup policy in `factory-operations` applies. The factory SHALL NOT delete candidate branches or PRs, merge changes, or perform human review.
>
> #### Scenario: Finish or stop a request
> - **WHEN** a request completes, is cancelled, or stops after exhausting recovery
> - **THEN** existing evaluation evidence and candidate links remain available for inspection
> - **AND** artifacts for repetitions ready for human review remain usable by the retained suite's human-review command until the reviewed item moves to Done
>
> #### Scenario: Prune a settled eval's evidence
> - **WHEN** an eval claim has been Done for longer than the configured retention and nothing still needs its evidence
> - **THEN** its logs, session state, and agent output under the artifact root are removed
> - **AND** its result and provenance records, candidate branches, and PRs remain

From `specs/factory-fix-execution/spec.md`, requirement "Preserve fix evidence" (this task's portion): "Evidence SHALL survive clone cleanup and SHALL be retained until the evidence retention rule in `factory-operations` removes it." The end-to-end journey below also re-exercises "Invoke the versioned fix workflow" (host launch and the mode-change scenario), "Isolate concurrent sandbox builds" (host cleanup without Docker), and `factory-fix-reporting` "Comment on fix activity" (the host note); those requirement texts are already implemented and are verified here through the journey rather than re-copied.

## Test Plan

Obligations from `openspec/changes/dockerless-option/test-plan.md` assigned to this task. Read each entry there for the full setup and assertions.

- `INT-005` (`tests/integration/test_retention.py`): `retention` over a real ClaimStore and realistic evidence trees copied from the layouts above (a fix claim with a Docker attempt and a host attempt, an eval claim with a repetition, a superseded claim with its own tree, each tree also holding an unknown `future.json`), retention 14 days, a controllable clock. Passes when the first Done observation writes `done_observed_at` and removes nothing; at 13 days nothing is removed; at 14 days with status Done the enumerated paths are gone and `input/issue.json`, `fix-outcome.json`, `host-provenance.json`, `result.json`, `run-state.json`, `phases/`, `evidence/`, `neutral/`, `report.html`, `.runtime/candidate-worktree/`, and every `future.json` remain; observing Review after Done clears `done_observed_at` and a later Done restarts the clock; a claim not visited is never pruned; a non-terminal run, an unverified run, pending reporting events, an incomplete sync, and `cleanup.complete` false on a settled claim each leave evidence untouched; the superseded claim is pruned without `cleanup.complete`; an unremovable directory records an error under `retention.errors`, leaves `pruned_at` unset, and the next reconcile succeeds once it is removable; a second reconcile after success removes nothing and records nothing new.
- `INT-006` (`tests/integration/test_cli_operations.py`, `tests/integration/test_operations_status_sync.py`): claims covering running; blocked; Review with cleanup incomplete; Done with a pending reporting event; Done with a pending sync; fully settled Done; superseded. Default `status` includes the first five and omits the settled and superseded claims with a header count of hidden claims; `status --all` lists all seven.
- `INT-007`, retention-key portion (`tests/integration/test_fix_config.py`): `limits.evidence_retention_days` defaults to 14 and accepts a configured value.
- `E2E-002` (`tests/e2e/test_fix_cycle.py`): the fix-cycle harness with `fix.execution = "host"`, PATH without `docker`, a stub `agent-runner` that honours `--session-dir` and `--param artifact_dir` and writes a `pull-request` outcome, and a controllable clock. The journey: cycle admits the bug, launches, reads the outcome, reports, observes Review then Done, cleans up, then cycles again after the retention period. Passes when the admitted run has no image tag and mode `host`; the PR comment carries the host note; cleanup removes the clone and the credential copy without any Docker invocation; `status` omits the claim once settled and `status --all` shows it; after the retention period the attempt's logs and session directory are gone and `fix-outcome.json` remains. Two further journeys cover mixed-mode recovery in each direction: attempt 1 launches in one mode and fails technically, the configuration switches `fix.execution`, and the recovery retry launches in the other mode (Docker stubbed through the existing sandbox stub). Each run record keeps the mode it launched with; only the Docker-mode run has an image tag; each run's outcome comment carries the host note only when that run was host; Done cleanup removes the Docker run's image and attempts no image removal for the host run; both attempts' credential copies are deleted.

Unit tests for the eligibility predicate, the removal lists, the status liveness predicate, and configuration parsing are implementation-time TDD decisions. `AT-001` (real host fix on the operator's Mac) is an operator acceptance step run later, not by this task.

## Done When

- Every scenario copied above passes under automated tests, and the assigned `INT-005`, `INT-006`, `INT-007` (retention key), and `E2E-002` obligations are implemented in the named files and pass.
- `src/agent_factory/retention.py` exists and is called from the runtime per-claim loop for every claim of every visited card, including superseded claims; the first tick after upgrade against an existing database records Done observations and removes nothing.
- No SQLite schema migration; retention state is entirely inside `cleanup_json`.
- `agent-factory status` hides settled and superseded claims with a hidden count, and `agent-factory status --all` lists every claim; the CLI parser accepts `--all` only on `status`.
- `config/local.example.toml` documents `[limits] evidence_retention_days`; `docs/operations.md` and `docs/installation.md` describe retention and `status --all`, and no document still says the factory never automatically deletes evidence or that artifacts remain until manual cleanup.
- `uv run ruff check`, `uv run pyright`, and `uv run pytest` (excluding `docker`-marked tests) pass.
