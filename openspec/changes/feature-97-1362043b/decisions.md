# Decisions: feature-97-1362043b

## propose: verdict

- **Decision:** go with caveats. Build a declarative registry of eval revision inputs that is
  behavior-preserving, and be explicit that inputs with their own semantics still need small hooks.
- **Alternatives considered:**
  - No-go, and keep hand-threading with review. Rejected: FEATURE-36 and FEATURE-91 show the
    per-input cost and the silent-omission risk (`refs_text`, `HONORED_REVISIONS`).
  - Only centralize the name tuples into constants. Rejected: it fixes ordering drift but leaves
    resolve, freeze, and report wiring hand-threaded.
- **Decision-bearing:** no. The issue asks for this refactor.

## propose: persisted data and operator-visible output stay byte-identical

- **Decision:** the frozen-spec shape and key order, request fingerprint, `Refs`, frozen-inputs
  comment, repetition reports, `honored-revisions` output, and suite argv do not change. A
  golden-output regression test enforces this.
- **Alternatives considered:** let the registry normalize persisted or reported output (for example
  a uniform per-input frozen-inputs line). Rejected: that would change persisted claim data and
  operator-visible output, which is the direction-level concern behind #95's decline, and would
  need claim migration and rollback guards.
- **Decision-bearing:** yes. It keeps the change inside "no public interface or persisted format
  change", so no definition stop is needed. A reviewer who wants output normalized should say so.

## propose: registry scope is revision inputs only

- **Decision:** the registry covers the five revisions `runner`, `skills`, `evals`, `validator`, and
  `fixture`. Roles, `skip_validator`, and `repetitions` keep their current parsing.
- **Alternatives considered:** register every eval request setting. Rejected: those settings are
  not refs and do not share the resolve-freeze-report path. The issue's touch points and its "new
  ref input" estimate are all about refs.
- **Decision-bearing:** no.

## propose: pull-request work kind revisions out of scope

- **Decision:** the revisions of fix, feature, and task claims (`target`, `runner`, `skills`) are
  not moved onto the registry.
- **Alternatives considered:** a shared registry across work kinds. Rejected: the issue is about
  eval inputs, and the pull-request kinds have different freeze and resume semantics.
- **Decision-bearing:** no.

## propose: no capability spec deltas

- **Decision:** no new or modified capabilities. The existing eval intake, execution, reporting,
  and operations scenarios are the acceptance contract.
- **Alternatives considered:** add a non-behavioral "single registry" requirement to
  `factory-eval-intake`. Rejected: it describes code structure, not observable behavior.
- **Decision-bearing:** no.

## propose: placement and shape details deferred to design

- **Decision:** `design.md` decides where the registry module lives (avoiding an eval and and-scene
  import cycle), the hook signatures, and whether `SourceRepositories` becomes name-keyed. The
  admission resolver returns a name-keyed mapping instead of a positional tuple.
- **Alternatives considered:** fix the issue's suggested `EVAL_INPUTS` field list verbatim.
  Rejected: the issue calls it an idea, not a requirement, and the Validator and fixture need extra
  hooks.
- **Decision-bearing:** no.

## proposal-review: PR-1 (registry-only claim overstated end to end): applied

- **Decision:** applied. Defined an *ordinary input* as an optional, request-settable ref resolved
  in a local source checkout that reaches the suite only through suite argv. The and-scene adapter
  builds that argv once, and the Fly launcher carries it to the guest unchanged, as the fixture
  shows. For other inputs the proposal now states the boundary: Fly guest cloning (runner and
  skills only), the claim-image build (Validator only), and local `[repositories]` configuration
  stay explicit per-input code. A new checkout needs a config key. The sample-input test now runs
  through admission, Docker and Fly planning, and the Fly guest job script.
- **Alternatives considered:** make the guest clone list, the image builder, and `RepositoryConfig`
  registry-driven. Rejected: that changes Fly backend and shared configuration code that the fix,
  feature, and task kinds also use, which goes beyond a behavior-preserving eval refactor.
- **Decision-bearing:** no. It narrows the claim and keeps the scope.

## proposal-review: PR-2 (derived HONORED_REVISIONS does not protect new inputs): applied

- **Decision:** applied by limiting the claimed benefit. A derived `HONORED_REVISIONS` keeps
  `honored-revisions` accurate and preserves the existing fixture guard and its messages. A future
  persisted input needs its own guard. A generic rollback check is listed as out of scope.
- **Alternatives considered:** add a generic check that compares every revision pinned by
  unfinished claims with the target's honored set. Rejected for this change: it would newly refuse
  some rollbacks (for example past Validator support) and change deploy output, which breaks the
  behavior-preserving constraint. It is a candidate follow-up.
- **Decision-bearing:** no.

## proposal-review: PR-3 (missing combined Validator-plus-fixture golden case): applied

- **Decision:** applied. Added a Fly claim pinned to both Validator and fixture to the golden-output
  cases, covering frozen-spec key order, `Refs`, frozen-inputs, repetition text, Fly manifest, and
  suite argv.
- **Alternatives considered:** none. It is the only existing shape with both optional revisions
  and `sources`.
- **Decision-bearing:** no.

## spec: express the behavior-preserving contract as ADDED cross-release requirements

- **Decision:** add one requirement to `factory-eval-intake` ("Keep frozen evaluation inputs
  compatible across releases") and one to `factory-eval-reporting` ("Report frozen inputs
  identically across releases"). Both are measured against the previous release, the one that
  introduced `fixture_ref`. The proposal's Capabilities section is updated to list these two
  deltas instead of "None".
- **Alternatives considered:**
  - No spec deltas, as the proposal first stated. Rejected: OpenSpec rejects a change without
    deltas ("Change must have at least one delta"). Deploy and rollback compatibility for
    in-flight claims is also observable operator behavior that deserves explicit scenarios.
  - MODIFIED copies of the existing freeze and reporting requirements. Rejected: their text does
    not change, and copying them would invite accidental drift.
  - A `factory-operations` delta for `honored-revisions`. Rejected: the existing deploy-guard
    requirement already covers it. The exact names and order are pinned as an intake scenario.
- **Decision-bearing:** no. It records the proposal's existing constraint as testable behavior.

## spec: exact names and order of honored-revisions output

- **Decision:** the requirement pins `runner`, `skills`, `evals`, `validator`, `fixture`, one per
  line in that order, which is the current output.
- **Alternatives considered:** require only that the same set of names is printed. Rejected: the
  proposal promises byte-identical operator output, and the order costs nothing to keep.
- **Decision-bearing:** no.

## design: registry location is `suites/and_scene/inputs.py`

- **Decision:** the registry lives in the and-scene suite package. The exception classes move to
  `suites/and_scene/errors.py`. `FIXTURE_REPOSITORY`, `resolve_fixture`, `github_https_origin`,
  `validator_source_url`, and `_public_diagnostic` move to `inputs.py`, and every one stays
  re-exported under its old import path.
- **Alternatives considered:**
  - `work_kinds/eval/inputs.py`: the adapter would import the work kind, reversing the dependency
    direction, or would need the registry injected everywhere.
  - A neutral top-level module: it would still depend on and-scene constants and resolvers.
- **Decision-bearing:** no. This is internal structure.

## design: entry shape is data fields plus optional callables, with separate admission rank

- **Decision:** each `RevisionInput` is a frozen dataclass with declared fields. Optional callables
  carry input-specific text and behavior: the resolver, the source URL, suite arguments, the
  frozen-inputs text, the report line, and the execution hold. Registry order (runner, skills,
  evals, validator, fixture) is the persisted and reported order. `admission_rank` keeps today's
  resolution order (runner, skills, validator, fixture, harness).
- **Alternatives considered:**
  - A subclass per input. Rejected as heavier than five entries need.
  - Reordering admission to registry order. Rejected: it changes which reason is posted when
    several revisions fail at once.
- **Decision-bearing:** no.

## design: name-keyed Resolution and freeze mapping; protocol return type widened

- **Decision:** `resolve_request` returns `Resolution(revisions, sources)`, and
  `ParsedRequest.freeze(revisions, *, suite, sources=None)` replaces the per-input keyword
  arguments. `WorkKindHandler.resolve_request`'s annotation widens to `object`. Tests that inject
  `resolve` or call `freeze` are updated mechanically.
- **Alternatives considered:** keep the `freeze` keyword arguments as a compatibility wrapper.
  Rejected: the wrapper is exactly the per-input threading the issue removes.
- **Decision-bearing:** no.

## design: SourceRepositories keeps named fields and adds `extra` and `checkout(name)`

- **Decision:** keep the named fields for existing construction. Registry-driven code uses
  `checkout(name)`, and new or test inputs can use `extra`.
- **Alternatives considered:** a fully name-keyed `SourceRepositories`. Rejected: it churns about
  15 test constructions and `PreparedWorktrees` with no behavior gain.
- **Decision-bearing:** no.

## design: sample input injected by monkeypatching `inputs.EVAL_INPUTS`

- **Decision:** consumers read `inputs.EVAL_INPUTS` as a module attribute, so a test can extend it
  with `monkeypatch`.
- **Alternatives considered:** an `inputs=` parameter on `parse_request`, `EvalHandler`, and
  `AndSceneAdapter`. Rejected as a production seam used only by tests.
- **Decision-bearing:** no.

## design: golden characterization tests land before the refactor

- **Decision:** the first implementation task captures golden outputs from the unchanged code.
  Cases: default Docker, Validator Fly, fixture Docker, Validator plus fixture Fly, legacy, invalid
  revisions, and each single resolution failure. The refactor keeps them green without editing
  them, and this is the evidence for the cross-release spec requirements.
- **Alternatives considered:** compare against a checkout of the previous release at test time.
  Rejected: it needs two installed releases in CI.
- **Decision-bearing:** no.

## design: no spec changes

- **Decision:** the design revealed no new behavioral implication. The existing spec deltas stand.
- **Alternatives considered:** none.
- **Decision-bearing:** no.

## test-plan: golden characterization tests captured from the unchanged code are the primary evidence

- **Decision:** INT-001 to INT-005 compare admission, freezing, rejection and readiness reasons,
  reporting, argv, the Fly manifest, and worktree records with golden files. The goldens are
  captured from the pre-refactor code (`ac887b9`) and committed under
  `tests/fixtures/eval_inputs_golden/`. Only volatile values (paths, ids, timestamps, nonce) are
  normalized.
- **Alternatives considered:** hand-written assertions per field. Rejected: they cannot prove
  byte-identity, especially JSON insertion order.
- **Decision-bearing:** no.

## test-plan: no new E2E; cross-release run allowed only in acceptance

- **Decision:** no new E2E obligation. The existing eval E2E journeys are the regression contract.
  Acceptance may build a temporary `origin/main` worktree and venv to run the real cross-release
  admit-and-continue check against a temporary state database.
- **Alternatives considered:** an automated E2E that installs two releases. Rejected: it is slow
  and not hermetic, and INT-005 already proves the persisted-format core.
- **Decision-bearing:** no.

## test-plan: acceptance envelope excludes the live service, Fly, and GitHub writes

- **Decision:** the acceptance envelope is local temporary directories and repositories with stub
  GitHub and Fly launchers. The live LaunchAgent, releases, config, and state, deploys, the Fly
  API, GitHub writes, and real suite or model runs are all off limits.
- **Alternatives considered:** a real Fly or Docker eval run. Rejected: it costs money and model
  quota without adding evidence beyond byte-identical argv and manifests.
- **Decision-bearing:** no.

## test-plan: limited test edits allowed

- **Decision:** existing tests may change only where they call the internal signatures this change
  replaces: `freeze` keyword arguments, tuple-returning `resolve` callables,
  `readiness(fixture_pinned=)`, and the `handler.subprocess.run` patch target. Their assertions do
  not change.
- **Alternatives considered:** none.
- **Decision-bearing:** no.

## approach-review: AR-1 (missing fixture checkout would silently drop the requested fixture): applied

- **Decision:** applied. A requestable optional entry is skipped only when the request omits its
  setting. When it is set, its resolver always runs, even with a `None` checkout, so the fixture
  keeps today's "and-scene checkout … is missing" readiness error and no claim is admitted. A
  configured-only optional entry (the Validator) is skipped only when its checkout is `None`.
  `RevisionInput.resolve` now accepts `Path | None`. INT-002 asserts that no claim is admitted and
  that the golden missing-checkout reason is posted.
- **Alternatives considered:** keep "skip when the checkout is None" for all optional entries.
  Rejected: it would admit a default-fixture evaluation for a fixture-pinned request, which changes
  behavior and contradicts the intake spec.
- **Decision-bearing:** no. It corrects the design to preserve existing behavior.

## approach-review: AR-2 (import-time HONORED_REVISIONS cannot reflect the INT-007 monkeypatch): applied

- **Decision:** applied the reviewer's preferred option. Replace the import-time constant with
  `honored_revisions()`, evaluated when the CLI command runs. INT-007 calls `cli.main()` in-process
  under the registry monkeypatch and expects the sample name last. INT-006 keeps the subprocess
  check of the five production names.
- **Alternatives considered:** keep the import-time tuple and have INT-007 assert only the
  registry-derived names. Rejected: the CLI output, which is what the deploy guard reads, would go
  untested for a new input.
- **Decision-bearing:** no. Production output is unchanged.

## tasks: one task, with golden capture as its first step

- **Decision:** `tasks.md` holds exactly one implementation task for the whole change, as
  instructed. Inside it, the work is ordered: capture and commit the goldens against the unchanged
  code first, then refactor, then add INT-006, INT-007, and the unit tests.
- **Alternatives considered:** separate tasks for golden capture and the refactor. Rejected: the
  workflow asks for one task. The ordering inside the task keeps the goldens trustworthy.
- **Decision-bearing:** no.
