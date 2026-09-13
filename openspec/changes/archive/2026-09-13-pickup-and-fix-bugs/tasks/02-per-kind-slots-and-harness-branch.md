# Task: Per-kind execution slots, scoped holds, and harness branch resolution

## Goal

Replace the single global execution slot with one atomically enforced SQLite slot per work kind (schema v3→v4 with a backup and one-transaction migration), scope quota and readiness holds by provider and kind, add the `blocked` claim lifecycle, and replace the `agent-evals` harness commit pin with a configured branch resolved to a commit at each eval admission and recorded on the claim.

## Background

All paths are in `agent-factory`. Planning sources are `openspec/changes/pickup-and-fix-bugs/proposal.md`, `design.md` (sections "Persistence", "Cycle" holds paragraph, "Harness branch", "Migration Plan", and "Decisions: Branch, not commit, for everything"), the specs under `specs/`, and `test-plan.md`.

Preconditions in the repository: the controller and runtime dispatch through `WorkKindHandler` in `src/agent_factory/work_kinds/base.py`, with `EvalHandler` in `src/agent_factory/work_kinds/eval/` owning eval parsing, freezing, planning, and presentation, and `Controller.__init__` taking `handlers: Mapping[str, WorkKindHandler]`. `src/agent_factory/store.py` is at `SCHEMA_VERSION = 3`, `_migrate` guards on `PRAGMA user_version`, the `run` table has the partial unique index `one_nonterminal_run` over `status IN ('reserved','running','observing')`, `reserve_run` raises `NonterminalRunError` on conflict, `nonterminal_runs()` takes no filter, `set_setting`/`get_setting` use `(namespace, key)`, and the global quota hold lives at `settings("admission", "quota")` while readiness lives at `settings("runtime", "readiness")`. `src/agent_factory/config.py` `EvalConfig.harness_sha` is validated as a 40-character SHA; `runtime._resolve_revision(source, revision)` resolves a ref in a local checkout; `operations.doctor` prints `loaded pinned harness <sha>`.

### Persistence

Add a v3→v4 migration inside the existing `user_version` guard, in one transaction:

```sql
ALTER TABLE run ADD COLUMN kind TEXT NOT NULL DEFAULT 'eval';
UPDATE run SET kind = (SELECT kind FROM claim WHERE claim.id = run.claim_id);
DROP INDEX one_nonterminal_run;
CREATE UNIQUE INDEX one_nonterminal_run_per_kind
  ON run(kind) WHERE status IN ('reserved','running','observing');
INSERT OR REPLACE INTO settings(namespace, key, value_json, updated_at)
  SELECT namespace, 'quota:codex', value_json, updated_at FROM settings
  WHERE namespace = 'admission' AND key = 'quota';
DELETE FROM settings WHERE namespace = 'admission' AND key = 'quota';
PRAGMA user_version = 4;
```

Before migrating, copy `state.sqlite3` to `state.sqlite3.v3.bak` beside it (skip if that file exists). A migration failure part-way leaves the database at v3 untouched. A newer `user_version` than the binary supports must still be refused (fail closed). Fresh databases create the v4 schema directly. `SCHEMA_VERSION` becomes 4.

`reserve_run` copies the claim's kind onto the run and raises `NonterminalRunError` only when a nonterminal run of the same kind exists. `nonterminal_runs(kind=None)` filters when asked. The admission advisory lock in `controller.advisory_lock` stays global; it protects the reserve step only. Claim `lifecycle` gains `blocked`; a run whose terminal result is classified `blocked` frees its kind's slot because the run is terminal. Store a `SupervisionLimits`-shaped `limits` on the run at reservation or configuration time so each kind's limits are recorded per run.

### Hold scoping

`settings("admission", "quota")` becomes `settings("admission", "quota:<provider>")`, one per provider (`codex`, `cursor`, `claude`). A hold blocks a kind only if `handler.providers(claim)` for the candidate claim intersects the held providers. Readiness holds are stored per kind under `settings("runtime", "readiness:<kind>")`. Pause, the free-disk floor, and (once it exists) the memory headroom check remain global. `Controller.record_result` writes the provider-scoped hold from the eval result's provider; `reserve_next` consults the scoped settings. `operations.status` must read the new keys; the existing `runtime.quota-error` setting keeps its meaning.

Admission in `runtime.cycle` becomes per kind: for each registered handler, if that kind's slot is free, the handler's window is open, no global hold applies, and no provider hold intersects the handler's providers, walk the board in order and stop at the first launch for that kind. The `tick` command applies the same guard. With only the eval handler registered the observable behavior is unchanged except for the scoped hold keys.

### Harness branch

`EvalConfig.harness_sha` → `harness_ref` (a branch name, default `main`). Configuration loading rejects a commit SHA as the value and fails with a message naming `harness_ref` when a leftover `harness_sha` key is present. `EvalHandler.accept` resolves `harness_ref` in the local `agent_evals` checkout (`origin/<ref>` after the existing fetch path, through `_resolve_revision`) and freezes it as `revisions.evals`; the adapter, the `Refs` rendering (`runner@<7> skills@<7> evals@<7>`), the frozen-inputs comment, and the suite's own `proof-metadata.json` (`agent_evals_commit`) already record the resolved value. A claim's repetitions and its recovery retry keep the recorded commit; a fresh claim re-resolves. `doctor`'s "loaded pinned harness" line becomes "harness branch <ref> → <sha>" using a resolve without fetch. Update `config/codagent.toml` (`harness_ref = "main"`, removing the pin comment) and any docs mention of the pinned harness in `docs/installation.md`, `docs/operations.md`, and `docs/suite-integration.md`, including a note that the recorded commit is the comparability key across nights and that `doctor` no longer proves a specific harness revision carries the suite features the factory depends on (the suite doc lists them).

Constraints: no fix handler, no fix configuration, no memory probe, no per-run image tags in this task. Keep `pyright` strict and `ruff` clean.

## Spec

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-claim-lifecycle/spec.md`.

### Requirement: Prevent overlapping execution

The factory SHALL execute at most one attempt per work kind at a time across the service and manual execution commands: one eval repetition and one fix attempt. Per-kind slots SHALL be enforced atomically in SQLite so two admission paths cannot both reserve the same kind's slot. The factory SHALL reconcile saved execution records with surviving processes, containers, and evidence before dispatching new work of either kind. A blocked fix claim SHALL NOT occupy a slot. Status and pause controls SHALL remain usable while execution is active.

#### Scenario: Attempt simultaneous dispatch

- **WHEN** a manual command attempts execution while the service already has an attempt of the same kind running
- **THEN** no second attempt of that kind starts
- **AND** status and pause controls remain available

#### Scenario: Run an eval and a fix together

- **WHEN** an eval repetition is running and an eligible bug is admitted
- **THEN** the fix attempt starts in its own slot and the eval continues unaffected

(This task's portion: the store admits one nonterminal run per distinct `kind` value and refuses a second of the same kind; the fix handler that admits bugs is delivered elsewhere.)

#### Scenario: Migrate the single-slot database

- **WHEN** the factory starts against a database at the current shipped schema version with claims, runs, reporting progress, a pause, and an active quota hold
- **THEN** it migrates the schema to per-kind slots in one transaction without losing claim, run, or settings history
- **AND** the existing quota hold continues to apply to the provider it was recorded for

### Requirement: Classify admission holds by scope

Pause, the free-disk floor, and the memory headroom check SHALL apply to every kind. A provider quota hold SHALL apply only to attempts whose configured roles use that provider. Eval suite readiness SHALL hold only eval work; fix readiness (mirrors, workflow contract, fix credential) SHALL hold only fix work. A hold on one kind SHALL NOT prevent admission of the other kind.

#### Scenario: Hold Codex while fixing with Cursor

- **WHEN** a Codex quota hold is active and the fix roles use Cursor
- **THEN** an eligible bug can still be admitted while eval work using Codex waits

#### Scenario: Lose suite readiness

- **WHEN** the eval suite's prerequisites are unavailable
- **THEN** eval admission is held and fix admission is unaffected

(This task's portion: the scoping mechanism and eval-side behavior; a second handler exercising the other side is delivered elsewhere and unit tests here may use a stub handler with `kind="other"`.)

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-eval-intake/spec.md`.

### Requirement: Interpret one evaluation configuration per request (harness portion)

Request-level revision selection SHALL apply to Agent Runner and Agent Skills. The `agent-evals` harness SHALL follow the configured harness branch (default `main`); request-level selection of harness revisions remains unsupported. Recording the resolved harness commit SHALL identify the test environment used for the result.

### Requirement: Freeze accepted evaluation inputs

A new claim SHALL record the effective evaluation settings, including the selected eval suite, and resolve the requested Runner and Skills refs and the configured `agent-evals` harness branch to immutable commits from the remote at admission. `agent-evals` is the evaluation harness and may contain multiple suites; `and-scene` is the default suite. Those accepted inputs SHALL remain fixed for the claim, including its repetitions and automatic recovery. Later changes to branches, defaults, or the issue SHALL NOT mutate an existing claim's frozen inputs. Configuration SHALL name the harness branch, not a commit.

#### Scenario: Continue after refs or defaults change

- **WHEN** an unfinished claim resumes after its requested branches, the harness branch, or configured defaults have changed
- **THEN** it uses the same accepted settings and immutable revisions
- **AND** its completed repetitions remain completed

#### Scenario: Admit two evals on different days

- **WHEN** the harness branch advances between two admissions
- **THEN** each claim records the harness commit it resolved at its own admission and the difference is visible in its Refs and results

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-eval-execution/spec.md`.

### Requirement: Evaluate Runner and Skills using an existing suite

The factory SHALL support evaluation of requested Agent Runner and Agent Skills revisions through the existing `agent-evals` harness. `and-scene` SHALL be the default eval suite. The harness SHALL be taken from a configured branch (default `main`), resolved to a commit at claim admission, recorded, and retained as part of the evaluation environment for that claim; requests SHALL NOT select different `agent-evals` revisions. Suite setup documentation SHALL state which harness behavior (score-failure contract, calibration-gate removal, linked-worktree metadata mounts, persisted sessions) the factory depends on, and `doctor` SHALL verify the resolved harness commit contains the suite entry points the factory invokes.

#### Scenario: Evaluate selected component revisions

- **WHEN** an accepted request specifies Runner and Skills refs
- **THEN** the factory evaluates their resolved revisions with the default `and-scene` suite at the harness commit resolved for the claim
- **AND** records the suite identity and that harness commit as part of the test environment

#### Scenario: Advance the harness branch during a claim

- **WHEN** `agent-evals` `main` receives commits while a claim has unfinished repetitions
- **THEN** the remaining repetitions and any recovery retry use the claim's recorded harness commit

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-operations/spec.md`.

### Requirement: Apply shared deployment changes through explicit updates (harness portion)

Shared deployment configuration SHALL be versioned with the factory and contain source repositories, routing rules, Project/field mappings, eval defaults, fix defaults, and the branch names for the `agent-evals` harness, Agent Runner, Agent Skills, and fix target repositories (each defaulting to `main`). Configuration SHALL NOT pin any of these repositories to a commit; commits are resolved per claim at admission and recorded on the claim.

#### Scenario: Migrate a pinned harness configuration

- **WHEN** the installed configuration still contains a harness commit pin
- **THEN** the factory reports the obsolete setting at startup and in doctor instead of silently ignoring it

## Test Plan

- `INT-001` (Schema migration and per-kind execution slots): `ClaimStore` against a real SQLite file produced by the v3 schema with one settled eval claim, one waiting claim with a consumed recovery retry, reporting progress, a saved pause, and an active global quota hold. Open with the new store; reserve an eval run and a run of a second kind; attempt a second run of each kind from a second connection; mark the second-kind run terminal with a `blocked` classification; reserve another run of that kind. Assert `user_version` is 4, `state.sqlite3.v3.bak` exists and opens at the previous schema, `run.kind` is backfilled, all prior rows and reporting progress are intact, the pause survives, the global hold now exists as `quota:codex` and still blocks eval admission, an injected mid-migration failure leaves the database at v3 untouched, one reserved run per kind succeeds and the second of the same kind fails atomically with no partial row, a terminal blocked run frees the slot, and `nonterminal_runs(kind=...)` filters. Since the fix handler is not part of this task, the second kind may be a test-only kind value written through the store. Execution: extend `tests/integration/test_durable_claims.py` or add `tests/integration/test_per_kind_slots.py`; `uv run pytest`.
- `INT-009` (Harness branch resolved per claim and rendered in eval Refs): `EvalHandler.accept` and reporting over a real temporary `agent-evals` remote and local checkout with the GitHub stub. Harness remote at commit A; admit an eval; advance the remote to B; force a technical failure and retry; admit a second fresh claim. Assert the first claim and its retry record and use A, the second records B, both cards' Refs read `runner@… skills@… evals@…` with the respective commits, the frozen-inputs comment carries the harness commit, and a shared configuration containing `harness_sha` fails to load naming `harness_ref`. Execution: extend `tests/integration/test_revision_resolution.py`; `uv run pytest`.

## Done When

- `SCHEMA_VERSION == 4`; the v3→v4 migration runs in one transaction after writing `state.sqlite3.v3.bak`; a fresh database creates the per-kind index directly; a newer `user_version` is refused.
- `reserve_run` enforces one nonterminal run per kind; `nonterminal_runs(kind=...)` filters; `blocked` is a valid claim lifecycle and a blocked terminal run holds no slot.
- Quota holds are keyed `quota:<provider>` and consulted through `handler.providers(claim)`; readiness holds are keyed `readiness:<kind>`; `status` prints them; pause and free-disk remain global.
- `runtime.cycle` and `tick` admit per registered kind with the per-kind slot guard and per-handler window.
- `EvalConfig.harness_ref` replaces `harness_sha`; a leftover `harness_sha` or a SHA value fails configuration loading naming `harness_ref`; `config/codagent.toml` and the three docs are updated; `doctor` prints `harness branch <ref> → <sha>`.
- Every eval claim records `revisions.evals` resolved at its own admission and renders `evals@<7>` in Refs.
- INT-001 and INT-009 pass; `uv run pytest`, `uv run ruff check .`, and `uv run pyright` pass; pre-existing eval tests still pass.
