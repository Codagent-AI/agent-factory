## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration and end-to-end obligations, the acceptance testing envelope, and exceptional human-only
obligations.

This change is a behavior-preserving refactor. Its two spec requirements say that what this release
admits, freezes, delivers to the suite, and reports is identical to the previous release. The
previous release is the one that introduced `fixture_ref`: `origin/main` at `ac887b9`. The main risk
is silent drift in persisted or reported output, so the plan relies on **golden characterization
tests**.

- Golden outputs are captured from the unchanged code in the first implementation task, before any
  refactoring.
- They are committed under `tests/fixtures/eval_inputs_golden/`.
- The refactor must keep them green without editing a golden file.

Volatile values are normalized before comparison and nowhere else: temporary paths, claim and run
ids, timestamps, and the Fly manifest `nonce`.

Every existing eval intake, resolution, reporting, adapter, Fly, CLI, deploy-guard, and E2E test must
also pass. Edits are allowed only where a test calls an internal signature this change replaces:
`ParsedRequest.freeze` keyword arguments, injected `resolve` callables returning a positional tuple,
`readiness(fixture_pinned=)`, and the `handler.subprocess.run` patch target in
`tests/integration/test_fixture_resolution.py`. Their assertions do not change.

Unit tests for the registry are left to implementation: derived key sets, admission order, the freeze
validation messages, and `SourceRepositories.checkout`.

## Integration Tests

### INT-001: Golden admission, freezing, and fingerprint across claim shapes

- **Covers:** `factory-eval-intake` "Keep frozen evaluation inputs compatible across releases",
  scenarios "Freeze the same inputs for the same request" and "Keep a continued request a
  continuation".
- **Boundary:** request parsing, then `EvalHandler.resolve_request` and `accept` against real Git
  source checkouts, then a real SQLite `ClaimStore` through `Controller.accept`.
- **Setup:**
  - local Git repositories with bare origins for Runner, Skills, agent-evals, Agent Validator, and
    and-scene;
  - the and-scene and Validator checkouts' origins set to their GitHub URLs and redirected to the
    bare repositories with `url.<bare>.insteadOf`, as `test_fixture_resolution.py` does;
  - fixed commits so the SHAs are deterministic.
- **Action:** admit one eval-request body per claim shape:
  1. default under Docker;
  2. default under Fly (Validator pinned);
  3. `fixture_ref` under Docker;
  4. `fixture_ref` under Fly (Validator and fixture pinned);
  5. role, `skip_validator`, and `repetitions` overrides with `fixture_ref` placed before and after
     other keys in the TOML.

  Then re-read each issue with its eval block unchanged.
- **Assertions:**
  - The persisted `frozen_spec` serializes, with `json.dumps` and no key sorting, exactly to the
    golden file for that shape. This checks the `settings`, `revisions`, and `sources` insertion
    order, and that `sources` appears only for Validator-pinned claims.
  - `request_fingerprint` equals the golden value.
  - Re-reading an unchanged block returns the existing claim and creates no fresh claim.
- **Execution:** `tests/integration/test_eval_inputs_golden.py`, run by `uv run pytest`.

### INT-002: Golden resolution-failure and rejection reasons

- **Covers:** the `factory-eval-intake` scenarios "Report the same unresolvable revision" and
  "Reject the same unsupported key".
- **Boundary:** admission resolution against real Git checkouts, then the runtime
  readiness-comment path with stub GitHub.
- **Setup:** the INT-001 repositories, broken one way at a time.
- **Action:** attempt admission for each single failure:
  - unknown Runner ref;
  - unknown Skills ref;
  - Validator checkout missing (Fly);
  - Validator origin is a local path (Fly);
  - fixture checkout missing;
  - fixture origin is another repository;
  - unknown `fixture_ref`;
  - unpublished fixture commit;
  - fixture kept only by a deleted remote branch;
  - unresolvable harness branch.

  Also submit eval blocks containing `agent_validator_ref`, an empty `agent_runner_ref`, and an
  empty `fixture_ref`. Finally, break the Runner and fixture refs together.
- **Assertions:**
  - Each failure creates no claim and posts exactly the golden "waiting for revision readiness"
    reason or invalid-request message.
  - With Runner and fixture broken together, the Runner reason is posted. This proves admission
    order is preserved.
  - With `fixture_ref` set and no and-scene checkout configured or present
    (`SourceRepositories.fixture` is `None`), no claim is admitted. The posted reason is the golden
    "and-scene checkout … is missing" text. The requested fixture is never dropped and the request
    is never admitted as a default-fixture eval.
- **Execution:** `tests/integration/test_eval_inputs_golden.py`.

### INT-003: Golden reporting for every claim shape

- **Covers:** `factory-eval-reporting` "Report frozen inputs identically across releases", all of
  its scenarios.
- **Boundary:** `EvalHandler.refs_text`, `frozen_inputs_event`, and `attempt_message` over claims
  read back from a real SQLite store.
- **Setup:** the five INT-001 claims, plus a legacy claim frozen without a Validator revision, plus
  two invalid-revision claims: a missing harness revision, and a fixture revision that is not a
  full SHA. Each repetition result is canned and taken from `tests/fixtures/and-scene-result-v7.json`.
- **Action:** render the `Refs` text, the frozen-inputs comment, and a settled, retry, and exhausted
  repetition report for each claim.
- **Assertions:**
  - Every string matches its golden file byte for byte.
  - An invalid claim yields no `Refs` text, and its recorded `invalid-revisions` event matches the
    golden text, including the order of the named revisions.
- **Execution:** `tests/integration/test_eval_inputs_golden.py`.

### INT-004: Golden suite invocation, Fly manifest, readiness, and worktree records

- **Covers:** `factory-eval-intake` "Keep frozen evaluation inputs compatible across releases",
  scenario "Continue a claim admitted by the previous release" (suite arguments, manifest, and
  persisted worktree records).
- **Boundary:** `EvalHandler.prepare`, `GitWorktreeManager`, `WorktreeCleanup.record`, and
  `plan_attempt`, then `AndSceneAdapter.plan` under Docker and Fly execution. Real Git worktrees are
  used. There is no Docker daemon and no Fly API.
- **Setup:**
  - the INT-001 claims;
  - a stub agent-evals `run.sh` that declares `--fixture-ref)` and `--repo)`, and a variant that
    does not;
  - a stub Fly launcher on `PATH` and a Fly local configuration pointing at a non-contacted app, as
    `test_fly_readiness.py` does.
- **Action:**
  - Prepare each claim and plan an initial attempt and a recovery attempt under both executions.
  - Hold a Validator-pinned claim under Docker.
  - Plan a fixture claim against the harness that lacks the fixture flags.
- **Assertions:**
  - The suite argv matches the golden list, including the position of
    `--fixture-ref <SHA> --repo https://github.com/Codagent-AI/and-scene.git`.
  - The Fly manifest JSON, minus `nonce`, temporary paths, and ids, matches the golden file. This
    covers `commits` (no fixture entry), `worktrees`, `repositories`, and `validator_repository`.
  - The stored `preparation` and `cleanup` worktree records match the golden file (runner, skills,
    evals).
  - The Docker hold message and the "does not accept --fixture-ref and --repo" readiness message
    match their golden text.
  - Worktree removal releases exactly the three worktrees.
- **Execution:** `tests/integration/test_eval_inputs_golden.py`.

### INT-005: Claims frozen by the previous release continue unchanged

- **Covers:** `factory-eval-intake` scenarios "Continue a claim admitted by the previous release"
  and "Roll back with a claim admitted by this release".
- **Boundary:** a real SQLite store seeded with `frozen_spec` rows exactly as the previous release
  persisted them, then this release's `prepare`, `plan_attempt`, and reporting.
- **Setup:** the golden `frozen_spec` files captured in INT-001 from the unchanged code, inserted as
  raw claim rows for each claim shape and the legacy shape. Each claim has one completed repetition
  and one unfinished repetition.
- **Action:** continue the unfinished repetition. Separately, admit the same requests through this
  release.
- **Assertions:**
  - The continued claims are neither rejected nor mutated, their `frozen_spec` is unchanged in the
    store, and their argv, manifest, and reports equal the golden outputs.
  - The `frozen_spec` that this release freezes for each request equals the golden file the
    previous release produced. Byte-identical persisted input is what makes rollback safe.
    Acceptance may additionally confirm it with a real previous-release binary.
- **Execution:** `tests/integration/test_eval_inputs_golden.py`.

### INT-006: `honored-revisions` and the deploy fixture guard

- **Covers:** `factory-eval-intake` scenario "List honored revisions".
- **Boundary:** the installed `agent-factory` console script as a subprocess, then
  `scripts/fixture-guard.sh`.
- **Setup:** none beyond the test environment. The command needs no configuration.
- **Action:** run `agent-factory honored-revisions`. Run the existing fixture-guard decision table
  with this release as the target.
- **Assertions:**
  - The output is exactly `runner\nskills\nevals\nvalidator\nfixture\n` and the exit status is 0.
  - The guard treats this release as fixture-capable, and every existing decision-table row is
    unchanged.
- **Execution:** extend `tests/integration/test_cli_operations.py`. The existing
  `tests/integration/test_deploy_fixture_guard.py` stays as is.

### INT-007: An ordinary input added only through the registry

- **Covers:** the proposal's ordinary-input boundary. It is evidence that a new optional,
  request-settable, argv-delivered ref needs no other source change. This is not a spec behavior.
- **Boundary:**
  - parsing;
  - admission resolution through a real Git checkout;
  - freezing into a real SQLite store;
  - `Refs`, frozen-inputs, and repetition reports;
  - `prepare` and readiness;
  - Docker and Fly `AndSceneAdapter.plan`;
  - `fly/guest.py:job_script` built from the Fly plan's manifest and argv.
- **Setup:**
  - `monkeypatch.setattr(inputs, "EVAL_INPUTS", inputs.EVAL_INPUTS + (sample,))` with an entry named
    `sample`, setting `sample_ref`, requestable and optional, argv `("--sample-ref", sha)`, and a
    report line;
  - a local Git checkout supplied through `SourceRepositories(extra={"sample": path})`;
  - a stub `run.sh` that declares `--sample-ref)`.
- **Action:** admit a request with `sample_ref = "main"` and plan Docker and Fly attempts. Then admit
  a request without `sample_ref`, and plan against a `run.sh` lacking `--sample-ref)`.
- **Assertions:**
  - `sample_ref` is accepted, and its SHA is frozen under `revisions.sample` after `fixture`.
  - `Refs` ends with `sample@<7>`.
  - The report line appears, and `--sample-ref <SHA>` appears in the Docker argv, the Fly argv, and
    the guest job script's suite invocation.
  - `sample` is absent from the manifest `commits`.
  - Running `agent_factory.cli.main()` in-process with `argv = ["agent-factory",
    "honored-revisions"]`, under the same monkeypatch, prints the five production names followed by
    `sample`. A subprocess cannot inherit the monkeypatch. INT-006 keeps the subprocess check of the
    production output.
  - A request without the key records nothing.
  - The harness lacking the flag yields the generic "does not accept --sample-ref" readiness
    message.
  - No production module other than the registry was patched.
- **Execution:** `tests/integration/test_eval_input_registry.py`.

## End-to-End Tests

No new E2E obligation. The cross-release journeys need two installed releases, which the hermetic
suite cannot provide. Their persisted-format core is proven at the integration layer by INT-001 and
INT-005, and acceptance may run the real cross-release check. The existing journeys are the E2E
regression contract and must pass without changes to their assertions:

- `tests/e2e/test_factory_cycle.py`, including `test_fixture_request_runs_and_reports_through_tick`
  and `test_unpublished_fixture_waits_then_admits_after_push`;
- `tests/e2e/test_fly_eval_cycle.py`, including the Validator-pinned and legacy unpinned-Validator
  journeys;
- `tests/e2e/test_docker_worktrees.py`, `tests/e2e/test_independent_execution.py`, and
  `tests/e2e/test_terminal_cleanup.py`.

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - this feature worktree on Paul's Mac, with temporary directories for state databases, storage
    roots, and local configuration;
  - local Git repositories and bare origins created by the pass;
  - a detached worktree of `origin/main` at `ac887b9` (the previous release), created under a
    temporary directory with its own `uv sync --frozen` venv;
  - the cross-release check is permitted: admit claims with this branch into a temporary state
    database, then continue or report them with the previous-release CLI, and the reverse.
- **Credentials and secrets:** none are needed. The Mac holds a GitHub App key, a Fly token, model
  CLI logins, and the suite environment file under `~/.agent-factory`. The pass must not read or use
  any of them.
- **Authorized effects:**
  - local files and Git repositories under temporary directories, removed afterward;
  - the temporary `origin/main` worktree, removed with `git worktree remove` afterward;
  - read-only `git fetch` of public repositories. There is no cost.
- **Off limits:**
  - the live service: the LaunchAgent, `~/.agent-factory/releases`, `releases/current`, the service
    clone, `~/.agent-factory/config.toml`, and the live state database;
  - `scripts/deploy.sh` and any `launchctl` command;
  - Paul's checkout at `/Users/paul/codagent/agent-factory`;
  - the Fly API, Fly Machines, and the Fly registry;
  - GitHub writes of any kind: issues, comments, Project fields, and pushes to and-scene or other
    repositories;
  - real model CLI runs or real and-scene suite runs.
- **Permitted substitutes:**
  - stub GitHub clients, and stub `run.sh` harnesses and Fly launchers as used by the existing
    tests;
  - local bare repositories with `url.<bare>.insteadOf` in place of GitHub origins;
  - Fly execution planning, manifests, and guest job scripts inspected without launching a Machine.
- **Known risk areas:**
  - JSON key insertion order in `settings` (a `fixture_ref` placed between other keys), `revisions`,
    and `sources`;
  - `sources` appearing empty, or missing for Validator-pinned claims;
  - message prefixes ("Agent Validator checkout: ", "and-scene checkout <path>: ") and the
    "harness" noun in freeze errors;
  - admission order when several revisions fail;
  - Validator claims under Docker execution, and legacy claims without a Validator.
  - FEATURE-91's prior defect cluster: fixture argv across Docker and Fly retries and resumes;
    credential redaction in fixture-origin messages; bounded tag-publication checks.
  - Import cycles introduced by `suites/and_scene/inputs.py` and `errors.py`, and the re-exported
    import paths used by `operations.py` and the tests.
  - **Accepted limitation:** Fly guest cloning, the claim-image build, local `[repositories]`
    parsing, and `fixture-guard.sh` remain explicit per-input code, so they are not
    registry-driven.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Keep frozen evaluation inputs compatible across releases: same inputs and fingerprint for the same request; continuation | INT-001 | — | — |
| Keep frozen evaluation inputs compatible across releases: same rejection and readiness reasons | INT-002 | — | — |
| Keep frozen evaluation inputs compatible across releases: continue a previous-release claim (argv, manifest, worktree records) | INT-004, INT-005 | — | — |
| Keep frozen evaluation inputs compatible across releases: roll back with a claim admitted by this release | INT-005 | — | — |
| Keep frozen evaluation inputs compatible across releases: list honored revisions | INT-006 | — | — |
| Report frozen inputs identically across releases | INT-003 | — | — |
| Ordinary input added only through the registry (proposal boundary) | INT-007 | — | — |
