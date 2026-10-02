# Decisions

## Proposal

### Verdict: go
- **Decision:** Go. The change is small and reversible, and the issue states it exactly.
  Without it, the factory would report an undelivered audit on every run once
  Codagent-AI/agent-runner#191 ships.
- **Alternatives considered:** No-go, keeping audits on. Rejected because it contradicts
  the issue.
- **Decision-bearing:** yes

### Reuse the existing `audit.AUDIT_ENABLED` switch
- **Decision:** Make the existing constant at `src/agent_factory/audit.py:30` the single
  switch. It already says #60 will own it, and `watch/session.py` already gates on it.
- **Alternatives considered:** A new constant, which would give two switches for one
  concern. A config or env toggle, which the issue's "smallest change" and "one-line
  revert" rule out.
- **Decision-bearing:** yes

### Guard `_settle_audit` in the runtime, not `audit.settle`
- **Decision:** `_settle_audit` returns early when the switch is off. `audit.settle` is
  unchanged.
- **Alternatives considered:** Guarding inside `settle`. Rejected because it changes the
  meaning of a function whose tests should keep covering the enabled path.
- **Decision-bearing:** no

### Readiness passes with an explanatory detail when disabled
- **Decision:** `readiness` returns `(True, "post-run audits are disabled (#60)", "")`
  before probing.
- **Alternatives considered:** Removing the doctor diagnostic. Rejected because it is more
  than a one-line revert.
- **Decision-bearing:** no

### Eval-side delivery also stops
- **Decision:** Making `_settle_audit` a no-op also stops host delivery of eval reports
  collected in a sandbox. This matches the issue's goal of disabling post-run audits for
  factory runs, and agent-runner#191 stops the sandbox audit hook.
- **Alternatives considered:** Keeping eval delivery. Rejected because the issue asks for
  no audit events, and it would keep a dependency on the Sheet connection.
- **Decision-bearing:** no

### Keep the `audit.log` progress globs
- **Decision:** Leave the evidence progress globs that match `audit.log` unchanged.
- **Alternatives considered:** Removing them. Rejected because Agent Runner still writes
  the execution `audit.log`, and the glob tracks progress, not post-run audits (see PR-2).
- **Decision-bearing:** no

### Existing audit tests forced on
- **Decision:** Existing audit tests set the switch to on with `monkeypatch`. New tests
  cover the off state.
- **Alternatives considered:** Deleting or skipping them. Rejected because the issue says
  to keep the code and tests.
- **Decision-bearing:** no

## Proposal review

### PR-1: status would list every new host run as missing its audit (applied)
- **Decision:** Applied. Confirmed in `operations._audit_lines`: a finished run that has
  `agent-runner-session/run-metrics.json` but no `audit.json` is listed as `missing` for
  seven days. The switch now also skips that fallback. Recorded `audit.json` outcomes are
  still listed, and a status test covers the off state.
- **Alternatives considered:** Leaving status unchanged. Rejected because it contradicts
  the goal of not reporting failures for disabled audits. Writing a "disabled" `audit.json`
  per run. Rejected because it adds persisted state, which is not a minimal change.
- **Decision-bearing:** no. It keeps the issue's intent within the same switch.

### PR-2: Agent Runner's execution `audit.log` is not the post-run audit (applied)
- **Decision:** Applied. Confirmed that Agent Runner writes `agent-runner-session/audit.log`
  during ordinary execution, and that `record-archive-block.sh` reads it. The proposal now
  reads the acceptance criterion "evidence has no audit log" as: no post-run audit outcome
  (`audit.json`) and no replay run. The execution `audit.log` and its progress glob stay,
  and tests assert that no replay ran, not that the file is absent.
- **Alternatives considered:** Suppressing the execution `audit.log`. Rejected because it
  belongs to Agent Runner, which is outside this repository, and the feature workflow
  depends on it. Stopping because the issue admits materially different readings. Rejected
  because the issue's goal and its "smallest change" constraint fit only the post-run
  replay reading. The literal reading would break feature reporting and needs a Runner
  change.
- **Decision-bearing:** yes. It is an interpretation of an acceptance criterion and is
  recorded as an assumption in the proposal.

## Specification

### Add the switch as a new requirement in `factory-operations`
- **Decision:** Add the requirement "Govern post-run audits with one switch" to
  `factory-operations`. It covers four places: host replay, the resident's settlement and
  `post-run-audit` events, `status` lines, and `doctor`. Each has scenarios for the switch
  off and a scenario for re-enabling.
- **Alternatives considered:** Modifying existing requirements. Rejected because no main
  spec states the host audit, settlement, or the doctor audit check: they were never
  promoted from the feature-support change. A new capability spec. Rejected because the
  proposal lists none, and doctor and status already live in `factory-operations`.
- **Decision-bearing:** no

### Doctor check stays and passes
- **Decision:** The spec says the `doctor` post-run audit check passes and states that
  audits are disabled. It does not say the check is removed.
- **Alternatives considered:** Hiding the check. Rejected because the issue's acceptance
  needs only that readiness not depend on the connection, and a visible "disabled" line
  tells the operator why there are no audits.
- **Decision-bearing:** no

### `factory-watch-dispatch`: wording only
- **Decision:** MODIFIED "Record each dispatched session's usage" names the factory-wide
  audit switch instead of "disabled by default pending #60". Its behavior does not change.
- **Alternatives considered:** Leaving it unchanged. Rejected because the text would then
  imply a separate watch-only switch.
- **Decision-bearing:** no

### Watch dispatch "watch audit: missing" status lines left as they are
- **Decision:** Out of scope. Watch sessions already record their audit as `missing`
  while the switch is off, and `status` lists each such ended dispatch as
  `watch audit: <id> missing`. That behavior was specified by the watch-dispatch change
  before this issue. The issue's change list and "no other behavior changes" cover only
  host replay, settlement, and readiness. The status fallback was added for factory
  attempts (PR-1) because this change would otherwise create new noise. This line is
  existing behavior, so it is not changed here. It is a candidate for a follow-up.
- **Alternatives considered:** Suppressing the watch audit status lines too. Rejected
  because it changes a separately specified requirement beyond what the issue asks for.
- **Decision-bearing:** no

## Design

### Read the switch as a module attribute at call time
- **Decision:** Every guard reads `audit.AUDIT_ENABLED`, and `launch.py` gains
  `from agent_factory import audit`.
- **Alternatives considered:** `from agent_factory.audit import AUDIT_ENABLED`. Rejected
  because a monkeypatch would not reach it. Passing a flag through the call chain.
  Rejected because it widens the change.
- **Decision-bearing:** no

### Readiness short-circuits before the `runner is None` check
- **Decision:** With the switch off, `readiness` returns success before any check,
  including the check for `agent-runner` on PATH.
- **Alternatives considered:** Still requiring the Runner on PATH. Rejected because the
  spec says the check must not depend on Runner audit support, and other doctor checks
  already cover a missing Runner.
- **Decision-bearing:** no

### Keep enabled-path tests live with an autouse fixture
- **Decision:** An autouse fixture in `test_post_run_audit.py` sets the switch to on.
  Disabled-state tests override it in their body.
- **Alternatives considered:** Marking the enabled tests as skipped. Rejected because the
  code would go untested. Per-test patches. Rejected as more churn.
- **Decision-bearing:** no

### No spec changes from design
- **Decision:** The design exposed no new behavior, so the specs are unchanged.
- **Alternatives considered:** None.
- **Decision-bearing:** no

## Test plan

### Integration-only coverage, no new E2E
- **Decision:** Five integration obligations (INT-001 to INT-005) in
  `tests/integration/test_post_run_audit.py` cover the host wrapper under real bash,
  settlement against the real SQLite store, status lines, doctor readiness, and the
  re-enabled path. No new E2E test.
- **Alternatives considered:** A CLI `tick`, `status`, and `doctor` E2E journey. Rejected
  because the change only removes work at four call sites, and every observable result is
  already proven with real components at the integration layer. The existing e2e suite
  must still pass with the shipped switch.
- **Decision-bearing:** no

### Acceptance envelope is local and isolated
- **Decision:** Acceptance runs only against temp storage, an isolated local config, and a
  stand-in Runner. The live service, the deploy script, the real audit connection, GitHub,
  and the Sheet are off limits. Human-only testing: none.
- **Alternatives considered:** Exercising the live factory after a deploy. Rejected
  because deploying belongs to factory-watcher-5 after merge.
- **Decision-bearing:** no

## Approach review

### AR-1: the factory switch cannot restore eval audits by itself (applied)
- **Decision:** Applied. Confirmed that `audit.settle` only delivers reports the eval
  sandbox's Agent Runner already collected, and that the Runner's automatic hook, which
  agent-runner#191 turns off, is what produces them. Changes:
  - The spec now scopes the switch to factory-owned triggers and settlement.
  - It names the Runner hook as a separate, required part of restoring eval audits.
  - It says Runner-side audits from claims pinned to a pre-#191 Runner are neither
    delivered nor reported while the switch is off.
  - The re-enable scenario gains an AND clause conditioned on collected reports.
  - Proposal, design (goals and migration plan), and test plan (INT-005 scope limit and
    known risks) are aligned.
- **Alternatives considered:** A factory-controlled eval audit trigger, so one line
  restores every audit. Rejected because it is a larger design change than the issue's
  "smallest change", and the issue explicitly splits responsibility with agent-runner#191.
  Treating this as a direction-level stop. Rejected because the issue already assigns the
  Runner hook to #191, so this only corrects how the artifacts state the guarantee.
- **Decision-bearing:** yes. It narrows the stated re-enable guarantee to factory-owned
  audits.

## Tasks

### One implementation task for the whole change
- **Decision:** `tasks.md` holds a single task covering the four guarded call sites, the
  docs, and tests INT-001 to INT-005, in the format of the archived feature-61 task.
- **Alternatives considered:** Splitting by call site. Rejected because the step asks for
  exactly one task, and the change is small and tightly coupled.
- **Decision-bearing:** no
