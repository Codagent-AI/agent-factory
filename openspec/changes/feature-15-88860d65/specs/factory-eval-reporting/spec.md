## MODIFIED Requirements

### Requirement: Provide executable human-review instructions

Each repetition reported by the suite as ready for human review without a product failure SHALL receive its own copyable review command in its completion comment.

- For `and-scene`, the command SHALL invoke the retained suite's `human-review.sh` with `--run-dir` pointing to that repetition's actual artifact directory.
- When the pinned script supports `--no-publish`, the command SHALL pass it. The factory saves results itself, and the suite's own publication cannot succeed from the pinned detached worktree.
- Script and artifact paths SHALL be absolute and safely quoted, with no placeholders for the user to fill in.

The comment SHALL state:

- that the review is optional;
- that the command runs on the Mac mini holding those files;
- how long the command remains usable: until the item moves to Done, or until the configured settled retention period (stated in days) has passed since the request settled with its card outside Done, whichever comes first. Either event, reviewed or not, removes its suite worktree and makes its evidence eligible for pruning.

When idle release, as defined in `factory-operations`, removes the suite worktree of a request that received at least one human-review command while its card was outside Done, the factory SHALL record an issue event. The event SHALL state that the optional human-review window has ended and the retained worktree has been released. The factory SHALL record that event once per claim. It SHALL NOT record it when the item reached Done first, or when the claim's card is no longer on the board, since an event can only be delivered through the card.

Failed or incomplete repetitions SHALL receive explanations rather than commands presenting them as ready for human review. A repetition that is ready SHALL receive its command even when another repetition in the same request failed. The factory SHALL NOT perform human review, invent human ratings, or assign an official pass.

#### Scenario: Finish a repetition ready for review

- **WHEN** the suite finishes a repetition ready for human review
- **THEN** the factory posts its available automated score and a complete command for reviewing that exact repetition on the Mac mini
- **AND** the comment states that the command remains usable until the item moves to Done or until the settled retention period, in days, has passed since the request settled, whichever comes first

#### Scenario: Include reviewable work in a failed request

- **WHEN** one repetition is ready for human review and another establishes a product failure
- **THEN** the reviewable repetition still receives its own review command
- **AND** the failed repetition does not

#### Scenario: Let the review window lapse

- **WHEN** a settled eval request that received a human-review command stays in Review past the settled retention period and idle release removes its suite worktree
- **THEN** the factory records one issue event stating that the optional human-review window has ended and the retained worktree was released
- **AND** later polls do not repeat the event

#### Scenario: Finish review by moving to Done

- **WHEN** a settled eval request that received a human-review command moves to Done before the settled retention period elapses
- **THEN** its worktree is released by the Done cleanup and no review-window event is recorded

#### Scenario: Release an off-board request without an event

- **WHEN** idle release removes the suite worktree of a settled eval request whose card is no longer on the board
- **THEN** no review-window event is recorded, so the claim's evidence is not held by an undeliverable event
