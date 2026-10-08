## Why

Each revision an eval claim freezes (Agent Runner, Agent Skills, the agent-evals harness, Agent
Validator, and the and-scene fixture) is threaded through the code by hand. Its name, request key,
default, source checkout, resolver, validation, report label, and suite wiring are repeated in
separate places that must stay in agreement. The two most recent inputs show the cost:

- Agent Validator (FEATURE-36) was added as a third positional slot in the `resolve_revisions`
  tuple, which `accept` unpacks with `resolved[2] if len(resolved) > 2`.
- `fixture_ref` (FEATURE-91, #93) touched about 12 places across 5 source files for one optional
  ref: `_KEYS`, the `_validate_overrides` key tuple, `ParsedRequest.freeze` keyword arguments and
  their checks, `EvalHandler.accept`, `SourceRepositories`, `RepositoryConfig` and `LocalConfig`
  defaults, `refs_text` (twice, for validity and for ordering), `frozen_inputs_event`,
  `_completion_message` and `attempt_message`, the `fixture_pinned` readiness argument in `prepare`,
  `_fixture_revision` and the argv in `AndSceneAdapter.plan`, and `HONORED_REVISIONS`.

Today the revision names are repeated as string literals. `("runner", "skills", "evals")` is
spelled separately in `refs_text`, `_revisions`, `GitWorktreeManager.prepare` and `remove`, and
`_fly_manifest`. The optional names `("validator", "fixture")` are spelled again in `refs_text` and
`HONORED_REVISIONS`. A missed spot is a silent bug: if a new revision is frozen but not added to
`refs_text`, it is not reported, and if it is not added to `HONORED_REVISIONS`, the
`honored-revisions` command misreports what the release can honor. More eval inputs are likely, because each one lets an evaluation test a
candidate change before it is pinned.

**Verdict: go with caveats.** The duplication is real, recent, and growing, and the refactor stays
inside this repository's eval work kind and and-scene adapter. The caveats:

1. The refactor must be behavior-preserving. Persisted claim data, the request fingerprint, and all
   operator-visible output stay byte-identical. The issue was re-filed from #95 because Task triage
   declined a change that touches persisted claim data and operator-visible output. This proposal
   keeps both unchanged, so no claim migration, rollback restriction, or report change is needed.
2. The issue's "about 10 lines plus a test" estimate holds only for an *ordinary input*: a ref,
   optionally set by the request, that is resolved to a commit in a local source checkout and
   reaches the suite only through suite argv. The fixture is today's example. Suite argv is built
   once by the and-scene adapter and carried unchanged to Docker and to the Fly guest, so an
   ordinary input needs no backend change. Even an ordinary input still needs:
   - its registry entry;
   - a `[repositories]` key in local configuration if it needs a new source checkout, because
     `RepositoryConfig` and `LocalConfig` name checkouts individually and are shared with the
     fix, feature, and task kinds;
   - its spec and operator-doc entries.

   Inputs outside that shape still need explicit per-input code, which this refactor does not try
   to make registry-driven:
   - an input that needs its own admission check, such as the fixture's "published on the
     and-scene origin" check, needs a small hook;
   - an input the Fly guest must clone, as it clones only runner and skills in
     `fly/guest.py:job_script`, needs guest code;
   - an input built into the claim image, as only the Validator is in
     `fly/transport.py:build_claim_image`, needs image-builder code;
   - an input whose rollback must be refused needs its own deploy guard (see the next caveat).
3. Deriving `HONORED_REVISIONS` from the registry keeps `honored-revisions` accurate. It does not
   extend rollback protection to new inputs, because `scripts/fixture-guard.sh` checks only the
   literal `fixture` name. The existing fixture guard and its message are preserved unchanged. A
   generic rollback check for every persisted revision is a separate, behavior-changing follow-up.

The alternative, leaving the code alone and relying on review, has already let FEATURE-91 need a
second pass (`4f2f88d`) to cover fixture argv across Docker and Fly retries. A lighter alternative
would only centralize the name tuples into constants. That fixes ordering drift but leaves the
resolve, freeze, and report wiring hand-threaded, which is most of the per-input cost.

## What Changes

- Add one declarative registry of eval revision inputs. Each entry declares:
  - its revision name (the key under the frozen `revisions`);
  - its request key, if a request can set it, and whether it is optional;
  - its configured default and source checkout;
  - how it is resolved and validated at admission, plus any input-specific admission check;
  - how it reaches the suite (claim worktree, `--flag` argv, or Fly image source);
  - its label in `Refs`, the frozen-inputs comment, and repetition reports.
- Derive these from the registry instead of maintaining them by hand:
  - accepted request keys and ref validation;
  - admission resolution and freezing, replacing the positional `resolve_revisions` tuple and the
    per-input `freeze` keyword arguments;
  - the source-checkout mapping, the `revisions` and `sources` entries of the frozen spec, and
    revision validation in `refs_text`;
  - the `Refs` ordering, frozen-inputs lines, and repetition-report lines;
  - worktree preparation, suite argv, and Fly manifest revision handling (`commits`,
    `worktrees`, `repositories`, and the Validator source), with the existing manifest shape
    unchanged;
  - `HONORED_REVISIONS`, and so the output of the `honored-revisions` command that the deploy
    fixture guard reads. The guard script itself is unchanged.
- Keep all five existing inputs (`runner`, `skills`, `evals`, `validator`, `fixture`) working
  exactly as they do now. Their existing differences become declared properties of each entry:
  - `evals` is configured only, never requested;
  - `validator` comes from shared configuration and is pinned only under Fly execution;
  - `fixture` is requested only, optional, must be published on the and-scene origin, and is passed
    as `--fixture-ref` with `--repo`.
- Add a regression test that pins the current observable contract: frozen-spec JSON (including key
  order), fingerprint, `Refs` text, the frozen-inputs comment, repetition reports, the
  `honored-revisions` output, the Fly manifest, and suite argv. Run it for:
  - default claims;
  - Validator-pinned claims;
  - fixture-pinned claims;
  - Fly claims pinned to both Validator and fixture, the only existing shape with both optional
    revisions and a `sources` object;
  - claims frozen before this change.
- Add a test-only sample ordinary input, declared only through a registry entry and a test source
  checkout. Exercise it through admission, freezing and reporting, Docker execution planning, Fly
  execution planning (manifest and argv), and the Fly guest job script. This shows that a new
  ordinary ref needs no other source change.

No **BREAKING** changes. The request format, persisted claim format, CLI output, and reports are
unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

This is a behavior-preserving refactor. The existing requirements and scenarios in
`factory-eval-intake`, `factory-eval-execution`, `factory-eval-reporting`, and `factory-operations`
(the deploy fixture guard) are its acceptance contract and stay unchanged. The only delta adds one
requirement to `factory-eval-intake`: a deploy or rollback does not change a claim's frozen inputs
or the reports rendered from them. It names no release and lists no inputs, so adding an eval
input does not require editing it.

## Technical Approach

The registry is an ordered, immutable sequence of frozen dataclass entries. Its order is today's
revision order (runner, skills, evals, validator, fixture). Freezing, `Refs`, and the frozen-inputs
JSON all follow that order, so their output stays byte-identical. Each entry has plain declared
fields plus a few optional callables for input-specific behavior. The fixture's publication check
and the Validator's source URL and Docker-incompatibility readiness message are examples. A new
ordinary input (see caveat 2) therefore needs no new code paths. Fly guest cloning, the
claim-image build, local configuration parsing, and the deploy guard stay explicit per-input code.
Their inputs are not ordinary, and making them registry-driven would change shared or backend
code beyond this refactor.

At admission, the handler resolves each applicable entry through its declared resolver into a
name-keyed result. That replaces the positional tuple that `accept` unpacks today. The
injectable `resolve` callable that tests use returns the same name-keyed mapping.
`ParsedRequest.freeze` takes that mapping, validates each SHA, and writes `revisions` (and
`sources` where an entry declares one) in registry order.

The registry must be importable by both the eval work kind and the and-scene adapter without an
import cycle. `design.md` decides where it lives and how suite-specific wiring (argv flags, Fly
manifest fields) attaches to it. It also decides whether `SourceRepositories` becomes name-keyed or
keeps named fields filled from the registry. Non-revision request settings (roles,
`skip_validator`, `repetitions`) keep their current parsing. They are not refs and have no shared
resolve-freeze-report path.

Readers must keep accepting claims frozen before this change. Since the format does not change,
those claims stay valid and in-flight claims are unaffected by deploying or rolling back this
release. The deploy fixture guard keeps working because `honored-revisions` prints the same names.
That guard protects only the fixture, as it does today.

## Out of Scope

- New eval inputs, request keys, or changes to any input's semantics, defaults, or validation.
- Any change to the persisted `frozen_spec` shape, the request fingerprint, CLI output, issue or PR
  comments, Project fields, or the eval-request format.
- Fix, feature, and task (pull-request work kind) revisions (`target`, `runner`, `skills`) and their
  `refs_text` and `frozen_inputs_event`.
- Non-revision eval settings (roles, `skip_validator`, `repetitions`, `max_repetitions`).
- Generating specs or operator documentation from the registry.
- Making the Fly guest's source cloning, the claim-image build, or local `[repositories]`
  configuration registry-driven.
- A generic deploy rollback check over every persisted revision. `scripts/fixture-guard.sh` and its
  messages are unchanged.
- Changes in agent-evals, Agent Runner, Agent Validator, or and-scene.

## Impact

- **Code:** `src/agent_factory/work_kinds/eval/__init__.py` and `handler.py`,
  `src/agent_factory/suites/and_scene/__init__.py` (`SourceRepositories`, `GitWorktreeManager`,
  `plan`, `_revisions`, `_fixture_revision`, `_fly_manifest`), `src/agent_factory/cli.py`
  (`HONORED_REVISIONS` import), and possibly a new registry module. `config.py` keeps its local
  configuration keys and defaults. Only how the eval handler reads them may change.
- **Tests:** existing eval intake, resolution, reporting, Fly, CLI, and deploy-guard tests must pass
  unchanged, apart from mechanical updates to calls into internal signatures (`freeze`, the
  `resolve` callable). New tests cover the golden-output regression and the sample input.
- **Users and operators:** no visible change. No deploy precautions, claim migration, or rollback
  guard changes are needed.
- **Risk:** a subtle ordering or format drift in persisted or reported output, for example JSON key
  order in the frozen-inputs comment or the request fingerprint. The golden-output regression test
  across claim shapes is the main mitigation.
