## MODIFIED Requirements

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

## ADDED Requirements

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
the card's status or Verdict and SHALL NOT close the issue. No expiry comment SHALL be
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
