# Decisions: feature-142-8fc644cc

## D1 (proposal): Verdict go with caveats
- Decision: Go. The gap recurs, it spends the single recovery retry, and it loses the explanation. The fix fits inside this repository.
- Alternatives: no-go (keep archive-only handling); wait for an Agent Runner change.
- Decision-bearing: yes.

## D2 (proposal): Record the block in the factory host wrapper
- Decision: After an unsuccessful Runner exit with no `feature-outcome.json`, the feature host wrapper (`launch.host_script`) runs one shared factory recorder. The recorder reads the session audit log and writes `needs-input` for the step whose failure ended the run, but only when that step's last event is an unsuperseded `repair_blocked`.
- Alternatives:
  - add `continue_on_failure` plus a recorder after every agent step (rejected: about twenty duplicated guards, the per-step pattern the issue asks to avoid);
  - synthesize the outcome in the supervisor or handler at settle time (rejected: moves workflow-outcome logic into result handling and away from the attempt's evidence);
  - change Agent Runner to write a hook outcome (rejected: outside the target repository).
- Decision-bearing: yes.

## D3 (proposal): verify and finalize stay `failed`
- Decision: Out of scope. They are `continue_on_failure` and already write a durable `failed` outcome, and #141 adds the repair explanation to it. Changing them would alter specified validator and CI semantics, and it is not the interrupted-run gap.
- Alternatives: `needs-input` with a resume at verify or finalize, which would need nested-block detection to tell a block apart from a red validator or CI.
- Decision-bearing: yes (orange: the issue left this open).

## D4 (proposal): Fix workflows unchanged
- Decision: Out of scope. The issue title and acceptance cover feature steps only, and `factory-fix-execution` specifies no partial resume. Recommend a follow-up issue.
- Alternatives: fresh-start `needs-input` for fixes; step-level resume for fixes.
- Decision-bearing: yes (orange: the issue left this open).

## D5 (proposal): Map non-resumable steps to the nearest earlier resume point (superseded by D8)
- Decision: Use one ordered mapping:
  - define-internal apply steps map to their review step;
  - nested `implement` steps map to `implement`;
  - `task-compliance-repair`, `classify`, and `verify-classification` map to `verify`;
  - `resolve-merge` and `reconcile-artifacts` map to the step the attempt was going to resume at.

  This keeps the next attempt from silently starting from scratch.
- Alternatives: add a resume point for every step (more surface, and nested sub-workflow steps cannot be skipped individually); pass unknown steps through as today (silent fresh start).
- Decision-bearing: yes.

## D6 (proposal): Do not commit or push partial work from a blocked step (evidence claim corrected by D10)
- Decision: Keep the last pushed checkpoint. Unpushed work stays in the attempt evidence (`clone-state.patch`), and the resumed attempt redoes the blocked step. This matches the existing rules that an unpushed phase is redone and that a stop never pushes a partial tree.
- Alternatives: commit and push the partial work as a non-checkpoint commit before recording `needs-input` (risks half-finished commits on the claim branch that resume and task-compliance would then build on).
- Decision-bearing: yes (orange: implementation work done before a block is redone).

## D7 (proposal): `stopped_step` stays the resume point; add an optional field for the blocked step
- Decision: Keep `stopped_step` meaning the step to resume at, because `feature_resume_point()` consumes it. Record the blocked step's full logged name in an additive optional field; design names the field. This change is additive and does not break the persisted format.
- Alternatives: put the raw blocked step path in `stopped_step` (breaks the resume consumer).
- Decision-bearing: no.

## D8 (proposal-review, PR-001): Applied. The resume point comes from published progress, not the blocked step
- Decision: Keep the no-partial-push choice (D6). Keep the reported blocked step separate from the resume point. The resume point is the later of the attempt's own effective resume point and the step after the newest checkpoint the attempt pushed. When neither exists, the outcome records no resume point, and the direction summary states that the next attempt starts a fresh definition. Direction stops from `record-stop.sh` keep their precise step because they push their drafts. Tests add complete second attempts after a first-attempt definition block and after a later definition block on a pushed partial plan. This supersedes D5's step-name mapping.
- Alternatives:
  - D5's step-name mapping (rejected: it skips producers whose output was never pushed, and it silently falls back to a fresh start when no branch exists);
  - pushing partial drafts as `record-stop.sh` does (rejected: contradicts D6 and the stop-never-pushes-a-partial-tree rule).
- Decision-bearing: yes (orange: a first-attempt definition block restarts definition).

## D9 (proposal-review, PR-002): Applied. Shared core with two entry contexts
- Decision: One shared parser and outcome builder:
  - the host wrapper context requires a failed terminal `run_end` and follows its causal failing step;
  - the in-workflow archive context passes the failed `archive` subtree as its endpoint, needs no `run_end`, and keeps the archive summary and resume point.

  Tests add the archive hook on an audit without `run_end`, and a full workflow that records archive `needs-input` and exits successfully.
- Alternatives: apply a single failed-run precondition at both entry points (rejected: it would reject the existing archive stop).
- Decision-bearing: no.

## D10 (proposal-review, PR-003): Applied (narrowed). Durable preservation rejected
- Decision: Narrow the evidence claim to what slimming actually keeps: `clone-state.patch`, which holds status and a truncated tracked diff. State that untracked drafted files and unpushed local commits may be lost. Durable preservation of unpushed work is listed as out of scope, with a suggested follow-up issue against slimming.
- Alternatives: add bounded preservation of untracked files and unpushed commits before slimming (rejected for this change: the issue offers discarding partial work as an acceptable option; preservation changes the slimming and retention capability, not feature execution; and the blocked step's explanation, transcript, and audit log stay in evidence).
- Decision-bearing: yes (orange: drafted or implemented work from a blocked attempt may be unrecoverable).

## D11 (specs): Spec the exception in "Invoke the versioned feature workflow" and add one new requirement
- Decision: Modify the outcome rule so that a missing outcome is a technical failure except when a step's repair was blocked. Put the repair-block behavior in a new requirement, "Record a repair-blocked step as needs-input". "Archive the change before verification" stays unchanged; its behavior is preserved, and a scenario in the new requirement covers it.
- Alternatives: fold everything into the archive requirement (rejected: archive-specific); modify "Resume and continue feature work" (rejected: the new requirement defines its own resume point and leaves the existing resume rules intact).
- Decision-bearing: no.

## D12 (specs): A repair block with an empty explanation stays a technical failure
- Decision: When the response is empty once the `REPAIR_BLOCKED` marker is removed, record no `needs-input` outcome and apply recovery. This matches the existing archive recorder, which refuses a block without an explanation, and the issue requires the outcome to carry the explanation.
- Alternatives: record `needs-input` with a generic reason pointing at the audit log.
- Decision-bearing: no.

## D13 (specs): A first-attempt fresh definition after a block is not an "unavailable resume point"
- Decision: A repair-blocked stop with nothing published records no resume point. The next attempt's fresh start is planned and announced in the stop comment, so it is not reported as an unavailable-resume fallback, and classification does not raise it as a red fresh-start fallback.
- Alternatives: record a resume point anyway and let `prepare-branch` fall back (rejected: reports a fallback for an expected case).
- Decision-bearing: no.

## D14 (specs): Modify the factory-feature-reporting comment requirement
- Decision: The proposal made a reporting delta conditional. It is needed: the stop comment would otherwise link a branch that was never published. The comment names the blocked step and the resume step through the direction summary, and when nothing was published it states that no branch was published and that the next attempt starts fresh, as `preflight` stops do. The board mapping is unchanged.
- Alternatives: no reporting delta (rejected: it would leave a dead branch link for first-attempt definition blocks).
- Decision-bearing: no.

## D15 (design): Run the recorder in the host wrapper as a staged stdlib script shared with archive
- Decision: Add `repair-block.py` to the packaged workflow scripts. The feature host wrapper runs it with the factory interpreter in isolated mode after an unsuccessful Runner exit, and `record-archive-block.sh` delegates to the same script with the `archive` endpoint. It never changes the wrapper's exit status.
- Alternatives: synthesize the outcome in the supervisor (rejected: it re-parses evidence outside the attempt and duplicates workflow outcome logic); a factory Python module run with `-m` (rejected: archive needs the same code inside the workflow, where only staged scripts are available).
- Decision-bearing: no.

## D16 (design): Identify the block through the causal failure chain
- Decision: The recorder requires all of the following:
  - a failed `run_end` whose `failure_kind` is not `infrastructure`;
  - an unbroken trailing chain of failed ends down to the deepest failed check;
  - that check's `step_end` carrying `repair_blocked: true`, plus an unsuperseded `repair_blocked` event on the same path.

  Anything else writes nothing, so behavior falls back to recovery.
- Alternatives: use the last `repair_blocked` event in the log (rejected: an earlier `continue_on_failure` or superseded block would mislabel an unrelated failure).
- Decision-bearing: no.

## D17 (design): `prepare-branch.sh` records `attempt-start.json`; checkpoints are read from git
- Decision: `prepare-branch.sh` writes the effective resume point, the starting head, and the prior head. The recorder counts only the checkpoints pushed since the starting head, on the first-parent chain of the remote-tracking branch.
- Alternatives: parse audit captures and checkpoint step ids (rejected: couples the recorder to step ids and capture formats); use the newest checkpoint on the whole branch (rejected: it can count target history and prior work).
- Decision-bearing: no.

## D18 (design): Relax needs-input validation only alongside `blocked_step`
- Decision: `blocked_step` is a new optional non-empty string. A `needs-input` outcome carrying it may omit `stopped_step` (meaning a fresh start) and `branch` (meaning unpublished). All other outcomes keep today's validation. The stop comment says "No branch was published; the next attempt starts fresh" when there is no branch.
- Alternatives: express a fresh start as `stopped_step: "proposal"`, as merge stops do (rejected: with no published branch, `prepare-branch` reports a fallback, which contradicts D13).
- Decision-bearing: no (additive contract change).

## D19 (design, spec revision): Push prior work for a continuation whose own branch was never pushed
- Decision: When a repair-blocked continuation has a resume point but its own branch is unpublished, the recorder pushes the prior head unchanged to the claim's branch, mirroring `record-merge-stop.sh` and the merge-stop requirement. If the push fails, it writes nothing, and the run goes to recovery. This is added to the spec, together with a scenario.
- Alternatives: record the stop without pushing (rejected: the needs-input resume passes no prior branch, so the next attempt would fall back to a fresh start and lose the continuation).
- Decision-bearing: yes (orange: the recorder pushes prior work in this edge case).

## D20 (design, spec revision): Scope the specs to repair-bearing checks
- Decision: The Runner emits `REPAIR_BLOCKED` only from checks with `repair`. Feature definition steps declare none today, and `resolve-merge`, `reconcile-artifacts`, and task-compliance repair are agent steps or `continue_on_failure` steps that cannot end the run with a block. The spec now names the blocked check and covers any repair-bearing check wherever it is nested, including ones Runner builtins add later. The resolve-merge scenario is replaced with a continuation scenario. The issue's define-step acceptance test uses a fake Runner audit with a check nested in `define`.
- Alternatives: add `repair` to definition steps so a define block can really occur (rejected: scope creep; the issue asks to record blocks, not create them).
- Decision-bearing: yes (orange: a define-step block is currently reachable only through future repair-bearing checks; the acceptance test simulates it).

## D21 (test-plan): Fake Runner audit logs pinned by a real-Runner contract fixture (extended by D24)
- Decision: Integration and E2E tests drive the real wrapper, git, supervisor, store, and handler with a fake `agent-runner` that writes audit logs. A committed, sanitized real-Runner audit fixture, with its source revision recorded, pins the event shapes, and `agent-runner -validate` runs when a Runner is installed.
- Alternatives: real Runner runs with model agents (rejected: nondeterministic and paid); synthetic logs alone (rejected: format drift would go unnoticed).
- Decision-bearing: no.

## D22 (test-plan): One E2E journey; no human-only tests
- Decision: E2E-001 covers the issue's acceptance journey (a block in implement leads to needs-input, no recovery, and a resume at implement) through the existing feature cycle harness. The define-step acceptance case is INT-002, at the integration layer, where the fresh-start chain is cheaper to assert. Human-only testing is None.
- Alternatives: a second E2E for the define block (rejected: it duplicates INT-002's assertions at a higher cost).
- Decision-bearing: no.

## D23 (approach-review, AR-001): Applied. Archive keeps its subtree-wide stale rule
- Decision: The shared parser takes a supersession scope. The run context uses `path` scope behind its causal chain. The archive context uses `subtree` scope: any later non-terminal event in `archive, sub:archive-change` clears the candidate, exactly as `record-archive-block.sh` does today. A block in `archive-transition` followed by a plain failure in `verify-archive-commit` therefore stays rejected. INT-006 names that existing regression case explicitly.
- Alternatives: derive a causal failed check inside the archive subtree without `run_end` (viable, but the existing rule is already proven by tests, and changing it buys nothing for archive).
- Decision-bearing: no.

## D24 (approach-review, AR-002): Applied. Runtime contract test against the installed Runner
- Decision: INT-007 keeps the committed fixture for portable regression and adds a runtime part. The installed `agent-runner` runs a throwaway workflow with a nested repair-bearing check, a stub agent CLI answers with an explanation followed by `REPAIR_BLOCKED`, and the recorder must identify the check and write a valid outcome from the fresh audit. It skips only when no Runner is installed, and it must execute on Paul's Mac. The design adds a stub agent harness section.
- Alternatives: fixture plus `-validate` only (rejected: `-validate` produces no execution audit, and host attempts use a Runner rebuilt from `main`).
- Decision-bearing: no.

## D25 (approach-review, AR-003): Applied. Resumed definition runs through plan publication
- Decision: New INT-008 runs the packaged feature workflow with the installed Runner, `--until define`, the stub agent harness, and a temporary bare origin. It covers a fresh definition (no resume point) and a resume at design over a pushed partial plan. It asserts that the producers run in order, that `write-tasks` reads the regenerated design and test plan, and that the `planned` checkpoint is published. INT-002 and INT-003 keep their recorder and branch-preparation assertions.
- Alternatives: stop at `prepare-branch.sh` and the skip predicates (rejected: they do not prove the resumed workflow regenerates and publishes); a paid-model or live-service E2E (rejected: outside the envelope).
- Decision-bearing: yes (orange: the runtime tests depend on emulating one agent CLI's headless protocol with a stub, and they skip on machines without an installed Runner).

## D26 (write-tasks): One task, with a short operations note and orange PR items
- Decision: `tasks.md` holds a single task covering the recorder, the resume point, the archive hook, the wrapper, the contract, reporting, tests, a short `docs/operations.md` note, and the PR description's orange items and suggested follow-ups (D3, D4, D10). `AGENTS.md` is left unchanged unless something there becomes false.
- Alternatives: no documentation change (rejected: operators otherwise cannot tell that blocked steps no longer consume recovery).
- Decision-bearing: no.
