- [ ] Let eval requests pin the and-scene fixture with `fixture_ref`, end to end

## Task: Request-selected and-scene fixture revisions (`fixture_ref`)

Implement the whole change described in these files, in the change directory:

- `proposal.md`, including its "Cross-Repository Deliverable" section;
- the delta specs under `specs/`:
  - `factory-eval-intake`: MODIFIED "Interpret one evaluation configuration per request", ADDED
    "Freeze a requested fixture revision";
  - `factory-eval-execution`: ADDED "Run against the claim's frozen fixture revision";
  - `factory-eval-reporting`: MODIFIED "Report traceable inputs and per-repetition results";
  - `factory-operations`: ADDED "Locate the and-scene fixture checkout", "Refuse a deploy that
    would drop frozen fixture revisions", and "Document request-selected fixture revisions";
- `design.md`, sections 1–8. These give the exact functions, messages, and order;
- the decision log `decisions.md`. Where entries differ, later ones override earlier ones. In
  particular:
  - proposal-review PR-1 to PR-4 override the original proposal decisions;
  - approach-review A-001 (pass `--repo`), A-002 (provenance fields), and A-003 (rollback
    procedure without re-admission) override the spec- and design-stage entries they revise;
- the automated obligations in `test-plan.md`: INT-001 to INT-006, E2E-001, and E2E-002.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or anything under `/Users/paul/codagent/*`, including the and-scene and
agent-evals checkouts. Use scratch clones. Never run `scripts/deploy.sh` or `launchctl`, and never
deploy. Make no change in agent-evals: the template line is the PR's orange item (see below).

### Scope

1. **Parsing and freezing** (`src/agent_factory/work_kinds/eval/__init__.py`, design §1):
   - Add `fixture_ref` to `_KEYS`. When supplied, it must be a non-empty `str`; otherwise raise
     `ValueError("fixture_ref must be a non-empty string")`.
   - Add it to the effective `settings` only when it was supplied. Default settings JSON must be
     unchanged.
   - Add the constant
     `HONORED_REVISIONS = ("runner", "skills", "evals", "validator", "fixture")`.
   - Add `ParsedRequest.freeze(..., fixture_sha: str | None = None)`. It validates the SHA,
     requires `settings["fixture_ref"]`, and writes `revisions["fixture"]`. `version` stays `1`.
2. **Resolution** (`work_kinds/eval/handler.py`, design §2):
   - Extract `github_https_origin(checkout, label)` from `validator_source_url`. The Validator's
     messages and behavior must be unchanged.
   - Add `resolve_fixture(checkout, ref)`. It runs the checkout, origin, resolve, and publication
     steps, with the exact `and-scene checkout <path>: …` messages.
     - The resolve step uses `runtime._resolve_revision`.
     - The publication step uses `for-each-ref --contains` over `refs/remotes/origin/`, then
       `ls-remote --tags origin` with peeled tag commits and `merge-base --is-ancestor`.
   - Add `FIXTURE_REPOSITORY = "https://github.com/Codagent-AI/and-scene.git"` in
     `src/agent_factory/suites/and_scene/__init__.py`.
   - Add `SourceRepositories.fixture: Path | None = None`. `EvalHandler.from_config` passes
     `local.repositories.and_scene` in every execution mode.
   - `EvalHandler.accept` calls `_resolve_fixture` only when `settings` has `fixture_ref`, after
     the `resolve` callable and before `_resolve_harness_ref`, and then passes `fixture_sha` to
     `freeze`. When `self.sources` is `None`, `_resolve_fixture` passes the ref through. Do not
     change the `resolve_request` tuple contract.
3. **Configuration and doctor** (`config.py`, `operations.py`, `config/local.example.toml`,
   design §3):
   - Add `RepositoryConfig.and_scene`, with the sibling default `runner_path.parent / "and-scene"`
     or an explicit `[repositories] and_scene` path.
   - Add the commented example line to `config/local.example.toml`.
   - Add an `eval`-group diagnostic, `_fixture_checkout_diagnostic`. It is appended only under
     `include_informational`, is always `available=True`, is marked informational, redacts
     credentials, and never fetches.
4. **Execution** (`suites/and_scene/__init__.py`, `EvalHandler.prepare`, design §4):
   - Add `_ACCEPTS_FIXTURE_REF` and `_ACCEPTS_REPO` regexes.
   - Change the signature to `readiness(worktrees, *, fixture_pinned=False)`. When it is pinned,
     the pinned `run.sh` must match both regexes; otherwise readiness fails with the design's
     message naming the harness commit.
   - Add `_fixture_revision(frozen)`, which raises `ReadinessError` for a malformed value.
   - In `plan()`, for pinned claims only, add
     `--fixture-ref <sha> --repo FIXTURE_REPOSITORY` after the role arguments and before
     `--skip-validator` / `--resume`.
   - `prepare` passes `fixture_pinned`.
   - Do not change `_revisions()`, the Fly manifest, or the worktree manager.
5. **Reporting** (`work_kinds/eval/handler.py`, design §5):
   - `refs_text` validates `fixture` as it validates `validator`, and appends `fixture@<7>` last.
   - `frozen_inputs_event` appends the design's `Fixture: …` line only for pinned claims.
   - `attempt_message` looks the claim up with `get_claim` and passes `fixture=` to
     `_completion_message`, which adds `Fixture: <full sha>` after the "Product verdict" line.
   - Default-claim text is byte-for-byte unchanged.
6. **Operator commands** (`cli.py`, design §6):
   - `agent-factory honored-revisions` needs no `--config` and prints `HONORED_REVISIONS` one per
     line.
   - `agent-factory --config X pinned-claims --revision <key>` prints
     `<claim id>\t<repository>#<issue>` for eval claims that are not in `TERMINAL_LIFECYCLES` and
     have that revision key. It is read-only.
7. **Deploy guard** (`scripts/fixture-guard.sh`, `scripts/deploy.sh`, design §7):
   - `fixture_guard <target-exe> <live-exe> <before|after>` follows the design's decision table.
     It warns and proceeds when the live release predates fixture revisions. It fails closed when
     the live release honors them but the listing fails.
   - The refusal message names each claim and issue and gives the procedure: "pause; let each
     claim settle, or cancel it; deploy the older release". It states that the older release
     cannot accept `fixture_ref`, so a pinned evaluation that is still needed means staying on a
     fixture-capable release, and that a new request without the key evaluates only the default
     fixture. It never suggests re-admission.
   - The message ends "nothing is deployed" for the `before` stage and "the factory stays paused"
     for the `after` stage.
   - `deploy.sh` sources the helper. It calls the `before` stage after the release build and
     before `pause`, and the `after` stage after the Runner and Validator builds and before
     `point_at`.
   - There is no bypass flag. Update the script header comment. The script must run under `bash`;
     the Mac has no `timeout`.
8. **Documentation** (`AGENTS.md`, `docs/operations.md`, design §8): cover every item in the
   operations "Document request-selected fixture revisions" requirement, including the A-003
   rollback procedure and the hand-rollback bypass.
9. **Tests**: implement INT-001 to INT-006, E2E-001, and E2E-002 at the locations and with the
   setup `test-plan.md` gives.
   - Origins use `remote.origin.url = https://github.com/Codagent-AI/and-scene.git` plus
     `url.<local bare>.insteadOf`.
   - Add unit tests for parsing, `freeze`, `refs_text`, report text, config, and origin
     normalization, as `test-plan.md` "Coverage Strategy" lists.
   - Existing default-path tests must pass unchanged. They include
     `test_shipped_eval_template_is_a_valid_production_request`, the adapter argv and Fly manifest
     tests, and the e2e default Refs and comment checks.
10. **Pull request description**: include an orange attention item saying #91 is only partly
    delivered until agent-evals' `.github/ISSUE_TEMPLATE/eval-request.md` adds, under the existing
    overrides:

    ```toml
    # fixture_ref = "<and-scene branch, tag, or commit>"  # default: the agent-evals pin
    ```

    Paul opens that agent-evals change, or assigns it to the factory, before closing #91.

### Done when

- Every requirement and scenario in the four delta specs is implemented.
- A request with `fixture_ref`:
  - is admitted only when the commit is published on the and-scene origin;
  - freezes `settings.fixture_ref` and `revisions.fixture`;
  - launches, retries, and resumes with `--fixture-ref <sha> --repo https://github.com/Codagent-AI/and-scene.git`
    under Docker and Fly;
  - is held, without consuming an attempt, when the pinned harness lacks either option;
  - reports the fixture in `Refs` (`fixture@<7>`), the frozen-inputs comment, and each repetition
    comment.
- Refusals post one readiness comment per distinct reason, with the attention label, and admit on
  a later cycle once fixed.
- Default requests and claims are byte-for-byte unchanged: settings, frozen spec, argv, Fly
  manifest, `Refs`, and comments. They never consult the and-scene checkout.
- `doctor` reports the and-scene checkout informationally and never fails or holds admission
  because of it.
- `honored-revisions` and `pinned-claims` behave as specified.
- `scripts/deploy.sh` refuses, at both stages, a target release that does not honor fixture
  revisions while unfinished fixture-pinned claims exist.
- `AGENTS.md` and `docs/operations.md` document the key, the checkout, publication, reporting,
  comparability, and the rollback procedure.
- INT-001 to INT-006, E2E-001, and E2E-002 pass, and the full suite passes under
  `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build` pass.
  `agent-validate run` passes.
- The PR description carries the agent-evals template orange item.
- No changes are made outside this repository.
