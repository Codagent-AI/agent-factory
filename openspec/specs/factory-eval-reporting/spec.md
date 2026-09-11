# factory-eval-reporting Specification

## Purpose
TBD - created by archiving change iteration-1. Update Purpose after archive.
## Requirements
### Requirement: Consume suite-established outcomes

The factory SHALL use suite result artifacts, including `result.json` for `and-scene`, as the authority for established execution and product outcomes. It SHALL preserve execution status separately from product verdict and SHALL NOT infer product quality from an exit code alone. A suite-established product failure SHALL remain visible even if a later technical error also occurs.

The integration SHALL use the separately delivered `and-scene` behavior that reports a completed automated subtotal below 40 out of 70 as a product failure before human review. The factory SHALL consume that verdict rather than calculate or enforce the threshold itself. Implementing that suite change is outside this change. A score of exactly 40 SHALL meet the automated subtotal minimum, subject to other suite requirements; missing or incomplete scoring SHALL NOT be treated as a below-threshold product result.

#### Scenario: Report an automated score below the minimum

- **WHEN** the suite reports a completed score of 39/70 with `product_verdict=fail` because it is below the automated minimum
- **THEN** the factory reports that repetition as failed with its score and the suite's reason
- **AND** it supplies no human-review command or technical recovery retry for that product failure
- **AND** remaining requested repetitions may continue under the admission controls

#### Scenario: Reach the minimum exactly

- **WHEN** the suite reports 40/70 and a result ready for human review
- **THEN** the factory reports the repetition as awaiting human review rather than failed or officially passed
- **AND** it supplies the human-review command

#### Scenario: Encounter incomplete scoring

- **WHEN** scoring cannot finish because of a technical problem and no product verdict is established
- **THEN** the factory reports the technical outcome and unavailable product verdict
- **AND** it does not turn missing scoring into a zero or below-threshold result

#### Scenario: Retain product evidence after a technical error

- **WHEN** a suite-established product failure is followed by a technical error
- **THEN** reporting preserves both the product failure and the technical error

### Requirement: Comment on meaningful factory activity

The factory SHALL post issue comments for meaningful request events: starting work, a technical failure and its retry, waiting for a usage reset, repetition completion, cancellation, and final handoff. Comments SHALL identify the affected repetition and attempt where relevant, explain what happened, and state what happens next. Routine polls and unchanged waiting states SHALL NOT produce repeated activity comments.

#### Scenario: Fail and retry a repetition

- **WHEN** a repetition fails technically and the factory starts its permitted recovery retry
- **THEN** issue activity identifies the failure, the affected repetition, and the recovery action

#### Scenario: Wait for a usage reset

- **WHEN** a usage limit defers unfinished work
- **THEN** a comment explains the reason and expected reset or next eligible start time
- **AND** unchanged subsequent polls do not repeat the same waiting comment

#### Scenario: Cancel a request

- **WHEN** the factory cancels execution after the user closes the issue
- **THEN** it records the cancellation in issue activity, including retained results and unstarted repetitions
- **AND** it respects the user's closed issue and Done card

### Requirement: Report traceable inputs and per-repetition results

The factory SHALL post the frozen evaluation inputs and report each repetition independently. Reporting SHALL include claim and suite run identities, suite identity, full Runner and Skills revisions, the deployed harness revision, accepted role and validator settings, repetition count, and relevant pinned evaluation inputs. The Project's `Refs` field SHALL display `runner@<7> skills@<7> evals@<7>`, using the first seven characters of each recorded commit SHA. The harness revision SHALL be identified as the test environment version.

For each repetition, results SHALL include execution status, established product verdict, available automated subtotal out of 70, duration, available costs, artifact location, and recorded candidate branch and draft-PR links. Unstarted, interrupted, completed, and failed repetitions SHALL remain distinguishable. Missing scores, costs, or other result data SHALL be described as unavailable rather than zero or fabricated values. Reporting SHALL NOT present absolute results as evidence of improvement over a baseline.

#### Scenario: Report mixed repetition outcomes

- **WHEN** one repetition is ready for human review, another has a product failure, and another remains unstarted
- **THEN** the issue shows each repetition's own status, available score, evidence location, and candidate links
- **AND** the aggregate board verdict does not hide those distinctions

#### Scenario: Omit unavailable cost data honestly

- **WHEN** a completed repetition lacks cost data
- **THEN** its result reports cost as unavailable while retaining its other available results

### Requirement: Provide executable human-review instructions

Each repetition reported by the suite as ready for human review without a product failure SHALL receive its own copyable review command in its completion comment. For `and-scene`, the command SHALL invoke the retained suite's `human-review.sh` with `--run-dir` pointing to that repetition's actual artifact directory. Script and artifact paths SHALL be absolute and safely quoted, with no placeholders for the user to fill in. The comment SHALL state that the command runs on the Mac mini holding those files and remains usable until the reviewed item moves to Done, which triggers removal of its suite worktree.

Failed or incomplete repetitions SHALL receive explanations rather than commands presenting them as ready for human review. A repetition that is ready SHALL receive its command even when another repetition in the same request failed. The factory SHALL NOT perform human review, invent human ratings, or assign an official pass.

#### Scenario: Finish a repetition ready for review

- **WHEN** the suite finishes a repetition ready for human review
- **THEN** the factory posts its available automated score and a complete command for reviewing that exact repetition on the Mac mini

#### Scenario: Include reviewable work in a failed request

- **WHEN** one repetition is ready for human review and another establishes a product failure
- **THEN** the reviewable repetition still receives its own review command
- **AND** the failed repetition does not

### Requirement: Apply aggregate board verdicts without hiding partial results

When all requested repetitions complete their automated evaluation or settle with a confirmed non-resumable implementation-workflow failure, the factory SHALL move the issue to Review. If any repetition has a suite-established product failure or a confirmed non-resumable implementation-workflow failure, the aggregate Verdict SHALL be `failed`; otherwise it SHALL be `pending-human-review`. The factory SHALL never assign `passed` in iteration 1 and SHALL NOT close the issue as part of automated completion.

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

#### Scenario: Stop after exhausting technical recovery

- **WHEN** a technical recovery retry fails before all repetitions finish
- **THEN** the card moves to Review with `infra-error`
- **AND** the comment preserves completed results, any established product failures, and the list of repetitions left unstarted

#### Scenario: Defer unfinished work automatically

- **WHEN** a quota or recoverable infrastructure interruption defers an unfinished claim
- **THEN** the card returns to Ready with the corresponding deferral verdict
- **AND** the factory preserves the claim's frozen inputs, completed results, and retry history

### Requirement: Deliver reports durably without duplicates

The factory SHALL persist per-claim reporting progress for comments and Project-field updates, including completion flags, stable report identifiers, and returned comment IDs. Activity identifiers SHALL distinguish repetitions and attempts where necessary. If a post succeeds but its response is lost, the factory SHALL discover the existing comment rather than create another copy. Pending delivery SHALL survive controller restarts and SHALL NOT cause completed evaluation work to run again.

When repairing reporting, the factory SHALL reconcile its saved delivery state with current GitHub state and preserve later human intent, including issue closure and subsequent field changes, subject to the explicit correction policy for Status edits that contradict factory execution in `factory-claim-lifecycle`. This requirement does not introduce a general-purpose outbox subsystem.

#### Scenario: Lose a successful comment response

- **WHEN** GitHub accepts a report comment but the factory does not receive the response
- **THEN** subsequent reconciliation finds the comment by its stable identifier and completes local reporting progress without duplicating the post

#### Scenario: Restart between comment and field delivery

- **WHEN** a result comment succeeds but a field update remains undelivered when the controller restarts
- **THEN** the factory recovers the missing field update without repeating the comment or evaluation
- **AND** it reconciles any newer human changes before writing

#### Scenario: Close the issue before pending delivery recovers

- **WHEN** the user closes an issue before a pending factory status update is repaired
- **THEN** the factory preserves the closure and does not move the card back to an active state while recovering reporting

