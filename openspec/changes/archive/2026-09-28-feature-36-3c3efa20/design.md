## Context

The factory runs Agent Runner built from `main`, but Agent Validator comes from npm:

- **Fly evals.** Each claim's image is built by `fly/transport.py:build_claim_image`. It runs
  `flyctl deploy --build-only` with the pinned Runner worktree's `docker/dev/Dockerfile`. That
  Dockerfile's `FACTORY_CLI_REFRESH` layer runs `npm install -g … agent-validator`.
- **Host fixes and features.** These run whatever `agent-validator` the service `PATH` finds.
  On Paul's Mac, `~/.local/bin/agent-validator` is a symlink to
  `/Users/paul/codagent/agent-validator/dist/index.js`, and the LaunchAgent `PATH` includes
  `~/.local/bin` and `~/.bun/bin`.
- **Deploy.** `scripts/deploy.sh` runs `scripts/update-runner.sh`. It returns 0 when the Runner
  checkout is ready to build, 3 when it is skipped with a warning, and anything else is fatal.
  After pausing the factory, the deploy runs `make build` in the Runner checkout. Pruning old
  releases uses `slots_free` from `scripts/slots.sh`.

The code this change touches:

- **Eval admission.** `EvalHandler.accept` calls `resolve_request` →
  `resolve_revisions(SourceRepositories, ParsedRequest)`. That calls
  `runtime._resolve_revision(path, ref)`, which fetches the checkout and resolves the ref to a
  commit. The result is `ParsedRequest.freeze(...)` → `frozen_spec["revisions"] = {runner,
  skills, evals}`. A `ReadinessError` at this point makes `runtime.py` post one "Waiting for
  revision readiness" comment per reason, set `needs-input`, and retry on later cycles.
- **Eval reporting.** `refs_text` renders `runner@ skills@ evals@`, and `frozen_inputs_event`
  posts the frozen spec as JSON.
- **Fly manifest and versions.**
  - The Fly launcher manifest is built by `suites/and_scene._fly_manifest`, which has `commits`,
    `repositories`, and `worktrees`.
  - The guest's setup script (`fly/guest.py`) writes `versions.json` for `claude` and `codex`.
  - `eval/handler._fly_versions` reads that file back into provenance.
- **Host provenance.** `work_kinds/pull_request/launch.build_host_plan` records
  `runner_executable` and `runner_version` in `host-provenance.json` and in the plan's
  ownership hints. `backends/host.py` copies those keys into the run record.
- **Doctor.** Host checks live in `work_kinds/pull_request/readiness._host_diagnostics`, which
  today only checks that `agent-validator` is on `PATH`. The check that host executables are on
  the LaunchAgent `PATH` is `operations._launch_agent_path_diagnostic`. Fly checks are in
  `fly/backend.FlyBackend.readiness` (group `eval-fly`).
- **Validator build facts.**
  - `Codagent-AI/agent-validator` is public.
  - Its local build is `bun install --frozen-lockfile` followed by `bun run build:local`, which
    runs `INJECT_GIT_VERSION=1 bun build.ts`.
  - The build bundles with `packages: "external"`, so `dist/index.js` needs the checkout's
    `node_modules` at runtime.
  - A local build's `agent-validator --version` prints `<short sha> <subject>`, for example
    `aa22fa7 Merge pull request #158 …`. An npm build prints a semver.

## Goals / Non-Goals

**Goals:**

- Host fixes and features run the Validator built from the operator's checkout, which each
  deploy keeps at `origin/main`.
- Each Fly eval claim installs the Validator at a commit frozen at admission.
- Provenance records the Validator commit for Fly evals and for host fixes and features.
- `doctor` reports the Validator build and fails when host runs would use a different Validator.

**Non-Goals:**

- The Docker backends. Their images are built by Agent Runner's sandbox launcher.
- Changing Agent Runner's Dockerfile or the Agent Evals result format.
- A per-request Validator ref.
- Creating the operator's `agent-validator` link.

## Approach

### 1. Configuration (`config.py`)

- **Local.** `RepositoryConfig` gains `agent_validator: Path` and
  `agent_validator_explicit: bool`. If `[repositories] agent_validator` is set, the loader uses
  that path, expanded as other repository paths are, and sets `agent_validator_explicit` to
  true. Otherwise the path is `agent_runner.parent / "agent-validator"` and the flag is false.
  The key is added, commented, to `config/local.example.toml`.
- **Shared.** `config/codagent.toml` gains `[eval.defaults] agent_validator_ref = "main"`.
  - `EvalHandler.from_config` reads it with default `"main"` into a new
    `EvalDefaults.agent_validator_ref`.
  - `_KEYS` (request keys) is not changed, so a request that supplies the key is already
    rejected as an unsupported setting.

### 2. Eval admission and freezing (`work_kinds/eval/`)

- **Where the checkout comes from.** `SourceRepositories` gains
  `validator: Path | None = None`. `from_config` sets it to `local.repositories.agent_validator`
  only when `local.eval_execution == "fly"`.
- **Resolution.** `resolve_revisions` returns `(runner, skills, validator | None)`. When
  `sources.validator` is set, it calls `_resolve_revision(sources.validator,
  request.settings["agent_validator_ref"])`. The request's effective settings carry the default
  `agent_validator_ref`, so the fingerprint, which is computed over overrides only, does not
  change.
- **A source the builder can reach.** Admission also normalizes the checkout's `origin` URL
  into `validator_source_url(checkout) -> str`, a new helper in `work_kinds/eval/`. It accepts:
  - `https://github.com/<owner>/<repo>[.git]`;
  - `git@github.com:<owner>/<repo>[.git]`;
  - `ssh://git@github.com/<owner>/<repo>[.git]`.

  Each becomes `https://github.com/<owner>/<repo>.git`, with no user, token, or port. Any other
  origin, such as a `file://` URL, a local path, or another host, raises `ReadinessError`:
  "Agent Validator checkout origin `<redacted>` is not a GitHub repository the Fly builder can
  fetch". The URL is frozen next to the commit as `sources.validator`, so a later change to the
  checkout's origin does not move the claim's source. Anonymous HTTPS fetch works because
  `Codagent-AI/agent-validator` is public. A private fork would fail at image build time, with
  the builder's diagnostic.
- **Failure.** A missing checkout or failed fetch raises `ReadinessError` through the existing
  path. The message names the Validator checkout, for example: `Cannot fetch <path> from origin
  …`.
- **Freezing.** `ParsedRequest.freeze(..., validator_sha: str | None = None)` checks the SHA
  with `_sha` and adds `revisions["validator"]` only when it is present. With the SHA, it also
  adds `sources: {"validator": <url>}`. The payload `version` stays 1, because the keys are
  additive and older readers ignore them.
- **A pinned claim under Docker execution.** In `EvalHandler.prepare`, next to the existing
  Cursor-on-Fly check: when `revisions.validator` is present and
  `local.eval_execution == "docker"`, raise `ReadinessError`. The message is: "this claim pinned
  Agent Validator `<7>` at admission under Fly execution; Docker execution installs the
  published npm release, so the claim runs only under Fly execution. Switch eval execution back
  to fly, or close the issue and submit a fresh request." The claim is held by the existing eval
  readiness behavior in `factory-claim-lifecycle`, its frozen inputs are not changed, and no
  Docker attempt launches. This keeps the reported Validator true. The reverse case, a claim
  with no Validator revision running under Fly, builds from the npm release and reports "not
  pinned", which is also true.
- **`refs_text`.** It still requires `runner`, `skills`, and `evals`. When `revisions` has a
  valid `validator`, it appends `validator@<7>`.
- **`frozen_inputs_event`.** After the JSON, it adds one line: either `Agent Validator: <full
  sha>`, or `Agent Validator: published npm release (not pinned)`.

### 3. Fly image (`suites/and_scene`, `fly/transport.py`, `fly/launcher.py`)

- **Manifest.** `_revisions` treats `validator` as optional: when it is present, it must be a
  full SHA. `_fly_manifest` adds `commits.validator`, and a `validator_repository` copied from the frozen
  `sources.validator`. It does this only when the claim has a Validator revision. It never reads
  the local checkout's origin at launch time.
- **`build_claim_image`** gains `validator: tuple[str, str] | None`, the revision and the
  repository URL. When it is `None`, the command stays exactly as it is today. When it is set:
  1. Read `<runner worktree>/docker/dev/Dockerfile`.
  2. Record the last `USER` and last `WORKDIR` instructions.
  3. Write `<factory artifact dir>/claim.Dockerfile`: the Runner Dockerfile, then the factory
     tail (below), then the recorded `USER` and `WORKDIR` again.
  4. Pass `--dockerfile <absolute path to claim.Dockerfile>`, with the Runner worktree still as
     the build context. Also pass `--build-arg AGENT_VALIDATOR_REVISION=<sha>` and
     `--build-arg AGENT_VALIDATOR_REPOSITORY=<url>`, next to `FACTORY_CLI_REFRESH`.

  The tail:

  ```dockerfile
  USER root
  ARG AGENT_VALIDATOR_REVISION
  ARG AGENT_VALIDATOR_REPOSITORY
  RUN set -eu; \
      git init -q /opt/agent-validator; cd /opt/agent-validator; \
      git fetch -q --depth 1 "$AGENT_VALIDATOR_REPOSITORY" "$AGENT_VALIDATOR_REVISION"; \
      git checkout -q FETCH_HEAD; \
      npm install -g --prefix /opt/bun bun@<BUN_VERSION>; \
      /opt/bun/bin/bun install --frozen-lockfile; \
      INJECT_GIT_VERSION=1 /opt/bun/bin/bun build.ts; \
      npm install -g /opt/agent-validator; \
      chmod -R a+rX /opt/agent-validator; \
      reported="$(agent-validator --version | cut -d' ' -f1)"; \
      test -n "$reported"; \
      case "$AGENT_VALIDATOR_REVISION" in "$reported"*) ;; \
        *) echo "agent-validator reports '$reported', expected $AGENT_VALIDATOR_REVISION" >&2; exit 1 ;; esac
  ```

  - `BUN_VERSION` is a constant in `transport.py`, changed on purpose rather than floating.
  - `npm install -g <folder>` replaces the npm-installed `agent-validator` package and its bin
    link with the pinned build.
  - A version mismatch fails the `RUN`, so the build fails and goes through the existing
    build-failure path: pre-suite failure, no digest recorded, rebuild on relaunch.
  - `ENV` and `CMD` from the Runner Dockerfile carry over, because the tail does not change
    them.
- **Launcher.** `_image` in `fly/launcher.py` passes `validator` from the manifest. Claims
  without `commits.validator` build exactly as before.
- **Guest.** `fly/guest.py` records versions for `("claude", "codex", "agent-validator")`.
  `_fly_versions` adds an `agent-validator` key, defaulting to `"unavailable"`, and the Fly
  provenance output lists it with the other versions.

### 4. Host provenance (`work_kinds/pull_request/launch.py`, `backends/host.py`)

A new `validator_provenance(checkout: Path) -> dict[str, str]` works as follows:

- It resolves `agent-validator` with `shutil.which` (the service `PATH`) and records the real
  path as `validator_executable`.
- It runs `--version` with a 15-second timeout. Failures are recorded, not raised, like
  `runner_version`. The output is `validator_version`.
- It takes the first token of the output. If that token looks hexadecimal, it expands it with
  `git -C <checkout> rev-parse --verify --quiet <token>^{commit}`, which does not fetch, and
  records the result as `validator_commit`. Otherwise `validator_commit` is `"unavailable"`.
  The checkout's `HEAD` is never read.

`build_host_plan` gains a `validator_checkout: Path` argument, which the caller takes from
`LocalConfig`. It calls `validator_provenance`, writes the three keys into
`host-provenance.json` and the plan's ownership hints, and extends `HOST_NOTE` to name the
Validator commit that ran. `backends/host.py` adds the three keys to the tuple it copies into
the run record. Feature attempts use the same `build_host_plan`, so they get this without extra
work.

### 5. Doctor

- **Host groups.** In `_host_diagnostics` (fix-host, and feature-host through the existing
  relabeling), `_which_diagnostic("agent-validator")` becomes `_validator_diagnostics(local)`.
  It returns:
  - **"host agent-validator build"**:
    - fails when the checkout is not a git repository;
    - fails when `agent-validator` does not resolve on `PATH`;
    - fails when the real path of what resolves is not `<checkout>/dist/index.js`, naming both
      paths and giving the action "link agent-validator on the service PATH to
      `<checkout>/dist/index.js`";
    - fails the same way for the `PATH` in the installed LaunchAgent definition. It reads the
      plist the same way `_launch_agent_path_diagnostic` does and skips this part when the
      definition is not installed;
    - otherwise passes, showing the checkout and the reported version.
  - **"host agent-validator freshness"**: always passes. When the reported commit is known and
    is not the checkout's `origin/main` (the ref as last fetched; doctor does not fetch), it
    reports "`<sha>` is behind origin/main (`<origin sha>`)" as informational. Otherwise it
    reports "current".
- **Fly group.** `FlyBackend.readiness` adds **"Agent Validator checkout"** (group `eval-fly`).
  It fails when `local.repositories.agent_validator` is not a git work tree, naming the path
  and the `[repositories] agent_validator` key.
- **Docker-only setups.** No Validator check runs when no kind uses host or Fly, so these
  setups see no change.

### 6. Deploy (`scripts/`)

- **`scripts/update-checkout.sh [--check-only] <label> <checkout>`** is the current
  `update-runner.sh` body, with the name in its messages taken from `<label>`. `--check-only`
  runs the fetch and the three safety checks and exits 0 or 3 without merging. `update-runner.sh` becomes
  `exec "$(dirname "$0")/update-checkout.sh" "Agent Runner" "$@"`, so its output and exit codes
  do not change. `update-validator.sh` is the same wrapper with the label "Agent Validator".
- **`agent-factory status`** gains one line, `host attempts: <n>`, from a new `_host_attempts`
  helper in `operations.py`. It counts the nonterminal runs of every pull-request kind whose
  saved plan's `ownership_hints.backend` is `host`. A nonterminal run with no plan yet, for
  example a reserved run, counts too, because its backend is not settled. The count uses the
  backend recorded for each run, not the current configuration, so a host fix that is still
  running after `[fix] execution` switches to `docker` is still counted. The line does not
  match the `<kind> slot:` pattern, so `slots_free` is unaffected.
- **`scripts/slots.sh`** gains `host_slots_free <status>`. It is true only when the status
  contains `host attempts: 0`. A missing status, or a missing line (status from an older
  release), counts as busy.
- **`scripts/validator.sh`** is a new file that `deploy.sh` sources, as it sources `slots.sh`.
  It holds the Validator step as functions, so the step can be tested with real git fixtures, a
  stub `bun`, and literal status text, without `launchctl`:
  - `validator_checkout` chooses the checkout and reports whether it came from explicit
    configuration;
  - `validator_preflight` runs the checks before the pause (below);
  - `validator_build` runs the slot guard and the build;
  - `validator_path_check` compares the plist `PATH` with the build.

  `deploy.sh` only calls them in order.
- **`deploy.sh`**:
  1. Parse `--no-validator` (`build_validator=true` by default).
  2. Work out the checkout:
     - `AGENT_FACTORY_VALIDATOR_CHECKOUT`, or else the `agent_validator = "…"` line in the
       local config (explicit); or else `$(dirname "$runner_checkout")/agent-validator` (the
       default). The Runner path is read the same way it is today, even with `--no-runner`.
  3. Before the pause:
     - If the path is the default and does not exist, warn and set `build_validator=false`.
     - If it is explicit and not a git work tree, `die`.
     - If `bun` is not on `PATH`, `die`, naming `bun` and `--no-validator`.
     - Run `update-validator.sh --check-only`, which fetches and runs the safety checks without
       changing the working tree: 0 continues, 3 sets `build_validator=false`, anything else is
       `die`.
  4. After the pause and the Runner `make build`:
     - Read status with the new release's executable, because the running release's status may
       lack the `host attempts:` line.
     - If `host_slots_free` fails, warn that the Validator update and build are skipped because
       a host attempt is running. Neither the fast-forward nor the build happens, so a running
       attempt's Validator files (`dist/`, `node_modules/`, `skills/`, `contracts/`) do not
       change under it.
     - Otherwise run `update-validator.sh`, which fetches again and fast-forwards. A result of 3
       at this point, from a race with a manual change, warns and skips the build; anything
       else non-zero is `die`, and the factory stays paused. Then run `(cd "$validator" && bun
       install --frozen-lockfile && bun run build:local)`. A failure is `die`, and the factory
       stays paused.
     - After a successful build, resolve `agent-validator` on the plist's `PATH`, with
       `PATH=$plist_path command -v`, and compare its real path (`python3 -c
       os.path.realpath`) with `$validator/dist/index.js`. Warn with both paths if they differ.
       Report `built Agent Validator at <short sha>`.
  5. Doctor, reload, and pruning are unchanged. Pruning still uses `slots_free`.

  The header comment and `AGENTS.md` describe the Validator step.

### Data flow (Fly eval)

```text
admission ──resolve agent_validator_ref via local checkout──▶ frozen revisions.validator
    │
    ▼
first launch: manifest.commits.validator + validator_repository
    │
    ▼
build_claim_image ──claim.Dockerfile (Runner Dockerfile + tail), build args──▶ Fly builder
    │                           (version mismatch ⇒ build fails ⇒ pre-suite failure)
    ▼
Machine: versions.json {claude, codex, agent-validator} ──▶ attempt provenance
    │
    ▼
report: Refs runner@ skills@ evals@ validator@, frozen inputs + Validator line
```

## Decisions

- **Build a derived Dockerfile rather than change Agent Runner.**
  - The rejected alternative was an `ARG` in Runner's Dockerfile, which is a change in another
    repository, a direction-level stop.
  - The derived file depends only on Runner's Dockerfile ending with `USER` and `WORKDIR`, and
    the tail restores whatever the last ones were rather than hardcoding them.
  - The build itself checks the version, so drift fails loudly.
- **Check the version in the image build, not in the Machine.** A failed build records no
  digest, and the relaunch builds again. A check in the Machine would reuse a bad digest on
  every attempt.
- **Pin `bun` in the image, but not on the host.** The host build uses the operator's `bun`, as
  `make build` uses the operator's Go. The image pins `BUN_VERSION` so claim builds are
  reproducible.
- **Take the host commit from `--version`, expanded against the checkout.** The build on `PATH`
  can lag `HEAD`, as it does today (`aa22fa7` against `a7323e9`). The expansion needs no fetch
  and fails soft.
- **Share one helper script instead of copying `update-runner.sh`.** The proposal said the
  script would be copied. A shared `update-checkout.sh` with a thin `update-runner.sh` wrapper
  keeps the Runner script's behavior and messages byte-for-byte and avoids two copies of the
  safety rules.
- **Base the rebuild guard on each run's recorded backend.** The guard uses a `host attempts:`
  status line computed from each run's saved plan, not from `[fix] execution`. A configuration
  switch therefore cannot hide a running host attempt. Both the fast-forward and the build wait
  for the guard, because the Validator reads `skills/` and `contracts/` from its checkout at
  runtime.
- **Build from a canonical GitHub HTTPS source frozen at admission.** The remote builder has no
  SSH keys and cannot see local paths. Normalizing at admission turns an unreachable origin into
  a readiness hold, rather than a build failure on every attempt.
- **Only Fly admissions resolve the Validator.** Docker claims never run a pinned Validator, so
  recording a commit for them would be false provenance.

## Risks / Trade-offs

- **`flyctl` and a Dockerfile outside the context.** `flyctl deploy --dockerfile` with an
  absolute path outside the build context is expected to work: BuildKit takes the Dockerfile as
  a separate input. If the remote builder rejects it, the fallback is to write
  `claim.Dockerfile` into the Runner worktree's `.git/`-excluded area (`<worktree>/.factory/`,
  added to that worktree's `info/exclude`). Mitigation: the build-command test asserts the
  path, and acceptance runs one real Fly build.
- **Slower image builds.** The Validator clone, `bun install`, and build add about a minute to
  each claim's build. They run in the detached launcher, not in the controller cycle.
- **A lockfile incompatible with the pinned `bun`.** `bun install --frozen-lockfile` fails the
  build with a clear diagnostic. The fix is to bump `BUN_VERSION`.
- **A host rebuild that is not atomic.** The build rewrites `dist/` and `node_modules`. Guarding
  on `host_slots_free` after the pause, when admission is stopped, rules out a concurrent host
  attempt. An operator who runs `agent-validator` by hand during a deploy may see a partial
  build. That is accepted.
- **Status parsing.** `host_slots_free` depends on the new `host attempts:` line. A missing line
  counts as busy, so an older status output never lets a rebuild through. It is covered by
  `test_deploy_slots.py` and by a status integration test.
- **A pinned claim held under Docker.** A Fly-pinned claim does not run while eval execution is
  Docker. That is the intended trade-off: correct provenance matters more than progress for an
  unusual configuration flip. The hold explains how to resume.
- **Doctor fails on hosts without the link.** This is intended: it is the new prerequisite, and
  it is documented. On Paul's Mac the link already exists.

## Migration Plan

1. Merge. `config/codagent.toml` gains `agent_validator_ref = "main"`. Paul's local config
   needs no change, because the sibling default resolves to
   `/Users/paul/codagent/agent-validator`.
2. Deploy. `deploy.sh` fast-forwards the Validator checkout, which is currently on `main` with
   no local changes, builds it, and checks the plist `PATH`. `doctor` passes because
   `~/.local/bin/agent-validator` already links to the checkout's `dist/index.js`.
3. New Fly eval claims pin the Validator. Unfinished claims admitted earlier keep building from
   the npm release and report "not pinned".
4. **Rollback.** Redeploy the previous release. Older code ignores the extra
   `revisions.validator` key and the manifest fields. Claims pinned in the meantime keep their
   recorded digest, because the image is already built, so no rebuild is needed.

## Testing Strategy

- **Scripts:**
  - Parametrize `tests/integration/test_update_runner.py` over `update-runner.sh` and
    `update-validator.sh`, with the same git fixtures and the label checked in messages.
  - Extend `test_deploy_slots.py` with `host_slots_free`: `host attempts: 0` gives true even
    with a busy eval slot; `host attempts: 1` gives false; a missing line or a missing status
    gives false.
  - A status test: a host fix run stays counted in `host attempts:` after the configuration
    switches `[fix] execution` to `docker`; a Docker fix run and a Fly eval run are not
    counted.
- **Configuration:** the sibling default, an explicit path, and a Docker-only setup without a
  checkout loading cleanly.
- **Eval intake and reporting:**
  - Freezing with and without a Validator revision.
  - `resolve_revisions` resolving the Validator only under Fly.
  - A request that supplies `agent_validator_ref` rejected.
  - An unresolvable checkout producing the existing readiness comment.
  - `validator_source_url` for HTTPS, `git@`, and `ssh://` GitHub origins (normalized), and for
    `file://`, local-path, and other-host origins (readiness error).
  - A Fly-pinned claim held under Docker execution with the explanation, and unheld once
    execution is Fly again.
  - `refs_text` with and without the Validator.
  - The frozen-inputs line.
- **Fly:**
  - `build_claim_image` with the fixture `flyctl`: `claim.Dockerfile` content (Runner
    Dockerfile, tail, restored `USER` and `WORKDIR`), the build arguments, and an unchanged
    command when `validator` is `None`.
  - Manifest fields.
  - `_fly_versions` including `agent-validator`.
  - `test_fly_eval_cycle` end to end with a pinned claim.
- **Host provenance:** `validator_provenance` for a local build (short SHA expanded), an npm
  semver (`unavailable`), a missing executable, and a build behind `HEAD` (records the build's
  commit). The run record and `host-provenance.json` carry the keys.
- **Doctor:** a matching link passes; an npm copy fails with both paths; a LaunchAgent `PATH`
  mismatch fails; a build that is behind is informational; a missing checkout fails `eval-fly`;
  a Docker-only setup shows no Validator lines.
- **Acceptance (manual):** one real deploy on the Mac, and one real Fly claim build that
  confirms `agent-validator --version` in the Machine matches the frozen SHA.

## Open Questions

None.
