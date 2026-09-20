## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration, end-to-end, agent-acceptance, and exceptional human-only obligations.

Automated tests never contact Fly. They use a local fake of the Machines REST API, a fake `flyctl`
shim on `PATH` that serves `ssh console`, `ssh sftp`, and `machine` subcommands against local
directories, and the guest init script run under real bash. Agent acceptance uses the real Fly
account with cheap stand-in jobs only; the full eval workload on Fly is human-only (HT-001).

Conventions: `tests/integration/test_fly_*.py`, `tests/e2e/test_fly_eval_cycle.py`, fixtures under
`tests/fixtures/fly/`. All automated tests run in `uv run pytest` and in CI with
`uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest`.

## Integration Tests

### INT-001: Launcher argument grammar and dry-run readiness against the pinned suite
- Covers: factory-eval-execution "Invoke the suite" (Fly readiness verifies the exact launcher
  argument sequence; hold on an unexpected argument); factory-fly-execution "Deliver credentials
  from an exact allowlist" (Cursor mount rejected).
- Boundary: the real `run.sh` at the pinned harness commit, vendored into
  `tests/fixtures/fly/run.sh` with that commit recorded in the fixture, run with `--dry-run` and
  `SANDBOX_RUNNER` pointing at the factory launcher in `--dry-run` mode.
- Setup: the fixture repository builder installs the vendored `run.sh` into the evals fixture
  repo in place of the stub; launcher invoked through the and-scene adapter's readiness path. The
  live `run.sh --dry-run` against the real checkout is covered by AT-003.
- Action: run readiness once unmodified; once with an extra `--docker-run-arg` injected via a
  patched `run.sh`; once with `--mount-cursor-auth` injected.
- Assertions: unmodified run passes and records the parsed manifest; each injected run yields an
  `eval-fly` diagnostic naming the offending argument and no Machine create call is recorded.
- Execution: `tests/integration/test_fly_launcher_grammar.py`.

### INT-002: Machines API client against a fake REST server
- Covers: factory-fly-execution "Establish containment before delivering anything", "Bound every
  Machine with one absolute deadline", "Collect the whole artifact tree once, then destroy",
  "Stop the Machine during a Codex quota hold", "Reconcile stale Machines every cycle".
- Boundary: `backends/fly/api.py` over urllib to a local `http.server` fake recording requests.
- Setup: fake server with an in-memory Machine table; token file with owner-only permissions.
- Action: create, get, update metadata for one key, update the config env of a stopped Machine,
  list with metadata filter, stop, start, destroy, destroy again.
- Assertions: create body carries image, guest size, `persist_rootfs = "always"`,
  `auto_destroy = true`, restart policy `no`, `init.exec`, and metadata with owner marker, run id,
  claim id, nonce, deadline epoch, unit key; the bearer token is sent and never logged; metadata
  update changes only the named key; list filtering returns only marked Machines; a 404 on destroy
  is treated as success; a 5xx surfaces as a typed error with the request path.
- Execution: `tests/integration/test_fly_api.py`.

### INT-003: Guest init script under bash
- Covers: factory-fly-execution "Bound every Machine with one absolute deadline", "Resume only
  inside the surviving Machine" (job loop), design job loop protocol.
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
- Covers: factory-fly-execution "Establish containment before delivering anything", "Deliver
  credentials from an exact allowlist after ownership", "Supervise through a live stream and
  heartbeat", "Collect the whole artifact tree once", "Resume only inside the surviving Machine",
  "Record Machine provenance".
- Boundary: `backends/fly/launcher.py` with the fake API from INT-002 and a fake `flyctl` shim
  whose ssh/sftp subcommands operate on a temp "guest" directory.
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

### INT-005: Supervisor ownership through the Fly backend
- Covers: factory-claim-lifecycle "restart adopts surviving Machine", "ownership verification via
  Machine identity", factory-fly-execution "Make the Machine the durable execution identity",
  "Settle a lost Machine as a failed repetition", inactivity limit interplay.
- Boundary: `supervisor.py` with `ownership_hints["backend"] = "fly-machine"`, the fake API, and
  the fake `flyctl` shim.
- Setup: a run marked running with a recorded launcher process that no longer exists.
- Action: restart the watcher with the Machine alive; with the Machine gone; with metadata nonce
  mismatched, then with that Machine gone on a later probe; with the Machine alive but the stream
  silent past the inactivity limit; cancel a running attempt; let a prior attempt fail before
  `checkpoint_seen` and plan the recovery attempt.
- Assertions: alive → attach launcher spawned, no retry consumed, no uncertainty recorded; gone →
  run finishes with failure `{"owner": "factory", "code": "machine-lost"}` and the handler
  settles it without recovery; mismatch → `fly:mismatch` recorded with id and remedy, no attach,
  and the run finishes as lost once the Machine is gone; silent → job killed over ssh, the
  launcher allowed to collect before the run finishes, Machine kept, classified technical;
  cancel → job killed, collected, and the Machine destroyed by the supervisor; pre-checkpoint
  failure → recovery plan without `--resume` in the same Machine consuming the retry.
- Execution: `tests/integration/test_fly_supervision.py`.

### INT-006: Disposal and reconciliation in the cycle
- Covers: factory-fly-execution "Collect ... then destroy the Machine", "Stop the Machine during a
  Codex quota hold", "Reconcile stale Machines every cycle"; factory-claim-lifecycle quota hold
  and lost-Machine settlement; factory-operations status reconciliation findings.
- Boundary: controller result consumption and reconciler against the fake API.
- Setup: finished runs with results classified settled, exhausted, cancelled, Codex quota, and
  technical-with-retry; fake Machines tagged for this factory: one past deadline with no run,
  one within deadline with no run, one untagged.
- Action: run one controller cycle.
- Assertions: settled and exhausted → destroy; quota → stop with the deadline set to the later of
  hold expiry and the next window opening plus total limit plus grace, `fly:machine:<claim>`
  recorded, and refreshed on a later cycle when the hold moves; technical with retry → Machine
  kept; expired orphan destroyed; unknown orphan recorded under `fly:unknown` and untouched;
  untagged Machine untouched; a stopped held Machine within its deadline untouched; a failed
  destroy recorded under `fly:cleanup-failed` and surfaced by status; the aggregate verdict with
  one lost repetition and two reviewable ones is `pending-human-review` with the loss listed, and
  with all lost is `infra-error`.
- Execution: `tests/integration/test_fly_disposal.py`.

### INT-007: Configuration, intake, doctor, and status
- Covers: factory-operations configuration (`eval.execution`, `[fly]` defaults, Codagent example
  defaults), doctor `eval-fly` group with no Docker probing, status Machine lines;
  factory-eval-intake Cursor refusal and frozen Cursor hold; factory-claim-lifecycle readiness
  classes.
- Boundary: `config.py`, eval intake, `operations.doctor`/`status` with the fake API and a fake
  `flyctl` on `PATH`.
- Setup: local config with evals on `fly` and fixes on `host`; shared config with the Codagent
  defaults; requests naming a Cursor role; a frozen claim with a Cursor role from Docker days.
- Action: load config; parse requests; run doctor and status; run one cycle with the Cursor claim.
- Assertions: `[fly]` defaults are region `ewr`, shared 4 CPUs, 8192 MiB, grace 900 s; shared
  defaults are opus lead and Luna implementor and tester; the Cursor request becomes needs-input
  with a comment naming the role and replacing it clears the flag; the frozen claim is held, not
  mutated; doctor output contains the `eval-fly` group and no line mentioning Docker; doctor
  never issues a create call; with `docker` configured the Docker group still runs; the
  mode-neutral checks appear under the `eval` group in both modes and a bad host Codex login
  holds eval under `fly`; status shows Machine id, state, and deadline for an active run and
  `stopped (quota hold)` with its deadline for a quota-held claim.
- Execution: `tests/integration/test_fly_operations.py`, `tests/integration/test_fly_intake.py`.

## End-to-End Tests

### E2E-001: Eval request to settled result through Fly with restart and Machine loss
- Covers: factory-eval-execution end to end under `fly`; factory-claim-lifecycle restart adoption
  and lost-Machine settlement; factory-fly-execution collect-then-destroy.
- Surface: the factory CLI (`tick`, `status`) with the fake GitHub used by `test_factory_cycle.py`.
- Setup: local config with evals on `fly`; fake Machines API; fake `flyctl` shim whose job
  execution copies a fixture result tree into the guest artifacts and writes `DONE`; three
  repetitions.
- Journey: request an eval; tick until the first Machine is created and the heartbeat advances;
  stop the controller and watcher mid-run and tick again; let repetition one settle; during
  repetition two delete the fake Machine out from under the launcher; tick until repetition three
  settles.
- Assertions: after restart the same Machine id is adopted and no retry is consumed; repetition
  one is collected once and its Machine destroyed; repetition two settles as failed with reason
  machine lost and consumes no retry; repetition three runs in a new Machine; the card reaches
  Review with `pending-human-review`, the results comment lists repetition two as lost with its
  reason and carries review commands for repetitions one and three only; status shows no live
  Machine at the end.
- Execution: `tests/e2e/test_fly_eval_cycle.py`.

## Agent Acceptance Tests

Each flow below creates real Fly Machines in the configured app with the default shared-cpu
size through the launcher's `stand-in` mode defined in the design (an operator-supplied script,
not the eval suite, through the same create, verify, deliver, stream, collect, and dispose path,
with `--deadline-seconds` setting the deadline), and destroys every Machine it creates. Expected cost is a few cents per flow, under a dollar in total. Credentials copied to
a Machine are the operator's existing Codex and Claude logins only; the Cursor login is never
copied. Evidence is the captured CLI output and the collected artifact directory.

### AT-001: Containment, two-phase launch, reattach, collect, destroy
- Classification: Required
- Covers: factory-fly-execution "Establish containment before delivering anything", "Bound every
  Machine with one absolute deadline", "Deliver credentials after ownership", "Make the Machine
  the durable execution identity", "Collect ... then destroy".
- Actor and surface: operator at the factory CLI and `flyctl`, using `launcher stand-in` against
  the real app.
- Setup: `local.toml` with evals on `fly`, deploy token in place, image pushed, a stand-in script
  that lists `/host-home` and writes a marker file, `--deadline-seconds 300`.
- Steps: launch; kill the launcher immediately after the create call returns and before delivery;
  inspect the Machine over ssh; wait for the deadline. Then launch again normally; kill the
  launcher while the job runs; run attach; wait for `DONE`.
- Expected: the abandoned Machine holds no credential files, its metadata carries the run id and
  deadline, and it is gone after the deadline without factory action. The second Machine's
  provenance file has the image digest and id; attach resumes the stream; the collected directory
  contains the marker and the job log; no credential file remains in the guest at `DONE`; the
  Machine is destroyed after collection and `flyctl machines list` shows none for the run.
- Evidence: CLI transcript, `flyctl machine status` before and after, collected artifact listing.
- Effects and cleanup: two Machines created and destroyed; a few cents.
- Permitted substitutes: None.

### AT-002: Stop and start across a hold, then reconcile an orphan
- Classification: Required
- Covers: factory-fly-execution "Stop the Machine during a Codex quota hold", "Resume only inside
  the surviving Machine", "Reconcile stale Machines every cycle".
- Actor and surface: operator at the factory CLI and `flyctl`.
- Setup: as AT-001, with a stand-in script that writes a checkpoint file under `/artifacts` and
  a file under `/var/lib/factory`, then exits 0, run with `--keep` and `--deadline-seconds 120`;
  a second Machine created by hand with the factory metadata marker and a deadline in the past.
- Steps: run the stand-in to completion; stop the Machine through the factory's API client with
  the deadline extended by config env update as the design specifies; wait past the original
  deadline; start it through the same client; run a stand-in resume that checks both files;
  destroy it. Then run one controller tick and status.
- Expected: the Machine reaches `stopped` and is not destroyed by the stop; after the original
  deadline passes it is still present; on start it does not self-destroy, and both the artifact
  checkpoint and the `/var/lib/factory` file are present, proving the rootfs persisted across
  stop, config update, and start. The hand-made orphan is destroyed by the reconciler and status
  reports it. A failure of the `stopped` observation blocks the change (design fallback applies).
- Evidence: CLI transcript, `flyctl machine status` showing `stopped` then `started`, status
  output with the reconciliation finding.
- Effects and cleanup: two Machines, both destroyed; a few cents.
- Permitted substitutes: None.

### AT-003: Doctor and status against the live account
- Classification: Required
- Covers: factory-operations doctor `eval-fly` group, no Docker output, status Machine lines.
- Actor and surface: operator at the factory CLI.
- Setup: evals on `fly`, fixes on `host`, Docker Desktop not running.
- Steps: run doctor; run status while an AT-001 Machine is live; run doctor with the token file
  removed and again with a bogus image reference.
- Expected: doctor passes with an `eval-fly` group and prints nothing about Docker; status shows
  the Machine id, state, and deadline; missing token and unresolvable image each fail the
  `eval-fly` group with a specific remedy and doctor creates no Machine.
- Evidence: CLI transcripts.
- Effects and cleanup: none beyond AT-001.
- Permitted substitutes: None.

### AT-004: Credential token-refresh gate
- Classification: Conditional: the operator's Codex and Claude logins are current at the start.
- Covers: proposal production gate (subscription credentials survive one refresh interval in a
  Machine without invalidating the Mac's session).
- Actor and surface: operator at the factory CLI, using a stand-in job that invokes each CLI for a
  trivial model call every 30 minutes for three hours.
- Setup: as AT-001 with `--deadline-seconds 14400`.
- Steps: launch; let the job run to completion; collect; then run a trivial Codex and Claude call
  on the Mac.
- Expected: every in-Machine call succeeds through the three hours; both Mac calls succeed
  afterwards; the Mac's credential files are unchanged in content.
- Evidence: the collected job log with timestamps and call results, Mac CLI output, checksums of
  the Mac credential files before and after.
- Effects and cleanup: one Machine for about three hours, roughly a dollar; a handful of trivial
  model calls against the operator's subscriptions.
- Permitted substitutes: None. If the logins are not current, record the gate as not run and why.

## Human-Only Testing

### HT-001: One full eval on Fly through the factory, including human review
- Reason: the full and-scene eval consumes hours of the operator's personal model quota on the
  operator's subscriptions, and the outcome ends in the operator's own rating of the candidate.
  Only the operator can authorize that spend and give that judgment.
- Prerequisites: all INT and E2E tests pass; AT-001 through AT-003 pass; AT-004 passed or its
  omission is recorded.
- Instructions: with evals on `fly`, file one eval request with the default roles; let the
  factory run all repetitions; when the review comment appears, run the human-review command
  from `docs/operations.md` against the collected artifact directory on the Mac; complete the
  review; check status shows no live Machine.
- Required decision or observation: every repetition settled with a recorded image digest and
  Machine identity; the collected directory served the candidate for review on the Mac; the
  review posted; the Fly dashboard shows no leftover Machines; the operator judges whether the
  cost and duration per attempt are acceptable for routine use.

## Coverage Map

| Requirement or journey | INT | E2E | AT | HT |
| --- | --- | --- | --- | --- |
| Fly: execute eval attempts in a Fly Machine | INT-004 | E2E-001 | AT-001 | HT-001 |
| Fly: establish containment before delivering anything | INT-002, INT-004 | — | AT-001 | — |
| Fly: bound every Machine with one absolute deadline | INT-002, INT-003 | — | AT-001 | — |
| Fly: Machine as the durable execution identity | INT-005 | E2E-001 | AT-001 | — |
| Fly: deliver credentials from an exact allowlist after ownership | INT-001, INT-004 | — | AT-001, AT-004 | — |
| Fly: supervise through a live stream and heartbeat | INT-004, INT-005 | E2E-001 | AT-001 | — |
| Fly: collect once, verified, then destroy the Machine | INT-002, INT-004, INT-006 | E2E-001 | AT-001 | HT-001 |
| Fly: cancel and timeout stop the job and collect before finishing | INT-005 | — | — | — |
| Fly: pre-checkpoint failure retries fresh in the same Machine | INT-004, INT-005 | — | — | — |
| Fly: mismatch reported with remedy and resolved when the Machine is gone | INT-005, INT-006 | — | — | — |
| Fly: resume only inside the surviving Machine | INT-003, INT-004 | E2E-001 | AT-002 | — |
| Fly: stop the Machine during a Codex quota hold | INT-002, INT-006 | — | AT-002 | — |
| Fly: settle a lost Machine as a failed repetition | INT-005, INT-006 | E2E-001 | — | — |
| Eval reporting: verdict from survivors, lost repetitions listed, all lost is infra-error | INT-006 | E2E-001 | — | — |
| Fly: reconcile stale Machines every cycle | INT-002, INT-006 | — | AT-002 | — |
| Fly: record Machine provenance | INT-004 | — | AT-001 | HT-001 |
| Eval execution: invoke the suite with verified launcher arguments | INT-001 | E2E-001 | — | HT-001 |
| Eval intake: refuse Cursor roles under Fly; hold frozen Cursor claims | INT-007 | — | — | — |
| Claim lifecycle: restart adopts the surviving Machine | INT-005 | E2E-001 | AT-001 | — |
| Claim lifecycle: readiness classes and no Docker holds under Fly | INT-007 | — | AT-003 | — |
| Operations: configuration and Codagent defaults | INT-007 | — | — | — |
| Operations: doctor `eval-fly` group, no Docker output | INT-007 | — | AT-003 | — |
| Operations: status shows Machine and reconciliation findings | INT-006, INT-007 | E2E-001 | AT-002, AT-003 | — |
| Production gate: credential refresh survives a Machine | — | — | AT-004 | — |
| Human review from the collected directory on the Mac | — | — | — | HT-001 |
