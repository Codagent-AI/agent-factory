# Task: Fly launcher, guest scripts, and the and-scene plan under `fly`

## Goal

Ship the factory-owned Fly launcher that the `and-scene` suite's `run.sh` invokes through
`SANDBOX_RUNNER`, together with the in-Machine guest init and job scripts and the `flyctl`
transport wrappers, and make the and-scene adapter build a Fly-shaped plan (manifest,
`SANDBOX_RUNNER`, `backend = "fly-machine"`) and prove argument compatibility at readiness with
`run.sh --dry-run`. After this task, running the eval plan under `eval.execution = "fly"` creates a
contained Machine, delivers inputs and credentials, streams output and a heartbeat, collects the
verified artifact tree on `DONE`, and exits with the guest job's exit code — in fresh, resume,
attach, stand-in, and dry-run modes.

## Background

Read `openspec/changes/fly/proposal.md` and `openspec/changes/fly/design.md` (sections "Launch
sequence", "Argument validation", "Guest init and job scripts", "Provenance", "Decisions") first.
`poc/fly/fix-guest.sh` and `poc/fly/README.md` hold the proof-of-concept this evolves from; do not
delete `poc/` in this task.

The controller, queue, store, and supervisor stay on the Mac. The supervisor spawns the plan's argv
(`run.sh …`) and captures its stdout as `<artifact>/factory-suite.log`; `run.sh` composes the
in-sandbox script and executes whatever executable `SANDBOX_RUNNER` names (it checks `-x`, so the
launcher must be an **executable file**, e.g. a packaged wrapper script or the path of a console
script — not `python -m …`). The launcher is a **transitional compatibility adapter pinned to the
harness commit admitted with the claim**, not a general emulation of Agent Runner's
`scripts/sandbox-run.sh`.

### What already exists

- `src/agent_factory/config.py`: `LocalConfig.eval.execution` accepts `"docker"` | `"fly"`, and a
  `[fly]` table provides `app`, `region`, `cpu_kind`, `cpus`, `memory_mb`, `image`, `token_file`,
  `collection_grace_seconds`, `heartbeat_seconds`.
- `src/agent_factory/fly/api.py`: urllib Machines REST client — create (it owns the create body:
  image, `guest{cpu_kind,cpus,memory_mb,persist_rootfs:"always"}`, `auto_destroy:true`,
  `restart:{policy:"no"}`, region, metadata `{factory-owner, run_id, claim_id, nonce,
  deadline_epoch, unit_key}`, env `{FACTORY_DEADLINE_EPOCH, FACTORY_RUN_ID, FACTORY_NONCE}`,
  `init.exec`), get, list by metadata, set one metadata key, update a stopped Machine's config env,
  stop, start, destroy (404 = success), typed errors, registry digest lookup.
- `src/agent_factory/backends/__init__.py`: `ExecutionBackend` protocol, `Probe`, `Disposal`;
  `src/agent_factory/fly/backend.py`: `FlyMachineBackend` with `readiness()` (group `eval-fly`)
  implemented and the other methods unimplemented.
- `operations.py` has `eval` and `eval-fly` diagnostic groups; `runtime._kind_failures` applies
  them to the eval kind under `fly`.
- `tests/fixtures/fly/`: a fake Machines REST server (in-memory Machine table, request log) and a
  minimal fake `flyctl`. Extend these; do not fork them.
- No third-party runtime dependencies are allowed (`pyproject.toml` `dependencies = []`).

### New modules

```
src/agent_factory/fly/guest.py      guest init script (string) + job script assembly
src/agent_factory/fly/transport.py  flyctl ssh/sftp wrappers: put file, run command, stream, tar-get
src/agent_factory/fly/launcher.py   SANDBOX_RUNNER adapter: fresh / resume / attach / stand-in / --dry-run
```

Packaged non-Python files need a `pyproject.toml` include (see the existing `force-include` for the
launchd plist).

### Plan builder (`src/agent_factory/suites/and_scene/__init__.py`, `work_kinds/eval/handler.py`)

`AndSceneAdapter.plan` today returns an `ExecutionPlan` with
`ownership_hints = {"artifact_path", "suite", "sandbox": "docker"}` and an empty
`allowed_environment`. Under `fly` it must instead:

- emit `ownership_hints["backend"] = "fly-machine"` and **no `sandbox` key** (so Docker container
  discovery in `supervisor.py` never runs), and no `image_tag` hint;
- set `allowed_environment` to `SANDBOX_RUNNER=<launcher executable>` and
  `AGENT_FACTORY_FLY_MANIFEST=<artifact>/.factory/manifest.json` (non-secret values only);
- write the non-secret manifest JSON at `<artifact>/.factory/manifest.json`: run and claim ids,
  nonce, unit key, deadline inputs (the attempt's total elapsed-time limit and
  `collection_grace_seconds`), `expect_checkpoint` (a boolean input to the plan builder, default
  false), repository URLs, the three full commits (Runner, Skills, harness) recorded on the claim,
  the image reference, Fly settings, and guest paths (`/agent-runner-source`,
  `/agent-skills-source`, `/eval-input`, `/artifacts`);
- use progress sources `<artifact>/factory-suite.log` and `<artifact>/.factory/heartbeat.json`.

The launcher derives **all meaning from the manifest**, never from `--docker-run-arg` strings. The
Docker plan is unchanged. `_recovery_mode` still decides `--resume` on the Mac exactly as under
Docker, because every job end is collected so the Mac's artifact tree is the guest's.

`AndSceneAdapter.readiness` under `fly` replaces the `sandbox-run.sh` `--docker-run-arg` check with:
execute the worktree's `run.sh --dry-run` with `SANDBOX_RUNNER` set to the launcher; a rejection maps
to an `eval-fly` readiness hold naming the offending argument and the harness commit. No Fly call
happens in dry-run.

### Argument grammar (exit 2 on violation, message names the argument)

`run.sh` at the pinned harness passes: `--dry-run?` `--artifact-dir P --input-dir P --dev-audit
--docker-run-arg --security-opt --docker-run-arg seccomp=unconfined` then zero or more
`--docker-run-arg --mount --docker-run-arg type=bind,source=S,target=T,readonly` (Git metadata dirs
of the Runner and Skills worktrees, and the Skills worktree at `/agent-skills-source`), then
`--env NAME`* `--env-file P`* `--mount-codex-auth`? `--mount-claude-auth`? `-- SCRIPT`. Accept
exactly that grammar. Mount sources must resolve inside the claim's worktrees or their Git common
dirs. Anything else, any other flag, and `--mount-cursor-auth` fail with exit 2. Confirm the grammar
against the real script at `evals/agent-runner/and-scene/run.sh` in the `agent-evals` repository
(a sibling checkout exists at `/Users/paul/codagent/agent-evals`) and vendor that file, with its
commit recorded, as `tests/fixtures/fly/run.sh`.

### Launch sequence (fresh mode)

```
1  validate argv shape; load manifest; if <artifact>/.factory/machine.json already exists
     (a launcher died after create): probe it; alive|stopped with matching metadata → adopt
     it and skip step 2 (no secret was ever delivered); anything else → destroy it, record
     the disposal in machine.json history, and continue with step 2
2  POST create Machine (metadata + deadline + destroy-on-exit + guest init via init.exec)
3  write <artifact>/.factory/machine.json {app, id, nonce, deadline, created_at, image_ref}
4  GET machine; verify metadata == expected; wait state=started; record image_ref (with
     digest), guest (cpu_kind, cpus, memory_mb), and region into machine.json
5  deliver (ssh): /eval-input (suite dir tar), /host-home/{codex,claude}/... (allowlist),
     /run/factory/env (env-file lines + --env values), /var/lib/factory/deadline
6  deliver job script; touch /artifacts/.factory/job/1/start
7  stream: ssh "tail -F job.log + heartbeat" → stdout (log) / heartbeat.json
8  on DONE: tar-over-ssh /artifacts → <artifact dir>/.factory/staging; verify against the
     guest's files manifest; rename into place; write guest-exit-code; exit with it
```

Deadline = launch time + total limit + collection grace, one value used in metadata, env, the
persisted file, and `machine.json`. **No secret is delivered before step 4 verifies ownership.**
Credentials come from an exact per-provider file allowlist for Codex and Claude only (mirror what
Agent Runner's `scripts/sandbox-run.sh` mounts for `--mount-codex-auth` / `--mount-claude-auth`),
are written owner-only in the guest, never enter the Machine config, artifacts, logs, or heartbeat,
and the factory never writes the Mac's credential files. A Cursor credential is never delivered.

Launcher diagnostics (connects, reconnect backoff, delivery steps) go to
`<artifact>/.factory/launcher.log`, **never stdout**: stdout carries only relayed guest bytes so
relay chatter cannot count as progress. `heartbeat.json` is rewritten only when guest-reported
mtimes change, and gains `checkpoint_seen: true` the first time the guest reports
`/artifacts/run-state.json` exists. The heartbeat also carries the suite's in-Machine Claude wait
signal. `flyctl ssh` drops reconnect with backoff; the job keeps running in the Machine. `flyctl`
exit status is not trusted as the remote result — the job's exit code is read from a file.

Exit codes: the guest job's exit code; `2` argument rejected; `70` Fly API or transport failure
before a Machine existed; `71` Machine lost (gone, or checkpoint expected and missing); `72`
collection failed (manifest mismatch or transfer interrupted; nothing partial is left in place);
`73` ownership mismatch (nothing terminated).

**Resume** differs only in steps 2–4: read `machine.json`; require probe alive|stopped with matching
metadata (else 71/73). Stopped Machine: first write the new deadline into the Machine config env
(`FACTORY_DEADLINE_EPOCH`, config update while stopped) and metadata, then start it, so it never
boots against an expired deadline. Alive Machine: write the new deadline to
`/var/lib/factory/deadline` and metadata. Redeliver step 5 (`/run` does not survive a stop;
credentials were deleted before `DONE`). If the manifest says `expect_checkpoint: true`, verify
`/artifacts/run-state.json` in the guest (missing → exit 71); otherwise no checkpoint is required.
Deliver job `n+1` with whatever argv `run.sh` composed. The launcher chooses fresh vs resume from
the manifest/`machine.json`, since `run.sh` invokes it the same way.

**Attach** (`launcher attach --run-dir <artifact>`) skips to step 7 for the current job.

**Stand-in** (`launcher stand-in --run-dir D --script S [--deadline-seconds N] [--mount-codex-auth]
[--mount-claude-auth] [--keep]`) runs steps 1–8 for an operator-supplied script with a manifest the
launcher writes itself (run id `stand-in-<timestamp>`), and destroys the Machine on exit unless
`--keep`. It never touches the claim store and readiness never invokes it. It exists for acceptance
testing and operator diagnosis.

Outside stand-in mode the launcher does **not** destroy or stop the Machine after collection; that
decision is made elsewhere after the run is classified.

### Guest init and job scripts (`fly/guest.py`)

Guest init (bash, passed via `init.exec`; the image needs only bash):

1. `deadline = max(FACTORY_DEADLINE_EPOCH, $(cat /var/lib/factory/deadline))`; a background loop
   re-reads the file every 30 s and exits the init nonzero when `now >= deadline` (init exit →
   `auto_destroy`). `/var/lib/factory` is on the persisted rootfs.
2. Loop: wait for `/artifacts/.factory/job/<n>/start`; run `/artifacts/.factory/job/<n>/job.sh` with
   stdout+stderr to `job.log`; write `exit-code`; write `files.txt` (every path under `/artifacts`
   with size and mtime, excluding `.factory/staging`); touch `DONE`; increment `n`.
3. No job runs until the launcher delivers one; a stop/start simply restarts the loop.
4. Make root paths overridable (for tests) so the script runs under real bash against a temp root.

Job script = factory copy of `sandbox-run.sh`'s bootstrap: clone `/agent-runner-source` and
`/agent-skills-source` at the recorded commits from the manifest's repository URLs,
`git checkout --detach`, copy Runner to `/tmp/agent-runner-local`, `go build` with the dev-audit tags
and ldflags the Docker launcher uses, run `sandbox-sync-home.sh` from
`/agent-runner-source/scripts`; then `export CI=1 HOME=/workspace/home AGENT_RUNNER_SOURCE_COMMIT
AGENT_RUNNER_SOURCE_DIRTY=false AGENT_RUNNER_DEV_AUDIT=1 AGENT_RUNNER_AUDIT_SMOKE=0`; then
`set -a; . /run/factory/env; set +a`; then the script text `run.sh` passed after `--`. A failed
clone/checkout ends the job nonzero before any model execution. `/eval-input` is the suite directory
from the harness worktree, delivered (not cloned) so it is byte-identical to what `run.sh` read. The
job wrapper deletes credentials (`/host-home`, `/workspace/home/.codex/auth.json`, the Claude
equivalents, `/run/factory/env`) **before** `DONE`. Read Agent Runner's `scripts/sandbox-run.sh`
(sibling checkout `/Users/paul/codagent/agent-runner`) for the exact bootstrap, build flags, and
auth file paths.

Collection is tar over ssh (one connection, preserves mtimes), verified against `files.txt`, staged
under `.factory/staging`, then renamed into place once.

## Spec

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Execute eval attempts in a Fly Machine

When the eval kind is configured for `fly` execution, the factory SHALL run each repetition attempt in one Fly Machine owned by the factory. The Machine SHALL obtain the Agent Runner, Agent Skills, and `agent-evals` harness sources by cloning them at the full commits recorded on the claim, at the paths the selected suite expects, and SHALL run the suite's unmodified workflow, controller, judging, and scoring inside the Machine. Each repetition SHALL use its own Machine; a recovery attempt for a repetition SHALL reuse that repetition's Machine as defined below. A failure to obtain any recorded commit SHALL end the attempt as a technical failure before model execution.

#### Scenario: Launch a repetition on Fly

- **WHEN** an eligible eval claim's repetition is admitted under `fly` execution
- **THEN** a Machine is created for that attempt and the suite runs inside it against clones at the claim's recorded Runner, Skills, and harness commits
- **AND** the suite's own source provenance and cleanliness checks pass against those clones

#### Scenario: Fail to obtain a recorded commit

- **WHEN** the Machine cannot obtain one of the recorded commits
- **THEN** the attempt ends as a technical failure without model execution and the diagnostic is preserved with the attempt

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Establish containment before delivering anything

The Machine create request SHALL carry the factory's ownership marker, the run identity, a launch nonce, the attempt's absolute deadline, and destroy-on-exit. The Machine's initial process, set in that same request, SHALL enforce the absolute deadline independently of the factory. The factory SHALL persist the Machine identity, launch nonce, and deadline and verify that the created Machine carries them before delivering any secret. A fresh launch that finds a Machine already recorded for the attempt SHALL adopt it when it is verified and never received a secret, or destroy it before creating another, and SHALL record which. No credential, candidate-delivery token, or other secret SHALL be delivered to a Machine whose ownership has not been persisted and verified.

#### Scenario: Lose the controller between creation and recording

- **WHEN** the factory creates a Machine and stops before recording its identity
- **THEN** the Machine still destroys itself no later than its deadline
- **AND** no secret was delivered to it

#### Scenario: Lose the controller before delivery

- **WHEN** the factory has recorded a Machine but stops before delivering credentials
- **THEN** on restart the factory either continues the launch from the recorded Machine or destroys it before creating another, records which, and the Machine never held a credential in the meantime

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Bound every Machine with one absolute deadline

Each attempt SHALL have one absolute deadline equal to its launch time plus the attempt's configured total elapsed-time limit plus a configured collection grace period. The supervisor, the Machine's in-Machine enforcement, the Machine metadata, and stale-Machine reconciliation SHALL all use that same recorded value. Existing eval limits SHALL be unchanged; Machine startup, cloning, and Runner compilation SHALL count against the attempt. When a recovery attempt starts in a surviving Machine, or when a stopped Machine is started after a quota hold, the factory SHALL record a fresh deadline computed from that attempt's start and update the Machine's enforcement and metadata before execution continues; for a stopped Machine the in-Machine enforcement SHALL carry the new deadline before the Machine is started, so it never boots against an expired one.

#### Scenario: Reach the total limit with the controller offline

- **WHEN** an attempt's total elapsed-time limit passes while the factory is offline
- **THEN** the Machine is destroyed no later than the collection grace period after that limit without factory involvement

#### Scenario: Start a recovery attempt

- **WHEN** a recovery attempt starts in a surviving Machine
- **THEN** the recorded deadline, the Machine metadata, and the in-Machine enforcement all reflect the new attempt's deadline before the suite resumes

#### Scenario: Start a stopped Machine whose old deadline has passed

- **WHEN** a stopped Machine is started after its previous deadline has passed
- **THEN** the in-Machine enforcement already holds the new deadline at boot and the Machine does not destroy itself

This task's portion: deadline computation, create-time metadata/env, guest enforcement, and the
launcher's deadline extension for resume on alive and stopped Machines.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Deliver credentials from an exact allowlist after ownership

Model credentials SHALL be delivered to the Machine only from an exact per-provider file allowlist for Codex and Claude, only after ownership is persisted and verified, over an authenticated transport rather than the Machine configuration, with owner-only permissions in the Machine. Cursor credentials SHALL never be delivered. Credentials and the candidate-delivery token SHALL never appear in the Machine configuration, the artifact tree, factory logs, or the heartbeat, and SHALL be removed from the Machine before the completion marker is written. The factory SHALL never write the factory host's own credential files.

#### Scenario: Deliver credentials

- **WHEN** ownership of a Machine has been persisted and verified
- **THEN** only the allowlisted Codex and Claude files and the candidate-delivery environment are delivered, and the Machine configuration contains none of them

#### Scenario: Collect artifacts

- **WHEN** the artifact tree is collected from a completed Machine
- **THEN** it contains no delivered credential or token

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Supervise through a live stream and heartbeat

While an attempt runs, the factory SHALL stream the Machine's suite output into the attempt's host log through a single writer and SHALL receive a heartbeat reporting changes in the suite's known progress and quota-hold signals at an interval well below the inactivity limit. Output and heartbeat content SHALL feed the existing progress, inactivity, and provider-quota behavior; relay activity itself SHALL NOT count as progress. If observation of a Machine is lost for the inactivity period while the Machine is verifiably alive, the factory SHALL treat the attempt as having reached the inactivity limit and apply same-Machine recovery rather than declaring the Machine lost.

#### Scenario: Observe a quota result from the Machine

- **WHEN** the collected result of a Fly attempt reports a recognized Codex usage limit
- **THEN** the factory records the provider hold exactly as it would for local execution

#### Scenario: Lose observation of a healthy Machine

- **WHEN** the factory cannot observe a Machine for the inactivity period and the Machine is verifiably running
- **THEN** the attempt is stopped by the inactivity limit and follows same-Machine recovery, and the Machine is not settled as lost

This task's portion: the single-writer stream to stdout, the heartbeat file and its
change-only rewrite, and reconnect behavior. Inactivity handling and quota recording are outside
this task.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Collect the whole artifact tree once, then destroy the Machine

When the Machine writes its completion marker, the factory SHALL collect the repetition's entire artifact tree, including the suite's checkpoint, phase results, judging output, session state, and the built candidate output the human-review command serves, verify the collected tree against a file manifest the Machine wrote with its completion marker, and only then place it in the attempt's artifact directory and record the guest exit code. A collection that does not verify SHALL NOT be recorded as a result and SHALL settle the attempt as a lost Machine. When a limit or cancellation stops an attempt, the factory SHALL stop the job inside the Machine and collect before the attempt is finished. After the attempt is classified, the factory SHALL destroy the Machine unless the attempt is retained for same-Machine recovery or stopped for a quota hold as defined below. Collection SHALL be verified complete before the Machine is destroyed or stopped. If collection has not completed within the collection grace period, the Machine's own enforcement SHALL destroy it and the attempt SHALL be settled as a lost Machine. The posted human-review command SHALL work against the collected directory on the factory host without any Fly resource.

#### Scenario: Complete a repetition

- **WHEN** the suite finishes and the Machine writes its completion marker
- **THEN** the full artifact tree is present under the repetition's host artifact directory, the exit code is recorded, and once the repetition is settled the Machine no longer exists

#### Scenario: Miss the collection grace period

- **WHEN** the factory does not complete collection within the grace period after the completion marker
- **THEN** the Machine destroys itself and the attempt is settled as a lost Machine with whatever evidence was streamed

#### Scenario: Collect a partial tree

- **WHEN** the transfer of the artifact tree is interrupted so the collected files do not match the Machine's manifest
- **THEN** no result is recorded from the partial tree and the attempt is settled as a lost Machine

#### Scenario: Cancel a running repetition

- **WHEN** the issue behind a running Fly repetition is cancelled
- **THEN** the job is stopped in the Machine, the evidence so far is collected, and the Machine is destroyed

This task's portion: verified collection on `DONE`, exit code recording, exit 72 on an unverified
tree with nothing partial left in place, and destroy in stand-in mode. Stopping a job for a limit
or cancellation and post-classification disposal are outside this task.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Resume only inside the surviving Machine

Bounded technical recovery for a Fly attempt SHALL run the suite's `--resume` inside the same Machine, as a new recorded attempt with a fresh deadline, subject to the existing recovery budget and the suite's resume checks. When the previous attempt verifiably stopped before the suite created its checkpoint and before candidate execution, recovery SHALL run the suite without `--resume` inside the same surviving Machine, consuming the ordinary retry; such a stop SHALL NOT be treated as a lost Machine. The factory SHALL NOT attempt to resume a repetition in a different Machine. When the recovery budget is exhausted, the Machine SHALL be destroyed after evidence collection and the existing exhaustion policy SHALL apply.

#### Scenario: Recover after a limit stops the suite

- **WHEN** a Fly attempt is stopped by a limit and a recovery retry remains
- **THEN** the recovery attempt resumes the suite inside the same Machine with a new attempt record and fresh deadline

#### Scenario: Recover a failure before the checkpoint existed

- **WHEN** a Fly attempt fails while cloning, building, or logging in, before the suite created its checkpoint, and a recovery retry remains
- **THEN** the recovery attempt runs the suite without `--resume` inside the same Machine and the retry is consumed

#### Scenario: Exhaust recovery on Fly

- **WHEN** the recovery attempt in a Machine also fails
- **THEN** the evidence is collected, the Machine is destroyed, and the claim follows the existing exhausted-recovery policy

This task's portion: the launcher's resume mode and `expect_checkpoint` handling.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Treat in-Machine authentication failure as attempt failure

A model login failure inside the Machine SHALL end the attempt as a technical failure with its diagnostic preserved, subject to the ordinary recovery policy. It SHALL NOT modify credentials on the factory host. When a subsequent readiness check finds a factory-host model login invalid, the existing prerequisite hold SHALL apply with an explanation naming the provider.

#### Scenario: Reject a copied login in the Machine

- **WHEN** a provider rejects the delivered credential inside the Machine
- **THEN** the attempt fails as a technical failure and the factory host's credential files are unchanged

#### Scenario: Find the host login invalid afterwards

- **WHEN** the next readiness check finds the factory host's Codex or Claude login invalid
- **THEN** eval admission is held with the provider named and no attempt is launched

This task's portion: "Reject a copied login in the Machine" — a nonzero job exit with diagnostics
preserved, and no write to the Mac's credential files.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Record Machine provenance

Before model execution, the factory SHALL record the Machine identity, the immutable image digest observed on the launched Machine, and the Machine's CPU kind, CPU count, memory, and region as observed execution provenance for the attempt. A configured mutable image tag SHALL NOT be recorded in place of the observed digest.

#### Scenario: Inspect a completed Fly repetition

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the Machine identity, observed image digest, size, and region used for that attempt are identifiable

This task's portion: `machine.json` records Machine id, observed `image_ref` with digest, guest
size, and region before the job starts.

_From `specs/factory-eval-execution/spec.md`:_

### Requirement: Execute against clean pinned worktrees

The factory SHALL prepare factory-owned clean Git worktrees for the accepted Runner and Skills commits and retain the deployed `agent-evals` version in a pinned worktree. These worktrees SHALL remain associated with the claim for its lifetime, including automatic deferrals and recovery, and SHALL NOT be repointed for another claim. Repetitions SHALL use the same accepted inputs rather than re-resolving moving refs. The worktrees SHALL remain the factory-host record of the accepted inputs in every execution mode; under `fly` execution the sandbox obtains the same commits by cloning, as defined in `factory-fly-execution`.

#### Scenario: Defer a claim while source branches advance

- **WHEN** a claim waits overnight or for quota and its original source branches advance
- **THEN** its next repetition or recovery attempt uses the original pinned worktrees and accepted revisions

#### Scenario: Prepare another claim

- **WHEN** the factory prepares another claim while an earlier claim retains resumable work
- **THEN** the earlier claim's worktrees remain pinned to their accepted revisions
- **AND** the new claim does not repoint them

#### Scenario: Verify pinned source provenance in the sandbox

- **WHEN** the selected suite runs against the claim's pinned Runner and Skills revisions
- **THEN** the sandbox can verify the actual pinned source SHAs and cleanliness: under Docker by reading the mounted linked-worktree Git metadata read-only, and under Fly through clones at the recorded commits
- **AND** no execution mode mutates the shared checkouts or their Git metadata

_From `specs/factory-eval-execution/spec.md`:_

### Requirement: Invoke the suite with accepted execution settings

For `and-scene`, the factory SHALL invoke `evals/agent-runner/and-scene/run.sh` from the retained harness worktree using its agent-execution mode. It SHALL supply the pinned Runner and Skills worktree paths, complete lead/implementor/tester profiles, the repetition's artifact directory, and required environment-file paths through the suite's supported interface. Under Docker execution the integration SHALL support the selected suite's Codex, Claude, and Cursor role adapters; under Fly execution it SHALL support Codex and Claude only. It SHALL pass the accepted `skip_validator` setting to the suite. The factory SHALL use the suite's existing workflow and execution behavior rather than implement a second evaluator. The suite SHALL run in one sandbox per repetition in the configured execution mode, with the controller and supervisor on the host. Under Docker a recovery attempt SHALL reuse the repetition's artifacts through a new container; under Fly it SHALL reuse the repetition's surviving Machine as defined in `factory-fly-execution`. Controller restart SHALL preserve verified surviving execution in every mode. Under Docker, integration SHALL verify the selected Runner sandbox launcher supports required mounts and arguments; under Fly it SHALL verify that the pinned harness invokes the factory's launcher with exactly the argument sequence the launcher supports. Missing compatibility SHALL be exposed as an actionable readiness problem before model execution.

Selected-suite readiness SHALL be verified before execution. Calibration SHALL remain an optional suite-maintainer diagnostic; the factory SHALL NOT require a calibration receipt or pass removed calibration-record arguments. Unavailable prerequisites SHALL follow the readiness-hold behavior in `factory-claim-lifecycle`.

#### Scenario: Launch an accepted repetition

- **WHEN** the claim is eligible and its prerequisites are available
- **THEN** the suite receives the saved role profiles, component worktree paths, artifact directory, environment paths, and validator setting
- **AND** the invocation uses the retained harness version

#### Scenario: Launch with Cursor role profiles under Docker

- **WHEN** the eval kind runs under Docker execution, an accepted request selects Cursor for one or more role profiles, and the host Cursor CLI is available
- **THEN** the factory passes those profiles unchanged to the selected suite
- **AND** the suite remains responsible for validating the mounted Cursor authentication at launch

#### Scenario: Fail selected-suite readiness

- **WHEN** the selected suite's required entry-point or fixture files are unavailable in the pinned environment
- **THEN** no evaluation starts and the factory reports the readiness problem without consuming an execution attempt or recovery retry

#### Scenario: Hold Fly readiness on an unexpected launcher argument

- **WHEN** the eval kind runs under Fly execution and the pinned harness would invoke the launcher with an argument the launcher does not support
- **THEN** no Machine is created, eval admission is held, and the readiness problem names the unsupported argument and the harness commit


## Test Plan

Automated tests never contact Fly. Extend `tests/fixtures/fly/` with a fake `flyctl` shim on `PATH`
whose `ssh console`, `ssh sftp`, and `machine` subcommands operate on a temp "guest" directory and
can run the real guest init script under bash. Unit cases are your TDD decisions.

### INT-001: Launcher argument grammar and dry-run readiness against the pinned suite
- Boundary: the real `run.sh` at the pinned harness commit, vendored into
  `tests/fixtures/fly/run.sh` with that commit recorded in the fixture, run with `--dry-run` and
  `SANDBOX_RUNNER` pointing at the factory launcher in `--dry-run` mode.
- Setup: the fixture repository builder installs the vendored `run.sh` into the evals fixture
  repo in place of the stub; launcher invoked through the and-scene adapter's readiness path.
- Action: run readiness once unmodified; once with an extra `--docker-run-arg` injected via a
  patched `run.sh`; once with `--mount-cursor-auth` injected.
- Assertions: unmodified run passes and records the parsed manifest; each injected run yields an
  `eval-fly` diagnostic naming the offending argument and no Machine create call is recorded.
- Execution: `tests/integration/test_fly_launcher_grammar.py`.

### INT-003: Guest init script under bash
- Boundary: the generated init script executed by real bash with `/artifacts` and `/run/factory`
  redirected to a temp root.
- Setup: environment deadline a few seconds ahead; job directory `.factory/job/1` with a `job.sh`.
- Action: start the script; drop a `start` marker; observe; write the persisted deadline file
  further ahead; queue job 2; let the deadline expire. Separately, start the script with an
  already-expired environment deadline and a persisted file deadline in the future.
- Assertions: job 1 runs, `job.log`, `exit-code`, `files.txt`, and `DONE` appear in order and the
  manifest lists every artifact file with size; job 2 runs after job 1 without restart; the
  larger of the file and environment deadlines wins, so the expired-env start does not exit; at
  expiry the script exits nonzero so the Machine's process ends; no credentials are referenced.
- Execution: `tests/integration/test_fly_guest_init.py`.

### INT-004: Launcher fresh, resume, and attach modes
- Boundary: `src/agent_factory/fly/launcher.py` with the fake API and the fake `flyctl` shim.
- Setup: manifest file from an accepted argument sequence; fake Codex and Claude credential files;
  a Cursor credential file present on the Mac.
- Action: run fresh mode to completion; run fresh mode again with a `machine.json` left by a
  dead launcher (once alive and matching, once gone); run resume against an alive Machine with
  `expect_checkpoint` true and the checkpoint present, then absent, then with `expect_checkpoint`
  false; run resume against a stopped Machine whose env deadline has passed; kill a running
  launcher and run attach mode; run a collection whose fake tar stream is truncated; run stand-in
  mode with a trivial script.
- Assertions: `.factory/machine.json` is written and metadata verified before any ssh delivery;
  a recorded alive Machine is adopted and a gone one is replaced with the disposal recorded;
  delivered files are exactly the allowlisted Codex and Claude paths with owner-only modes and the
  Cursor file is never delivered; credentials are removed in the guest before `DONE`; stdout
  carries only relayed guest bytes and launcher diagnostics go to `.factory/launcher.log`;
  `.factory/heartbeat.json` mtime changes only when content changes and gains `checkpoint_seen`
  when `run-state.json` appears; resume on an alive Machine extends the deadline via metadata and
  the persisted file, an expected-and-missing checkpoint exits 71, an unexpected checkpoint is
  not required; resume on a stopped Machine updates the config env deadline before the start
  call; attach reconnects and continues streaming; a verified collection lands once by rename and
  a truncated one exits 72 leaving no partial tree in place; stand-in runs the same path and
  destroys its Machine; provenance records image digest, Machine id, size, region.
- Execution: `tests/integration/test_fly_launcher.py`.

## Done When

- Every scenario in the Spec section that falls in this task's portion is covered by a passing test,
  and INT-001, INT-003, and INT-004 pass at the named paths.
- The Docker plan and Docker readiness of the and-scene adapter are unchanged
  (`tests/integration/test_and_scene_adapter.py` still passes unmodified for Docker).
- The launcher is an executable file that `run.sh`'s `-x` check accepts when installed from the
  built package.
- Test time stays reasonable: deadline tests use seconds-scale deadlines and an overridable
  watchdog interval rather than real 30 s sleeps.
- `uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest` passes.
