## Why

The factory builds Agent Runner from `main` but runs Agent Validator from its latest npm
release. A merged Validator fix therefore does not reach factory work until someone publishes a
release. For example, agent-validator #158 is merged, but factory runs still use npm 1.14.0. Two
paths are affected:

- **Fly evals.** Each claim image is built from Agent Runner's `docker/dev/Dockerfile`
  (`fly/transport.py`). That Dockerfile runs `npm install -g … agent-validator`, which installs
  whatever version npm publishes last. Because the layer is rebuilt for each claim
  (`FACTORY_CLI_REFRESH`), two claims of the same eval can run different Validators, and nothing
  records which one ran.
- **Host fixes and features.** These run whatever `agent-validator` is on the operator's `PATH`.
  `scripts/deploy.sh` updates and builds only the Agent Runner checkout. Paul has moved his `PATH`
  to a build from `/Users/paul/codagent/agent-validator` by hand, but nothing keeps that build
  current. When this proposal was written, the build on `PATH` reported `aa22fa7` (#158) while
  the checkout's `HEAD` was `a7323e9` (#161).

The Validator decides whether a fix or feature passes, and it affects eval scores. Running an
unpinned, unrecorded Validator makes factory outcomes hard to reproduce, and eval results cannot
be tied to the Validator that produced them. The issue asks the factory to handle the Validator
the way it handles Agent Runner.

**Verdict: go with caveats.** The change is small and fits patterns the factory already has:
`update-runner.sh`, per-claim frozen revisions, and per-claim Fly images. There is no build-free
alternative. Publishing npm releases more often still leaves claims unpinned and unrecorded, and
reading a Validator SHA from a card is not enough when the running binary comes from npm. The
caveats:

1. The Fly change has to work without editing Agent Runner's Dockerfile, which lives in another
   repository (see Technical Approach).
2. Changing the Validator changes eval results. Existing baselines stay tied to the SHA they
   recorded.
3. Only the `host` fix and feature backend and the `fly` eval backend change. The Docker backends
   build their images through Agent Runner's sandbox launcher and keep today's behavior.

## What Changes

- **Local configuration.** Add an optional `[repositories] agent_validator` checkout path. When
  it is unset, the default is an `agent-validator` directory next to the `agent_runner`
  checkout. For Paul that is `/Users/paul/codagent/agent-validator`, so his machine-local config
  needs no edit. If the key is unset and nothing exists at the default path, the deploy skips the
  Validator step with a warning. A deployment that uses only the Docker backends therefore keeps
  deploying without a Validator checkout. If the key is set but the path is not a git checkout,
  the deploy stops before changing anything, as it does today for a missing Runner checkout.
- **Shared configuration.** Add `[eval.defaults] agent_validator_ref = "main"`, next to
  `agent_runner_ref`.
- **Deploy (host).** Add `scripts/update-validator.sh`. It uses the same fast-forward and skip
  rules as `update-runner.sh`: skip with a warning when the checkout is on another branch, has
  uncommitted changes, or has commits not on `origin/main`, and never push. The checks run
  before the pause. After the factory is paused, `scripts/deploy.sh` fast-forwards and builds
  the checkout. It then checks that the service's `PATH`
  (the plist's `PATH`) resolves `agent-validator` to the checkout's built `dist/index.js`. If
  not, it warns, and `doctor` fails host readiness (see below). The deploy never creates or
  replaces the operator's link; the installation docs describe setting it up once. Both the
  fast-forward and the build are skipped with a warning while any attempt recorded as running
  on the host is unfinished, because the Validator is rebuilt in place and a running attempt
  calls it repeatedly. A new `host attempts: <n>` line in `agent-factory status` carries that
  count. Evals, which never use
  the host Validator, do not block the build. `--no-runner` keeps its current meaning. A new
  `--no-validator` flag skips the Validator step.
- **Fly evals.** Resolve `agent_validator_ref` to a full SHA once, at eval admission, and freeze
  it in the claim's revisions next to the Runner SHA. With it, freeze the checkout's GitHub
  origin, normalized to a public HTTPS URL the remote builder can fetch. The claim image
  installs Agent Validator built from that SHA instead of from npm. A pinned claim is held,
  rather than run with the npm Validator, if eval execution is switched to Docker. Claims admitted before this change have no Validator
  SHA and keep the npm install.
- **Provenance.**
  - Eval claims record `revisions.validator`.
  - Eval reports list the full Validator revision.
  - The Project `Refs` field becomes `runner@<7> skills@<7> evals@<7> validator@<7>`.
  - A host fix or feature attempt records the path and reported version of the Validator
    executable that ran, and the commit that build came from. This matches how host attempts
    already record the Runner.
  - When a SHA is not known, reports say it is unavailable.
- **`doctor`.** Report the Validator checkout, the commit of the build on `PATH`, and whether the
  build is behind the checkout's `origin/main`. Being behind is reported but does not fail
  `doctor`: an intentionally skipped build keeps a known older revision. `doctor` fails:
  - when evals run on Fly and the checkout is missing, because eval admission needs it to resolve
    the SHA;
  - when host fixes or features are enabled and the service's `PATH` resolves `agent-validator`
    to anything other than the checkout's build, such as an npm install.
- **Docs.** Update `AGENTS.md` and `docs/operations.md`, and add the new key to
  `config/local.example.toml`.

Nothing is **BREAKING** in data or interfaces. The new configuration keys are optional or have
defaults, and deployments that use only the Docker backends need nothing new. Frozen claim
payloads only gain a key, and older payloads without it are still read. There is one new
prerequisite. An installation that runs fixes or features on the host, or evals on Fly, needs
the Validator checkout. Host fixes and features also need an `agent-validator` link on the
service `PATH` that points at the checkout's build. Until both are in place, `doctor` fails for
those backends and names the missing step. On Paul's Mac both already exist.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-operations`: the deploy updates and builds the Agent Validator checkout with the same
  safety rules as the Runner. It skips the build while a host fix or feature runs, and skips the
  whole step when the default checkout is absent. It checks that the service `PATH` uses that
  build. `doctor` reports the Validator's provenance and fails host readiness when the service
  `PATH` uses a different Validator. There is a new local repository key and its default.
- `factory-fly-execution`: the per-claim image installs Agent Validator from the claim's pinned
  SHA, and Machine provenance includes the Validator revision.
- `factory-eval-intake`: admission resolves and freezes the Agent Validator revision next to the
  Runner, Skills, and harness revisions.
- `factory-eval-reporting`: reports and the `Refs` field include the Validator revision.
- `factory-fix-execution`: host provenance records the Agent Validator executable, its reported
  version, and the commit it was built from. Feature attempts inherit this through their
  existing host execution rule in `factory-feature-execution`, so that spec needs no delta.

## Technical Approach

- **Host update and build.** `update-validator.sh` copies `update-runner.sh`: the same exit-code
  contract (0 = ready to build, 3 = skipped with a warning). The two scripts could later share a
  helper, but duplicating them now keeps the tested Runner script unchanged. The build runs after
  `deploy.sh` pauses the factory. While paused, the factory admits nothing new, so a status
  check at that point reliably shows whether a host attempt is still running. The check uses a
  new predicate in `scripts/slots.sh`, `host_slots_free`, which looks only at the feature slot
  (features run only on the host) and at the fix slot when fixes execute on the host. The
  existing `slots_free`, which also requires the eval slot to be free, stays in use for release
  pruning only. The build runs the repository's own local build: dependency install, then
  `build:local`, which embeds the git SHA in `--version`. On Paul's Mac,
  `~/.local/bin/agent-validator` already points at the checkout's `dist/index.js`, so the build
  replaces what is on `PATH` without the deploy changing any links. On another host, the deploy's
  `PATH` check and `doctor` report a missing or wrong link instead of letting an npm copy pass
  silently. A failed Validator build stops the deploy with the factory paused, the same as a
  failed `make build` for the Runner.
- **Host provenance comes from the build, not the checkout.** The build on `PATH` can lag the
  checkout, for example after a skipped build. So each attempt records the SHA the running
  executable reports and expands it to a full SHA against the checkout when it can. It never
  records the checkout's `HEAD` in its place.
- **Fly image without changing Agent Runner.** The factory keeps using the claim's pinned Runner
  worktree as the build context and Runner's `docker/dev/Dockerfile` as the base. In the claim's
  scratch space it writes a derived Dockerfile: Runner's Dockerfile followed by a factory-owned
  tail. The tail builds Agent Validator at the claim's frozen SHA from
  `Codagent-AI/agent-validator` and installs it globally over the npm copy, then restores the
  final user and working directory. The Validator SHA is passed as a build argument, so the
  layers above it stay cacheable. The derived Dockerfile depends on the end of Runner's
  Dockerfile (`USER pwuser`, `WORKDIR /workspace`). `design.md` will decide how to guard that
  dependency, for example by checking inside the Machine that `agent-validator --version` reports
  the frozen SHA before the suite starts, and failing the attempt as pre-suite otherwise. The
  alternative was a build argument added to Runner's Dockerfile. It was rejected because it
  requires a change in another repository.
- **Freezing.** Admission resolves `agent_validator_ref` with the existing
  `runtime._resolve_revision` against the local Validator checkout, the same way it resolves the
  Runner. The eval request grammar does not change, so an individual request cannot override the
  Validator ref yet.

## Out of Scope

- Docker-backend evals and fixes. Their images are built by Agent Runner's sandbox launcher,
  outside this repository. They keep the npm Validator, and their provenance says the Validator
  revision is not pinned.
- Changing Agent Runner's Dockerfile, the Agent Evals suite's `result.json` format, or the
  curated results files the factory commits.
- Overriding the Validator ref per eval request.
- Publishing Validator npm releases, and removing old `claim-` image tags (#15).
- Re-baselining existing eval results.

## Impact

- **Code:**
  - `scripts/deploy.sh` and a new `scripts/update-validator.sh`.
  - `src/agent_factory/config.py`: the new repository key and eval default.
  - `src/agent_factory/work_kinds/eval/`: freezing, reporting, and the `Refs` field.
  - `src/agent_factory/fly/transport.py` and `fly/launcher.py`: the derived Dockerfile, the
    build argument, and provenance.
  - Host launch provenance for fixes and features (`work_kinds/pull_request/`, `backends/host.py`).
  - `operations.py`: `doctor`.
- **Configuration:** `config/codagent.toml` (`agent_validator_ref`) and
  `config/local.example.toml`.
- **Tests:** a counterpart of `test_update_runner.py` for the Validator, deploy slot tests, Fly
  build-command tests, eval freeze and reporting tests, and host provenance tests.
- **Operations:**
  - Deploys also move the Validator build to `origin/main`.
  - Fly image builds take somewhat longer, because the Validator is built from source.
  - Eval scores can shift when the Validator changes. Reports now say which Validator produced
    each result.
  - The host needs `bun` to build the Validator. `doctor` reports it when it is missing.
