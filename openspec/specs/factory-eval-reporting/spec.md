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

The factory SHALL post the frozen evaluation inputs and report each repetition independently. Reporting SHALL include:

- claim and suite run identities;
- suite identity;
- full Runner and Skills revisions;
- the deployed harness revision;
- the Agent Validator revision;
- the requested fixture ref and the full fixture revision, when the claim recorded one;
- accepted role and validator settings;
- repetition count;
- relevant pinned evaluation inputs.

When the claim recorded a Validator revision, reporting SHALL give its full SHA. When it did not, because the claim was admitted under Docker execution or before the factory recorded Validator revisions, reporting SHALL state that the Validator was the published npm release and was not pinned.

When the claim recorded a fixture revision:

- the posted frozen inputs SHALL state, outside the raw input listing, the requested fixture ref and the full fixture SHA, and that the fixture was selected by the request instead of the harness's pin;
- each repetition's report SHALL give the full fixture SHA.

When the claim recorded no fixture revision, the posted frozen inputs and repetition reports SHALL be exactly as before this change and SHALL NOT add any fixture statement.

The Project's `Refs` field SHALL display `runner@<7> skills@<7> evals@<7>`, followed by ` validator@<7>` when the claim recorded a Validator revision, followed by ` fixture@<7>` when the claim recorded a fixture revision. Each `<7>` is the first seven characters of the recorded commit SHA. The harness revision SHALL be identified as the test environment version.

For each repetition, results SHALL include execution status, established product verdict, available automated subtotal out of 70, duration, available costs, artifact location, and recorded candidate branch and draft-PR links. Unstarted, interrupted, completed, and failed repetitions SHALL remain distinguishable. Missing scores, costs, or other result data SHALL be described as unavailable rather than zero or fabricated values. Reporting SHALL NOT present absolute results as evidence of improvement over a baseline.

#### Scenario: Report mixed repetition outcomes

- **WHEN** one repetition is ready for human review, another has a product failure, and another remains unstarted
- **THEN** the issue shows each repetition's own status, available score, evidence location, and candidate links
- **AND** the aggregate board verdict does not hide those distinctions

#### Scenario: Omit unavailable cost data honestly

- **WHEN** a completed repetition lacks cost data
- **THEN** its result reports cost as unavailable while retaining its other available results

#### Scenario: Report a pinned Validator

- **WHEN** a Fly eval claim recorded Validator revision `C`
- **THEN** the posted inputs give the full SHA `C`, and the `Refs` field contains `validator@` followed by the first seven characters of `C`

#### Scenario: Report an unpinned Validator

- **WHEN** a claim recorded no Validator revision and no fixture revision
- **THEN** the posted inputs state that the Validator was the published npm release and was not pinned, and the `Refs` field is `runner@<7> skills@<7> evals@<7>` as before this change

#### Scenario: Report a pinned fixture

- **WHEN** a Fly eval claim recorded Validator revision `C`, requested fixture ref `eval/fixture-sonnet-validator`, and fixture revision `b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a`
- **THEN** the posted inputs state the requested ref `eval/fixture-sonnet-validator` and the full SHA `b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a`, and that the request selected the fixture instead of the harness's pin
- **AND** the `Refs` field ends with `validator@<first seven of C> fixture@b83deca`
- **AND** each repetition report gives the full fixture SHA

#### Scenario: Report a pinned fixture without a Validator revision

- **WHEN** a Docker eval claim recorded fixture revision `F` and no Validator revision
- **THEN** the `Refs` field is `runner@<7> skills@<7> evals@<7> fixture@<7>`, using the first seven characters of `F`

#### Scenario: Report a default fixture

- **WHEN** a claim recorded no fixture revision
- **THEN** its posted inputs, repetition reports, and `Refs` field contain no fixture statement and are identical to what the factory reported before this change

### Requirement: Provide executable human-review instructions

Each repetition that the suite reports as ready for human review, without a product
failure, SHALL receive its own copyable review command in its completion comment. For
`and-scene`, the command SHALL invoke the retained suite's `human-review.sh` with
`--run-dir` pointing to that repetition's actual artifact directory. When the pinned script
supports `--no-publish`, the command SHALL pass it. The factory saves results itself, and
the suite's own publication cannot succeed from the pinned detached worktree. Script and
artifact paths SHALL be absolute and safely quoted, with no placeholders for the user to
fill in.

The comment SHALL state three things:

- the review is optional;
- the command runs on the Mac mini holding those files;
- the command remains usable until the item moves to Done, reviewed or not, or until the
  configured unreviewed retention period has passed since the request settled, whichever
  comes first. The comment SHALL state that period in days. Either event triggers removal
  of the suite worktree.

Failed or incomplete repetitions SHALL receive explanations rather than commands presenting
them as ready for human review. A repetition that is ready SHALL receive its command even
when another repetition in the same request failed. The factory SHALL NOT perform human
review, invent human ratings, or assign an official pass.

#### Scenario: Finish a repetition ready for review

- **WHEN** the suite finishes a repetition ready for human review
- **THEN** the factory posts its available automated score and a complete command for reviewing that exact repetition on the Mac mini
- **AND** the comment states that the command stops working when the item moves to Done or after the unreviewed retention period, in days

#### Scenario: Include reviewable work in a failed request

- **WHEN** one repetition is ready for human review and another establishes a product failure
- **THEN** the reviewable repetition still receives its own review command
- **AND** the failed repetition does not

### Requirement: Capture finished repetition results in the eval repository

When `eval.results_repository` is configured, the factory SHALL commit each consumed repetition whose suite result is ready for human review or a conclusive product result to that repository's configured results branch, whether or not it is ever human-reviewed. For `and-scene`, the commit SHALL contain only the suite's curated files (`result.json`, `report.html`, `ambiguity-ledger.json`, `implementation.diff`, `artifact-manifest.json`, and `human-review.json` once present) under `evals/agent-runner/and-scene/results/<run-id>/`, and SHALL NOT include logs, session state, or credentials. A repetition missing any required curated file SHALL NOT be committed as a partial record. Harness failures SHALL NOT be captured. The factory SHALL link each commit on the eval issue, SHALL commit a repetition again only when its curated files change, and SHALL report a failed commit on the issue once per snapshot and retry it on later cycles without blocking the cycle. The branch update SHALL be a fast-forward that never overwrites a concurrent push.

#### Scenario: Capture an unreviewed repetition

- **WHEN** a repetition's attempt is consumed as ready for human review and nobody reviews it
- **THEN** its curated files are committed to the results branch and the commit is linked on the eval issue

#### Scenario: Add a later human review

- **WHEN** a human review writes `human-review.json` into a captured repetition's run directory
- **THEN** a later cycle commits the updated snapshot including the review

#### Scenario: Results commit is refused

- **WHEN** the results commit fails, for example because the App lacks write access
- **THEN** the factory reports the failure on the eval issue once and retries on later cycles

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

### Requirement: Report the expiry of unreviewed human-review commands

A settled eval claim may have posted at least one human-review command. When such a claim
reaches the unreviewed retention period defined in `factory-operations` with its card not
observed as Done, the factory SHALL post one issue comment before releasing the claim's
worktrees. The comment SHALL say that:

- the request's human-review commands have expired;
- the suite worktree they use is being removed;
- the captured results and result records remain available, and it links them where they
  were captured;
- reviewing a fresh run requires a new request.

The comment SHALL be delivered durably and without duplicates, like the factory's other
reports. The worktree SHALL NOT be released until that delivery succeeds. A failed post
SHALL be retried on later polls and SHALL be shown by `status`. The expiry SHALL NOT change
the card's status or Verdict and SHALL NOT close the issue. An observed Done card SHALL
receive no expiry report, including when no Review observation was recorded or the
unreviewed retention period has already elapsed. No expiry comment SHALL be
posted for:

- an eval claim that posted no human-review command;
- an eval claim whose card is observed as Done before the period elapses;
- a cancelled or superseded eval claim.

#### Scenario: Expire an unreviewed eval

- **WHEN** a settled `pending-human-review` eval has stayed in Review for longer than the unreviewed retention period
- **THEN** the factory posts one expiry comment on the issue, then removes the evals, Runner, and Skills worktrees on the same or a later poll
- **AND** the card keeps its status and Verdict

#### Scenario: Fail to post the expiry

- **WHEN** posting the expiry comment fails
- **THEN** the worktrees are kept, status shows the undelivered expiry, and a later poll posts it once and then releases the worktrees

#### Scenario: Expire nothing for a failed eval

- **WHEN** a settled eval with Verdict `failed` and no reviewable repetition stays in Review past the unreviewed retention period
- **THEN** no expiry comment is posted and its worktrees are released under `factory-operations`

#### Scenario: Move to Done before expiry

- **WHEN** a reviewed eval's card moves to Done before the unreviewed retention period elapses
- **THEN** no expiry comment is posted and the existing Done cleanup applies

#### Scenario: Reach Done without a recorded Review observation

- **WHEN** a settled eval with a published human-review command is observed as Done without a recorded Review observation
- **THEN** no human-review expiry report is delivered and terminal release follows `factory-operations`

### Requirement: Report frozen inputs identically across releases

For every claim, this release's reporting SHALL be identical to what the previous release (the
release that introduced `fixture_ref`) reports for the same claim. This covers:

- the posted frozen-inputs comment, including the raw input listing, the Agent Validator line,
  and the fixture statement;
- each repetition's report, including the fixture line;
- the Project `Refs` field;
- the event recorded when a claim's saved revisions are missing or invalid.

This holds for claims without optional revisions, claims with a Validator revision, claims with a
fixture revision, claims with both, and claims admitted before the factory recorded Validator
revisions. Neither a deploy of this release nor a rollback from it SHALL change any of these for
an existing claim.

#### Scenario: Report a claim with both optional revisions

- **WHEN** a Fly eval claim recorded Validator revision `C`, requested fixture ref
  `eval/fixture-sonnet-validator`, and fixture revision `F`
- **THEN** its frozen-inputs comment, repetition reports, and `Refs` field
  (`runner@<7> skills@<7> evals@<7> validator@<7> fixture@<7>`) are byte-identical to those the
  previous release posts for the same claim

#### Scenario: Report a default claim

- **WHEN** a claim recorded no Validator revision and no fixture revision
- **THEN** its frozen-inputs comment states that the Validator was the published npm release and
  was not pinned, contains no fixture statement, and is byte-identical to the previous release's
  comment, and its `Refs` field is `runner@<7> skills@<7> evals@<7>`

#### Scenario: Report invalid saved revisions

- **WHEN** a claim's saved revisions lack a Runner, Skills, or harness revision, or record a
  Validator or fixture revision that is not a full commit SHA
- **THEN** no `Refs` text is produced, and the claim records the same invalid-revisions event,
  naming the same revisions in the same order, as the previous release records

