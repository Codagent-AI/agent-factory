## Context

An eval claim freezes up to five revisions: `runner`, `skills`, `evals` (the agent-evals harness),
`validator`, and `fixture`. Each one is threaded by hand through request parsing, admission,
freezing, reporting, readiness, and the and-scene adapter.

| Concern | Where it lives today |
| --- | --- |
| Accepted keys, ref validation, effective settings | `work_kinds/eval/__init__.py`: `_KEYS`, `_validate_overrides`, `parse_request` |
| Freezing | `ParsedRequest.freeze(runner_sha=, skills_sha=, harness_sha=, validator_sha=, validator_source=, fixture_sha=)` |
| Admission resolution | `handler.resolve_revisions` returns a positional tuple `(runner, skills[, validator])`; `EvalHandler.accept` unpacks it, then resolves the fixture (`_resolve_fixture`) and the harness (`_resolve_harness_ref`) itself |
| Reporting | `EvalHandler.refs_text`, `frozen_inputs_event`, `attempt_message` → `_completion_message(fixture=)` |
| Readiness | `EvalHandler.prepare` (Validator Docker hold, `fixture_pinned=`), `AndSceneAdapter.readiness(fixture_pinned=)` |
| Suite wiring | `AndSceneAdapter.plan` (`_fixture_revision`, `--fixture-ref … --repo …`), `_revisions`, `_fly_manifest`, `GitWorktreeManager.prepare/remove`, `WorktreeCleanup.record`, `_recorded_worktrees` |
| Deploy guard | `HONORED_REVISIONS` → `agent-factory honored-revisions` (`cli.py` imports the tuple at module load) → `scripts/fixture-guard.sh` |

Constraints:

- **Byte-identical output.** The specs require it (`factory-eval-intake`: "Keep frozen evaluation
  inputs compatible across releases"; `factory-eval-reporting`: "Report frozen inputs identically
  across releases"). This covers:
  - the persisted `frozen_spec` (key names and insertion order of `settings`, `revisions`,
    `sources`) and the request fingerprint;
  - error and readiness messages;
  - suite argv and the Fly manifest;
  - the frozen-inputs comment, repetition reports, `Refs`, and the invalid-revisions event;
  - `honored-revisions` output;
  - the persisted worktree `preparation` and `cleanup` records (`runner`, `skills`, `evals`).
- **Admission order.** Revisions resolve in this order today: runner, skills, validator, fixture,
  harness. When two revisions fail together, the first failure's reason is the one posted, so this
  order is kept.
- **Import graph.** `work_kinds/eval/handler.py` imports `suites.and_scene` at module level. The
  and-scene package imports `config`, `controller`, and `store`, and none of those import
  `work_kinds.eval`. `work_kinds/eval/__init__.py` imports only the standard library.
  `runtime._resolve_revision` must be imported lazily, as it is today.
- **Out of scope.** The Fly guest (`fly/guest.py`) clones only runner and skills, and the
  claim-image build (`fly/transport.py`) consumes only the Validator. Both stay explicit, as do
  `config.py`'s `RepositoryConfig` and `LocalConfig` and `scripts/fixture-guard.sh`.

## Goals / Non-Goals

**Goals:**

- One ordered registry describes every eval revision input. The duplicated name tuples and
  per-input branches listed above derive from it.
- A new *ordinary input* is added by appending one registry entry and supplying its checkout. An
  ordinary input is an optional, request-settable ref, resolved in a local checkout, that reaches
  the suite only through argv. The sample-input test proves this end to end.
- Every observable output stays byte-identical, and golden tests captured from the pre-refactor
  code enforce it.

**Non-Goals:**

- New inputs, new request keys, or changed semantics.
- The revisions of pull-request work kinds.
- Registry-driving Fly guest cloning, the image build, local configuration, or the deploy guard.
- Non-revision settings (roles, `skip_validator`, `repetitions`).

## Approach

### Registry module

Add a new module, `src/agent_factory/suites/and_scene/inputs.py`. It owns the declaration of what
the and-scene suite can be pinned to and how each pin is resolved, delivered, and reported. The
eval work kind already depends on the and-scene package, so that dependency direction is unchanged.

At import time the module may import only the standard library and `agent_factory.suites.and_scene.errors`
(below). It must not import the and-scene package `__init__` or `work_kinds`. It imports
`runtime._resolve_revision` lazily inside resolver functions.

```python
@dataclass(frozen=True)
class RevisionInput:
    name: str  # key under frozen revisions; Refs label; honored-revisions
    noun: str  # name used in freeze validation messages ("harness" for evals)
    required: bool  # always frozen (runner, skills, evals) vs optional
    setting: str | None  # settings key carrying the ref (agent_runner_ref, ..., fixture_ref)
    requestable: bool  # the eval block may set `setting`
    has_default: bool  # effective settings carry EvalDefaults.<setting>
    admission_rank: int  # resolution order at admission
    resolve: Callable[
        [Path | None, str], str
    ]  # checkout (may be None), ref -> full SHA; raises ReadinessError
    executions: frozenset[str] = frozenset({"docker", "fly"})
    worktree: bool = False  # claim-owned worktree (runner, skills, evals)
    fly_commit: bool = False  # listed in the Fly manifest `commits` (and _revisions validation)
    source_url: Callable[[Path], str] | None = None  # recorded under frozen `sources`
    suite_arguments: Callable[[str], tuple[str, ...]] | None = None
    frozen_inputs_text: Callable[[Mapping[str, object]], str | None] | None = (
        None  # frozen spec -> text
    )
    report_line: Callable[[str], str] | None = None  # revision -> repetition-report line
    execution_hold: Callable[[str], str] | None = None  # revision -> readiness hold text


EVAL_INPUTS: tuple[RevisionInput, ...] = (runner, skills, evals, validator, fixture)
```

The registry tuple order is the persisted and reported order: runner, skills, evals, validator,
fixture. The entries:

| name | noun | required | setting | requestable | has_default | rank | other |
| --- | --- | --- | --- | --- | --- | --- | --- |
| runner | runner | yes | `agent_runner_ref` | yes | yes | 0 | `worktree`, `fly_commit` |
| skills | skills | yes | `agent_skills_ref` | yes | yes | 1 | `worktree`, `fly_commit` |
| evals | harness | yes | — | no | no | 4 | `worktree`, `fly_commit`; ref comes from configured `harness_ref` |
| validator | validator | no | `agent_validator_ref` | no | yes | 2 | `executions={"fly"}`, `fly_commit`, `source_url`, `frozen_inputs_text`, `execution_hold`; resolver prefixes `Agent Validator checkout: ` |
| fixture | fixture | no | `fixture_ref` | yes | no | 3 | resolver `resolve_fixture`, `suite_arguments`, `frozen_inputs_text`, `report_line` |

The entry callables reproduce today's text exactly:

- `validator.frozen_inputs_text` always returns `"\nAgent Validator: " + (sha or "published npm release (not pinned)")`.
- `fixture.frozen_inputs_text` returns the `\nFixture: \`<sha>\` (requested \`<ref>\`), selected by this request instead of the agent-evals pin.` statement only when a fixture revision is recorded.
- `fixture.report_line` returns `f"Fixture: {sha}"`.
- `fixture.suite_arguments` returns `("--fixture-ref", sha, "--repo", FIXTURE_REPOSITORY)`.
- `validator.execution_hold` returns today's "this claim pinned Agent Validator <7> at admission
  under Fly execution; …" message.

Moves that support this, each re-exported under its old import path:

- `ReadinessError`, `WorktreeError`, and `RecoveryStateError` move to `suites/and_scene/errors.py`.
  `suites/and_scene/__init__.py` re-exports them.
- `FIXTURE_REPOSITORY`, `resolve_fixture`, `github_https_origin`, `validator_source_url`, and
  `_public_diagnostic` move to `inputs.py`. `suites/and_scene/__init__.py` re-exports
  `FIXTURE_REPOSITORY`. `work_kinds/eval/handler.py` re-exports the other four, and its
  `_completion_message` keeps using `_public_diagnostic`. Existing imports in `operations.py` and
  the tests keep working.

Consumers read the registry as a module attribute (`inputs.EVAL_INPUTS`), not through
`from … import EVAL_INPUTS`. A test can then `monkeypatch.setattr(inputs, "EVAL_INPUTS", …)` to add
a sample entry without any production test seam. Small derived helpers live in `inputs.py`:

- `by_name(name)`;
- `admission_order()`, which sorts by `admission_rank`;
- `worktree_names()`, which today yields runner, skills, evals.

### SourceRepositories

`SourceRepositories` keeps its named fields `runner`, `skills`, `evals`, `validator`, and
`fixture`, so existing positional and keyword construction still works. It gains:

- `extra: Mapping[str, Path] = {}`;
- `checkout(name) -> Path | None`, which returns the named field when one exists and otherwise
  `extra.get(name)`.

Registry-driven code always calls `checkout(name)`. `EvalHandler.from_config` keeps building it from
`local.repositories`, nulling any checkout whose entry's `executions` excludes the configured eval
execution (today, Validator under Docker). A future input with a new `[repositories]` key adds that
config field explicitly, as the proposal's boundary states. The sample test passes its checkout
through `extra`.

### Request parsing (`work_kinds/eval/__init__.py`)

- `_KEYS` becomes the setting of every `requestable` entry, plus the roles, `skip_validator`, and
  `repetitions`.
- `_validate_overrides` checks the non-empty-string rule for each `requestable` entry in registry
  order (runner, skills, fixture, as today), then roles, `skip_validator`, and `repetitions`,
  unchanged.
- `parse_request` builds the effective settings with one key per `has_default` entry in registry
  order (`agent_runner_ref`, `agent_skills_ref`, `agent_validator_ref`), each read from
  `getattr(defaults, entry.setting)`. Then it adds `roles`, `skip_validator`, and `repetitions`,
  and applies the overrides in document order, as today. Insertion order and the fingerprint (still
  computed from the parsed overrides) are unchanged.
- The `HONORED_REVISIONS` constant is replaced by `honored_revisions() -> tuple[str, ...]`, which
  returns `tuple(entry.name for entry in inputs.EVAL_INPUTS)` each time it is called.
  `cli.py` calls it when the `honored-revisions` command runs instead of importing a tuple fixed at
  module load. A registry monkeypatch in a test is therefore reflected when `cli.main()` runs
  in-process. The printed output for the production registry is unchanged.
- `ParsedRequest.freeze(revisions: Mapping[str, str], *, suite: str, sources: Mapping[str, str] | None = None)`
  replaces the per-input keyword arguments. It walks the registry in order and checks:
  - every `required` entry is present and is a full SHA, with message
    `"{noun} revision must be a full commit SHA"`;
  - every optional entry, when present, is a full SHA;
  - an entry with `source_url` has a non-empty source, with message
    `"{name} source is required with its revision"`;
  - an optional `requestable` entry has its setting in `settings`, with message
    `"{name} revision requires {setting}"`.

  It then writes `version`, `suite`, `settings`, `revisions` (registry order, present entries
  only), and `sources` (only when non-empty), in today's key order.

### Admission (`work_kinds/eval/handler.py`)

`resolve_revisions(sources, request, *, configured_refs)` returns
`Resolution(revisions: dict[str, str], sources: dict[str, str])`. It walks `admission_order()`:

- An optional entry is skipped only when it is legitimately inactive:
  - a **requestable** entry (the fixture) is skipped exactly when its setting is absent from
    `request.settings`. When the request sets it, its resolver is always called, even if
    `sources.checkout(name)` is `None`. A missing checkout therefore fails admission with the
    resolver's actionable error, as `resolve_fixture`'s "is missing; clone … or set
    [repositories] and_scene" message does today, and the requested revision is never silently
    dropped;
  - a **configured-only** entry (the Validator) is skipped exactly when its checkout is `None`.
    `from_config` nulls that checkout when the eval execution is outside the entry's `executions`,
    which today means the Validator under Docker.
- The ref comes from `request.settings[entry.setting]`, or from `configured_refs[entry.name]` when
  the entry has no setting. That is `{"evals": harness_ref}`.
- The entry's resolver runs against `sources.checkout(entry.name)`, which may be `None` for a
  requestable entry. Resolvers whose checkout is never `None` (runner, skills, harness) may assert
  that it is set. Their error messages are today's
  messages (raw for runner, skills, and harness; prefixed for the Validator; `resolve_fixture`'s
  own prefixes for the fixture).
- `source_url`, when declared, is recorded under `sources`.

When `EvalHandler.sources` is `None`, as in unit tests built without checkouts, today's
pass-through is kept: `resolve_request` raises as it does now, and tests inject `resolve`.

`EvalHandler.resolve_request` returns that `Resolution`. `EvalHandler.accept` becomes:
parse → `resolver(request)` → `request.freeze(resolution.revisions, suite=…, sources=resolution.sources)`.
`_resolve_fixture` and `_resolve_harness_ref` disappear into the registry walk. The
`WorkKindHandler.resolve_request` protocol return type in `work_kinds/base.py` widens from
`tuple[str, ...]` to `object`. `accept` already receives `resolve: object`, and the pull-request
handler keeps returning its tuple.

### Reporting and readiness (`handler.py`)

- `refs_text` walks the registry in order:
  - a `required` entry is invalid if it is missing or not a non-empty string (today's looser rule
    for required revisions);
  - an optional entry is invalid if it is present and not a full SHA.

  Because every required entry precedes every optional one, the invalid list keeps today's order.
  `Refs` text is `name@sha[:7]` for present entries, in registry order.
- `frozen_inputs_event` builds the JSON block as today, then appends each entry's
  `frozen_inputs_text(frozen_spec)` in registry order, skipping `None`.
- `attempt_message` collects `report_line(sha)` for recorded revisions in registry order.
  `_completion_message(unit_key, result, *, pinned_lines: Sequence[str] = ())` inserts them where
  the `Fixture:` line sits today.
- `prepare` puts a claim on hold with `execution_hold(sha)` for the first recorded revision whose
  entry's `executions` excludes the current eval execution. It passes the set of recorded revision
  names to the adapter's readiness.

### and-scene adapter (`suites/and_scene/__init__.py`)

- `readiness(worktrees, *, pinned: Collection[str] = ())` replaces `fixture_pinned`. Each pinned
  entry with `suite_arguments` must have every `--flag` it emits accepted by `run.sh`. A flag is
  accepted when `run.sh` has a case line matching `^[ \t]*<flag>\)`. The check is built
  generically from the entry's arguments by taking the elements that start with `--`. The message
  stays `selected and-scene harness <12> does not accept <flags joined by " and ">, which this claim's frozen <name> revision needs`.
  The fixture still gives `--fixture-ref and --repo`.
- `plan` replaces `_fixture_revision` with a registry walk. Each recorded revision whose entry has
  `suite_arguments` is validated as a full SHA, raising
  `ReadinessError("accepted {name} revision is not a full commit SHA")`, and its arguments are
  appended after the role arguments and before `--skip-validator`, as today.
- `_revisions` validates the `fly_commit` entries that are required or present, and raises
  `WorktreeError("accepted {name} revision is not a full commit SHA")`. It returns them in registry
  order and is used for worktrees and for the manifest's `commits`. The fixture is excluded, as
  today.
- The worktree loops in `GitWorktreeManager.prepare` and `remove`, `WorktreeCleanup.record`,
  `_recorded_worktrees`, and the `worktrees` and `repositories` maps of `_fly_manifest` iterate
  `worktree_names()`, using `getattr` on the named `PreparedWorktrees` and `SourceRepositories`
  fields. `git_common_dirs`, `guest_paths`, the dry-run manifest, and the `--agent-*-dir` argv stay
  explicit. They are the Fly guest and suite structure, not per-input wiring.
- `_fly_manifest` keeps `validator_repository` explicit, since it is the image-build boundary.

### Data flow

```text
issue body ──parse_request──► ParsedRequest(settings, fingerprint)
                                   │
       EVAL_INPUTS (admission order) ▼
  resolve_revisions ──► Resolution{revisions, sources} ──freeze──► frozen_spec (registry order)
                                                                     │
         ┌───────────────────────────────┬──────────────────────────┼─────────────────────────┐
         ▼                               ▼                          ▼                         ▼
  refs_text / frozen_inputs_event   prepare (execution holds,   adapter.plan (worktrees,    honored-revisions
  attempt_message (report lines)    pinned → readiness)         suite_arguments, manifest)  (names)
```

## Decisions

1. **Put the registry in `suites/and_scene/inputs.py`.** Alternatives:
   - `work_kinds/eval/inputs.py`: the adapter would have to import the work kind, which reverses
     the dependency direction, or receive the registry by injection everywhere.
   - A neutral top-level module: it would still need `FIXTURE_REPOSITORY` and the resolvers from
     the and-scene side.

   The and-scene package already owns the suite's arguments and checkouts, and the eval kind is
   already and-scene-specific.
2. **Plain data fields plus small optional callables**, rather than a subclass per input or a
   plugin protocol. Five entries do not justify a class hierarchy, and callables keep the
   input-specific text in the entry.
3. **Separate `admission_rank` from registry order.** Persisted and reported order (registry order)
   differs from today's resolution order (harness last). Reordering admission would change which
   reason is posted when several revisions fail at once.
4. **A name-keyed `Resolution` replaces the positional tuple.** Freeze takes a mapping. This
   removes the `resolved[2] if len(resolved) > 2` unpacking and the per-input keyword arguments.
   Tests that inject `resolve` are updated mechanically to return a `Resolution`.
5. **`SourceRepositories` keeps its named fields and adds `extra` and `checkout(name)`.** Making it
   fully name-keyed would churn about 15 test constructions and `PreparedWorktrees`, with no
   behavior gain. `extra` lets a test or future input supply a checkout without a new field.
6. **Monkeypatch the module attribute for the sample input**, rather than threading an `inputs=`
   parameter through `parse_request`, `EvalHandler`, and `AndSceneAdapter`. That parameter would
   add a production seam used only by tests.
7. **Keep explicit**: Fly guest cloning, the claim-image build, `git_common_dirs` and guest paths,
   the `--agent-*-dir` argv, local `[repositories]` parsing, `EvalDefaults`' named fields, and
   `fixture-guard.sh`. This matches the proposal's ordinary-input boundary.

## Risks / Trade-offs

- **Silent output drift**, for example dict insertion order, a reworded message, or `sources`
  appearing when empty. Mitigation: before any refactoring, the first task adds characterization
  tests and captures golden outputs from the unchanged code. The refactor must keep them green
  without editing the golden files. Golden cases:
  - default Docker;
  - Validator-pinned Fly;
  - fixture-pinned Docker;
  - Fly with both Validator and fixture pinned;
  - a legacy claim with no Validator;
  - invalid saved revisions;
  - each single-revision resolution failure.

  Each case captures the `frozen_spec` JSON, fingerprint, `Refs`, frozen-inputs comment, repetition
  report, invalid-revisions event, argv, Fly manifest (minus nonce and paths), `honored-revisions`
  output, readiness messages, and the worktree `preparation` and `cleanup` records.
- **Moving functions breaks the one test** that patches `agent_factory.work_kinds.eval.handler.subprocess.run`
  (`tests/integration/test_fixture_resolution.py`). It is retargeted to
  `agent_factory.suites.and_scene.inputs.subprocess.run`, which is a mechanical update.
- **Import cycle** if `inputs.py` imports the package `__init__` at module level. Mitigation:
  `errors.py` and lazy imports. The test suite imports every module, so a cycle fails fast.
- **Over-generalization.** Some callables (`execution_hold`, `frozen_inputs_text`) have exactly one
  user. This is accepted: they keep per-input text in one place, which is the issue's goal.

## Migration Plan

No data migration. Persisted claims, request fingerprints, and every reported string are
unchanged, so this release can be deployed or rolled back with unfinished claims in flight. The
deploy fixture guard sees identical `honored-revisions` output. No operator or documentation
changes are needed beyond a short note in `AGENTS.md` or `docs/operations.md` (implementer's
choice). The note points to `suites/and_scene/inputs.py` and states the ordinary-input boundary.

## Open Questions

None.
