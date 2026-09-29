# Decisions

## propose: verdict

- **Decision:** go with caveats.
- **Alternatives considered:**
  - No-go, relying on more frequent npm releases. Rejected: claims would stay unpinned and
    unrecorded.
  - Recording only a Validator ref without building from source. Rejected: the recorded SHA
    would not be what ran.
- **Decision-bearing:** yes.

## propose: Fly image without editing Agent Runner

- **Decision:** the factory writes a derived Dockerfile: Runner's `docker/dev/Dockerfile`
  followed by a factory-owned tail that builds Agent Validator at the claim's frozen SHA.
  Everything stays inside this repository.
- **Alternatives considered:**
  - Add a build argument to Runner's Dockerfile. Rejected: it needs a change in another
    repository, which would be a direction-level stop.
  - `npm install -g github:…#sha`. Rejected: the package has no `prepare` script, so it would
    install without `dist`.
- **Decision-bearing:** yes.

## propose: default checkout path

- **Decision:** `[repositories] agent_validator` is optional. It defaults to `agent-validator`
  next to the `agent_runner` checkout, which is `/Users/paul/codagent/agent-validator` on Paul's
  Mac. This follows the issue ("use the same checkout path by default") without putting a
  machine path in code and without an edit to the local config outside the repository.
- **Alternatives considered:**
  - A required key. Rejected: it needs an out-of-repository config edit, and existing configs
    would fail validation.
  - A hardcoded absolute default. Rejected: it puts a machine path in code.
- **Decision-bearing:** yes.

## propose: avoiding a rebuild during host work

- **Decision:** after the deploy pauses the factory, it skips the Validator build with a warning
  if any host fix or feature slot is busy (`slots_free`). The deploy itself continues.
- **Alternatives considered:**
  - Wait for the slots to free. Rejected: deploys are meant to run at any time.
  - Fail the deploy. Rejected: too disruptive.
- **Decision-bearing:** no. The issue requires this behavior; how it is done is a default.

## propose: host provenance source

- **Decision:** record the SHA that the Validator executable on `PATH` reports (expanded against
  the checkout), not the checkout's `HEAD`. The build can lag the checkout: `aa22fa7` on `PATH`
  versus `a7323e9` at `HEAD` when this was written.
- **Alternatives considered:**
  - Record the checkout's `HEAD`. Rejected: it can be wrong.
  - Have the deploy write a marker file. Left as an option for the design.
- **Decision-bearing:** no.

## propose: scope limited to host and Fly

- **Decision:** the Docker-backend evals and fixes keep the npm Validator, and their provenance
  says the Validator is not pinned. The issue names only Fly evals and host fixes and features.
  Docker images are built by Agent Runner's launcher, outside this repository.
- **Alternatives considered:**
  - Also change the Docker path. Rejected: it needs a change in Agent Runner.
- **Decision-bearing:** no.

## propose: Validator ref configuration

- **Decision:** add `[eval.defaults] agent_validator_ref = "main"` and resolve it once at
  admission into the frozen `revisions.validator`. An individual eval request cannot override it.
- **Alternatives considered:**
  - Hardcode `main`. Rejected: a pin could then only be made through code.
  - Allow a per-request override. Deferred: it widens the request grammar.
- **Decision-bearing:** no.

## propose: reporting format

- **Decision:** add `validator@<7>` to the Project `Refs` field and the full SHA to the eval
  reports. Do not change the curated results files in agent-evals or the suite's `result.json`.
- **Alternatives considered:**
  - Add a factory file to the committed results. Rejected: it changes a persisted format in
    another repository.
- **Decision-bearing:** no.

## proposal-review: PR-01 (a missing checkout would break Docker-only deploys)

- **Status:** applied.
- **Decision:** if `[repositories] agent_validator` is unset and nothing exists at the sibling
  default path, the deploy skips the Validator step with a warning, so Docker-only deployments
  keep deploying. If the key is explicitly configured but the path is not a git checkout, the
  deploy stops before changing anything, as it does today for a missing Runner checkout. The
  "non-breaking" claim now names the new prerequisite for host and Fly backends.
- **Alternatives considered:** make the checkout a prerequisite for every deployment. Rejected:
  Docker backends are out of scope and do not use the checkout.
- **Decision-bearing:** no. It is a compatibility default inside the issue's scope.

## proposal-review: PR-02 (nothing checks that the Validator on PATH is the checkout's build)

- **Status:** applied.
- **Decision:** after building, the deploy checks that the plist's `PATH` resolves
  `agent-validator` to the checkout's `dist/index.js`. `doctor` fails host readiness, when host
  fixes or features are enabled, if it resolves anywhere else. A build that is merely behind
  (an intentionally skipped build) is reported but does not fail `doctor`. The deploy does not
  create or replace the link itself; the installation docs cover setting it up once.
- **Alternatives considered:** have the deploy create a link in a directory on `PATH`. Rejected:
  the deploy would then modify an operator-owned directory, and the release's `.venv/bin` is
  immutable.
- **Decision-bearing:** no.

## proposal-review: PR-03 (`slots_free` also waits on the eval slot)

- **Status:** applied.
- **Decision:** add a `host_slots_free` predicate to `scripts/slots.sh` that looks only at the
  feature slot and, when fixes execute on the host, the fix slot. It guards only the Validator
  rebuild. `slots_free` stays in use for release pruning.
- **Alternatives considered:** keep using `slots_free`. Rejected: a long Fly eval would block
  Validator refreshes for no reason.
- **Decision-bearing:** no.

## spec: feature-execution needs no delta

- **Decision:** add Validator provenance to `factory-fix-execution` "Record host provenance"
  only. `factory-feature-execution` already requires feature attempts to follow its host
  provenance rule. The proposal's capability list was revised to match.
- **Alternatives considered:** a separate feature-execution delta. Rejected: it would duplicate
  the inherited rule.
- **Decision-bearing:** no.

## spec: Validator pinned only under Fly execution

- **Decision:** only claims admitted under Fly execution resolve and record a Validator revision.
  Docker-admitted claims and claims admitted before this change record none. They are reported
  as "published npm release, not pinned", and their `Refs` field keeps the existing
  three-part form.
- **Alternatives considered:** always resolve and record the Validator. Rejected: Docker
  attempts would then report a commit that never ran.
- **Decision-bearing:** no.

## spec: the Validator ref is not request-selectable

- **Decision:** `agent_validator_ref` comes only from shared configuration. An eval block that
  supplies it is rejected as an unsupported key, under the existing unsupported-key rule.
- **Alternatives considered:** allow it as a request override, like `agent_runner_ref`.
  Deferred: it widens the request grammar, and the issue does not ask for it.
- **Decision-bearing:** no.

## spec: an unresolvable Validator holds admission

- **Decision:** if the Validator commit cannot be resolved at a Fly eval admission, the eval is
  not admitted. It is reported as an unavailable prerequisite that later cycles recheck, not as
  a user-correctable `needs-input`.
- **Alternatives considered:** admit it unpinned with the npm release. Rejected: it would
  silently lose the provenance the issue asks for.
- **Decision-bearing:** no.

## spec: guard the pinned Validator in the image build

- **Decision:** the Fly image build fails if the installed `agent-validator` does not report the
  claim's frozen revision. The failure goes through the existing build-failure path: pre-suite
  failure, no digest, rebuild on relaunch. Fly Machine provenance also records
  `agent-validator --version`.
- **Alternatives considered:** check the version inside the Machine before the suite starts.
  Rejected: a mismatch would reuse the bad digest on every relaunch.
- **Decision-bearing:** no.

## spec: Validator doctor checks

- **Decision:**
  - A build that is behind `origin/main` is informational.
  - The host groups fail when the checkout is missing, or when `agent-validator` on the service
    or LaunchAgent PATH is not the checkout's build.
  - eval-fly fails when the checkout is missing.
  - Doctor never builds, fetches into, or relinks the Validator.
- **Alternatives considered:** fail on a build that is behind. Rejected: an intentionally skipped
  build must still pass.
- **Decision-bearing:** yes. A host installation without the linked checkout build now fails
  doctor, which is a new prerequisite. On Paul's Mac it is already met.

## spec: new requirements, not edits to the doctor and documentation blocks

- **Decision:** add the Validator's doctor and documentation behavior as new
  `factory-operations` requirements, rather than rewriting the very long "Diagnose readiness
  with doctor" and "Document installation" blocks.
- **Alternatives considered:** modify those blocks in place. Rejected: the added requirements
  are self-contained, and editing the long blocks risks conflicts with unrelated changes.
- **Decision-bearing:** no.

## design: follow the existing readiness path when the Validator cannot be resolved (spec revised)

- **Decision:** an unresolvable Validator at a Fly eval admission raises `ReadinessError`
  through the existing admission path. That path posts one "Waiting for revision readiness"
  comment per reason, sets the attention label, and retries on later cycles. The
  `factory-eval-intake` scenario, which said "not flagged with `needs-input`", was revised to
  match.
- **Alternatives considered:** a separate path for the Validator that does not set the label.
  Rejected: it would behave differently from Runner and Skills resolution failures for no gain.
- **Decision-bearing:** no.

## design: missing `bun` stops the deploy before the pause (spec revised)

- **Decision:** when the Validator step will run and `bun` is not on the deploy's `PATH`, the
  deploy stops before pausing and names `bun` and `--no-validator`. This follows `deploy.sh`'s
  rule that anything which can fail without changing the deployment happens before the pause.
  A scenario was added to `factory-operations`.
- **Alternatives considered:** let the build fail after the pause. Rejected: it would leave the
  factory paused for a precondition that could have been checked first.
- **Decision-bearing:** no.

## design: a shared `update-checkout.sh` instead of a copied script

- **Decision:** `update-runner.sh` and `update-validator.sh` become thin wrappers over
  `update-checkout.sh <label> <checkout>`. The Runner script's messages and exit codes are
  unchanged.
- **Alternatives considered:** copy the script, as the proposal said. Rejected: two copies of
  the safety rules could drift apart.
- **Decision-bearing:** no.

## design: derived Dockerfile, with the version checked during the build

- **Decision:** write `claim.Dockerfile` in the claim's factory artifact directory: the Runner
  Dockerfile, a factory tail, and the Runner Dockerfile's last `USER` and `WORKDIR` restored.
  The tail:
  - fetches the frozen SHA from the checkout's credential-free origin URL;
  - builds with a pinned `BUN_VERSION`;
  - runs `npm install -g` on the folder;
  - fails the `RUN` when `agent-validator --version` does not match the frozen SHA.
- **Alternatives considered:**
  - Write the file into the Runner worktree. Kept only as a fallback, in case `flyctl` rejects
    a Dockerfile outside the build context.
  - A second image stage built on the Runner image. Rejected: it would need two builds.
- **Decision-bearing:** no.

## design: where the host Validator commit comes from

- **Decision:** take the first token of `agent-validator --version` and expand it with
  `git rev-parse` in the checkout, without fetching. A non-hexadecimal token, such as an npm
  semver, gives `validator_commit = "unavailable"`.
- **Alternatives considered:** have the deploy write a marker file of the built SHA. Rejected:
  it would not reflect manual rebuilds.
- **Decision-bearing:** no.

## design: reading the fix execution mode in `deploy.sh`

- **Decision:** read `[fix] execution` with `python3 -c` and `tomllib`, defaulting to `docker`.
  `host_slots_free` counts the fix slot only when fixes run on the host.
- **Alternatives considered:**
  - Parse the TOML with `sed`. Rejected: fragile for a key inside a section.
  - Add the backend to the `status` output. Rejected: it would change output that other tools
    read.
- **Decision-bearing:** no.

## test-plan: move the deploy's Validator step into sourced functions

- **Decision:** the Validator step lives in `scripts/validator.sh` and `deploy.sh` sources it.
  Integration tests can then exercise every deploy scenario with real git fixtures and a stub
  `bun`, without running `deploy.sh`, which uses `launchctl` and would restart the live service.
  The design was updated to match.
- **Alternatives considered:** test `deploy.sh` end to end with stubbed `launchctl`, `plutil`,
  and `PlistBuddy`. Rejected: brittle, and a mistake could bootstrap the live LaunchAgent.
- **Decision-bearing:** no.

## test-plan: acceptance may run one real Fly image build

- **Decision:** the acceptance pass may run at most two real `flyctl deploy --build-only --push`
  builds against the configured Fly app (one plus one retry), with an acceptance-only tag. That
  proves the Dockerfile tail builds and passes its version check. It may not create Machines or
  run the live deploy. The pushed tag stays in the registry (#15) and is recorded in the
  evidence.
- **Alternatives considered:**
  - A local Docker build. Rejected: this Mac runs with Docker off by default, and it would not
    test the remote builder or a Dockerfile outside the build context.
  - No real build. Rejected: the risk of `flyctl` rejecting the Dockerfile path would go
    unverified.
- **Decision-bearing:** yes. It authorizes a small paid external effect.

## approach-review: AR-01 (a Fly-pinned claim could run under Docker and report a Validator that never ran)

- **Status:** applied.
- **Decision:** a claim with a frozen `revisions.validator` is held under the eval readiness
  behavior while eval execution is Docker. The hold is raised in `EvalHandler.prepare`, next to
  the Cursor-on-Fly hold, and explains how to resume. No Docker attempt launches, and the
  frozen inputs are unchanged. `factory-eval-intake` gained the requirement text and the
  "Switch a pinned claim to Docker execution" scenario. The design and INT-004 were updated.
- **Alternatives considered:** run under Docker and relabel the provenance as npm. Rejected: the
  claim's `Refs` and its frozen inputs would still name a Validator that did not run, and
  repetitions of one claim would mix Validators.
- **Decision-bearing:** no. It follows the existing hold for a frozen Cursor claim after a
  switch to Fly.

## approach-review: AR-02 (the rebuild guard used current configuration, and the fast-forward happened before the pause)

- **Status:** applied.
- **Decision:**
  - `agent-factory status` gains `host attempts: <n>`, counting unfinished runs whose saved
    plan's backend is `host`, plus runs with no recorded backend yet.
  - `host_slots_free` reads only that line. A missing line counts as busy.
  - Before the pause, the deploy only fetches and checks (`update-checkout.sh --check-only`).
  - After the pause, the fast-forward and the build both run only when there are no host
    attempts.
  - `factory-operations` gained "Report host attempts in status" and a mode-change scenario.
    The deploy requirement now fast-forwards after the pause.
  - The design, INT-002, and the new INT-007 were updated, and the proposal text was aligned.
- **Alternatives considered:**
  - Keep reading `[fix] execution`. Rejected: a configuration switch would hide a running host
    attempt.
  - Query SQLite from `deploy.sh`. Rejected: it couples the script to the store's schema.
- **Decision-bearing:** no. Adding a status line is additive; existing slot lines are unchanged.

## approach-review: AR-03 (the checkout's origin might not be reachable from the Fly builder)

- **Status:** applied.
- **Decision:** admission normalizes the Validator checkout's GitHub origin (HTTPS, `git@`, or
  `ssh://`) to `https://github.com/<owner>/<repo>.git` and freezes it as `sources.validator`.
  Any other origin is a readiness hold with a credential-free reason. The launcher builds from
  the frozen URL, never the live origin. `factory-eval-intake` gained two scenarios, and
  `factory-fly-execution` now names the recorded source. The design, INT-003, INT-004, and the
  proposal were updated.
- **Alternatives considered:**
  - Hardcode `Codagent-AI/agent-validator`. Rejected: it puts a deployment name in the code.
  - Add a shared-config repository key. Rejected: it adds configuration when the checkout
    already names the repository.
- **Decision-bearing:** no.
- **Note:** this replaces the earlier design decision "reading the fix execution mode in
  `deploy.sh`". The deploy no longer reads `[fix] execution`.

## tasks: a single implementation task

- **Decision:** `tasks.md` has exactly one checkbox task covering the whole change. Its
  details, the files and components to change, are sub-sections of that task, not further
  checkboxes, so the plan stays one task as the feature workflow requires. The done criteria
  include the repository's validator checks, strict OpenSpec validation, backward
  compatibility for claims without a Validator revision, and never touching live factory
  state.
- **Alternatives considered:** split the work into per-component tasks. Rejected: the feature
  workflow requires a single task.
- **Decision-bearing:** no.

## define (feature-36-3c3efa20): re-check against issue #36

- **Decision:** the specs, design, test plan, and tasks still match issue #36. The issue has no
  eligible comments and has not changed since the first definition (`reason: initial`), so their
  direction stays as it is. Each of the issue's three requirements (host checkout, deploy
  update and build with the Runner's safety rules and no rebuild during host work; a Fly claim
  image pinned to a SHA resolved once per claim; Validator provenance for evals, fixes, and
  features, reported by `doctor`) maps to existing requirements. The only revision is to
  `proposal.md`. Its Technical Approach and Impact sections still described choices that later
  steps replaced, and they now match the design:
  - a shared `update-checkout.sh` instead of a copied script;
  - `host_slots_free` reads the `host attempts:` status line, not the fix execution mode;
  - the fast-forward waits until after the pause;
  - the image build checks the Validator version, not the Machine;
  - admission freezes a normalized GitHub source URL;
  - a missing `bun` stops the deploy before the pause rather than being reported by `doctor`.
- **Alternatives considered:** leave the proposal stale, since `decisions.md` records the
  superseding decisions. Rejected: implementers and reviewers read the proposal, and the
  contradictions could lead them back to the rejected choices.
- **Decision-bearing:** no. Wording only; no behavior changed.
