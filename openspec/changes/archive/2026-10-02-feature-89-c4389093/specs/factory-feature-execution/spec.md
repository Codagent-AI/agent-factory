## ADDED Requirements

### Requirement: Gate the feature on a bound task-compliance verdict

When the target's Agent Validator configuration declares a review named `task-compliance` under any entry point, the feature workflow SHALL obtain a task-compliance verdict for the change before it classifies review attention, and SHALL NOT treat a validator pass as one. A task-compliance verdict SHALL count only when it is bound to all of the following:

- the base: the merge base of the reviewed head and the target branch head the attempt started from or last merged, so that the reviewed diff is exactly the change the pull request carries, including every commit since the claim's branch left the target branch and no change the target branch brought in;
- the reviewed head and its tree;
- the content of the change's `tasks.md` given to the review as context, identified by its hash;
- the full diff from the base to the reviewed head, not only the commits of one step, with every changed path outside `openspec/` lying under an entry point whose task-compliance review was dispatched, except paths that entry point itself excludes;
- a task-compliance review that actually executed and reached a terminal pass or fail.

The target branch head in the base SHALL be the one the claim's branch actually contains: on a resume or continuation, the target head that the resume merged, or found already contained; on a fresh start, the target head the branch was created from. A validator result of `Trusted`, `no_applicable_gates`, `no_changes`, a skipped prior pass, an error, or a run whose base, scope, or task context cannot be shown SHALL NOT be a verdict. Evidence that a review was dispatched and passed SHALL be accepted even when the validator has cleaned or rotated its logs after the pass. A review that passed for some changed paths but did not see others SHALL be `not-run`, naming the paths it did not see.

The workflow SHALL apply this gate once after verification, which includes simplify, acceptance, and any acceptance repair, and before classification. No earlier verdict exists in the attempt, so it SHALL run regardless of the implementation step's validator status or trust history. It SHALL review the base-to-head diff with `tasks.md` from the archived change directory as context and refuse a verdict whose task content differs from the archived tasks. The implementation step's own task-compliance run remains an early repair opportunity but SHALL NOT count as the attempt's verdict. Violations SHALL be repaired and reviewed again within the bounded repair. These repairs are commits added after acceptance. Commits added after the last verdict, including those finalization adds, SHALL NOT be reviewed again in the attempt.

The workflow SHALL record one task-compliance result for the attempt in its evidence: `passed`, `failed` when violations remain after the bounded repair, `not-run` with the validator status or error that prevented a verdict, or `not-declared` when the target declares no task-compliance review. The record SHALL name the base, the reviewed head, the tasks hash, and the validator output it relied on. When the target does not declare task-compliance, the workflow SHALL run no task-compliance review and SHALL record `not-declared`. A `failed` or `not-run` result SHALL NOT stop the workflow, push a different outcome, or withhold the pull request.

Each task-compliance review the workflow runs SHALL be unaffected by the claim clone's validator trust and review history, so that a tree the validator already trusts, or a review it already ran once, is still reviewed. A review that ends in an error SHALL be tried once more before the result becomes `not-run`.

#### Scenario: The implementation step's validator returns Trusted

- **WHEN** the implementation step's validator run reports `Status: Trusted` and evaluates no gates for a target that declares task-compliance
- **THEN** the workflow runs a task-compliance review of the diff from the base to the current head with the archived tasks as context before classification
- **AND** the attempt's task-compliance result is that review's result, not the implementation step's pass

#### Scenario: The implementer already committed its work

- **WHEN** the implementation step's validator run reports `no_applicable_gates` because the implementer committed before validation
- **THEN** the workflow still reviews the full diff from the base to the current head for task compliance before classification

#### Scenario: The implementation step's review ran and passed

- **WHEN** the implementation step's validator ran task-compliance and passed, but its review record does not name the base, the head, or the tasks it reviewed
- **THEN** the workflow still runs its own task-compliance review of the diff from the base to the current head before classification, and the attempt's result is that review's result

#### Scenario: A review ends in an error

- **WHEN** the workflow's task-compliance review ends in a reviewer error
- **THEN** the workflow runs it once more, and records `not-run` with the error only when the second run also produces no verdict

#### Scenario: Task-compliance finds a gap

- **WHEN** the workflow's task-compliance review reports a violation before classification
- **THEN** the violation is repaired and the review runs again within the gate's bounded repair before classification

#### Scenario: Task-compliance stays red

- **WHEN** task-compliance violations remain after the bounded repair
- **THEN** the attempt's task-compliance result is `failed` with the unresolved violations
- **AND** the workflow continues to classification and finalization

#### Scenario: No verdict can be produced

- **WHEN** every task-compliance run the workflow makes ends without an executed review, for example with `no_applicable_gates` because the change touches only paths the declaring entry point excludes, or with a validator error
- **THEN** the attempt's task-compliance result is `not-run` with that status or error
- **AND** the workflow continues to finalization and the pull request opens

#### Scenario: Verification adds commits

- **WHEN** simplify or an acceptance repair commits changes after implementation
- **THEN** before classification the workflow reviews the diff from the base to the current head, using the archived tasks
- **AND** the attempt's task-compliance result names the reviewed head

#### Scenario: A resume merges the target branch

- **WHEN** a resumed attempt merges the target branch's head into the claim's branch and continues at verification or finalization
- **THEN** the current attempt has no earlier verdict, and the workflow reviews the diff from the new merge base to the head before classification
- **AND** changes the merge brought in from the target branch are not part of the reviewed diff

#### Scenario: Resume after implementation

- **WHEN** an attempt resumes at archive, verification, or finalization and so skips implementation
- **THEN** the workflow determines the base from the claim's head and the target branch head again and still produces a task-compliance result before classification

#### Scenario: The validator cleans its logs after a pass

- **WHEN** the workflow's task-compliance review passes and the validator moves or deletes its review logs as part of cleaning up after a passing run
- **THEN** the attempt's task-compliance result is `passed`, not `not-run`

#### Scenario: Task-compliance is declared only under a subdirectory

- **WHEN** the target declares task-compliance only on an entry point under `packages/app`, and the change touches files both there and under `packages/lib`
- **THEN** the attempt's task-compliance result is `not-run`, naming the `packages/lib` files the review did not see

#### Scenario: A resume merged a newer target head

- **WHEN** a resumed attempt merged a target head newer than the one recorded at admission
- **THEN** the reviewed diff starts at the merge base with the merged head, and contains no change that exists only on the target branch

#### Scenario: The tasks changed after the review

- **WHEN** a recorded verdict's tasks hash differs from the archived change's tasks
- **THEN** the workflow does not use that verdict and reviews again with the archived tasks

#### Scenario: The target does not declare task-compliance

- **WHEN** the target's `.validator/config.yml` declares no review named `task-compliance`
- **THEN** the workflow runs no task-compliance review and records `not-declared`

### Requirement: Qualify the validator status in the feature outcome

The `feature-outcome.json` the workflow writes SHALL NOT report a validator pass for a change whose declared task-compliance review did not pass. For every outcome that carries a validator status, `validator.checks` SHALL hold the status of the validator gates other than the workflow's task-compliance gate, and `validator.status` SHALL be:

- `passed` when the checks passed and the task-compliance result is `passed` or `not-declared`;
- `incomplete` when the checks passed and the task-compliance result is `not-run`;
- `review-failed` when the checks passed and the task-compliance result is `failed`;
- `failed` when the checks failed, as before.

An outcome written after the task-compliance gate SHALL also carry a `task_compliance` object with the result, the reason for `not-run`, the base, the reviewed head, and the tasks hash. When the checks passed but no task-compliance result was recorded, the outcome SHALL carry a `not-run` result with reason `no task-compliance record`, and `validator.status` SHALL be `incomplete`. The outcome value SHALL be unchanged by the task-compliance result: an attempt that produces a pull request with CI passing SHALL return `pull-request` whatever the task-compliance result is. The contract version SHALL remain `factory-feature/1`, and outcomes written before the gate runs, such as definition stops, SHALL keep their current shape.

#### Scenario: Skipped task-compliance is not a pass

- **WHEN** an attempt returns `pull-request` with every check passing and a task-compliance result of `not-run`
- **THEN** the outcome's `validator.status` is `incomplete`, `validator.checks` is `passed`, and `task_compliance` names the reason

#### Scenario: Task-compliance stays red in the outcome

- **WHEN** an attempt returns `pull-request` with a task-compliance result of `failed`
- **THEN** the outcome's `validator.status` is `review-failed` and the outcome is still `pull-request`

#### Scenario: Every gate passes

- **WHEN** the checks and the task-compliance review pass
- **THEN** the outcome's `validator.status` and `validator.checks` are both `passed` and `task_compliance` names the reviewed head

#### Scenario: The task-compliance record is missing

- **WHEN** the checks passed and the attempt reaches its outcome with no recorded task-compliance result
- **THEN** the outcome's `validator.status` is `incomplete`, `task_compliance` records `not-run` with reason `no task-compliance record`, and the issue comment says that task-compliance did not run

#### Scenario: Undeclared task-compliance

- **WHEN** the target does not declare task-compliance and the checks pass
- **THEN** the outcome's `validator.status` is `passed` and `task_compliance` records `not-declared`

## MODIFIED Requirements

### Requirement: Classify review attention without blocking

After the plan commit the feature workflow SHALL NOT stop for human input. Whether or not acceptance completed, and whether or not assumption review left decision-bearing assumptions, the workflow SHALL continue to finalization. Before finalizing, it SHALL classify every item a reviewer may need to examine into exactly one tier, making items that share one root cause a single item in the highest tier any of them reaches:

- red: an acceptance criterion that failed, or that could not be verified and that no automated test covers, acceptance that did not complete, a validator that stayed red after an acceptance fix, any known deviation from the specifications or from a decision the issue settled, a resume or continuation that fell back to a fresh start, and a declared task-compliance review that did not run or that stayed red after its bounded repair;
- orange: decision-bearing assumptions, which settle a choice the issue left open in a way that changes scope, weakens a guarantee, affects other callers, or is costly to reverse; plan revisions made in response to human comments; and commits added after acceptance ran, including those the finalization loop adds after classification, which acceptance evidence does not cover, where the classification's item for commits it saw states their diff size and whether tests cover them, and the item names which of those commits come after the head the last task-compliance verdict reviewed and states that the verdict does not cover them;
- yellow: an acceptance criterion that acceptance did not exercise but that a named automated test covers, naming that test; an item that only follows a decision the issue settled or an acceptance criterion, which SHALL NOT be ranked higher; a target that does not declare a task-compliance review; and every other recorded assumption or decision;
- white: acceptance criteria that passed, with their evidence, and a task-compliance review that passed, naming the head it reviewed.

Related orange assumptions SHALL be grouped into one item per topic that cites the decisions behind it, and orange items SHALL be ordered most important first. Each item SHALL cite the committed file, and the line or range within it where one applies, or the evidence that shows it. The task-compliance items SHALL be added from the attempt's recorded task-compliance result, not from agent judgment, so the classifying agent cannot omit or re-tier them. The classification SHALL be recorded in the attempt's evidence and used by `factory-feature-reporting`. After finalization, the workflow SHALL add an orange item for any commits made after acceptance that the classification did not cover, and the tier counts in the outcome SHALL be those of the final classification.

#### Scenario: Acceptance does not converge

- **WHEN** acceptance preparation ends without completing
- **THEN** the workflow continues to finalization and classifies the unmet criteria and the incomplete acceptance as red

#### Scenario: Validator stays red after an acceptance fix

- **WHEN** the validator remains red after its bounded repair in an acceptance round
- **THEN** acceptance and finalization continue, and the pull request lists the red validator, with its failing checks, as a red item

#### Scenario: An automated test covers a criterion acceptance did not exercise

- **WHEN** acceptance did not exercise an acceptance criterion but a named automated test covers it
- **THEN** the workflow classifies the criterion as yellow, naming the covering test, rather than red

#### Scenario: The change deviates from a settled issue decision

- **WHEN** the implementation departs from a decision the issue settled
- **THEN** the workflow classifies the deviation as red

#### Scenario: An item only follows a settled decision

- **WHEN** an assumption or choice only restates a decision the issue settled or an acceptance criterion
- **THEN** the workflow classifies it as yellow at most

#### Scenario: Several failures share one root cause

- **WHEN** several failing criteria or findings come from one root cause
- **THEN** the workflow records them as one item in the highest tier any of them reaches, listing each symptom

#### Scenario: Commits follow acceptance

- **WHEN** commits were added after the accepted head
- **THEN** the orange item naming them states their diff size and whether tests cover them, and the workflow's classification check rejects an item that omits either

#### Scenario: A decision-bearing assumption remains

- **WHEN** assumption review leaves a decision-bearing assumption unresolved
- **THEN** the workflow continues to finalization and classifies the assumption as orange

#### Scenario: Everything passes

- **WHEN** every acceptance criterion passes and no assumption is decision-bearing
- **THEN** the red and orange tiers are empty, the assumptions are yellow, and the criteria are white

#### Scenario: Task-compliance did not run

- **WHEN** the target declares task-compliance and the attempt's task-compliance result is `not-run`
- **THEN** the classification has a red item naming the reason, such as `Trusted`, `no_applicable_gates`, `no_changes`, or a validator error
- **AND** the workflow continues to finalization

#### Scenario: Task-compliance stays red

- **WHEN** the attempt's task-compliance result is `failed` after its bounded repair
- **THEN** the classification has a red item listing the unresolved task-compliance violations
- **AND** the workflow continues to finalization

#### Scenario: The classifying agent omits the task-compliance item

- **WHEN** the agent's classification leaves out a `not-run` or `failed` task-compliance result, or ranks it below red
- **THEN** the classification used for reporting still lists it as red, exactly once

#### Scenario: Commits follow the last task-compliance review

- **WHEN** finalization adds commits after the head the last task-compliance verdict reviewed
- **THEN** the orange item naming commits after acceptance names those commits and states that the task-compliance verdict does not cover them

#### Scenario: Task-compliance is not declared

- **WHEN** the target's validator configuration declares no task-compliance review
- **THEN** the classification has a yellow item stating that task-compliance is not declared and did not run, and no red task-compliance item

