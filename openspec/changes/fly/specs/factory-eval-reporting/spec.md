## MODIFIED Requirements

### Requirement: Apply aggregate board verdicts without hiding partial results

When all requested repetitions complete their automated evaluation, settle with a confirmed non-resumable implementation-workflow failure, or settle as lost Machines under `factory-fly-execution`, the factory SHALL move the issue to Review. If any repetition has a suite-established product failure or a confirmed non-resumable implementation-workflow failure, the aggregate Verdict SHALL be `failed`; otherwise, when at least one repetition is reviewable, it SHALL be `pending-human-review`. Lost repetitions SHALL NOT influence the choice between `failed` and `pending-human-review`; the results comment SHALL list each lost repetition with its infrastructure reason and SHALL NOT present it as a product result. When every repetition is lost, the card SHALL move to Review with `infra-error` and each loss explained. The factory SHALL never assign `passed` in iteration 1 and SHALL NOT close the issue as part of automated completion.

If a technical failure exhausts its recovery retry, the lifecycle's stop behavior SHALL take precedence: move to Review with `infra-error`, preserving all completed results and explaining which repetitions remain unstarted. Any already-established product failures SHALL remain visible in the results comment. While unfinished work is automatically deferred, the card SHALL use Ready with the applicable `quota-deferred` or `infra-error` verdict so the existing claim can continue under the intake rules.

While a current claim has verified active execution, the card SHALL not retain a Verdict delivered for a superseded or earlier claim. The factory SHALL clear that stale Verdict while reconciling the active claim so the board does not present a concluded technical outcome as the status of running work.

#### Scenario: Complete without a product failure

- **WHEN** all repetitions finish ready for human review without a suite-established product failure
- **THEN** the card moves to Review with `pending-human-review`
- **AND** the issue remains open without an official pass assigned by the factory

#### Scenario: Complete with a failed repetition

- **WHEN** all repetitions finish and at least one has a suite-established product failure
- **THEN** the card moves to Review with `failed`
- **AND** the issue retains the separate results and review commands for any reviewable repetitions

#### Scenario: Complete with a non-resumable workflow failure

- **WHEN** all repetitions have settled and at least one has a suite-confirmed non-resumable implementation-workflow failure
- **THEN** the card moves to Review with `failed` and explains the workflow failure
- **AND** the report preserves the suite's separate product verdict, including unavailable, and any other repetition's review command

#### Scenario: Complete with a lost repetition

- **WHEN** one repetition settled as a lost Machine and the others finished ready for human review
- **THEN** the card moves to Review with `pending-human-review`
- **AND** the results comment lists the lost repetition with its infrastructure reason and carries review commands only for the surviving repetitions

#### Scenario: Lose every repetition

- **WHEN** every repetition of a claim settled as a lost Machine
- **THEN** the card moves to Review with `infra-error` and each loss is explained

#### Scenario: Stop after exhausting technical recovery

- **WHEN** a technical recovery retry fails before all repetitions finish
- **THEN** the card moves to Review with `infra-error`
- **AND** the comment preserves completed results, any established product failures, and the list of repetitions left unstarted
