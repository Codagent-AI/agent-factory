## MODIFIED Requirements

### Requirement: Classify review attention without blocking

After the plan commit the feature workflow SHALL NOT stop for human input. Whether or not acceptance completed, and whether or not assumption review left decision-bearing assumptions, the workflow SHALL continue to finalization. After finalization ends, whether CI passed or stayed red after the bounded loop, and before annotating the pull request, it SHALL classify every item a reviewer may need to examine into exactly one tier, making items that share one root cause a single item in the highest tier any of them reaches. The classification SHALL observe the branch, the pull request, and CI as finalization left them, so the published items never describe the branch state from before finalization:

- red: an acceptance criterion that failed, or that could not be verified and that no automated test covers, acceptance that did not complete, a validator that stayed red after an acceptance fix, any known deviation from the specifications or from a decision the issue settled, a resume or continuation that fell back to a fresh start, and a declared task-compliance review that did not run or that stayed red after its bounded repair;
- orange: decision-bearing assumptions, which settle a choice the issue left open in a way that changes scope, weakens a guarantee, affects other callers, or is costly to reverse; plan revisions made in response to human comments; and commits added after acceptance ran, including those the finalization loop added, which acceptance evidence does not cover. The classification's item for those commits SHALL state their diff size and whether tests cover them, SHALL name which of them come after the head the last task-compliance verdict reviewed, and SHALL state that the verdict does not cover them;
- yellow: an acceptance criterion that acceptance did not exercise but that a named automated test covers, naming that test; an item that only follows a decision the issue settled or an acceptance criterion, which SHALL NOT be ranked higher; a target that does not declare a task-compliance review; and every other recorded assumption or decision;
- white: acceptance criteria that passed, with their evidence, and a task-compliance review that passed, naming the head it reviewed.

Related orange assumptions SHALL be grouped into one item per topic that cites the decisions behind it, and orange items SHALL be ordered most important first. Each item SHALL cite the committed file, and the line or range within it where one applies, or the evidence that shows it. The task-compliance items SHALL be added from the attempt's recorded task-compliance result, not from agent judgment, so the classifying agent cannot omit or re-tier them. The classification SHALL be recorded in the attempt's evidence and used by `factory-feature-reporting`. When annotating, the workflow SHALL add an orange item for any commits made after acceptance that the classification did not cover, and the tier counts in the outcome SHALL be those of the final classification.

#### Scenario: Acceptance does not converge

- **WHEN** acceptance preparation ends without completing
- **THEN** the workflow continues to finalization and then classifies the unmet criteria and the incomplete acceptance as red

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

#### Scenario: Classification follows finalization

- **WHEN** commits made after acceptance are pushed by finalization and CI passes on the resulting head
- **THEN** classification runs only after finalization has pushed them and CI has settled, so the published pull request description does not describe those commits as unpushed or as lacking a CI run

#### Scenario: Finalization adds CI-fix commits

- **WHEN** the finalization loop adds commits to fix CI
- **THEN** the classification's orange item for commits after acceptance names those commits with the diff size and a `Tests:` statement covering them, rather than relying only on the annotation's later-commit addition

#### Scenario: CI stays red after finalization

- **WHEN** CI remains red after the finalization loop and the validator had passed
- **THEN** the workflow still classifies review attention and annotates the pull request, and the outcome is `failed` with the failing checks

#### Scenario: A decision-bearing assumption remains

- **WHEN** assumption review leaves a decision-bearing assumption unresolved
- **THEN** the workflow continues to finalization and then classifies the assumption as orange

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

### Requirement: Finalize the feature pull request

After verification and the task-compliance gate, the feature workflow SHALL reuse the Runner's generic finalization workflow to mark the pull request ready, wait for CI, and address failures within its bounded loop. Review-attention classification and pull request annotation SHALL follow finalization. CI that remains red after the loop SHALL return `failed` with reasons while leaving the pull request open. The pull request SHALL reference the issue with a closing keyword, so GitHub links the pull request to the issue and closes the issue when the pull request merges, and SHALL identify the factory claim in a stable marker. The workflow SHALL return `pull-request` with the pull request reference when CI passes and classification and annotation succeed.

#### Scenario: Finalize a passing pull request

- **WHEN** finalization completes with CI passing and classification and annotation succeed
- **THEN** the pull request is ready for review and the workflow returns `pull-request`, whatever the red and orange tiers contain

#### Scenario: Fail CI after the bounded loop

- **WHEN** CI remains red after the finalization loop
- **THEN** the workflow returns `failed` with the failing checks and leaves the pull request open

## ADDED Requirements

### Requirement: Record a durable outcome when classification fails after finalization

Because classification follows finalization, the pull request is already ready for review when classification runs. When the classifying session fails, when the classification check still rejects the classification after its repair, or when the classification record is missing, truncated, not valid JSON, or structurally invalid, the feature workflow SHALL NOT end the attempt without an outcome. It SHALL keep the rejected classification, when one exists, in the attempt's evidence, and SHALL say in the reason when it could not be kept; SHALL NOT annotate the pull request from it; and SHALL record a `failed` outcome that carries the pull request reference, the CI status, and a reason naming the classification failure. When CI also stayed red, the reasons SHALL also name the CI failure. The outcome SHALL NOT carry tier counts taken from an unverified classification, and SHALL pass the workflow's outcome verification. The ready pull request without review-attention annotation is an accepted failed hand-off: the failure is reported through the issue comment and card update that `factory-feature-reporting` defines for a `failed` outcome, and the workflow SHALL NOT post a separate notice on the pull request.

#### Scenario: The classifying session fails

- **WHEN** finalization completes and the classifying session fails before writing a classification
- **THEN** the attempt records a `failed` outcome with the pull request reference, the CI status, and a reason naming the classification failure, and no tier counts
- **AND** the pull request description is not annotated with review-attention items

#### Scenario: The classification stays invalid after repair

- **WHEN** the classification check rejects the classification and its repair does not correct it
- **THEN** the rejected classification is kept in the attempt's evidence, the pull request is not annotated from it, and the attempt records a `failed` outcome naming the classification failure

#### Scenario: The classification record is malformed

- **WHEN** the classification record left after classification is truncated, not valid JSON, or not a JSON object
- **THEN** the attempt still writes a `failed` outcome that passes outcome verification, rather than ending without an outcome

#### Scenario: Classification and CI both fail

- **WHEN** CI remains red after the finalization loop and classification also fails
- **THEN** the `failed` outcome's reasons name both the CI failure and the classification failure, and its CI status is `failed`

#### Scenario: The failure is reported on the issue

- **WHEN** an attempt records a `failed` outcome because classification failed after finalization
- **THEN** the issue comment states the classification failure reason and links the open pull request, and the card moves to Review with `Verdict=failed`
