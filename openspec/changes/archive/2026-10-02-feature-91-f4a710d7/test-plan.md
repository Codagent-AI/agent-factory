## Coverage Strategy

The specifications remain the source of unit-test requirements. This plan records only the extra
obligations: integration and end-to-end tests, the envelope for the acceptance pass, and any
human-only checks.

Unit tests should cover the logic that needs no real boundary:

- `parse_request` validation and settings for `fixture_ref`, including the fingerprint;
- `ParsedRequest.freeze` with and without `fixture_sha`;
- `refs_text` for none, Validator only, fixture only, both, and an invalid fixture value;
- the text of `frozen_inputs_event` and `_completion_message` / `attempt_message`, with and
  without a fixture;
- config parsing of `[repositories] and_scene`, both the sibling default and an explicit path;
- `github_https_origin` normalization and redaction.

The existing tests that already check default behavior stay as they are and must still pass. They
are the regression guard for the promise that default claims are unchanged byte for byte. Examples:

- the argv and Fly manifest tests in `tests/integration/test_and_scene_adapter.py` and
  `test_fly_*`;
- the default-claim Refs and comment checks in `tests/e2e/test_factory_cycle.py` and
  `test_fly_eval_cycle.py`;
- the contract test `test_shipped_eval_template_is_a_valid_production_request`.

The obligations below cover the boundaries where unit tests would rely on mocks: real Git remotes
and fetch, pruning, and tag listing; real worktrees and the and-scene `run.sh`; the SQLite claim
store behind CLI commands; the bash deploy helper and the executables it calls; and the full
`agent-factory tick` path that delivers comments and the `Refs` field.

Test repositories stand in for the GitHub origin. Each test checkout sets `remote.origin.url` to
`https://github.com/Codagent-AI/and-scene.git` and adds
`git config url.<local bare repo>.insteadOf https://github.com/Codagent-AI/and-scene.git`. With
that, the real origin check, `fetch --prune --tags`, `for-each-ref`, and `ls-remote` paths all run
offline.

## Integration Tests

### INT-001: Fixture resolution against a real Git origin

- **Covers:**
  - `factory-eval-intake` "Freeze a requested fixture revision": branch, published commit,
    unpublished commit, pruned branch, unknown ref, wrong origin, missing checkout;
  - the readiness reasons.
- **Boundary:** `resolve_fixture` with real `git` subprocesses: `rev-parse`, `fetch --prune --tags`,
  `for-each-ref --contains`, `ls-remote --tags`, and `merge-base --is-ancestor`, run against a
  checkout and a bare origin redirected with `insteadOf`.
- **Setup:** a bare origin with `main` and a branch `eval/fixture-x`, and a checkout cloned from it.
  The checkout also gets:
  - a commit that exists only locally;
  - a local-only tag on another local commit;
  - an origin tag (lightweight and annotated) on a commit that is not on any branch;
  - a commit whose only branch was deleted from the origin after the checkout fetched it.

  Variant checkouts:
  - one with no `.git`;
  - one whose origin is `file://…`;
  - one whose origin is `https://github.com/someone/and-scene.git`;
  - one whose redirected origin path does not exist, so the fetch fails.
- **Action:** call `resolve_fixture(checkout, ref)` once for each ref and checkout variant.
- **Assertions:**
  - The branch name returns the origin branch's full SHA, even after the local branch is moved
    elsewhere.
  - A full SHA and an abbreviated SHA on `eval/fixture-x` return the full SHA.
  - The origin-tag commit is accepted.
  - The local-only commit, the local-only tag, and the commit kept only by the deleted branch each
    raise `ReadinessError`. The message names the checkout and says the commit is not published
    on `https://github.com/Codagent-AI/and-scene.git`.
  - An unknown ref, the wrong origin, the `file://` origin, a missing checkout, and a fetch failure
    each raise a distinct message prefixed with `and-scene checkout <path>:`.
  - No message contains credentials embedded in an origin URL.
  - The checkout's working tree and `HEAD` are unchanged.
- **Execution:** `tests/integration/test_fixture_resolution.py`, run by `uv run pytest`.

### INT-002: Admission freezes the fixture and leaves default admissions untouched

- **Covers:**
  - `factory-eval-intake`: "Interpret one evaluation configuration per request", "Freeze a
    requested fixture revision" (freeze branch; admit a default request without the checkout;
    continue a pre-change claim);
  - `factory-operations` "Locate the and-scene fixture checkout": sibling default, explicit path,
    and running without a checkout.
- **Boundary:** configuration loading, then `LocalConfig`, then `EvalHandler.from_config`, then
  `EvalHandler.accept`. This uses real Git sources for Runner, Skills, evals, and the and-scene
  checkout, under both `docker` and `fly` execution settings.
- **Setup:**
  - A local TOML with `agent_runner` and no `and_scene` key. The sibling `and-scene` directory
    holds an `insteadOf`-redirected checkout. A second config sets an explicit `and_scene` path,
    and a third config has neither.
  - Request bodies: one with `fixture_ref = "eval/fixture-x"` and one with no fixture key.
- **Action:** build the handler from each configuration and call `accept` for each request snapshot.
- **Assertions:**
  - The fixture request's draft payload has `settings.fixture_ref == "eval/fixture-x"` and
    `revisions.fixture` set to the origin SHA. The explicit path takes precedence over the
    sibling.
  - The default request's payload has no `fixture_ref` or `fixture` key, and is equal to the
    payload produced with the same inputs when no and-scene checkout exists at all. The checkout
    is not consulted: a nonexistent path does not raise.
  - With no checkout, the fixture request raises the missing-checkout `ReadinessError`.
  - A stored claim payload without a fixture still plans through the adapter.
- **Execution:** `tests/integration/test_revision_resolution.py`, run by `uv run pytest`.

### INT-003: Suite invocation and readiness with a frozen fixture

- **Covers:** `factory-eval-execution` "Run against the claim's frozen fixture revision": launch,
  recover, default launch, and a pinned harness that lacks fixture selection.
- **Boundary:** `EvalHandler.prepare` and `AndSceneAdapter.plan` / `readiness`, over real
  claim-owned worktrees made by `GitWorktreeManager`. This runs once under Docker and once under
  Fly. For Fly, the existing launcher test doubles provide `fly_launcher()` and the dry run.
- **Setup:**
  - An evals repository whose `run.sh` option dispatch contains `--fixture-ref)` and `--repo)`
    cases.
  - A second harness commit without the `--fixture-ref)` case, and a third without the `--repo)`
    case.
  - A fourth harness commit whose controlled `run.sh` defaults `REPO` to
    `https://github.com/someone/other.git`. It parses its options as the real script does and
    writes the effective `REPO` and `FIXTURE_REF` to the artifact directory.
  - Frozen specs with and without `revisions.fixture`.
  - An artifact directory in each recovery state the existing tests already build: initial, a
    resume with a checkpoint, and a fresh retry proven to have stopped before checkpoint creation.
- **Action:** call `prepare` and `plan` for each combination.
- **Assertions:**
  - With a fixture, argv contains exactly one `--fixture-ref <full SHA>` pair and exactly one
    `--repo https://github.com/Codagent-AI/and-scene.git` pair, in initial, fresh-retry, and
    `--resume` plans, under both modes.
  - Running the planned command against the fourth harness records an effective `REPO` of
    `https://github.com/Codagent-AI/and-scene.git` and the frozen SHA, not the harness default.
  - Without a fixture, argv is identical to today's expected argv, with no `--fixture-ref` and no
    `--repo`.
  - Under Fly, the manifest JSON (excluding its nonce) is identical with and without a fixture, and
    `commits` has no `fixture` key.
  - With either harness that lacks `--fixture-ref)` or `--repo)`, `prepare` raises
    `ReadinessError` naming the harness commit. The readiness check launches no process, and a
    default claim on the same harness still prepares.
  - A malformed `revisions.fixture` raises `ReadinessError`.
- **Execution:** `tests/integration/test_and_scene_adapter.py` and
  `tests/integration/test_fly_readiness.py`, run by `uv run pytest`.

### INT-004: Operator CLI commands over the real store

- **Covers:** `factory-operations` "Refuse a deploy that would drop frozen fixture revisions": the
  `honored-revisions` and `pinned-claims` contracts, and the scenario "List fixture-pinned claims".
- **Boundary:** `python -m agent_factory.cli` (the `agent-factory` entry point) as a subprocess,
  against a real SQLite `ClaimStore`.
- **Setup:** a state database with five claims:
  - an eval claim in `waiting` with `revisions.fixture`;
  - an eval claim in `active` with `revisions.fixture`;
  - an eval claim in `settled` with `revisions.fixture`;
  - an eval claim in `active` without a fixture;
  - a fix claim.

  A minimal local config points at the store.
- **Action:**
  - Run `agent-factory honored-revisions` with no `--config`.
  - Run `agent-factory --config <local> pinned-claims --revision fixture`.
  - Run the same `pinned-claims` command against an empty store.
- **Assertions:**
  - `honored-revisions` exits 0 and prints `runner`, `skills`, `evals`, `validator`, `fixture`, one
    per line.
  - `pinned-claims` prints exactly the waiting and active fixture claims as
    `<claim id>\t<repository>#<issue>` and exits 0.
  - The empty store prints nothing and exits 0.
  - The database is byte-identical before and after.
- **Execution:** `tests/integration/test_cli_operations.py`, run by `uv run pytest`.

### INT-005: Deploy guard helper with stub releases

- **Covers:** `factory-operations` "Refuse a deploy that would drop frozen fixture revisions": all
  scenarios except "List fixture-pinned claims".
- **Boundary:** `bash` sourcing `scripts/fixture-guard.sh`, together with the `say`, `warn`, and
  `die` conventions from `deploy.sh`, calling stub target and live executables. This follows the
  harness in `tests/integration/test_deploy_validator.py`.
- **Setup:** stub executables that answer `honored-revisions`, either with or without `fixture` or
  as an older release that exits 2, and that answer `pinned-claims` with a list, with nothing, or
  with a failure. Each stub records every invocation to a log file.
- **Action:** call `fixture_guard <target> <live> before` and `fixture_guard <target> <live> after`
  for each combination.
- **Assertions:**
  - The target honors fixtures: exit 0, and the live executable is never asked.
  - Neither the target nor the live release honors fixtures: exit 0, with a warning that the live
    release predates fixture revisions.
  - Both stages, with no pinned claims: exit 0.
  - The `before` stage with pinned claims: a failing exit. The message names every claim and
    issue, gives the procedure "pause; let each claim settle, or cancel it; deploy the older
    release", states that the older release cannot accept `fixture_ref`, never suggests
    re-admitting after rollback, and ends "nothing is deployed".
  - The `after` stage with pinned claims: a failing exit, ending "the factory stays paused".
  - The live release honors fixtures and the listing fails: a failing exit at either stage.
  - No bypass variable or flag changes any of these outcomes.

  A static ordering test over `scripts/deploy.sh` also asserts the call order: the `before` call
  comes after the release build and before `pause`, and the `after` call comes after `pause` and
  before `point_at`, the `launchctl` calls, and the `releases/current` link.
- **Execution:** `tests/integration/test_deploy_fixture_guard.py`, run by `uv run pytest`.

### INT-006: Doctor reports the and-scene checkout without holding admission

- **Covers:** `factory-operations` "Locate the and-scene fixture checkout": diagnose the wrong
  origin, and run without a checkout.
- **Boundary:** `operations.doctor` over a real local configuration and real Git checkouts. It also
  covers the runtime's admission gate, which is the same call with `include_informational=False`.
- **Setup:** three configurations:
  - the and-scene checkout is present with the correct origin;
  - the checkout is missing;
  - the origin is `https://user:secret@github.com/someone/and-scene.git`.
- **Action:** run `doctor(config)` and `doctor(config, include_informational=False)` for each.
- **Assertions:**
  - Each informational run has exactly one `eval`-group and-scene diagnostic with
    `available=True`. Its detail matches the case.
  - The wrong-origin detail redacts `user:secret` and states that `fixture_ref` requests will wait.
  - The admission-gate run contains no and-scene diagnostic.
  - No `git fetch` runs. Checking the checkout's `FETCH_HEAD` mtime is enough to show this.
- **Execution:** `tests/integration/test_cli_operations.py` or `tests/integration/test_mac_operations.py`,
  run by `uv run pytest`.

## End-to-End Tests

### E2E-001: A fixture-pinned eval request runs and reports through the CLI

- **Covers:**
  - the eval request → admission → launch → report journey for a pinned fixture;
  - `factory-eval-reporting` "Report traceable inputs and per-repetition results", including the
    scenarios for a pinned fixture with and without a Validator revision;
  - `factory-eval-execution` "Launch a repetition with a pinned fixture" and "Inspect the fixture
    used by a repetition".
- **Surface:** the `agent-factory --config <local> tick` CLI, with the stub GitHub board and
  comments used by `tests/e2e/test_factory_cycle.py`.
- **Setup:**
  - The existing `_setup`, with an and-scene checkout next to the runner, redirected through
    `insteadOf` to a local bare origin with a branch `eval/fixture-x`.
  - The controlled suite `run.sh` gains `--fixture-ref)` and `--repo)` option-dispatch lines and
    writes its full argv to the artifact directory.
  - Imitating the real suite's evidence layout, the suite writes `result.json` with
    `candidate_source.fixture_commit`, and `proof-metadata.json` with `repo`, `fixture_ref`, and
    `fixture_commit`. Each value is taken from the arguments it received.
  - The board item's eval block adds `fixture_ref = "eval/fixture-x"`.
- **Journey:**
  1. `tick` admits and launches.
  2. The test finishes the repetition with a `pending-human-review` result.
  3. `tick` settles and reports.
- **Assertions:**
  - The recorded argv contains `--fixture-ref <origin SHA of eval/fixture-x>` and
    `--repo https://github.com/Codagent-AI/and-scene.git`.
  - After settlement, the claim's frozen inputs give `settings.fixture_ref == "eval/fixture-x"`
    and `revisions.fixture` equal to that SHA.
  - The retained artifact directory still holds `result.json` (with
    `candidate_source.fixture_commit` equal to the frozen SHA) and `proof-metadata.json`,
    byte-identical to what the suite wrote.
  - The board's `Refs` field is `runner@<7> skills@<7> evals@<7> fixture@<7>`, with the first seven
    characters of that SHA.
  - The frozen-inputs comment contains the full SHA and the requested ref `eval/fixture-x`.
  - The repetition comment contains `Fixture: <full SHA>`.

  A second card in the same board with no `fixture_ref` produces argv without `--fixture-ref` or
  `--repo`, and `Refs` and comments without any fixture text.
- **Execution:** `tests/e2e/test_factory_cycle.py`, run by `uv run pytest`. No Docker marker is
  needed.

### E2E-002: An unpublished fixture waits, then is admitted once pushed

- **Covers:** `factory-eval-intake` "Refuse an unpublished commit": a single readiness comment, the
  attention label, and admission on a later cycle.
- **Surface:** the `agent-factory --config <local> tick` CLI with the stub GitHub board.
- **Setup:** the same as E2E-001. The board item's `fixture_ref` is the full SHA of a commit that
  exists only in the local and-scene checkout.
- **Journey:**
  1. `tick`.
  2. `tick` again, with nothing changed.
  3. Push the commit to a new branch on the bare origin.
  4. `tick`.
- **Assertions:**
  - After the first two ticks, no claim exists. There is exactly one comment saying the fixture
    commit is not published on `https://github.com/Codagent-AI/and-scene.git`, and the attention
    label is set.
  - After the push and the next tick, a claim exists with `revisions.fixture` equal to that SHA,
    the attention label is removed, and a run is reserved.
- **Execution:** `tests/e2e/test_factory_cycle.py`, run by `uv run pytest`.

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - This feature worktree with its own `uv` venv.
  - Temporary local configurations, state roots, and Git repositories under a scratch directory.
  - The repository's stub GitHub board and comment doubles (as in `tests/e2e/test_factory_cycle.py`)
    for driving `agent-factory tick`, `status`, `doctor`, `honored-revisions`, and `pinned-claims`
    end to end.
  - `scripts/fixture-guard.sh` exercised directly with stub executables.
- **Credentials and secrets:**
  - None are needed.
  - Anonymous HTTPS read access to public `https://github.com/Codagent-AI/and-scene.git` is
    available. A scratch clone may resolve the issue's real case:
    `eval/fixture-sonnet-validator` at `b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a`, using
    `fetch`, `rev-parse`, and `ls-remote` only.
- **Authorized effects:**
  - Local scratch files, Git repositories, and SQLite databases, all removed afterward.
  - Read-only network fetches of public GitHub repositories.

  No cost.
- **Off limits:**
  - The live factory and everything under `~/.agent-factory`: its config, state database, releases,
    artifacts, and clones.
  - The `com.codagent.agent-factory` LaunchAgent. Running `scripts/deploy.sh` itself (it pauses,
    reloads, and switches the live service) and running `launchctl`.
  - Paul's checkouts under `/Users/paul/codagent`, including `/Users/paul/codagent/and-scene`. Use
    scratch clones instead.
  - The factory GitHub App key and Paul's `gh` login.
  - Creating or editing issues, PRs, comments, or Project items on GitHub.
  - Pushing to any remote.
  - Starting real evals, Fly Machines, image builds, or model CLI runs.
- **Permitted substitutes:**
  - The stub GitHub board for real GitHub.
  - A controlled `run.sh` that records argv, standing in for the real and-scene suite.
  - `insteadOf`-redirected local bare repositories for the and-scene origin, used whenever an
    effect would require pushing.
  - Stub release executables for the deploy guard, instead of real older releases.
- **Known risk areas:**
  - **Default claims changing.** Any drift in default settings JSON, frozen-inputs text, `Refs`,
    argv, or the Fly manifest would break the "byte-for-byte unchanged" promise.
  - **Publication false positives and negatives.** Tags (annotated versus lightweight, local-only
    tags surviving `--tags` fetches), pruned branches, and abbreviated or ambiguous SHAs.
  - **Origin normalization and redaction.** This shared helper is extracted from
    `validator_source_url`; Validator messages must stay unchanged.
  - **Readiness reasons as comment keys.** Each distinct reason string posts one comment, so
    unstable text, such as embedding timestamps, would spam the issue.
  - **The deploy guard's two stages and its fail-open edge.** A live release that predates the
    feature makes the guard warn and proceed.
  - **Repository binding.** The certified repository and the one the suite clones must stay the
    same through `--repo`. Today's harness default equals it, so only a changed harness would
    reveal a mistake.
  - **Observed provenance location.** The suite's observed fixture commit lives in its own
    `result.json` `candidate_source` and in `proof-metadata.json`, not in factory records.
  - **Shell portability.** The Mac has no `timeout` and `zsh` does not split words; the helper must
    run under `bash` exactly as `deploy.sh` sources it.
  - **Accepted limitations, not defects:**
    - a branch deleted after admission fails later repetitions at checkout;
    - hand rollbacks and older `deploy.sh` copies bypass the guard;
    - the agent-evals template line is a cross-repository follow-up.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| factory-eval-intake: Interpret one evaluation configuration per request (`fixture_ref` key) | INT-002 | E2E-001 | — |
| factory-eval-intake: Freeze a requested fixture revision | INT-001, INT-002 | E2E-001, E2E-002 | — |
| factory-eval-execution: Run against the claim's frozen fixture revision | INT-003 | E2E-001 | — |
| factory-eval-reporting: Report traceable inputs and per-repetition results | — | E2E-001 | — |
| factory-operations: Locate the and-scene fixture checkout | INT-002, INT-006 | — | — |
| factory-operations: Refuse a deploy that would drop frozen fixture revisions | INT-004, INT-005 | — | — |
| Journey: request a fixture-pinned eval, run it, and read its reports | — | E2E-001 | — |
| Journey: unpublished fixture waits, then is admitted after a push | — | E2E-002 | — |
