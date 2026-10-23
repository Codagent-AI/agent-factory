## MODIFIED Requirements

### Requirement: Invoke the versioned feature workflow

The factory SHALL run the packaged `factory-feature` workflow, with its `factory-define` sub-workflow, through the operator's installed Agent Runner, passing the configured feature role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, the attempt's artifact directory, and the attempt's resume point when one exists. The resume point SHALL be the claim's own pushed branch, or, for a new claim continuing a settled prior claim, the prior claim's branch supplied at admission. The factory SHALL ship the feature workflows beside the fix, review, and shared implementation workflows and stage all of them into the Runner catalog the attempt uses. The workflow contract SHALL be versioned as `factory-feature/1`. The factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the installed Runner does not provide what the workflow requires, including the `core/verify-change` builtin workflow, and SHALL hold the claim and report the problem in status and doctor. The workflow SHALL write exactly one structured outcome to `feature-outcome.json` in the attempt's artifact directory, declaring its contract version: `pull-request` with the pull request reference; `needs-input` with reasons; or `failed` with reasons. When the workflow ends because a step's repair was blocked, the attempt SHALL instead record that outcome as `Record a repair-blocked step as needs-input` requires. Any other absence of a structured outcome SHALL be treated as a technical failure. A feature attempt SHALL work on a deterministic branch named from the issue and claim.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged feature workflow declares a compatible contract version and the installed Runner provides `core/verify-change`
- **THEN** the attempt starts under its own supervisor on the host with the configured feature roles
- **AND** the staged workflow directory holds the feature, define, fix, review, and implementation workflow files

#### Scenario: Launch against a Runner without verify-change

- **WHEN** the installed Runner lacks the `core/verify-change` builtin workflow
- **THEN** no attempt is recorded, the feature is held, and status and doctor name the missing workflow

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `feature-outcome.json`, and the step whose failure ended it was not blocked by its repair
- **THEN** the factory records a technical failure and applies the recovery policy

#### Scenario: Finish after a blocked repair

- **WHEN** the workflow exits without writing `feature-outcome.json` because a step's repair declared `REPAIR_BLOCKED`
- **THEN** the attempt records a `needs-input` outcome naming that step
- **AND** the factory records no technical failure and launches no recovery retry

## ADDED Requirements

### Requirement: Record a repair-blocked step as needs-input

A feature attempt can end with no `feature-outcome.json` when its Runner process exits unsuccessfully. When that happens and the step whose failure ended the run had its failure-recovery repair declare `REPAIR_BLOCKED`, the attempt SHALL write a `needs-input` outcome before it ends. The declaration counts only if no later successful end or new attempt of that same step superseded it. `REPAIR_BLOCKED` is declared through the repair of a check, meaning a step that declares `repair`. The blocked step is that check. This applies to every such check in the feature workflow and its nested sub-workflows whose failure ends the run, wherever it is nested, including checks inside definition, `implement`, and classification, and checks that Runner builtin sub-workflows add later. The rule SHALL be one shared mechanism that uses the blocked step's name, not a separate recorder per step.

The outcome SHALL carry:
- the blocked step as the Runner names it;
- the agent's explanation as its reason and question, which is the repair response with its trailing `REPAIR_BLOCKED` line removed;
- the resume point defined below;
- the claim's branch when work is published on it;
- a direction summary.

The direction summary SHALL name the blocked step and the step the next attempt resumes at. It SHALL say that the cause can be fixed on the target branch or committed to the claim's branch, and that commenting on the issue resumes the claim with the target branch merged in. When the next attempt starts a fresh definition, the summary SHALL say so. The supervisor SHALL record the attempt with that `needs-input` outcome, so the run is not recorded as interrupted and the claim's recovery retry is not spent.

A blocked step SHALL NOT commit or push partial work. The resume point SHALL be the earliest step needed to regenerate what is missing from the published branch. It is the later, in feature workflow order, of two steps:
- the step this attempt started at;
- the step after the newest phase checkpoint this attempt pushed before blocking: implementation after `planned`, archive after `implemented`, verification after `archived`.

When neither exists, the outcome SHALL record no resume point, and the next attempt SHALL start a fresh definition. That fresh start SHALL NOT be reported as an unavailable resume point. An attempt can continue a prior claim's branch while its own branch was never pushed. When such an attempt records a resume point, its own branch SHALL be pushed with the prior branch's work unchanged, so the next attempt resumes from it. If that push fails, no `needs-input` outcome SHALL be recorded. Definition stops that commit and push their drafted artifacts, and merge-conflict stops, SHALL keep their existing resume points.

The rule SHALL NOT apply in four cases:
- The workflow already wrote an outcome. Archive's existing `needs-input` and verification's or finalization's `failed` outcomes remain as specified, including a verify reason that carries a repair response.
- The step that ended the run failed without an unsuperseded `REPAIR_BLOCKED`.
- The repair response is empty once the marker is removed.
- The attempt was stopped by a limit, cancellation, or loss of its process.

Each of these SHALL remain a technical failure subject to the recovery policy, unless an outcome exists.

The attempt's evidence SHALL keep the Runner session's audit log and transcripts. It SHALL NOT be required to preserve untracked files or unpushed commits from the blocked attempt beyond what clone slimming already keeps.

#### Scenario: Implementation repair is blocked

- **WHEN** an attempt that pushed its `planned` checkpoint ends because the repair of a check inside `implement` declares `REPAIR_BLOCKED` after an explanation
- **THEN** the attempt records `needs-input` naming the blocked `implement` step, with that explanation as its reason and no `REPAIR_BLOCKED` marker
- **AND** its resume point is implementation
- **AND** no recovery run is created and no pull request is opened
- **AND** the claim's pushed branch still ends at the `planned` checkpoint

#### Scenario: First-attempt definition repair is blocked

- **WHEN** a claim's first attempt ends because the repair of a check inside definition declares `REPAIR_BLOCKED` before anything was pushed
- **THEN** the attempt records `needs-input` naming that check, with the explanation and no resume point
- **AND** the direction summary says that the next attempt starts a fresh definition
- **AND** no recovery run is created

#### Scenario: Resume after a first-attempt definition block

- **WHEN** a writer comments on a claim stopped as in "First-attempt definition repair is blocked"
- **THEN** the next attempt starts a fresh definition and completes it
- **AND** its admission is not reported as an unavailable resume point

#### Scenario: Definition repair is blocked after resuming a pushed partial plan

- **WHEN** an attempt resumed at design from a pushed definition stop produces a design and test plan, then ends because the repair of a check later in definition declares `REPAIR_BLOCKED`
- **THEN** the attempt records `needs-input` naming that check with resume point design
- **AND** the next attempt, after a writer comments, regenerates the design and test plan before writing tasks

#### Scenario: Step without its own resume point is blocked after archive

- **WHEN** an attempt that pushed its `archived` checkpoint ends because the repair of `verify-classification` declares `REPAIR_BLOCKED`
- **THEN** the attempt records `needs-input` naming `verify-classification` with resume point verification
- **AND** the next attempt resumes at verification rather than starting from scratch

#### Scenario: Continuation repair is blocked before its own branch was pushed

- **WHEN** a new claim continuing a prior branch at implementation, whose own branch was never pushed, ends because the repair of a check inside `implement` declares `REPAIR_BLOCKED`
- **THEN** the claim's branch is pushed with the prior branch's work unchanged
- **AND** the attempt records `needs-input` with resume point implementation and links the claim's branch
- **AND** the next attempt resumes at implementation from that branch rather than starting fresh

#### Scenario: Step fails without a repair block

- **WHEN** an attempt ends because a step fails and its repair never declares `REPAIR_BLOCKED`
- **THEN** no outcome is written, the factory records a technical failure, and the recovery policy applies

#### Scenario: Superseded repair block

- **WHEN** a step's repair declares `REPAIR_BLOCKED`, a later attempt of that step succeeds, and a different step then ends the run with a failure that its repair did not block
- **THEN** no `needs-input` outcome is written and the recovery policy applies

#### Scenario: Repair block without an explanation

- **WHEN** the step that ended the run has a `REPAIR_BLOCKED` response with no text before the marker
- **THEN** no `needs-input` outcome is written and the recovery policy applies

#### Scenario: Attempt stopped by a limit

- **WHEN** the factory stops an attempt for exceeding its inactivity, execution, or total limit
- **THEN** no `needs-input` outcome is recorded and the recovery policy applies

#### Scenario: Verification stays failed

- **WHEN** a repair inside verification declares `REPAIR_BLOCKED` and the workflow records its `failed` outcome
- **THEN** the outcome stays `failed` with the repair response in its reason, as verification requires

#### Scenario: Archive block is unchanged

- **WHEN** archive repair declares `REPAIR_BLOCKED` and the workflow continues to record the archive stop
- **THEN** the outcome is the archive `needs-input` with stopped step `archive` and its existing direction summary
- **AND** the Runner run itself ends successfully
