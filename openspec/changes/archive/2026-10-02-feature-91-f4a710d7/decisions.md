# Decisions: feature-91-f4a710d7

## propose: verdict

- **Decision:** go. Add an optional `fixture_ref` eval-request key that is resolved, frozen, passed
  to the suite as `--fixture-ref`, and reported.
- **Alternatives considered:**
  - No-go, and keep manual `run.sh --fixture-ref` runs. Rejected: those runs lose frozen inputs,
    repetitions, recovery, Fly execution, and reporting, and the issue has an immediate use
    (and-scene#42 at `b83deca4…`).
  - Re-pin agent-evals to the candidate fixture first. Rejected: that is the merge-first,
    test-later order the issue wants to avoid.
- **Decision-bearing:** no. The issue asks for exactly this.

## propose: agent-evals eval-request template is out of scope for this PR

- **Decision:** document `fixture_ref` in this repository's specs and docs. Treat the agent-evals
  template line (`.github/ISSUE_TEMPLATE/eval-request.md`) as a separate agent-evals follow-up.
  The proposal gives the exact line to add. (Revised by proposal-review PR-1 below: it is now an
  explicit cross-repository deliverable, not out of scope.)
- **Alternatives considered:**
  - Stop the definition, because the issue's fourth bullet needs a change in another repository.
    Rejected: the factory accepts the key without the template, the edit is one commented
    documentation line with no design choice, and stopping would hold back the deliverable core.
    Precedent: feature-36 also kept its changes inside agent-factory and left agent-evals alone.
  - Change agent-evals from this run. Rejected: a feature run delivers a pull request only to its
    target repository.
- **Decision-bearing:** no. It changes where one documentation line lands, not the feature's
  behavior.

## propose: where the fixture ref is resolved

- **Decision:** add an optional local `[repositories] and_scene` checkout that defaults to a sibling
  of `agent_runner`, as `agent_validator` does. Fetch, then `rev-parse` to a full SHA. Consult the
  checkout only when a request sets `fixture_ref`.
- **Alternatives considered:**
  - `git ls-remote` against the public URL. Rejected: it cannot resolve an arbitrary commit SHA,
    which is the immediate use case.
  - Use the GitHub API through the factory's app token. Rejected: it adds a new resolution path,
    while every other revision is resolved through a local checkout.
  - Require explicit operator configuration. Rejected: the sibling default works on the factory Mac
    without a configuration change.
- **Decision-bearing:** no. It is an implementation default that follows an existing pattern.

## propose: frozen spec shape

- **Decision:** add an optional `revisions.fixture` (and the effective `fixture_ref` setting) only
  when the key is set, and keep frozen-spec version 1. Default claims are unchanged.
- **Alternatives considered:**
  - Always freeze the fixture SHA, reading the pin out of `run.sh`. Rejected: that changes
    default-claim behavior and reports, and it couples the factory to the script's text.
  - Bump the frozen-spec version. Rejected: the key is additive and is read like the optional
    Validator revision.
- **Decision-bearing:** no. The persisted format change is additive and backward compatible.

## propose: reporting

- **Decision:** the `Refs` field appends `fixture@<7>` only for pinned fixtures. The frozen-inputs
  comment names the requested ref and the resolved SHA, or states that the agent-evals pin was
  used. Repetition reports carry the fixture SHA. (Revised by proposal-review PR-4 below: default
  claims keep today's comment unchanged, without an "agent-evals pin" statement.)
- **Alternatives considered:**
  - Show the fixture only in the frozen-inputs comment. Rejected: the issue asks for a
    non-default fixture to be obvious, and the board `Refs` field is where runs are compared.
- **Decision-bearing:** no.

## proposal-review: PR-1 (template deferral leaves #91 incomplete): applied

- **Decision:** moved the agent-evals template update out of "Out of Scope" into a new
  "Cross-Repository Deliverable" section. The section says #91 is incomplete until the template
  documents `fixture_ref`. The agent-factory PR description must carry an orange attention item
  with the exact template line, so Paul opens the agent-evals change or assigns it to the factory
  before closing #91. This run still makes no change outside agent-factory.
- **Alternatives considered:**
  - Direction-level stop. Rejected: the reviewer's recommendation itself allows an agent-factory-only
    run as long as the remaining deliverable is explicit and tracked. That is now the case, and the
    template line involves no design decision.
  - File the agent-evals issue or PR from this run. Rejected: it is an outward-facing action
    outside the target repository, and a define step does not do that.
- **Decision-bearing:** no.

## proposal-review: PR-2 (resolved SHA may not be published): applied

- **Decision:** admission requires two things. The and-scene checkout's origin must be the GitHub
  repository the suite clones. The resolved commit must be reachable from a remote-tracking branch
  after the pruning fetch, or from a tag that origin advertises. Otherwise the request waits for
  revision readiness. Bare SHAs are accepted only when published. Later branch deletion stays a
  separately documented, time-dependent limitation.
- **Alternatives considered:**
  - Check only the origin URL. Rejected: `_resolve_revision` accepts any local object, including
    unpushed commits and commits kept after their remote ref was pruned.
  - Let the suite fail at checkout. Rejected: every repetition would fail before evaluation, and
    each would cost a sandbox or Machine launch.
- **Decision-bearing:** no.

## proposal-review: PR-3 (rollback silently drops the fixture pin): applied

- **Decision:** `scripts/deploy.sh` refuses to switch to a release that cannot honor fixture pins
  while any unfinished claim carries `revisions.fixture`. It names those claims and leaves the live
  release in place. `AGENTS.md` and `docs/operations.md` document the rollback procedure (pause;
  settle, cancel, or re-admit fixture-pinned claims; roll back). The design picks the detection
  mechanism, for example a capability marker in the release.
- **Alternatives considered:**
  - Documentation only, as for first-parent checkpoint reading. Rejected: silently running the
    wrong fixture breaks the frozen-input guarantee and produces misleading reports, so a check is
    proportionate.
  - Bump the frozen-spec version. Rejected: older readers do not enforce the version.
- **Residual risk:** a rollback done by hand or with an older deploy script bypasses the guard.
  The proposal records this.
- **Decision-bearing:** no. Deploy behavior is internal to this repository, and the guard only
  restricts an unsafe operation.

## proposal-review: PR-4 (default reporting contract contradicts itself): applied

- **Decision:** keep today's frozen-inputs comment, repetition reports, and `Refs` text unchanged
  for claims without a pinned fixture. Add the requested ref and the full SHA only for
  pinned-fixture claims.
- **Alternatives considered:**
  - Always state "agent-evals pin used". Rejected: it changes every default report without a need,
    and it contradicts the promise that default evals are unchanged.
- **Decision-bearing:** no.

## spec: modify the request-key requirement and add a separate freezing requirement

- **Decision:** add `fixture_ref` by modifying "Interpret one evaluation configuration per request"
  (key list, validation, and selection scope). Put admission, resolution, and publication into a
  new requirement, "Freeze a requested fixture revision". Leave the long "Freeze accepted evaluation
  inputs" requirement untouched.
- **Alternatives considered:**
  - Modify "Freeze accepted evaluation inputs". Rejected: it adds no behavior and risks
    accidentally rewording the Validator rules.
- **Decision-bearing:** no.

## spec: fixture repository identity

- **Decision:** the fixture repository the checkout's origin must match is the suite's default
  `https://github.com/Codagent-AI/and-scene.git`. The adapter knows it, and the factory does not
  pass `--repo`.
  (Revised by approach-review A-001 below: pinned claims now pass this constant with `--repo`.)
- **Alternatives considered:**
  - Pass `--repo <normalized origin>` to the suite. Rejected: it changes the suite invocation for
    every pinned claim, and it would let a fork's origin silently redirect evaluations.
  - Read `REPO` out of `run.sh`. Rejected: it couples the factory to the script's text.
- **Decision-bearing:** no.

## spec: abbreviated SHAs and tags

- **Decision:** accept branches, tags, and full or abbreviated SHAs, as for Runner and Skills refs.
  An ambiguous abbreviation is an unresolvable ref. Tags count as published only when the origin
  advertises them.
- **Alternatives considered:**
  - Accept only full SHAs. Rejected: it is inconsistent with the other refs, and the issue's own
    example uses a branch.
- **Decision-bearing:** no.

## spec: harness readiness for `--fixture-ref`

- **Decision:** before launch, readiness verifies that the claim's pinned harness accepts
  `--fixture-ref`, but only for fixture-pinned claims. The detection method is deferred to design.
- **Alternatives considered:**
  - Let the suite fail on an unknown argument. Rejected: the attempt would be a pre-suite failure
    that consumes retries, where today's spec catches such incompatibility as a readiness hold.
- **Decision-bearing:** no.

## spec: doctor treats the and-scene checkout as informational

- **Decision:** `doctor` reports the checkout's presence and origin as informational and never
  fails on them, because only fixture-selecting requests need the checkout. Those requests wait for
  revision readiness with a specific reason.
- **Alternatives considered:**
  - A failing doctor check. Rejected: it would hold all eval admission for an optional feature.
- **Decision-bearing:** no.

## spec: deploy rollback guard timing and override

- **Decision:** check before pausing, which deploys nothing, following the script's convention that
  failures which change nothing happen before the pause. Check again after pausing and before
  switching, which leaves the factory paused, as other post-pause failures do. Offer no bypass flag.
  Detection of release support and the claim listing are deferred to design.
- **Alternatives considered:**
  - Check only before the pause. Rejected: a claim admitted during the Runner build would escape.
  - Offer a `--force` flag. Rejected: settling, cancelling, or re-admitting the claims is the safe
    path, and a bypass would recreate the silent wrong-fixture outcome.
- **Decision-bearing:** no.

## spec: cross-repository template item

- **Decision:** no spec requirement covers the agent-evals template or the PR description's orange
  item. They concern delivering this change, not factory behavior, so the task plan carries them.
- **Decision-bearing:** no.

## design: resolve the fixture in the handler, outside the `resolve` tuple

- **Decision:** `EvalHandler.accept` calls `resolve_fixture(sources.fixture, ref)` itself, as it
  already does for the harness. The runtime's `resolve_request -> tuple[str, ...]` contract is
  unchanged.
- **Alternatives considered:**
  - Append the fixture SHA to the positional tuple. Rejected: the optional Validator element
    already makes positions fragile.
- **Decision-bearing:** no.

## design: publication check

- **Decision:** the SHA is published if `for-each-ref --contains` finds an `refs/remotes/origin/*`
  ref after the pruning fetch. Otherwise the check falls back to `ls-remote --tags origin` with an
  ancestry test against advertised tag commits.
- **Alternatives considered:**
  - Use the GitHub API. Rejected: it is a second authentication and resolution path.
  - Trust local `refs/tags`. Rejected: `--tags` fetches do not prune local-only tags.
- **Decision-bearing:** no.

## design: harness support detection resolves the execution-spec deferral

- **Decision:** a regex for a `--fixture-ref)` case in the pinned `run.sh`, mirroring
  `_ACCEPTS_NO_PUBLISH`. The spec now states this.
- **Alternatives considered:**
  - A dry run. Rejected: it is slower, and the argument never reaches the Fly launcher's grammar.
- **Decision-bearing:** no.

## design: deploy guard mechanism resolves the operations-spec deferral

- **Decision:** two read-only CLI commands carry the guard:
  - `agent-factory honored-revisions` needs no configuration and prints the code constant
    `HONORED_REVISIONS`. A target release that lacks it does not honor fixtures.
  - `agent-factory --config X pinned-claims --revision fixture` runs on the live release and lists
    unfinished eval claims carrying the key.

  A sourced `scripts/fixture-guard.sh` runs both, before the pause and after the pause.
- **Alternatives considered:**
  - A static marker file. Rejected: it can drift from the code.
  - Grep the release's source. Rejected: fragile.
  - Read SQLite directly from bash. Rejected: it couples the script to the store schema.
- **Decision-bearing:** no.

## design: guard fail-open and fail-closed edges (added to the operations spec)

- **Decision:** if the live release itself predates fixture revisions, the deploy warns and
  proceeds, because that release cannot have admitted fixture claims. If the live release honors
  fixtures but the listing fails, the deploy stops. Building the target release's immutable
  worktree before the first check is added as a second exception to "deploy nothing", beside the
  Runner fast-forward, since the check needs the target's executable.
- **Alternatives considered:**
  - Always fail closed when the listing fails. Rejected: it would block every deploy from a
    pre-feature live release, which no guard-aware release could ever have been.
- **Residual risk:** fixture claims admitted by a newer release, followed by an unguarded hand
  rollback to a pre-feature release, escape the guard. This falls under the documented
  hand-rollback bypass.
- **Decision-bearing:** no.

## design: doctor line excluded from the admission gate

- **Decision:** the and-scene checkout diagnostic is appended only when `include_informational` is
  set and is always `available=True`, so the runtime's admission gate never sees it.
- **Decision-bearing:** no.

## design: tests use `url.<local>.insteadOf`

- **Decision:** resolution tests set the checkout's origin to the real GitHub URL and redirect it to
  a local bare repository with `insteadOf`. This exercises the real fetch, origin, and `ls-remote`
  code offline.
- **Decision-bearing:** no.

## test-plan: obligations and layers

- **Decision:** six integration obligations and two end-to-end obligations.
  - **INT-001:** real-Git fixture resolution.
  - **INT-002:** wiring from configuration through admission.
  - **INT-003:** adapter argv, readiness, and the Fly manifest.
  - **INT-004:** the CLI commands over SQLite.
  - **INT-005:** the bash deploy guard with stub executables, plus a static ordering check over
    `deploy.sh`.
  - **INT-006:** the informational doctor line.
  - **E2E-001:** a pinned-fixture CLI journey, with a default card alongside it.
  - **E2E-002:** an unpublished commit that waits and is admitted after a push.

  Reporting text and parsing stay at the unit layer. Existing default-path tests are the
  byte-for-byte regression guard.
- **Alternatives considered:**
  - Run `scripts/deploy.sh` end to end. Rejected: its LaunchAgent label is fixed and it would
    touch the live service. The helper plus an ordering test covers the behavior.
  - Use a Fly or Docker-marked E2E test for the fixture path. Rejected: the argv and manifest are
    proven in INT-003, and the CLI journey needs no sandbox.
- **Decision-bearing:** no.

## test-plan: acceptance envelope

- **Decision:**
  - **Allowed:** scratch repositories, configurations, and state; stub GitHub; stub executables;
    and anonymous read-only fetches of the public and-scene repository. The fetches may resolve
    the issue's real case, `b83deca4…` on `eval/fixture-sonnet-validator`.
  - **Off limits:** the live service and everything under `~/.agent-factory`; `deploy.sh` and
    `launchctl`; Paul's checkouts; the App key and `gh` login; any GitHub writes or pushes; real
    evals and Fly Machines.
  - **Human-only testing:** none.
- **Decision-bearing:** no.

## approach-review: A-001 (certified repository may differ from the one the harness clones): applied

- **Decision:** for fixture-pinned claims, the adapter passes
  `--repo https://github.com/Codagent-AI/and-scene.git` (the same `FIXTURE_REPOSITORY` constant
  admission certifies against) alongside `--fixture-ref`. Readiness requires the pinned `run.sh`
  to have both `--fixture-ref)` and `--repo)` cases. Default claims pass neither argument.
  - The execution spec, design, and proposal are updated.
  - INT-003 adds a controlled harness whose `REPO` default differs, and asserts the effective
    `REPO` is the certified one.
  - INT-003 and E2E-001 assert the `--repo` argument.
- **Alternatives considered:**
  - Read and validate the harness's `REPO` default before admission and launch. Rejected: it
    couples the factory to the script's text, and it still fails a valid fixture request when the
    default changes.
  - Keep relying on the harness default, my earlier spec decision. Rejected: the reviewer is right
    that the frozen harness is not constrained to that default.
- **Note:** `run.sh` uses `REPO` for the reference clone as well. Both live in and-scene, and
  today's default equals the constant, so current behavior is unchanged.
- **Decision-bearing:** no. This is a correctness fix within the approved approach.

## approach-review: A-002 (observed provenance fields misnamed and untested): applied

- **Decision:** name the real evidence. Requested inputs are the frozen `settings.fixture_ref` and
  `revisions.fixture`. Observed provenance is the suite's `result.json` field
  `candidate_source.fixture_commit` and `proof-metadata.json` (`repo`, `fixture_ref`,
  `fixture_commit`) in the retained artifact directory. The factory retains that evidence
  unmodified and does not compare observed with frozen, because the suite checks out the exact SHA
  and revalidates it on resume.
  - The execution spec's requirement and its "Inspect" scenario, the design §4, and the proposal's
    out-of-scope note are corrected.
  - E2E-001 now writes both files in the suite's layout and asserts that they are retained
    byte-identical, with `fixture_commit` equal to the frozen SHA.
- **Alternatives considered:**
  - Have the factory surface a mismatch or missing observed commit. Rejected: the suite already
    owns that check, so a factory check would only add a new reporting path.
- **Decision-bearing:** no.

## approach-review: A-003 (rollback procedure not executable): applied

- **Decision:** the procedure is now: pause; let every fixture-pinned claim settle, or cancel it;
  deploy the older release. The refusal message, operations docs requirement, and documentation
  scenario all add that the older release cannot accept `fixture_ref`. A pinned evaluation that is
  still needed means staying on, or redeploying, a fixture-capable release, and a new request
  without the key evaluates only the default fixture. "Re-admit after rollback" is removed from the
  proposal, operations spec, design §7, and the migration plan. INT-005 asserts the new message
  and that it never suggests re-admission.
- **Alternatives considered:**
  - Keep re-admission as an option. Rejected: the guard blocks while the original claim is
    unfinished, and an older release rejects `fixture_ref`, so the step cannot be completed.
- **Decision-bearing:** no.

## tasks: one task for the whole change

- **Decision:** `tasks.md` holds exactly one task, as the workflow requires. It points at every
  artifact, states the precedence order (proposal-review, then approach-review), lists ten scope
  areas mapped to design sections 1–8, the tests, and the PR-description orange item, and ends with
  a "Done when" list. It forbids changes outside this repository, deploys, and touching the live
  state or Paul's checkouts.
- **Decision-bearing:** no.
