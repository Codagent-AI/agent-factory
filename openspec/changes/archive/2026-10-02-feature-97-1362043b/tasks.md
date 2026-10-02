- [x] Refactor eval revision inputs into a declarative registry, with no change to any observable output

## Task: Declarative eval revision-input registry (behavior-preserving refactor)

Implement the whole change described in these files, in the change directory:

- `proposal.md`, including its caveats on the *ordinary input* boundary and the deploy guard;
- the delta specs under `specs/`:
  - `factory-eval-intake`: ADDED "Keep frozen evaluation inputs compatible across releases";
  - `factory-eval-reporting`: ADDED "Report frozen inputs identically across releases";
- `design.md`, which gives the module layout, the `RevisionInput` fields, the entry table, and the
  exact messages and orders;
- the decision log `decisions.md`. Where entries differ, later ones override earlier ones. In
  particular:
  - proposal-review PR-1 to PR-3 narrow the ordinary-input claim, limit the deploy-guard benefit,
    and add the combined Validator-plus-fixture golden case;
  - approach-review AR-1 (a requested fixture is never skipped for a missing checkout) and AR-2
    (`honored_revisions()` is evaluated when the command runs) override the design-stage text
    they revise;
- the automated obligations in `test-plan.md`: INT-001 to INT-007, and the existing E2E journeys
  it lists as the regression contract.

Always compare against `origin/main` (`ac887b9` at definition time). Never edit a release, the
service clone, the live `~/.agent-factory` state or configuration, or anything under
`/Users/paul/codagent/*`. Never run `scripts/deploy.sh` or `launchctl`, and never deploy. Make no
change outside this repository.

### Order of work

1. **Capture the goldens first, before touching production code.** Write
   `tests/integration/test_eval_inputs_golden.py` (INT-001 to INT-005) against the unchanged code.
   Record the outputs under `tests/fixtures/eval_inputs_golden/`, normalizing only temporary
   paths, claim and run ids, timestamps, and the Fly manifest `nonce`. Commit the golden files
   and tests while they pass on unchanged code. From then on, never edit a golden file. If a
   golden comparison fails during the refactor, the refactor is wrong.
2. Refactor, keeping the goldens and every existing test green.
3. Add INT-006 and INT-007 and the unit tests.

### Scope

1. **Errors module** (`src/agent_factory/suites/and_scene/errors.py`): move `ReadinessError`,
   `WorktreeError`, and `RecoveryStateError` there. `suites/and_scene/__init__.py` re-exports
   them under the same names.
2. **Registry** (`src/agent_factory/suites/and_scene/inputs.py`). At module import it uses only
   the standard library and `errors.py`. It imports `runtime._resolve_revision` lazily, and never
   imports the package `__init__` or `work_kinds`. It contains:
   - the frozen dataclass `RevisionInput` with the design's fields. `resolve` takes
     `(Path | None, str)`;
   - `EVAL_INPUTS = (runner, skills, evals, validator, fixture)`, matching the design's entry
     table (noun, required, setting, requestable, has_default, admission rank, executions,
     worktree, fly_commit, and callables);
   - helpers `by_name`, `admission_order` (runner, skills, validator, fixture, evals),
     `worktree_names`, and `honored_revisions()`, each reading `EVAL_INPUTS` at call time;
   - `FIXTURE_REPOSITORY`, `resolve_fixture`, `github_https_origin`, `validator_source_url`, and
     `_public_diagnostic`, moved here unchanged. `suites/and_scene/__init__.py` re-exports
     `FIXTURE_REPOSITORY`. `work_kinds/eval/handler.py` re-exports the other four. `operations.py`
     and existing test imports must keep working;
   - entry callables that reproduce today's text exactly:
     - the Validator's `Agent Validator checkout: ` resolver prefix, frozen-inputs line, source
       URL, and Docker execution hold;
     - the fixture's `resolve_fixture`, its `--fixture-ref <sha> --repo FIXTURE_REPOSITORY`
       arguments, its frozen-inputs statement, and its `Fixture: <sha>` report line.
3. **`SourceRepositories`**: keep the named fields. Add `extra: Mapping[str, Path]` (default
   empty) and `checkout(name) -> Path | None`, which returns the named field, else
   `extra.get(name)`. `EvalHandler.from_config` nulls a checkout when the eval execution is
   outside its entry's `executions`.
4. **Request parsing** (`work_kinds/eval/__init__.py`):
   - derive `_KEYS` from the registry;
   - `_validate_overrides` walks the requestable entries in registry order (the same messages);
   - `parse_request` places `has_default` settings in registry order from
     `getattr(defaults, setting)`. Settings insertion order and the fingerprint are unchanged;
   - replace the `HONORED_REVISIONS` constant with a call to `inputs.honored_revisions()`;
   - `ParsedRequest.freeze(revisions, *, suite, sources=None)` performs the design's checks, with
     the exact messages, and writes the payload keys in today's order. It adds `sources` only when
     that mapping is non-empty.
5. **Admission** (`work_kinds/eval/handler.py`):
   - `resolve_revisions(sources, request, *, configured_refs)` returns
     `Resolution(revisions, sources)` and walks `admission_order()`.
   - A requestable entry is skipped only when its setting is absent. When it is set, its resolver
     always runs, even with a `None` checkout (AR-1).
   - A configured-only entry is skipped only when its checkout is `None`.
   - The harness ref comes from `configured_refs["evals"]`.
   - `EvalHandler.resolve_request` returns the `Resolution`, and `accept` freezes from it.
     `_resolve_fixture` and `_resolve_harness_ref` are removed.
   - Widen the `WorkKindHandler.resolve_request` annotation in `work_kinds/base.py` to `object`.
6. **Reporting and readiness** (`handler.py`):
   - `refs_text`, `frozen_inputs_event`, and `attempt_message`, with
     `_completion_message(..., pinned_lines=())`, walk the registry as the design describes;
   - `prepare` uses each entry's `execution_hold`, and passes the names of the recorded revisions
     to the adapter's readiness.
7. **Adapter** (`suites/and_scene/__init__.py`):
   - `readiness(worktrees, *, pinned=())` checks the generic flags, with the same message, which
     still reads "--fixture-ref and --repo" for the fixture;
   - `plan` adds registry `suite_arguments` after the role arguments and before
     `--skip-validator`;
   - `_revisions` covers the `fly_commit` entries;
   - the worktree loops in `GitWorktreeManager.prepare` and `remove`, `WorktreeCleanup.record`,
     `_recorded_worktrees`, and `_fly_manifest`'s `worktrees` and `repositories` iterate
     `worktree_names()`;
   - `git_common_dirs`, `guest_paths`, the dry-run manifest, the `--agent-*-dir` arguments, and
     `validator_repository` stay explicit;
   - remove `_fixture_revision`, `_ACCEPTS_FIXTURE_REF`, and `_ACCEPTS_REPO`.
8. **CLI** (`cli.py`): `honored-revisions` prints `inputs.honored_revisions()` evaluated when the
   command runs. Its output is unchanged.
9. **Out of scope and left untouched:**
   - `fly/guest.py` and `fly/transport.py`;
   - `config.py`, including the `RepositoryConfig` and `LocalConfig` keys and defaults;
   - `scripts/fixture-guard.sh` and `scripts/deploy.sh`;
   - the pull-request work kinds;
   - roles, `skip_validator`, and `repetitions` parsing;
   - specs outside the two deltas.
10. **Documentation**: add a short note to `docs/operations.md` or `AGENTS.md` (your choice). It
    points to `suites/and_scene/inputs.py` as the place eval revision inputs are declared, and
    states the ordinary-input boundary. Anything that needs Fly guest cloning, the claim image, a
    new `[repositories]` key, or a rollback guard is still explicit code.
11. **Tests**:
    - Implement INT-001 to INT-007 at the locations, with the setup, and with the assertions
      `test-plan.md` gives. INT-002 includes the missing-fixture-checkout case (no claim, golden
      "is missing" reason). INT-007 calls `cli.main()` in-process under the registry monkeypatch.
    - Origins use the GitHub URL plus `url.<local bare>.insteadOf`.
    - Add unit tests for the derived key set, admission order, freeze validation messages,
      `SourceRepositories.checkout`, and `honored_revisions()`.
    - Existing tests change only where they call replaced internal signatures: `freeze` keyword
      arguments, tuple-returning `resolve` lambdas (which now return `Resolution`),
      `readiness(fixture_pinned=)`, and the `handler.subprocess.run` patch target in
      `tests/integration/test_fixture_resolution.py` (which becomes
      `agent_factory.suites.and_scene.inputs.subprocess.run`). Their assertions do not change.

### Done when

- Both delta-spec requirements and all their scenarios hold. The goldens captured from the
  unchanged code pass without edits for every claim shape:
  - default Docker;
  - Validator-pinned Fly;
  - fixture-pinned Docker;
  - Validator plus fixture Fly;
  - legacy;
  - invalid revisions;
  - each single resolution failure.
- The goldens cover the frozen spec and its key order, the fingerprint, the rejection and
  readiness reasons, `Refs`, the frozen-inputs comment, repetition reports, the invalid-revisions
  event, argv, the Fly manifest, the worktree records, and `honored-revisions`.
- No per-input name tuple or per-input branch from the design's Context table remains in the eval
  handler, request parsing, or adapter. The only exceptions are the explicit boundaries listed in
  scope item 7.
- A sample ordinary input added only through the registry (INT-007) is accepted, frozen, reported,
  passed in Docker and Fly argv and in the guest job script, and listed by `honored-revisions`.
- A `fixture_ref` request with no and-scene checkout is refused with today's message and is never
  admitted.
- The existing E2E journeys listed in `test-plan.md`, and the full suite, pass under
  `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build` pass.
  `agent-validate run` passes.
- No persisted-format, CLI-output, report, or deploy-script change is made, and nothing outside
  this repository changes.
