## Context

An eval request's fenced `eval` TOML block is parsed by `parse_request` in
`src/agent_factory/work_kinds/eval/__init__.py`. That function produces a `ParsedRequest` holding:

- `overrides`: the keys the request supplied;
- `settings`: the effective settings after defaults;
- `fingerprint`: a SHA-256 over the canonical overrides.

At admission, `EvalHandler.accept` (`work_kinds/eval/handler.py`) builds the frozen spec:

1. It calls the `resolve` callable that the runtime passes in, which is `handler.resolve_request`,
   which calls `resolve_revisions`. That returns a positional tuple: the Runner and Skills SHAs,
   plus the Validator SHA under Fly.
2. It resolves the harness branch itself through `_resolve_harness_ref`.
3. It calls `ParsedRequest.freeze`, which writes
   `{"version": 1, "suite", "settings", "revisions": {...}, "sources"?}`.

Any `ReadinessError` raised during `accept` reaches `runtime.py`. There,
`Controller.report_request_readiness` posts one "waiting for revision readiness" comment per
distinct reason string and applies the attention label. Later cycles retry.

`runtime._resolve_revision(source, ref)` has three steps:

1. It runs `git fetch --quiet --prune --tags origin`.
2. It maps the ref to candidates: a 7–40 character hex string is used as is; a branch becomes
   `refs/remotes/origin/<b>` and then `refs/tags/<ref>`.
3. It runs `rev-parse --verify <candidate>^{commit}` on each candidate.

A hex ref therefore resolves to any object present locally, whether or not it was ever pushed.

The and-scene suite (`AndSceneAdapter` in `src/agent_factory/suites/and_scene/__init__.py`) builds
the `run.sh` argv in `plan()` and checks readiness in `readiness(worktrees)`. `run.sh` already
accepts `--fixture-ref REF` (its option dispatch has a `--fixture-ref)` case). It forwards the ref
inside `CONTROLLER_ARGS` to `controller.mjs`, which clones `REPO` (default
`https://github.com/Codagent-AI/and-scene.git`) inside the sandbox or Machine and checks the ref
out. `--resume` revalidates the recorded fixture. The ref never reaches the Fly launcher's argument
grammar, and the Fly manifest's `commits` come from `_revisions()`, which reads only `runner`,
`skills`, `evals`, and `validator`.

Reporting uses three hooks:

- `EvalHandler.refs_text`, for the Project `Refs` field;
- `frozen_inputs_event`, which posts the frozen spec as JSON plus an "Agent Validator:" line;
- `attempt_message`, which builds per-repetition text through `_completion_message`.

Local configuration (`config.py`, `RepositoryConfig`) already has one optional checkout with a
sibling default, `agent_validator`.

`scripts/deploy.sh` does all failure-prone work that changes nothing before it pauses. It sources
testable helpers (`scripts/slots.sh`, `scripts/validator.sh`) that are exercised by
`tests/integration/test_deploy_*.py`. It reads status with the live release's executable
(`$running`) and builds the target release (`$executable`) before the pause.

## Goals / Non-Goals

**Goals:**

- Accept, resolve, publish-check, freeze, pass, and report an optional `fixture_ref`, following the
  existing Validator-revision pattern.
- Keep every default claim byte-for-byte unchanged: settings, frozen spec, comments, `Refs`, argv,
  and Fly manifest.
- Make the and-scene checkout optional and consulted only for fixture-selecting requests.
- Stop `scripts/deploy.sh` from making live a release that would drop frozen fixture revisions.

**Non-Goals:**

- Fixture worktrees on the host, changes to the Fly image, or changes to the Fly manifest.
- Request selection of `REFERENCE_REF`, `REPO`, the change name, the judge, or the harness.
- Changes to agent-evals, including the eval-request template (a cross-repository follow-up, see
  `proposal.md`) and the committed results format.
- A general capability or versioning framework for frozen specs beyond what the deploy guard needs.

## Approach

### 1. Parsing (`work_kinds/eval/__init__.py`)

- Add `"fixture_ref"` to `_KEYS`.
- In `_validate_overrides`, treat `fixture_ref` like the Runner and Skills refs: when present, it
  must be a non-empty `str`, otherwise `ValueError("fixture_ref must be a non-empty string")`.
- When building `effective`, add `"fixture_ref"` only when it was supplied. The defaults dict does
  not gain the key, so a default claim's `settings` JSON is unchanged. The generic
  `for key, value in parsed.items()` loop already copies it.
- The fingerprint already covers all supplied overrides, so no change is needed there.
- Add a module constant:

  ```python
  HONORED_REVISIONS = ("runner", "skills", "evals", "validator", "fixture")
  ```

  It names every frozen revision key this release acts on. The deploy guard reads it (step 7), and
  it lives next to the code that freezes those keys so that it cannot drift from them.
- Extend `ParsedRequest.freeze` with a keyword `fixture_sha: str | None = None`. When it is set:
  - `_sha(fixture_sha, "fixture")`;
  - require `"fixture_ref" in self.settings`, otherwise `ValueError`;
  - set `revisions["fixture"] = fixture_sha`.

  `version` stays `1`.

### 2. Fixture resolution (`work_kinds/eval/handler.py`)

Add `resolve_fixture(checkout: Path | None, ref: str) -> str` next to `resolve_revisions`. Every
error is a `ReadinessError` whose message starts with `and-scene checkout <path>: `, so each
distinct cause gets its own readiness comment. The steps:

1. **Checkout.** If `checkout` is `None`, not a directory, or has no `.git`, the error is
   `… is missing; clone https://github.com/Codagent-AI/and-scene.git there or set [repositories] and_scene`.
2. **Origin.** Read `remote.origin.url` and normalize it with a shared helper,
   `github_https_origin(checkout, label) -> str`. This is extracted from `validator_source_url`,
   which becomes a thin wrapper with unchanged messages. Compare the result case-insensitively
   with `FIXTURE_REPOSITORY` (defined in the and-scene suite module as
   `"https://github.com/Codagent-AI/and-scene.git"`, the suite's `REPO` default). If they differ,
   the error is `… origin <normalized-or-redacted> is not the and-scene fixture repository <FIXTURE_REPOSITORY>`.
   Messages never include credentials (the helper already redacts).
3. **Resolve.** Call `runtime._resolve_revision(checkout, ref)`. It fetches with `--prune --tags`
   and maps branches to `refs/remotes/origin/*`. Re-raise its errors with the checkout prefix.
4. **Publication.** The resolved SHA counts as published if either check passes:
   - `git for-each-ref --contains <sha> --format=%(refname) refs/remotes/origin/` prints any ref.
     After the pruning fetch, these refs mirror origin's branches.
   - Otherwise, `git ls-remote --tags origin` (timeout 60 seconds) lists origin's tags. Each
     tag's commit is the peeled `^{}` entry when present, otherwise the listed object. If any such
     commit exists locally and `git merge-base --is-ancestor <sha> <commit>` succeeds, the SHA is
     published.

   Local-only tags are never considered, because `--tags` fetching does not prune them. If neither
   check passes, the error is `… fixture commit <sha[:12]> is not published on <FIXTURE_REPOSITORY>; push it to a branch or tag there`.
   A failure or timeout of `ls-remote` raises `… cannot list tags on origin (git exit N); check remote access`.
5. Return the full SHA.

`SourceRepositories` gains `fixture: Path | None = None`. `EvalHandler.from_config` passes
`local.repositories.and_scene` in every execution mode. `GitWorktreeManager` keeps building its own
three-field `SourceRepositories`, so worktree handling is unaffected.

In `EvalHandler.accept`, after the `resolve` callable and before `_resolve_harness_ref`:

```python
fixture_ref = request.settings.get("fixture_ref")
fixture_sha = self._resolve_fixture(str(fixture_ref)) if fixture_ref is not None else None
```

`_resolve_fixture` mirrors `_resolve_harness_ref`. When `self.sources is None` (unwired handlers
in tests), it returns the ref unchanged, and `freeze` then insists on a full SHA. Otherwise it calls
`resolve_fixture(self.sources.fixture, ref)`. It then calls `request.freeze(..., fixture_sha=fixture_sha)`.

Resolution stays outside the positional `resolve` tuple on purpose. The tuple's optional third
Validator element already makes positions fragile. The fixture is resolved by the handler itself,
as the harness is, so the runtime contract `resolve_request -> tuple[str, ...]` is unchanged.

### 3. Configuration and doctor (`config.py`, `operations.py`)

- `RepositoryConfig.and_scene: Path | None = None`. `LocalConfig.from_file` sets it to
  `_path(repositories, "and_scene", ...)` when the key is present, else
  `runner_path.parent / "and-scene"`, exactly as for `agent_validator`.
- `config/local.example.toml` gains
  `# and_scene = "/srv/src/and-scene" # defaults to sibling of agent_runner; only fixture_ref requests use it`.
- `doctor` gains `_fixture_checkout_diagnostic(config)` in group `eval`. It is appended only when
  `include_informational` is true, so the runtime's admission gate never sees it. It is always
  `available=True`, with the detail marked "(informational)", and reports one of:
  - the checkout is present and its origin matches `FIXTURE_REPOSITORY`;
  - the checkout is missing;
  - the checkout is not a Git repository;
  - the origin differs: it names the normalized or redacted origin and says that `fixture_ref`
    requests wait for revision readiness until this is fixed.

  It reads `remote.origin.url` locally and never fetches.

### 4. Execution (`suites/and_scene/__init__.py`)

- Add `_ACCEPTS_FIXTURE_REF = re.compile(r"^[ \t]*--fixture-ref\)", re.MULTILINE)` and
  `_ACCEPTS_REPO = re.compile(r"^[ \t]*--repo\)", re.MULTILINE)`, mirroring
  `_ACCEPTS_NO_PUBLISH`.
- Change `readiness(worktrees, *, fixture_pinned: bool = False)`. When `fixture_pinned` is true and
  the pinned `run.sh` does not match both patterns, return
  `selected and-scene harness <harness commit[:12]> does not accept --fixture-ref and --repo, which this claim's frozen fixture revision needs`.
  An unreadable `run.sh` is already reported by the existing required-files check.
- Add `_fixture_revision(frozen) -> str | None`. It returns `frozen["revisions"]["fixture"]` when
  present, and raises `ReadinessError` if that value is not a full SHA. `_revisions()` is not
  changed, so the Fly manifest's `commits` and the worktree manager stay exactly as they are.
- In `plan()`, compute `fixture = _fixture_revision(frozen)` first, then call
  `self.readiness(worktrees, fixture_pinned=fixture is not None)`. After the role arguments, if
  `fixture` is set, add
  `arguments.extend(("--fixture-ref", fixture, "--repo", FIXTURE_REPOSITORY))`. Passing `--repo`
  binds the suite's clone to the repository admission certified the SHA against. Today's harness
  defaults `REPO` to the same URL, so its behavior is unchanged, while a harness with another
  default cannot clone a different repository. `run.sh` uses `REPO` for both the fixture and the
  reference clone, and both live in and-scene. Default claims get neither argument. This happens
  before
  `--skip-validator` and `--resume`, and it applies to initial, fresh-retry, and resume
  invocations alike, because they all go through `plan()`.
- In `EvalHandler.prepare`, call
  `self.adapter.readiness(worktrees, fixture_pinned="fixture" in revisions)`, so a held claim
  reports the problem before reservation.

Provenance has two sources:

- **Requested inputs:** the claim's frozen `settings.fixture_ref` and `revisions.fixture`.
- **Observed provenance:** the suite's own evidence in the repetition's artifact directory.
  `result.json` carries `candidate_source.fixture_commit`, written by `lib/result.mjs` from the
  controller's `candidate_source`. `proof-metadata.json`, written by `run.sh`'s `write_metadata`,
  carries `repo`, `fixture_ref`, and `fixture_commit`.

The factory already retains the artifact directory unmodified and reads `result.json` through
`read_result`. It adds no field and does not compare the observed commit with the frozen SHA: the
suite checks out the exact SHA and its resume revalidates it, so a comparison would only duplicate
the suite's own check.

### 5. Reporting (`work_kinds/eval/handler.py`)

- **`refs_text`.** If `"fixture"` is in the revisions, validate it as a full SHA exactly as
  `validator` is validated: on failure, add it to `invalid`, which records the existing
  `invalid-revisions` event and returns `None`. The key order is
  `runner, skills, evals, [validator], [fixture]`.
- **`frozen_inputs_event`.** The JSON dump and the "Agent Validator:" line are unchanged. When
  `revisions.fixture` exists, append:

  ```
  \nFixture: `<full sha>` (requested `<settings.fixture_ref>`), selected by this request instead of the agent-evals pin.
  ```

  Default claims get no new text. Their JSON is also unchanged, because neither `settings` nor
  `revisions` gained a key.
- **`attempt_message`.** Look up the claim with `self._store.get_claim(run.claim_id)`, when a
  store is attached. If the claim has a fixture revision, pass it to `_completion_message(...,
  fixture=sha)`, which adds `Fixture: <full sha>` directly after the "Product verdict" line.
  Otherwise the call and its output are unchanged.

### 6. Operator commands (`cli.py`)

These two read-only subcommands exist for the deploy guard:

- `agent-factory honored-revisions` needs no `--config`. It prints `HONORED_REVISIONS`, one key
  per line, and exits 0. Older releases do not have this subcommand: argparse exits 2, which the
  guard reads as "does not honor `fixture`".
- `agent-factory --config X pinned-claims --revision <key>` prints
  `<claim id>\t<repository>#<issue>` for each `eval` claim that meets both conditions:
  - its `lifecycle` is not in `TERMINAL_LIFECYCLES`;
  - its `frozen_spec.revisions` contains `<key>`.

  It exits 0 and prints nothing when there are none. It uses `ClaimStore.all_claims()` and never
  writes.

### 7. Deploy guard (`scripts/fixture-guard.sh`, `scripts/deploy.sh`)

A new sourced helper defines `fixture_guard <target-exe> <live-exe> <stage>`:

```text
target honors fixture?  ("$target" honored-revisions | grep -qx fixture)  -> return 0
live honors fixture?    no  -> warn "live release predates fixture revisions; none can be pinned"; return 0
list = "$live" --config "$config" pinned-claims --revision fixture
   fails             -> die "cannot list fixture-pinned claims with the live release …" (+ stage suffix)
   empty             -> return 0
   non-empty         -> die "<target> cannot honor frozen fixture revisions; unfinished claims: <list>.
                          Roll back by: 1. pause; 2. let each claim settle, or cancel it;
                          3. deploy the older release. The older release cannot accept fixture_ref:
                          stay on a fixture-capable release if a pinned evaluation is still needed;
                          a new request without the key evaluates only the default fixture."
                          (+ stage suffix)
```

`deploy.sh` sources the helper and calls it twice:

- **`before` stage:** right after the release is built and before `pause`. Nothing live has
  changed yet. The die message ends "nothing is deployed", and the pause state is untouched.
- **`after` stage:** right after `pause` and the Runner and Validator builds, immediately before
  `point_at`. The die message ends "the factory stays paused". The LaunchAgent, `shared_config`,
  and `releases/current` are untouched.

`--no-runner` and `--no-validator` do not affect the guard, and no bypass flag exists. The script
header comment documents the guard.

### 8. Documentation

`AGENTS.md` (Deploying, and "Code and models each kind of work uses") and `docs/operations.md`
document the following:

- the `fixture_ref` key, and that omitting it keeps the agent-evals pin;
- the and-scene checkout and its sibling default;
- the publication requirement, and that deleting the fixture branch fails later repetitions;
- how `Refs` and the frozen-inputs comment show a pinned fixture;
- that results from different fixtures are not comparable;
- the deploy refusal and rollback procedure, and that a hand rollback, or an older `deploy.sh`,
  bypasses the guard.

The specs that list eval request keys (`factory-eval-intake`) are updated through this change's
deltas.

### Data flow

```text
issue eval block ──parse_request──► settings.fixture_ref (only if supplied)
                                         │
EvalHandler.accept ──resolve_fixture──► and-scene checkout: origin check → fetch/rev-parse
                                         → published? (origin branches | origin tags)
                                         │ ReadinessError → one readiness comment + label, retry later
                                         ▼
frozen_spec.revisions.fixture = <sha>  (absent for default claims)
        │                       │                                  │
AndSceneAdapter.plan       refs_text / frozen_inputs_event     deploy.sh fixture_guard
  run.sh … --fixture-ref <sha> attempt_message
           --repo <FIXTURE_REPOSITORY>                     pinned-claims (live) vs
  (readiness: run.sh accepts it)  (fixture lines only if pinned)   honored-revisions (target)
```

## Decisions

- **Resolve the fixture in the handler, not through the `resolve` tuple.** The tuple's optional
  Validator element makes positions fragile, and the harness is already resolved by the handler.
  Unwired handlers pass the ref through, as the harness does, which keeps tests simple.
- **Check publication with remote-tracking branches first, then `ls-remote --tags`.** Branch
  containment is local and cheap after the pruning fetch. Tags need the remote listing because
  local-only tags survive `--tags` fetches. Asking the GitHub API was rejected: every other
  revision is resolved through Git, and it would add a second authentication path.
- **Fixed fixture repository constant, passed explicitly with `--repo` for pinned claims.**
  Admission certifies the SHA against `FIXTURE_REPOSITORY`, and the invocation passes the same
  constant, so the certified and cloned repositories cannot diverge whatever the pinned harness
  defaults to. Relying on the harness default was rejected because the pinned harness can change
  it. Parsing `REPO` out of `run.sh` was rejected because it couples the factory to the script's
  text. Passing the checkout's normalized origin was rejected because a fork origin is already
  refused at admission. Default claims pass no `--repo`, so they are unchanged. The constant lives
  in the suite module, which the module docstring names as the only place that knows suite
  specifics.
- **Detect harness support with a parser regex.** This reuses the `_ACCEPTS_NO_PUBLISH` technique.
  A dry run was rejected as slower and launcher-dependent under Fly, and the argument never
  reaches the launcher.
- **Capability through `honored-revisions`, a CLI command backed by a code constant.** A static
  marker file was rejected because it can drift from the code that acts on the keys. Probing
  source text in the release was rejected as fragile. Releases that predate the command fail it,
  which is exactly the "does not honor" answer.
- **List claims with the live release; fail closed only when the live release honors fixtures.**
  A live release that predates the feature cannot have admitted fixture claims. A live release
  that has the feature and cannot list them is an error worth stopping for. Reading SQLite
  directly from bash was rejected because it couples the script to the store schema.
- **Doctor line is informational and excluded from the admission gate.** Only fixture-selecting
  requests need the checkout, and they already get a precise readiness reason.
- **No frozen-spec version bump.** Older readers do not check it, and the key is additive.

## Risks / Trade-offs

- **The commit becomes unreachable after admission.** A deleted branch makes the in-sandbox
  checkout fail, which is handled as an ordinary suite failure under the existing recovery policy.
  This is documented and accepted.
- **Hand rollbacks and older deploy scripts bypass the guard.** This is documented, and the guard
  cannot protect against it.
- **A live release that predates the feature while newer-admitted fixture claims exist.** This
  happens only after an earlier unguarded hand rollback. The guard then warns and proceeds. This is
  accepted as part of the bypass risk above.
- **`ls-remote` needs network access at admission.** Only fixture requests whose commit is not on
  any branch reach this step. Failures are readiness reasons and are retried.
- **`fetch --prune --tags` in `_resolve_revision` acts on the operator's and-scene checkout.** This
  is the same existing behavior that already applies to the Runner, Skills, and Validator
  checkouts. It updates remote-tracking refs and tags only, never the working tree.
- **Tests and the GitHub origin check.** Tests point the checkout's origin URL at
  `https://github.com/Codagent-AI/and-scene.git` and redirect it to a local bare repository with
  `git config url.<local>.insteadOf <github url>`. This exercises the real fetch and `ls-remote`
  paths offline.

## Testing Strategy

- **Parsing:** `fixture_ref` accepted alone; empty or non-string rejected with the spec's message;
  absent means no key in `settings`; fingerprint changes with the value; other unsupported keys
  still rejected.
- **Resolution** (integration, real Git with an `insteadOf` origin): branch → origin SHA; published
  full and abbreviated SHA; unpushed local commit refused; commit reachable only from a pruned
  remote branch refused; commit reachable only from a local-only tag refused; commit reachable
  from an origin tag accepted; wrong origin, a `file://` origin, and a missing checkout each
  refused with distinct reasons; fetch failure; ambiguous or unknown ref.
- **Admission** (`EvalHandler.accept` and the runtime readiness path): the frozen spec contains
  `settings.fixture_ref` and `revisions.fixture`; a default request does not consult a missing
  checkout and freezes the same payload as before; a refusal produces one readiness comment and
  the attention label, and a later cycle admits after the commit is pushed.
- **Adapter:** argv contains `--fixture-ref <sha> --repo <FIXTURE_REPOSITORY>` for initial,
  fresh-retry, and resume plans under Docker and Fly. Default argv and the Fly manifest are
  byte-for-byte unchanged. A harness without `--fixture-ref)` or `--repo)` holds readiness in
  `prepare` and `plan` without consuming an attempt. A controlled harness whose `REPO` default
  differs ends up cloning from `FIXTURE_REPOSITORY`.
- **Reporting:** `refs_text` for none, validator only, fixture only, and both, plus an invalid
  fixture value; `frozen_inputs_event` and `attempt_message` with and without a fixture,
  confirming default text is identical.
- **Config and doctor:** sibling default, explicit path, missing checkout informational, wrong
  origin informational and redacted, and doctor's exit status unaffected.
- **CLI:** `honored-revisions` without `--config`; `pinned-claims` filters by kind, lifecycle, and
  key.
- **Deploy guard** (bash harness like `test_deploy_validator.py`, with stub executables): the
  target honors fixtures, so it passes; the live release predates them, so it warns and passes; no
  pinned claims, so it passes; pinned claims with the `before` stage stop with nothing deployed and
  the pause state unchanged; pinned claims with the `after` stage stop, leaving the factory paused
  and nothing switched; a listing failure while the live release honors fixtures stops.
- **End to end:** extend the existing Fly or Docker eval cycle test with one fixture-pinned
  request, checking the argv, `Refs`, and comments.

## Migration Plan

- **Rollout:** a normal deploy. The new `deploy.sh` guard sees that the target honors `fixture`
  and passes. No local configuration change is needed on the factory Mac, because
  `/Users/paul/codagent/and-scene` is the sibling default. Existing claims have no fixture key and
  behave as before.
- **Rollback:** use `scripts/deploy.sh <older ref>` from a checkout that contains this change.
  While fixture-pinned claims are unfinished it refuses and prints the procedure: pause; let every
  pinned claim settle, or cancel it; then deploy. The older release cannot accept `fixture_ref`.
  After rollback, a new request without the key evaluates only the agent-evals default fixture, so
  if a pinned evaluation is still needed, stay on (or redeploy) a fixture-capable release. With no
  such claims, rollback proceeds as before.
- **Follow-up outside this repository:** add the commented `fixture_ref` line to the agent-evals
  eval-request template. The PR description carries this as an orange item.

## Open Questions

None.
