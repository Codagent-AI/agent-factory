## Why

Evals are down. The factory runs on a Mac mini with 16 GB of non-upgradeable RAM and a single
volume that Docker Desktop repeatedly fills; Docker crashed mid-iteration when the disk filled, and
it is now disabled. Fixes already escaped Docker through host execution, but
`eval.execution` only supports `"docker"`, so every eval request is held with Docker named as the
reason. An eval executes untrusted candidate code for hours, up to the configured attempt limits,
so running it on the host with the operator's real HOME is not an acceptable substitute.

The execution-backends decision record
(`Projects/Codagent/Agent Runner/Software Factory - execution backends.md` in the Codagent Obsidian
vault) selected Fly Machines on cost (roughly $9-19 per month at factory usage, billed per second)
and because the existing sandbox image runs there as a Firecracker microVM. A manual proof of
concept on 2026-09-19 proved the launch-and-collect path: subscription logins for Codex and Claude
worked from a Fly datacenter address, the Runner image built and booted for amd64, a Machine cloned
pinned inputs and built Runner in place, and the full artifact tree returned through a `DONE`
marker plus recursive SFTP. `poc/fly/fix-guest.sh` holds the reusable launcher from that work.

This change turns that proof into a supported eval execution mode so nightly evals resume without
Docker on the Mac.

## What Changes

- Add `fly` as a value of `eval.execution` beside `docker`. Docker execution stays supported for a
  better-resourced host. Fix and review execution are unchanged.
- Ship a factory-owned Fly launcher that the `and-scene` suite's `run.sh` invokes through the
  `SANDBOX_RUNNER` override it already honors, so the suite's workflow, controller, judge, and
  scoring run unmodified inside the Machine. The launcher is a **transitional compatibility
  adapter** pinned to the harness commit admitted with the claim, not a general emulation of the
  Docker launcher.
- Replace host bind mounts with in-Machine clones of Runner, Skills, and the harness at the commits
  recorded on the claim, so the suite's provenance and cleanliness checks stay meaningful.
- Make the Machine, not any process on the Mac, the durable identity of an attempt, through a
  narrow execution-backend ownership interface implemented for Fly. A restarted controller or
  launcher reattaches to the recorded Machine instead of reporting uncertainty.
- Establish billing containment at Machine creation, before any secret is delivered: factory
  metadata (run identity, launch nonce, ownership marker, absolute deadline) and no platform restart
  are part of the create request, and the Machine's initial process, set in that same request,
  enforces the deadline. A
  reconciliation pass each cycle inspects every factory-tagged Machine, including Machines the
  local store never recorded, and destroys any past its deadline. Losing artifacts is preferred
  over unbounded billing.
- Use one absolute deadline per attempt — launch time plus the attempt's total elapsed-time limit
  plus a fixed collection grace period — shared by the supervisor, the in-Machine watchdog, the
  Machine metadata, and the reconciler. Existing eval limits are unchanged; Machine startup counts
  against the attempt.
- Supervise through a live stream, not a copied tree: the launcher streams Machine output into the
  attempt's existing host log and relays a small heartbeat covering the suite's known progress
  files, including whether its checkpoint exists, and the suite's in-Machine Claude wait; Codex
  quota holds are still recognized from the collected result. The full artifact tree is collected once, after the `DONE` marker, and the
  Machine is then destroyed.
- Recover by **same-Machine resume only**. When the Machine survives a timeout, launcher death, or
  Mac restart, the recovery attempt runs the suite's `--resume` inside that Machine with a fresh
  deadline, bounded by the existing retry budget. When the Machine is lost, the repetition ends as
  an infrastructure error with the evidence already streamed preserved; the factory never silently
  starts fresh. Resuming in a different Machine is not supported.
- Deliver model credentials and the candidate environment file after ownership is persisted, over
  an authenticated channel rather than the Machine configuration, from an exact per-provider file
  allowlist with owner-only permissions. Credentials never enter artifacts, logs, or the heartbeat,
  and are deleted before the `DONE` marker. The factory never writes the Mac's credential files.
- Refuse Cursor role profiles at intake when evals execute on Fly, and never copy a Cursor
  credential to a Machine; Cursor's CLI is currently unreliable as a headless agent. Docker
  execution keeps Cursor support. The shared eval role defaults (already moved off Cursor on
  `dev`) become `lead = claude:opus:medium`, `implementor = codex:gpt-5.6-luna:medium`,
  `tester = codex:gpt-5.6-luna:medium`; only the implementor changes, from Claude Sonnet.
- Under `fly`, stop holding eval admission on Docker availability and the Docker memory allowance;
  hold instead on Fly readiness (API access, token, app, resolvable image, SSH transport).
- Record the immutable image digest observed on the launched Machine, with its size and region,
  before model execution.
- Extend `doctor` and `status`: `doctor` verifies Fly API access, the deploy token, the app, that
  the configured image resolves, and `flyctl` for transport; `status` shows the Machine backing a
  running repetition and its deadline.
- Document the one-time operator setup: Fly organization, app, deploy token, and the amd64 base
  image build.

Repetitions stay sequential, one Machine at a time, exactly as one container at a time today.

## Capabilities

### New Capabilities
- `factory-fly-execution`: Running an eval attempt in a Fly Machine — create-time ownership
  metadata and containment, durable Machine identity and reattachment, the absolute deadline and
  its extension for recovery attempts, input delivery by pinned clone, credential delivery and
  removal, streamed supervision and heartbeat, final artifact collection, same-Machine resume,
  Machine-loss outcome, stale-Machine reconciliation, and authentication-failure handling. Machine
  lifecycle and containment requirements are worded generically; nothing here commits a later
  fix-on-Fly change to reuse it unchanged.

### Modified Capabilities
- `factory-eval-execution`: Suite invocation currently requires one container per repetition
  attempt with read-only source and Git-metadata mounts, and recovery through a new container on
  the same artifact directory. It changes to allow the configured execution mode; under Fly,
  pinned-source provenance is satisfied by in-Machine clones, recovery resumes only inside the
  surviving Machine, and the recorded image identity is the digest observed on the Machine.
- `factory-eval-reporting`: The aggregate verdict is decided by the surviving repetitions;
  lost repetitions are listed with their infrastructure reason and never count as product
  results, and a claim whose every repetition is lost reports `infra-error`.
- `factory-eval-intake`: Requests selecting a Cursor role profile are refused when evals execute
  on Fly; the shared role defaults change.
- `factory-claim-lifecycle`: Ownership verification before termination extends from process or
  container to Machine, and restart recovery adopts a verified surviving Machine. Readiness holds
  and the sandbox memory gate become conditional on the eval execution mode; Fly unavailability
  is a readiness hold that consumes no attempt. A lost Machine is a defined technical outcome.
- `factory-operations`: Configuration gains the eval execution mode and Fly settings (app, region,
  size, image, deploy-token location, collection grace). `doctor`, `status`, and the installation
  documentation cover Fly, including an operator-visible hold when a Mac-side model login is
  found invalid.

## Technical Approach

The controller, queue, SQLite store, and supervisor stay on the Mac. Only the sandbox moves.

```
supervisor ──spawns──> run.sh (SANDBOX_RUNNER = factory Fly launcher, + typed manifest)
                          └─> Fly launcher (replaceable transport; may be restarted)
                                 1. create Machine: metadata + deadline, no restart     
                                 2. persist and verify Machine ownership
                                 3. deliver credentials, env file, suite input
                                 4. Machine: clone pinned inputs, build Runner, run suite script
                                 5. stream output + heartbeat ──> host log and progress files
                                 6. on DONE: collect /artifacts once, destroy Machine
```

Key decisions:

- **Pinned compatibility adapter, fed by a typed manifest.** The factory sets the environment for
  `run.sh`, so it owns both ends of the seam. It hands its launcher a non-secret manifest — run
  identity, repository URLs, the three full commits, deadline, image, and guest paths
  (`/agent-runner-source`, `/agent-skills-source`, `/eval-input`, `/artifacts`). The launcher
  never derives meaning from `--docker-run-arg` strings; it only checks that the pinned harness
  passed exactly the expected option sequence, and readiness fails otherwise. The accepted cost is
  that a harness change to those arguments holds Fly evals until the adapter is updated. A
  provider-neutral sandbox request in `agent-evals` is the intended follow-up.
- **Backend ownership interface, for Fly only.** Launch returning a durable handle, validate
  ownership, observe lifecycle, terminate safely, reconcile after restart, collect execution
  metadata. The supervisor dispatches to it for Fly attempts; existing process and Docker
  ownership code is left untouched, and migrating it behind the interface is a follow-up.
- **Machines REST API for lifecycle and identity.** Create, inspect, metadata, stop, and destroy
  use the API, whose responses are structured and expose the resolved image. `flyctl` is limited
  to SSH and SFTP transport, where its exit status is not trusted as the remote command's result.
- **Two-phase launch.** Containment exists before the Machine can hold anything worth stealing or
  bill unobserved: create with immutable metadata and deadline, persist and verify ownership,
  then mark the attempt running and deliver secrets.
- **Streamed supervision.** The supervisor already captures the launcher's output as the
  attempt's host log, so streaming Machine output through the launcher keeps quota-hold parsing
  and output-based progress working with one writer. The heartbeat reports change in the suite's
  known progress files without copying them, at an interval well below the inactivity limit.
  Relay activity itself never counts as progress.
- **Prebuilt base image.** An operator step builds the Runner sandbox image for amd64 with Fly's
  remote builder. Configuration may name a tag; the launched Machine's digest is what is
  recorded. Runner is still compiled in-Machine from the evaluated commit, as the Docker
  bootstrap does today, so there is no per-run image build.
- **Defaults.** Size is configurable and defaults to shared-cpu-4x / 8 GB (the proof of concept
  only showed 2x / 4 GB sufficing for the lighter fix path); region defaults to `ewr`. The deploy
  token lives with the controller's other credentials and never enters a Machine. No persistent
  volume or retained root filesystem is used.

Risks to close early, in this order:

1. **No full eval has run in a Machine.** Chrome, Agent Validator, and Codex's user-namespace
   read-only sandbox under Firecracker are unproven, as is 8 GB being enough.
2. **Token rotation over a multi-hour run is unproven** (the proof covered about 30 minutes). A
   copied refresh token can be rotated or invalidated server-side without any file on the Mac
   changing. Before `fly` becomes the production setting, one real refresh interval is exercised
   for Codex and Claude with the Mac's logins rechecked afterwards. An in-Machine authentication
   failure is an explicit attempt failure, and a Mac login found invalid raises an
   operator-visible hold.
3. **A lost Machine costs the whole repetition.** Accepted for this version; expected to be rare.
   Cross-Machine resume needs a suite-owned, quiesced checkpoint export and is a later change.
4. **Adapter coupling to `run.sh`'s argument list**, mitigated by the readiness check above.

## Out of Scope

- Fix or review attempts on Fly; fixes stay on host or Docker execution.
- Cursor as an eval agent on Fly, and any fix for the Cursor CLI's headless behavior.
- Resuming a repetition in a different Machine, periodic artifact mirroring or snapshots,
  persistent volumes, and object-storage artifact upload.
- Migrating existing process and Docker ownership code behind the new interface.
- Parallel repetitions or more than one eval Machine at a time.
- Removing Docker execution for evals.
- Changes to `agent-evals`, including a provider-neutral sandbox request, a no-sandbox suite mode,
  or a checkpoint export.
- Changes to eval attempt time limits.
- Other cloud providers and network egress controls. The containment guarantee for repositories
  remains credential scoping plus branch rulesets.
- Automating Fly account, organization, app, or token creation, and automating the base image
  build.
- API-key authentication for the model CLIs.

## Impact

- **Code:** `config.py` (eval execution mode, Fly settings), `config/codagent.toml` (eval role
  defaults), eval intake (Cursor refusal under Fly), `suites/and_scene` (plan, readiness, manifest
  and `SANDBOX_RUNNER` environment), `supervisor.py` (dispatch to the ownership interface,
  Machine adoption on restart), `runtime.py` and `controller.py` (mode-conditional holds,
  reconciliation pass), `operations.py` (`doctor`, `status`), a new ownership interface, Fly
  launcher, and Machines API client under `src/agent_factory/`, and the packaged in-Machine
  scripts (deadline watchdog, guest runner, heartbeat) evolved from `poc/fly/fix-guest.sh`.
  `poc/` is removed once absorbed.
- **Docs:** `installation.md`, `operations.md`, `suite-integration.md`.
- **Dependencies:** `flyctl` on the Mac for transport; a Fly organization, app, and app-scoped
  deploy token; ongoing Fly compute and egress cost. Worst case per attempt is bounded by the
  deadline, roughly 75 cents at the default size and limits.
- **Companion change in `agent-runner`:** the sandbox Dockerfile's Chrome discovery must accept
  the amd64 Playwright path (`chrome-linux64/chrome`), and the repository needs a `.dockerignore`
  so remote
  builds do not scan the whole working tree. The base image cannot be built correctly until the
  Chrome fix lands.
- **Operator:** one-time Fly setup and image build; `local.toml` switches evals to `fly`. Eval
  requests that relied on the Sonnet implementor default now run with the Luna implementor. Existing Docker
  deployments are otherwise unaffected because `docker` remains the default execution mode.
- **Security:** Codex and Claude subscription credentials and the candidate-delivery token are
  copied to a third-party microVM for the duration of a run. They are delivered after ownership is
  established, never stored in the Machine configuration, deleted before collection, and gone with
  the Machine.
