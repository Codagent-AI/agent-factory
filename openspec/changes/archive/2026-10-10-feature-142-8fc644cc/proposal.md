## Why

Any Agent Runner agent step can end its failure-recovery repair with `REPAIR_BLOCKED`. That is a
deterministic, explained stop: the repair agent has said why it cannot continue. Today only `archive`
turns it into a durable outcome (`record-archive-block.sh`, PR #51). When any other feature step that
is not `continue_on_failure` blocks (for example `implement`, `proposal`, `specs`, `design`,
`write-tasks`, `resolve-merge`, or `classify`), the Runner stops the workflow and no
`feature-outcome.json` is written. Then:

- the supervisor records the run as `interrupted` ("owned process exited without durable result");
- `_needs_recovery` spends the claim's single recovery retry on a stop that will happen again;
- the claim settles as `infra-error`, and the agent's explanation never reaches the card.

The operator loses the explanation and the claim. They have to dig through the audit log and re-admit
the issue by hand. With #53 a resumed feature claim already merges the target branch, so a
`needs-input` stop that resumes at the blocked step is enough to recover once the cause is fixed. The
missing piece is recording that stop.

**Verdict: go with caveats.** The problem recurs, is costly when it happens, and has a contained fix
in this repository: one recorder plus a resume point derived from published progress. Caveats:
- The recorder depends on the Runner's audit-log format (already a dependency of
  `record-archive-block.sh` and `verify-failure.py`).
- Unpushed partial work is neither carried over to the resumed attempt nor durably preserved. A
  first-attempt definition block restarts definition.
- Verify, finalize, and fix workflows are deliberately left alone (see Out of Scope).

## What Changes

- When a feature attempt's Runner process exits unsuccessfully without `feature-outcome.json`, and the
  step whose failure ended the run last reported `repair_blocked`, the factory writes a `needs-input`
  outcome before the attempt ends. The outcome carries:
  - the blocked step's name, as the Runner logged it;
  - the agent's explanation: the repair response with its trailing `REPAIR_BLOCKED` line removed;
  - a resume point chosen from the work that is actually published on the claim's branch (see below);
  - the claim's branch;
  - a direction summary saying that the fix can be merged to the target branch or committed to the
    claim's branch, and that a writer comment resumes the claim with the target branch merged in. The
    summary names both the blocked step and the step the next attempt resumes at, and says plainly
    when the next attempt starts a fresh definition.
- The supervisor then sees a normal `needs-input` result. The run is not `interrupted`, no recovery
  retry is spent, and the explanation reaches the card through the existing feature stop message.
- The reported blocked step and the resume point are separate. The blocked step is what the Runner
  logged. The resume point is the earliest step needed to regenerate everything that is missing from
  the published branch, because a block does not push partial work. Concretely, it is the later of:
  - the step this attempt itself started at (its effective resume point, which an earlier pushed
    definition stop or checkpoint established);
  - the step after the newest phase checkpoint (`planned`, `implemented`, or `archived`) this attempt
    pushed before blocking.

  When neither exists (for example, a first attempt that blocks during definition, before anything
  was pushed), the outcome records no resume point and the next attempt starts a fresh definition.
  The direction summary says so; this is not a silent fallback. Some examples:
  - a block in `write-tasks` after an attempt that resumed at `design` resumes at `design`, not at
    `write-tasks`, because the new design and test plan were never pushed;
  - a block inside `implement` resumes at `implement` from the `planned` checkpoint;
  - a block in `classify` or `task-compliance-repair` resumes at `verify`;
  - a block in `resolve-merge` or `reconcile-artifacts` resumes at the step the attempt was going to
    resume at.

  Direction stops written by `record-stop.sh` keep their precise resume step, because they commit and
  push their drafted artifacts first.
- An unsuccessful exit without `repair_blocked` keeps today's behavior: no outcome, technical
  failure, recovery policy.
- Archive keeps its current outcome, summary, and resume at archive. It shares the block parser and
  outcome builder with the new recorder, so there is one mechanism rather than one script per step.
- No **BREAKING** changes. The `factory-feature/1` outcome gains only an optional, additive field for
  the blocked step's full name. `stopped_step` keeps its meaning (the resume point), and is omitted
  when the next attempt starts fresh.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-feature-execution`: the "Archive repair is blocked" behavior is generalized to any agent
  step that ends with `REPAIR_BLOCKED` before an outcome is written. The rule that a missing outcome is
  a technical failure gains this one exception. Resume rules gain a resume point derived from
  published progress, and an explicit, reported fresh start when nothing was published. Recovery stays
  for runs without a repair block.
- `factory-feature-reporting`: only if needed, so the card's stop message names the blocked step
  alongside the explanation. The existing feature stop message already shows `questions`, the
  direction summary, and the branch.

## Technical Approach

The Runner stops the workflow at the first failed step that is not `continue_on_failure`, so no
workflow step can record the block afterwards. Making every step `continue_on_failure` with a recorder
after each one would duplicate guards across about twenty steps, and that is the per-step pattern the
issue asks to avoid. Changing Agent Runner is outside this repository.

The recorder therefore runs in the factory-owned host wrapper (`launch.host_script`). Feature work is
host-only (`allowed_modes=("host",)`). After the Runner exits unsuccessfully, and only for the feature
kind, the wrapper runs a factory Python module, the same way it already runs the post-run audit.

The module has one shared core and two entry contexts:

- **Shared core.** A parser takes an audit log and an endpoint step subtree. It returns the latest
  `repair_blocked` response in that subtree that no later successful end or new attempt of the same
  step superseded, with its trailing `REPAIR_BLOCKED` line stripped. An outcome builder writes a
  `needs-input` outcome that `verify-feature-outcome.py` and `read_interpreted_outcome` accept. The
  stale-response rules are the same ones `record-archive-block.sh` and `verify-failure.py` use today.
- **Wrapper context (new).** The wrapper context requires a failed terminal run: the Runner exited
  unsuccessfully, `run_end` reports failure, and `feature-outcome.json` is absent. It takes the
  causal failing step behind that `run_end` as the endpoint and computes the resume point from
  published progress. To do that, it reads the attempt's effective resume point (captured by
  `prepare-branch`) and the checkpoints the attempt pushed, which the audit log records.
- **Archive context (existing hook).** `record-archive-block.sh` still runs inside the still-running
  workflow after the archive sub-workflow fails. At that point there is no `run_end`, and the run may
  later end successfully. The hook passes the failed `archive` subtree as the endpoint and does not
  require `run_end`. It keeps the archive-specific summary wording and its `archive` resume point,
  which is correct because the `implemented` checkpoint is pushed before archive.

The wrapper's exit status is unchanged. The supervisor reads the outcome as it does for any completed
feature attempt. A block does not commit or push partial work. The next attempt resumes from published
progress and redoes everything after it. This follows the existing rules that an unpushed phase is
redone and that a stop never pushes a partial tree.

Evidence is limited to what the factory already keeps. After the block, clone slimming keeps only
`clone-state.patch`, which holds `git status` and a truncated `git diff HEAD`. Untracked drafted
files (for example new specs from a first-attempt definition) and local commits that were never pushed
are not preserved and may be lost when the clone is slimmed. The durable record of a block is the
agent's explanation in the outcome, plus the session transcript and audit log in the evidence.

Design decides whether the shared core is a standalone module called by the wrapper and by
`record-archive-block.sh`, or a workflow script both invoke. It also decides the exact field name for
the blocked step path and how the wrapper context reads the attempt's effective resume point and
pushed checkpoints.

## Out of Scope

- **verify and finalize.** They run with `continue_on_failure: true`. A `REPAIR_BLOCKED` inside them
  already produces a durable `failed` outcome, and #141 put the repair explanation into the verify
  reason. That is not the interrupted-run gap this issue targets, and turning it into `needs-input`
  would change the specified validator and CI semantics. The wrapper recorder never runs for them,
  because they write an outcome.
- **Fix workflows (`factory-fix`).** `factory-fix-execution` specifies no resume of a partial attempt.
  Changing that is a separate decision; a follow-up issue should decide between leaving fixes alone, a
  fresh-start `needs-input`, and step-level resume for fixes.
- **Task and review workflows.**
- **Committing or pushing partial work** from a blocked step before recording `needs-input`.
- **Durable preservation of unpushed work.** This change does not preserve untracked drafted files or
  unpushed local commits beyond what clone slimming already keeps (`clone-state.patch`). Salvaging
  that work belongs in a follow-up issue against slimming, if operators need it.
- **Agent Runner changes**, including the `REPAIR_BLOCKED` protocol and the audit-log format.
- **Timeouts, kills, and crashes without a repair block.** They stay technical failures under the
  existing recovery policy.

## Impact

- Code: `src/agent_factory/work_kinds/pull_request/launch.py` (host wrapper); a new shared recorder
  module with a shared core and two entry contexts; `workflow/record-archive-block.sh` (archive entry
  context on the shared core); possibly `workflow/prepare-branch.sh` or the checkpoint script, so the
  wrapper can read the effective resume point and pushed checkpoints; `handler.feature_resume_point()`
  (needs-input without a resume point starts fresh); possibly `workflow/verify-feature-outcome.py` and
  `outcome.py`, to accept the optional blocked-step field.
- Tests:
  - integration tests in which `implement` and one define step (for example `specs`) each block with
    `REPAIR_BLOCKED`, giving a `needs-input` outcome that names the step and creating no recovery run;
  - a step that fails without `REPAIR_BLOCKED` still goes to recovery;
  - complete second attempts after a first-attempt definition block (explicit fresh definition) and
    after a later definition block on an already-pushed partial plan (resume at the attempt's own
    start point, regenerating the unpushed artifacts);
  - the archive hook on an audit that ends at the failed archive step and sub-workflow events with no
    `run_end`, and a complete workflow that records archive `needs-input` and exits successfully.
- Operations: the factory spends fewer recovery retries and records fewer `infra-error` settlements.
  Operators see blocked steps as `needs-input` cards with the agent's explanation. A deploy is needed;
  running attempts keep their release.
- Specs: `factory-feature-execution` delta; `factory-feature-reporting` delta only if needed.
