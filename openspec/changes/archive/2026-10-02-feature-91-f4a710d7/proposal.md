## Why

An eval request can choose which Agent Runner and Agent Skills revisions it tests, but not which
and-scene fixture it tests against. The fixture is whatever agent-evals pins as `FIXTURE_REF` in
`evals/agent-runner/and-scene/run.sh` (currently `2262a9f1…`). To evaluate a fixture change before
re-pinning agent-evals to it, Paul's only option today is a manual local `run.sh --fixture-ref`
run, outside the factory. That run gets no frozen inputs, no repetitions or recovery, no Fly
execution, and no reporting.

There is an immediate case. and-scene#42 adds the opt-in `task-compliance` review, and the fixture
branch `eval/fixture-sonnet-validator` already has it at
`b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a`. Paul wants a factory eval against that commit before
he decides whether to merge. The suite already supports what is needed (`run.sh --fixture-ref REF`
clones and checks out the ref itself), so the missing piece is entirely in the factory: accept the
key, freeze it, pass it on, and report it.

**Verdict: go.** The change is small and follows the existing per-revision pattern (Runner, Skills,
Validator). It reuses a suite interface that already exists and leaves default evals unchanged.
Building nothing would keep fixture changes merge-first, test-later, or tested by hand without
provenance.

## What Changes

- The fenced `eval` TOML block accepts a new optional `fixture_ref` key: a non-empty string naming
  an and-scene branch, tag, or commit. Other unsupported keys are still rejected.
- When `fixture_ref` is omitted, nothing changes. The suite uses the fixture that the frozen
  agent-evals harness commit pins. No fixture revision is recorded, and existing claims, reports,
  and the `Refs` field look exactly as they do today.
- When `fixture_ref` is set, admission resolves it to a full commit SHA and freezes it with the
  other revisions. Every repetition and recovery of the claim passes that SHA to the suite as
  `--fixture-ref`.
- Admission accepts only a commit that is published on the and-scene GitHub repository, meaning the
  suite's own clone can fetch it. The commit must be reachable from a branch or tag that exists on
  origin after a fresh fetch, and this applies to bare SHAs too.
- An unresolvable or unpublished ref is treated like an unresolvable Runner or Skills ref: the
  claim is not admitted, the issue gets one "waiting for revision readiness" comment, and the
  attention label is applied.
- Reporting makes a pinned fixture obvious. For pinned-fixture claims only:
  - the frozen-inputs comment adds the requested ref and the full resolved fixture SHA;
  - the per-repetition reports carry the fixture SHA;
  - the Project `Refs` field appends `fixture@<7>`.

  Claims without a pinned fixture keep today's comment, reports, and `Refs` text exactly.
- Rollback cannot silently drop a pinned fixture. The deploy script refuses to switch to a release
  that cannot honor fixture pins while any unfinished claim carries one, and it names those claims.
  The operator docs describe the rollback procedure:
  1. pause the factory;
  2. let every fixture-pinned claim settle, or cancel it;
  3. roll back.

  After the rollback, the older release cannot accept `fixture_ref`. A new request without the key
  is appropriate only if evaluating the agent-evals default fixture is what is wanted. If a pinned
  evaluation is still needed, stay on (or redeploy) a fixture-capable release instead of rolling
  back.
- The factory's own documentation of eval-request keys (specs and operator docs in this repository)
  documents `fixture_ref`.
- The issue's last bullet, documenting the key in the agent-evals eval-request template, is a
  cross-repository deliverable of #91. See "Cross-Repository Deliverable" below.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-eval-intake`: the eval block accepts `fixture_ref`. A set ref is resolved at admission
  and frozen in the claim's revisions, and resolution failures are handled like Runner and Skills
  refs. Claims without a fixture revision stay valid.
- `factory-eval-execution`: a claim with a frozen fixture revision passes it to the and-scene suite
  as `--fixture-ref` on every repetition, retry, and resume, under both Docker and Fly execution.
- `factory-eval-reporting`: the frozen-inputs comment, repetition reports, and `Refs` field identify
  the fixture revision when one was pinned, and are unchanged otherwise.
- `factory-operations`: deploy refuses a release that cannot honor fixture pins while unfinished
  claims carry them, and the rollback procedure is documented.

## Technical Approach

The change follows the existing Validator-revision pattern, which already adds an optional revision
to a claim's frozen spec without breaking older claims.

- **Parsing** (`work_kinds/eval/__init__.py`): add `fixture_ref` to `_KEYS`, require a non-empty
  string, and carry it into the effective settings. It is part of the request fingerprint, so
  editing it creates a new request, as editing any other key does.
- **Resolution** (`resolve_revisions` in `work_kinds/eval/handler.py`): resolve only when
  `fixture_ref` is set. Fetch from a local and-scene source checkout, then `rev-parse` to a full
  SHA, reusing `runtime._resolve_revision`. The checkout is a new optional
  `[repositories] and_scene` local path that defaults to a sibling of `agent_runner`, the same
  default rule `agent_validator` uses (`/Users/paul/codagent/and-scene` exists on the factory Mac).
  The checkout is consulted only for requests that set `fixture_ref`, so a missing checkout never
  blocks default evals.

  `_resolve_revision` accepts any commit object present locally, including unpushed commits and
  commits kept after their remote ref was pruned. So after resolving, admission also requires:
  - the checkout's origin is the GitHub repository the suite clones;
  - the commit is reachable from a remote-tracking branch (`refs/remotes/origin/*`) after the
    pruning fetch, or from a tag that origin advertises.

  A missing checkout, a wrong origin, an unknown ref, or an unpublished commit produces a readiness
  reason that names the and-scene checkout.
- **Freezing** (`ParsedRequest.freeze`): record `revisions["fixture"]` only when it is set, and keep
  the frozen-spec version unchanged. This is an additive optional key, read the same way as
  `revisions["validator"]`. Claims without it keep today's behavior.
- **Execution** (`AndSceneAdapter.plan`): append `--fixture-ref <sha>` and
  `--repo https://github.com/Codagent-AI/and-scene.git` when the frozen spec has a fixture
  revision. Readiness requires the pinned harness to accept both options. The suite clones and checks out the fixture inside the sandbox or Machine, and
  its `--resume` path revalidates the recorded fixture, so the factory needs no new worktree. The
  Fly image and manifest do not change.
- **Reporting** (`refs_text`, `frozen_inputs_event`, repetition report text): add the fixture
  revision only when present, and validate it as a full SHA, as the Validator revision is
  validated. Text for claims without one is byte-for-byte unchanged.
- **Rollback guard** (`scripts/deploy.sh`, operator docs): before switching the LaunchAgent, deploy
  checks whether the target release honors fixture pins. If it does not and any unfinished claim
  carries `revisions.fixture`, deploy stops, names the claims, and leaves the live release in
  place. The design chooses how to detect support, for example a capability marker in the
  release. Older releases ignore unknown revision keys and never enforce the frozen-spec version,
  so a version bump alone would not protect these claims.

Material risks:

- **Fixture repository mismatch.** The suite clones its own `REPO`, which defaults to
  `https://github.com/Codagent-AI/and-scene.git` in today's harness. The factory resolves against a
  local checkout. Two measures close this gap:
  - the origin and publication checks at admission;
  - passing that repository explicitly with `--repo` for pinned claims, so a harness with another
    default cannot clone a different repository from the one admission certified.
- **Later branch deletion.** A commit that was published at admission can become unreachable if
  its only branch is deleted before a later repetition or recovery runs. The in-sandbox clone then
  cannot check it out, and the suite fails the run at fixture checkout as an ordinary pre-suite
  failure. This time-dependent risk is accepted and documented, not mitigated.
- **Rollback by an older deploy script.** The guard protects only deploys run with a
  `scripts/deploy.sh` that contains it. An operator who rolls back by hand, or with an older
  checkout's script, bypasses it, which is why the documented procedure matters.
- **Comparability.** A run against a non-default fixture is not comparable with runs against the
  pin. The reporting changes above exist to make that visible.

## Cross-Repository Deliverable

Issue #91 also asks for the agent-evals eval-request template
(`.github/ISSUE_TEMPLATE/eval-request.md`) to document the new key. That template lists every
supported override and is the public way to make a request, so the issue is not complete until it
changes.

This feature's pull request can change only agent-factory, so it cannot make that edit. Instead,
the PR description carries an explicit orange attention item: until a matching agent-evals change
lands, #91 is only partly delivered. The item gives the exact line to add under the existing
overrides:

```toml
# fixture_ref = "<and-scene branch, tag, or commit>"  # default: the agent-evals pin
```

Paul can open that agent-evals change himself, or assign it to the factory, before closing #91.

## Out of Scope

- Selecting the reference ref (`REFERENCE_REF`), the change name, the judge model, the fixture
  repository URL, or the agent-evals harness revision from a request.
- Changing agent-evals' `FIXTURE_REF` pin or its default.
- Comparing or baselining results across fixture revisions.
- Changing the committed results format in agent-evals. The suite already records what it
  observed: `result.json` has `candidate_source.fixture_commit`, and `proof-metadata.json` has
  `repo`, `fixture_ref`, and `fixture_commit`.

## Impact

- **Code:** `src/agent_factory/work_kinds/eval/__init__.py` (keys, validation, freeze),
  `work_kinds/eval/handler.py` (resolution, refs text, frozen-inputs and report text),
  `suites/and_scene/__init__.py` (argv and `SourceRepositories`), local configuration loading and
  `doctor` for the optional `[repositories] and_scene` path, `scripts/deploy.sh` (rollback
  guard), and `AGENTS.md` / `docs/operations.md` (key and rollback procedure), with tests for each.
- **Persisted data:** an optional `revisions.fixture` (and the effective `fixture_ref` setting) in
  new claims' frozen specs. This is additive and backward compatible. A release without this change
  would run such a claim against the agent-evals pin, which is why deploy refuses that rollback
  while such claims are unfinished.
- **Operators:** no required configuration change on the factory Mac, because the sibling default
  resolves to the existing and-scene checkout. A deploy is needed because this is an Agent Factory
  change.
- **Users:** eval requesters gain one optional key. Existing requests and claims behave exactly as
  before.
