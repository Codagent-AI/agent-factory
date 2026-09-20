## Context

Evals run through the `and-scene` suite in `agent-evals`. The factory's eval handler builds an
`ExecutionPlan` (argv, environment, credential paths, progress files, `ownership_hints`) that runs
the suite's `run.sh` on the Mac; `run.sh` composes the in-sandbox script and executes whatever
launcher `SANDBOX_RUNNER` names, by default Agent Runner's `scripts/sandbox-run.sh`, which builds
the image, bind-mounts the Runner worktree at `/agent-runner-source`, the suite directory at
`/eval-input`, the artifact directory at `/artifacts`, and host auth files under `/host-home`, and
runs a bootstrap that copies and compiles Runner into `/workspace/bin` before the suite script.

There is no named "where does it run" abstraction today. `fix.execution` (`docker` | `host`) and
`eval.execution` (`docker` only) are consumed in four unrelated places: two fix plan builders,
`ownership_hints["sandbox"]` conditionals in `supervisor.py`, doctor groups
(`shared`, `eval-sandbox`, `fix-sandbox`, `fix-host`), and `_kind_failures` in `runtime.py`. The
supervisor treats a missing recorded process as unverifiable and reports uncertainty, which is
correct for Docker and host but wrong for a Machine that outlives the Mac process.

The package has no third-party dependencies (`pyproject.toml` `dependencies = []`); the Fly
Machines API is called with `urllib`. The Runner sandbox image is `docker/dev/Dockerfile` in
`agent-runner` (Playwright noble base, `pwuser`, bash and zsh present).

Fly semantics this design relies on (Machines API docs, verified 2026-09-20):

- `auto_destroy: true` destroys the Machine when its main process exits **and also on an API stop**
  (observed in acceptance: event `exit ... requested_stop=true` followed by `destroy`). The earlier
  reading of the documentation, that a manual stop leaves it stopped, was wrong.
- `restart: {policy: "no"}` prevents Fly restarting the process.
- Metadata is set per key with `POST /v1/apps/{app}/machines/{id}/metadata/{key}` without a config
  update or restart.
- `guest.persist_rootfs: "always"` keeps the root filesystem across stop/start and updates; Fly may
  still wipe it for maintenance.
- `init.exec` overrides the image's command line; `guest` sets `cpu_kind`, `cpus`, `memory_mb`.
- Machines are x86-64 only; the image must be built for `linux/amd64`.

## Goals / Non-Goals

**Goals:**

- Run eval repetitions in Fly Machines with the suite unmodified and the controller on the Mac.
- Introduce the execution-backend concept and its interface, implemented for Fly only.
- Make the Machine the durable execution identity; survive Mac restarts.
- Bound every Machine's lifetime and cost from the create request onward.
- Keep supervision (progress, inactivity, quota) working from streamed output and a heartbeat.
- Same-Machine resume; stop-and-start across Codex quota holds; settle lost Machines.

**Non-Goals:**

- Migrating Docker or host execution behind the backend interface.
- Cross-Machine resume, artifact mirroring, volumes, object storage.
- Fixes or reviews on Fly; Cursor on Fly; parallel repetitions; `agent-evals` changes.

## Approach

### Execution backend concept

An **execution backend** is where an attempt runs, chosen per work kind by configuration. Values:
`local-process` (today's fix `host` mode), `docker` (today's sandbox), `fly-machine` (this
change). This change names the concept in `ownership_hints["backend"]`, defines the interface, and
implements it for `fly-machine`. Docker and local-process keep their existing inline code.
The and-scene adapter currently emits `ownership_hints["sandbox"] = "docker"`, which routes the
supervisor to container ownership; under `fly` it emits `backend = "fly-machine"` and no `sandbox`
key, so the supervisor's Docker container discovery never runs for a Machine-backed attempt.

```
src/agent_factory/
  backends/__init__.py      ExecutionBackend protocol, ExecutionIdentity, Probe, Disposal
  fly/api.py                Machines REST client (urllib), registry manifest lookup
  fly/guest.py              guest init script (string) + job script assembly
  fly/launcher.py           SANDBOX_RUNNER adapter: fresh / resume / attach / stand-in / --dry-run
  fly/backend.py            FlyMachineBackend: readiness, probe, terminate, dispose, reconcile
  fly/transport.py          flyctl ssh/sftp wrappers: put file, run command, stream, tar-get
```

```python
class ExecutionBackend(Protocol):
    name: str  # "fly-machine"

    def readiness(self, local, shared) -> list[Diagnostic]: ...  # group "eval-fly"
    def identity_from_plan(self, plan, run) -> Mapping | None: ...  # reads .factory/machine.json
    def probe(self, identity) -> Probe: ...  # alive | stopped | gone | mismatch | unknown
    def terminate(self, identity) -> bool: ...  # stop the owned job, keep the Machine
    def dispose(self, identity, decision) -> None: ...  # "destroy" | "stop" | "keep"
    def attach_argv(self, plan, run) -> tuple[str, ...]: ...  # re-spawn transport after restart
    def reconcile(self, store) -> list[str]: ...  # stale-Machine pass, returns report lines
    def provenance(self, identity) -> Mapping: ...  # image digest, size, region
```

Shared, backend-neutral logic stays where it is: the supervisor loop (timers, progress, quota,
cancellation, result finalization), the and-scene adapter (argv composition, result reading, quota
recognition, resume-mode decision), claim lifecycle, readiness holds, reporting.

### Configuration

`LocalConfig.eval.execution` accepts `"docker"` (default) or `"fly"`. A `[fly]` table is required
when `fly` is selected:

```toml
[eval]
execution = "fly"

[fly]
app = "agent-factory-sandbox"
region = "ewr"                       # default
cpu_kind = "shared"                  # default
cpus = 4                             # default
memory_mb = 8192                     # default
image = "registry.fly.io/agent-factory-sandbox:base"
token_file = "/Users/paul/.agent-factory/credentials/fly-deploy-token"
collection_grace_seconds = 900       # default
heartbeat_seconds = 20               # default
```

Shared `config/codagent.toml` eval defaults become `lead = "claude:opus:medium"`,
`implementor = "codex:gpt-5.6-luna:medium"`, `tester = "codex:gpt-5.6-luna:medium"` (lead and
tester already hold these values; the implementor moves from `claude:sonnet:medium`).

Intake (`work_kinds/eval/__init__.py` `parse_request`) receives the execution mode and rejects a
`cursor` CLI in any effective role with the needs-input feedback. `EvalHandler.prepare` raises
`ReadinessError` naming the role for an already-frozen Cursor claim under `fly`.

### Launch sequence

```
runtime._launch ─► supervisor watcher ─► run.sh (SANDBOX_RUNNER=<launcher>, AGENT_FACTORY_FLY_MANIFEST=<path>)
                                            └► launcher (foreground)
   1  validate argv shape; load manifest; if <artifact>/.factory/machine.json already exists
        (a launcher died after create): probe it; alive|stopped with matching metadata → adopt
        it and skip step 2 (no secret was ever delivered); anything else → destroy it, record
        the disposal in machine.json history, and continue with step 2
   2  POST create Machine {image, guest{cpu_kind,cpus,memory_mb,persist_rootfs:"always"},
        auto_destroy:false, restart:{policy:"no"}, region,
        metadata{factory-owner, run_id, claim_id, nonce, deadline_epoch, unit_key},
        env{FACTORY_DEADLINE_EPOCH, FACTORY_RUN_ID, FACTORY_NONCE},
        init{exec:["bash","-c",<guest init script>]}}
   3  write <artifact>/.factory/machine.json {app, id, nonce, deadline, created_at, image_ref}
   4  GET machine; verify metadata == expected; wait state=started
   5  deliver (ssh): /eval-input (suite dir tar), /host-home/{codex,claude}/... (allowlist),
        /run/factory/env (env-file lines + --env values), /var/lib/factory/deadline
   6  deliver job script; touch /artifacts/.factory/job/1/start
   7  stream: ssh "tail -F job.log + heartbeat" → stdout (log) / heartbeat.json
   8  on DONE: tar-over-ssh /artifacts → <artifact dir>/.factory/staging; verify against the
        guest's files manifest; rename into place; write guest-exit-code; exit with it
```

The launcher's own diagnostics (connects, reconnect backoff, delivery steps) go to
`<artifact>/.factory/launcher.log`, never to stdout, so the progress log holds only relayed guest
bytes and relay chatter cannot count as progress.

Launcher exit codes, mapped by the handler: the guest job's exit code (0 or the suite's own
codes); `2` argument rejected; `70` Fly API or transport failure before a Machine existed
(technical, ordinary recovery, nothing to dispose); `71` Machine lost (gone, or checkpoint
expected and missing); `72` collection failed (manifest mismatch or transfer interrupted; the
handler maps it to the lost-Machine outcome because the Machine's evidence cannot be trusted);
`73` ownership mismatch (uncertainty, nothing terminated).

Fresh launch and resume differ only in step 2-4: resume reads `machine.json` and requires
`probe == alive|stopped` with matching metadata. For a stopped Machine it first writes the new
deadline into the Machine config env (`FACTORY_DEADLINE_EPOCH`, `POST .../machines/{id}` config
update, allowed while stopped; `persist_rootfs: "always"` keeps the rootfs across the update) and
into metadata, then starts it, so the guest never boots against an expired deadline. For an alive
Machine it writes the new deadline to `/var/lib/factory/deadline` and metadata. It then
redelivers step 5 (`/run` does not survive a stop; credentials were deleted before `DONE`
anyway). If the manifest says `expect_checkpoint: true` it verifies `/artifacts/run-state.json`
in the guest (missing → exit 71); otherwise the checkpoint is not expected and the job runs the
suite fresh. It delivers job `n+1` with whatever argv `run.sh` composed: the Mac-side
`_recovery_mode` decides `--resume` exactly as under Docker, because every job end is collected,
so the Mac's copy of the artifact tree is the guest's.

Attach (`launcher attach --run-dir <artifact>`) skips to step 7 for the current job and is what the
supervisor spawns after a restart when `probe == alive` and no launcher process is alive.

Stand-in (`launcher stand-in --run-dir D --script S [--deadline-seconds N] [--mount-codex-auth]
[--mount-claude-auth]`) runs steps 1-8 for an operator-supplied script instead of the suite
script, with a manifest the launcher writes itself (run id `stand-in-<timestamp>`, deadline from
`--deadline-seconds` or the configured limits), and disposes by destroying the Machine on exit
unless `--keep` is given. It exists for acceptance testing and operator diagnosis; it never
touches the claim store and readiness never invokes it.

The manifest is non-secret JSON written by the plan builder under `<artifact>/.factory/manifest.json`:
run and claim ids, nonce, unit key, deadline inputs (total limit, grace), `expect_checkpoint`
(true when the previous attempt's progress recorded `checkpoint_seen`), repository URLs, the
three full commits, the image reference, Fly settings, and the guest paths.

### Argument validation

`run.sh` at the pinned harness passes: `--dry-run?` `--artifact-dir P --input-dir P --dev-audit
--docker-run-arg --security-opt --docker-run-arg seccomp=unconfined` then zero or more
`--docker-run-arg --mount --docker-run-arg type=bind,source=S,target=T,readonly` (Git metadata dirs
of the Runner and Skills worktrees, and the Skills worktree at `/agent-skills-source`), then
`--env NAME`* `--env-file P`* `--mount-codex-auth`? `--mount-claude-auth`? `-- SCRIPT`. The launcher
accepts exactly that grammar. Mount sources must resolve inside the claim's worktrees or their Git
common dirs; anything else, any other flag, and `--mount-cursor-auth` fail with exit 2 and a
message naming the argument. `--dry-run` runs the validation and prints the plan without any Fly
call; `AndSceneAdapter.readiness` under `fly` executes `run.sh --dry-run` with the launcher and
maps a failure to the `eval-fly` readiness hold.

### Guest init and job scripts

Guest init (bash, embedded in `fly/guest.py`, passed via `init.exec`):

1. `deadline = max(FACTORY_DEADLINE_EPOCH, $(cat /var/lib/factory/deadline))`; a background
   loop re-reads the file every 30 s and `kill -TERM 1`-equivalent exits the init when
   `now >= deadline` (init exit → the Machine stops; the reconciler destroys it). `/var/lib/factory` is on the persisted rootfs,
   so a file deadline survives stop/start; the env deadline is refreshed by config update before
   any start of a stopped Machine (above), so the larger of the two is always current.
2. Loop: wait for `/artifacts/.factory/job/<n>/start`; run `/artifacts/.factory/job/<n>/job.sh`
   with stdout+stderr to `job.log`; write `exit-code`; write `files.txt` (every path under
   `/artifacts` with size and mtime, excluding `.factory/staging`); touch `DONE`; increment `n`.
3. The loop is what a stop/start restarts; no job runs until the launcher delivers one.
4. The heartbeat reports, among the suite's progress files, whether `/artifacts/run-state.json`
   exists; the launcher records `checkpoint_seen: true` in `heartbeat.json` the first time it
   does, and the supervisor copies it into `progress`.

Job script = factory copy of `sandbox-run.sh`'s bootstrap (clone
`/agent-runner-source` and `/agent-skills-source` at the recorded commits, `git checkout --detach`,
copy Runner to `/tmp/agent-runner-local`, `go build` with the dev-audit tags and ldflags the Docker
launcher uses, `sandbox-sync-home.sh` from `/agent-runner-source/scripts`) + `export` of
`CI=1 HOME=/workspace/home AGENT_RUNNER_SOURCE_COMMIT AGENT_RUNNER_SOURCE_DIRTY=false
AGENT_RUNNER_DEV_AUDIT=1 AGENT_RUNNER_AUDIT_SMOKE=0` + `set -a; . /run/factory/env; set +a` +
the script text `run.sh` passed after `--`. `/eval-input` is the suite directory from the harness
worktree at the recorded commit (delivered, not cloned, so it is byte-identical to what `run.sh`
read on the Mac). Credentials are deleted by the job wrapper (`rm -rf /host-home /workspace/home/.codex/auth.json …`)
before `DONE`.

### Supervision

For a `fly-machine` plan the supervisor:

- ignores local process identity for ownership; `identity = backend.identity_from_plan()` read
  from `machine.json` (durable on the Mac before secrets are delivered) and copied into
  `progress["machine"]`;
- on restart (`supervise()`), if `probe == alive` spawns `attach_argv` and observes; if `stopped`
  and no quota hold is recorded, treats it as interrupted; if `gone`, finishes the run as
  `interrupted` with `reason = "machine lost"`; if `mismatch`, records
  `runtime`/`fly:mismatch` (Machine id, expected and observed metadata, the remedy: destroy the
  Machine by hand or wait for its deadline) and reports uncertainty; once a later probe finds the
  mismatched Machine gone the uncertain run finishes as `machine lost`;
- progress sources are `<artifact>/factory-suite.log` and `<artifact>/.factory/heartbeat.json`;
  the launcher rewrites `heartbeat.json` only when the guest-reported mtimes change; Codex quota
  holds are recognized from the collected `result.json` exactly as under Docker (never from the
  log, see the comment in `and_scene/__init__.py`); the heartbeat's quota signal is the suite's
  in-Machine Claude wait, which keeps the Machine running and billed for up to six hours, within
  the per-attempt cost bound;
- `terminate` (inactivity, timeout, cancel) is a sequence, not a kill of the launcher: ssh-kill
  the job's process group (the guest writes `DONE` with exit 143 and `files.txt`), wait for the
  launcher's own exit bounded by `collection_grace_seconds` (it collects on `DONE`), then
  `finish_run`; only if the launcher does not exit in time is it killed and the run's result
  marked `collection: "failed"`, which classifies as lost Machine;
- after a verified cancel terminate the supervisor calls `backend.dispose(identity, "destroy")`
  itself, because `_consume_results` skips cancelled claims; timeout disposal follows
  classification as for any other run;
- `checkpoint_seen` from the heartbeat is the pre-checkpoint proof: `plan_attempt` treats a
  previous attempt whose progress lacks `checkpoint_seen` (and whose collected tree holds no
  candidate marker) as stopped before checkpoint creation, so recovery runs the suite without
  `--resume` in the same Machine and consumes the ordinary retry, mirroring the Docker scenario;
  `checkpoint_seen` and now missing is the lost-Machine outcome;
- if the watcher dies between `Popen` and copying `machine.json` into `progress`, the replacement
  watcher reads `machine.json` from the artifact directory (fresh mode adopts it, above); a
  Machine nobody ever recorded is caught by the reconciler at its deadline and shown in status
  as unknown.

### Disposal after classification

`Controller`/`runtime._consume_results` calls `backend.dispose(identity, decision)` after the
handler classifies the run:

| Classification | Decision |
|---|---|
| settled (completed, product failure, non-resumable), exhausted recovery | `destroy` |
| cancelled | `destroy` (from the supervisor after terminate, see above) |
| quota | `stop` (deadline := next eligible start + total limit + grace, written to metadata + file first; the next eligible start is the later of the hold's expiry and the next admission-window opening) |
| technical, retry remaining | `keep` (deadline unchanged until the recovery attempt extends it) |
| lost Machine | nothing to dispose; record |

While a hold stands, each cycle recomputes the stopped Machine's deadline from the current hold
(Codex holds move as the suite reports new reset times) and updates metadata when it changes.

`dispose` persists `{machine_id, decision, deadline_epoch, state}` under
`runtime`/`fly:machine:<claim_id>` and clears it on destroy; `status` renders it in
`_hold_lines` for waiting claims (`stopped (quota hold), deadline …`) and in `_progress_lines`
for active runs. `status` may issue a read-only `GET` per recorded Machine to show the live state
and tolerates API failure by printing the recorded state with a note.

`_technical_failure` gains `reason == "machine lost"` → not technical: the handler's `classify`
returns `settled` with `result["failure"] = {"owner": "factory", "code": "machine-lost"}`.
`_run_needs_recovery`, `_nonresumable_workflow`, and `settle()` recognize a factory-owned
`machine-lost` failure as settled without retry. `settle()` computes the aggregate verdict from
the surviving repetitions only (`failed` or `pending-human-review`), lists the lost repetitions
with their reasons in the event body, and yields `infra-error` when every repetition is lost; the
`factory-eval-reporting` delta in this change states that rule.

### Reconciliation

`FlyMachineBackend.reconcile(store)` runs once per `cycle()` before dispatch: `GET
/v1/apps/{app}/machines?metadata.factory-owner=<marker>`; for each, destroy if
`now > metadata.deadline_epoch` (idempotent: 404 is success; verify by GET); Machines unknown to the
store but within deadline are recorded under `store.set_setting("runtime", "fly:unknown", …)` for
status; failures under `"fly:cleanup-failed"`. Machines recorded under `fly:mismatch` are never
touched by the reconciler except by the deadline rule; when one is gone the setting is cleared
and the uncertain run is finished as lost. Machines recorded under `fly:machine:<claim_id>` with
decision `stop` are destroyed only by the deadline rule, which the disposal table sets to cover
the earliest possible restart.

### Doctor and status

The checks now labelled `eval-sandbox` that are mode-neutral (harness branch resolution, host
Codex/Claude login, suite environment file, free disk space against the shared floor) move to a
new `eval` group that holds the eval kind under every mode; `eval-sandbox` keeps only the Docker
launcher, Docker availability, and memory checks. `_kind_failures` for eval filters on
`{"shared", "eval", <mode group>}` and, under `fly`, appends `backend.readiness()` and skips the
memory probe.

`eval-fly` group (only when `eval.execution == "fly"`): token file private and single-line; `GET
/v1/apps/{app}` with the token; image manifest resolves (`HEAD registry.fly.io/v2/<repo>/manifests/<tag>`
with the token, digest captured); `flyctl` executable on the service PATH; `flyctl ssh` can reach
the app (issue `fly ssh issue`-style check without a Machine). Docker checks are constructed only
when a kind is configured for Docker; `doctor()` gets the kinds' modes and skips `_command_check("Docker")`
and the reclaimable line otherwise. Status: `_progress_lines` prints Machine id, state, deadline
from `progress["machine"]`; `_hold_lines` prints the stopped Machine for a quota-held claim from
`fly:machine:<claim_id>`; `fly:unknown`, `fly:mismatch`, and `fly:cleanup-failed` settings print
under blocking conditions.

### Provenance

At step 4 the launcher records `image_ref` (with digest) from the Machine response, `guest`, and
`region` into `machine.json`; the supervisor copies them into `progress["machine"]`; the handler's
`read_result` merges them into `result["execution_provenance"]["fly"]`. Under `fly` no
`image_tag` hint is set.

## Decisions

- **Adapter through `SANDBOX_RUNNER`, validated by grammar, fed by manifest.** The factory owns
  both ends of the seam, so no `agent-evals` change is needed; meaning comes from the manifest, and
  Docker arguments are only checked for shape. Readiness proves the shape with `run.sh --dry-run`.
- **Guest init as `init.exec`, no image change.** Containment starts at creation with no delivered
  file; the image needs only bash. The job-loop shape makes stop/start and resume uniform.
- **Deadline via env at create, persisted file override while running, env update before any
  start.** The guest takes the larger of the two, so extension while running is a file write and
  extension across a stop is a config update made while stopped; `persist_rootfs: "always"`
  keeps the rootfs across that update per Fly's documentation.
- **Collect after every job end, verified against the guest's file manifest, dispose by
  classification.** The Mac always holds the latest checkpoint and `result.json`; a partial
  collection is never accepted as a result; the classification, not the launcher, decides
  destroy/stop/keep.
- **Transport via `flyctl` (ssh console, sftp), lifecycle via REST.** REST responses are typed and
  carry `image_ref`; `flyctl` exit codes are not trusted for remote command results (the job's
  exit code is read from a file).
- **Tar over ssh for collection**, not recursive sftp: one connection, streams, preserves mtimes.
- **`ExecutionBackend` implemented for Fly only.** Approved scope; Docker/host migration is a
  follow-up.

## Risks / Trade-offs

- **Argument-shape coupling to the harness.** A harness change to `run.sh`'s launcher arguments
  holds Fly evals until the adapter is updated. Mitigation: the dry-run readiness check names the
  argument and commit; the accepted follow-up is a provider-neutral request in `agent-evals`.
- **Stopped-Machine disk wiped by Fly.** Mitigation: checkpoint verification before resume; loss
  settles the repetition, remaining repetitions continue.
- **`auto_destroy` fires on an API stop (confirmed on real Fly).** The recorded fallback is now
  the design: Machines are created with `auto_destroy: false`, the guest watchdog still ends the
  process at the deadline, which stops the Machine and its compute billing, and the reconciler
  performs every destroy. A Machine that stopped itself keeps only its root filesystem, billed as
  storage, until the next controller cycle destroys it. Because the disk now outlives a deadline
  stop, the guest clears a stale expiry marker at boot when its current deadline is still ahead.
  A config update leaves a Machine `replacing` briefly, so the launcher waits for `stopped` before
  it starts one.
- **Token rotation over long runs.** Mitigation: production gate (one refresh interval, Mac logins
  rechecked) before `fly` is the live setting; in-Machine auth failure is a plain technical failure.
- **Unproven eval workload in a Machine** (Chrome, validator, Codex user namespaces under
  Firecracker, 8 GiB). Mitigation: the first implementation task is a manual full eval on Fly with
  the proof-of-concept scripts before backend code; size is configurable.
- **`flyctl ssh` session drops.** Mitigation: the launcher reconnects with backoff; a gap counts as
  no progress only until the inactivity limit; the job continues in the Machine regardless.
- **Cost bound.** Worst case per attempt ≈ (total_seconds + grace) × $0.0617/h ≈ $0.75 at
  defaults; an unnoticed orphan is bounded by the same deadline.

Alternatives considered: periodic mirroring (rejected: inconsistent live copies, false progress);
per-run volume (rejected: second billable resource, region-pinned); running the transport inside
the supervisor watcher instead of a launcher (rejected: `run.sh` must compose the script, and a
foreground launcher keeps the Docker-shaped contract).

## Migration Plan

1. Land the `agent-runner` companion (Dockerfile Chrome path for `chrome-linux64`, `.dockerignore`).
2. Operator: Fly org/app/token; build and push the amd64 base image; place the token file.
3. Deploy the factory with `eval.execution` unset (Docker behavior unchanged); run `doctor`.
4. Set `eval.execution = "fly"` and `[fly]`; `doctor` must pass `eval-fly`; run one eval by hand
   through the factory (`tick`) and complete a human review from the collected directory.
5. Production gate: one attempt spanning a token refresh interval, Mac logins rechecked.
6. The proof-of-concept scripts were never committed; the launcher's `stand-in` mode replaces them
   for diagnosis and acceptance.

Rollback: set `eval.execution` back to `docker` (or unset). Frozen claims are unaffected; a claim
mid-Machine at rollback time is settled or destroyed by the reconciler at its deadline.

## Open Questions

None. Verification items (auto_destroy on stop, persist_rootfs across stop/start and config
update, `/var/lib` persisted) are confirmed from Fly's documentation and verified by AT-002
before the first full eval.
