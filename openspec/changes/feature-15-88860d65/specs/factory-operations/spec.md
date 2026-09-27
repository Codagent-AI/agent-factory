## MODIFIED Requirements

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done, the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees. For a fix or feature, it SHALL instead remove the per-attempt clones and, for sandbox attempts, the run-specific images. It SHALL preserve results, logs, SQLite history, candidate branches, PRs, and mirrors, subject to the evidence retention requirement. Worktrees and clones SHALL remain available while work is running, waiting, or blocked. They SHALL also remain available while a settled item is in Review, until the settled retention period defined below has elapsed. Reconciliation of verified running work dragged to Done SHALL restore Running before cleanup is considered; that edit SHALL NOT remove worktrees.

A fix or feature claim cancelled by issue closure SHALL have its clones, run-specific images, and credential copies released on the first poll after its execution has stopped, whatever its card's status. A cancelled card may never pass through Review, so this release SHALL NOT wait for it. The claim's evidence SHALL remain subject to the retention requirement.

The factory SHALL also release a terminal claim that has been idle for long enough, even when its card never reaches Done.

- **Idle clock.** The factory SHALL durably record the first time it observes a claim in a terminal lifecycle: settled, cancelled, or superseded. Observing the claim in any non-terminal lifecycle SHALL clear that record. It SHALL also clear the claim's recorded cleanup completion and pruning completion, so that anything a later attempt prepares, such as a review round's fresh clones and credential copies, is released and pruned again when the claim next becomes terminal.
- **Idle periods.** Two configurable periods SHALL govern idle release:
  - the abandoned retention period, `[limits] abandoned_retention_days`, default 3 days, applies to cancelled and superseded claims;
  - the settled retention period, `[limits] settled_retention_days`, default 14 days, applies to settled claims.
- **Cancelled and superseded claims.** Once the abandoned retention period has elapsed since the terminal observation, the factory SHALL release the claim's recorded factory-owned worktrees, clones, run-specific images, and credential copies, whatever its card's status. This covers cancelled eval claims and every superseded claim, which are otherwise never released.
- **Settled claims.** Once the settled retention period has elapsed since the terminal observation, the factory SHALL release a settled claim's recorded factory-owned worktrees, clones, run-specific images, and credential copies when its card is in any status other than Done or is no longer on the board. The same idle release SHALL apply to a settled claim whose card is Done when the Done cleanup has not released its workspace, for example because its card was never observed in Review. The evidence of such a claim then follows the Done path.
- **Safety guards.** Idle release SHALL NOT occur while any of the following holds:
  - any run of the claim is non-terminal or of unverified ownership;
  - the claim still has a recorded Fly Machine;
  - the claim has an active stop for a quota hold.

The factory SHALL apply cleanup to every terminal claim in its store after each successful poll, including a claim whose card is no longer on the board. A failed or partial board read SHALL NOT cause any claim to be treated as off the board. The factory SHALL bound the removal, pruning, registry, and measurement work each poll does across all claims, whether their card is on the board or not. It SHALL still record observations for every claim, and SHALL continue the remaining work on later polls.

The factory SHALL persist cleanup progress and failures, retry incomplete cleanup on later polls, and continue processing other jobs. Repeated cleanup and controller restarts SHALL tolerate already-removed owned worktrees. Cleanup SHALL operate only on recorded factory-owned worktrees, clones, and images. It SHALL NOT remove shared source checkouts, the operator's working clones, or another item's worktrees.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned worktrees or clones and any run-specific images are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, or blocked, including an active item incorrectly dragged to Done
- **THEN** its worktrees or clones remain available for execution, recovery, and human judging, however long the item has existed

#### Scenario: Preserve a settled item in Review within the settled period

- **WHEN** a settled item has been in Review for less than the settled retention period since the factory first observed it settled
- **THEN** its worktrees or clones remain available

#### Scenario: Release a settled item left in Review

- **WHEN** a settled eval claim's card has stayed in Review for longer than the settled retention period since the factory first observed it settled, and none of its runs is non-terminal
- **THEN** the next successful poll removes its factory-owned worktrees and run-specific images without the card moving to Done
- **AND** results, SQLite history, candidate branches, and PRs remain available

#### Scenario: Release a cancelled fix claim

- **WHEN** a fix claim's issue is closed while an attempt is running and the attempt has since stopped
- **THEN** the next poll removes its clones, run-specific images, and credential copies without waiting for Review or Done
- **AND** its evidence remains until the retention rule removes it
- **AND** a contradictory Done edit follows the existing Running correction policy

#### Scenario: Release a cancelled eval claim

- **WHEN** an eval claim was cancelled more than the abandoned retention period ago, its runs have stopped, and it has no recorded Fly Machine
- **THEN** the next successful poll removes its factory-owned Runner, Skills, and evals worktrees and any run-specific images, whatever its card's status

#### Scenario: Release a superseded claim

- **WHEN** a claim was superseded by a fresh claim more than the abandoned retention period ago and none of its runs is non-terminal
- **THEN** the next successful poll removes the superseded claim's recorded worktrees or clones, run-specific images, and credential copies
- **AND** the worktrees and clones of the claim that superseded it are untouched

#### Scenario: Release a claim whose card left the board

- **WHEN** a cancelled claim's card has been removed from the project board and the abandoned retention period has elapsed since its terminal observation
- **THEN** a successful poll removes its recorded worktrees, clones, run-specific images, and credential copies

#### Scenario: Do not act on a failed board read

- **WHEN** a poll fails or returns an incomplete board
- **THEN** no claim is treated as off the board on that poll and no idle release happens because of the missing cards

#### Scenario: Reset the idle clock on reactivation

- **WHEN** a settled fix claim becomes active again for a review round before the settled retention period has elapsed
- **THEN** its terminal observation is cleared and idle release does not occur while it is active
- **AND** the period starts again from the next time the factory observes it settled

#### Scenario: Release clones of a review round that followed an idle release

- **WHEN** a settled fix claim was idle-released, a review comment then starts a review round with fresh clones and credential copies, and the round settles
- **THEN** after the settled retention period from that new settlement, idle release removes the round's clones and credential copies
- **AND** the round's attempt evidence is pruned under the retention rule rather than being treated as already pruned

#### Scenario: Release a settled claim in Done that was never seen in Review

- **WHEN** a settled fix claim's card is Done, the factory never observed it in Review, and the settled retention period has elapsed since its terminal observation
- **THEN** the next successful poll releases its clones, run-specific images, and credential copies
- **AND** its evidence is pruned once the Done path's retention period has elapsed

#### Scenario: Spread a cleanup backlog across polls

- **WHEN** more terminal claims become eligible on one poll than the per-poll cleanup bound allows, whether their cards are on the board or not
- **THEN** that poll releases or prunes only up to the bound and later polls continue until the backlog is done
- **AND** every claim's observations are still recorded on each poll

#### Scenario: Keep a claim with a Fly Machine

- **WHEN** a terminal eval claim's idle period has elapsed but a Fly Machine is still recorded for it
- **THEN** its worktrees remain until the Machine record is gone

#### Scenario: Retry incomplete cleanup

- **WHEN** removal of a reviewed Done item's or an idle terminal claim's worktrees fails, or the controller restarts partway through cleanup
- **THEN** the factory records the remaining cleanup and retries on later polls without blocking other jobs
- **AND** already-removed worktrees do not cause a new failure or affect retained evidence

### Requirement: Retain evidence for a bounded period

The factory SHALL prune the attempt evidence of terminal claims (settled, cancelled, or superseded) once one of two retention paths is satisfied. It SHALL prune as soon as either path is satisfied.

**The Done path.** A configurable evidence retention period, default 14 days, SHALL apply. The Done path SHALL be satisfied only when:

- the claim's card is currently Done, established from the board observation of the same poll that prunes;
- the evidence retention period has elapsed since the factory first durably recorded that Done observation.

A card observed in any other state SHALL reset the recorded Done observation.

**The idle path.** The idle path SHALL be satisfied only when:

- the claim is cancelled or superseded and the abandoned retention period has elapsed since its terminal observation; or
- the claim is settled, its card is in a status other than Done or is no longer on the board, and the settled retention period has elapsed since its terminal observation.

Both periods and the terminal observation are defined in "Clean up worktrees after review". A settled claim whose card is currently Done SHALL be judged only by the Done path, so moving a card to Done always grants the full evidence retention period from that observation to whatever evidence remains.

**Conditions on both paths.** On either path, pruning SHALL also require all of the following:

- no run of the claim is non-terminal or of unverified ownership;
- no Fly Machine is recorded for the claim;
- all of the claim's reporting has been delivered;
- any post-merge sync the claim requires has completed;
- when an eval results repository is configured, every repetition of the claim that qualifies for capture has its current curated files committed there;
- for a settled claim, its worktree, clone, image, and credential cleanup has settled.

A cancelled claim's workspace is released at cancellation or by idle release, rather than at Done. A superseded claim's workspace is released only by idle release. For cancelled and superseded claims, the cleanup condition SHALL be that any release due under "Clean up worktrees after review" has settled.

**What pruning removes.** Pruning SHALL remove:

- logs, Runner session state, agent session state, and agent output under the attempt's artifact directory;
- for an eval repetition, its candidate worktree, the candidate checkout the suite built and the human-review command serves;
- for a host attempt, its recorded Runner session directory.

**What pruning keeps.** Pruning SHALL keep the fix or feature outcome, the eval result and provenance records, and the attempt's issue input. It SHALL NOT touch candidate branches, PRs, mirrors, SQLite history, the eval results repository, or the operator's working clones. It SHALL remove only enumerated evidence paths and SHALL keep any file or directory it does not recognise.

**Recording, retry, and sweeping.** The factory SHALL record what it removed and any failures, and SHALL retry failed pruning on later polls. It SHALL judge every terminal claim in its store for pruning after each successful poll, including claims whose card is no longer on the board, so that evidence predating this rule is covered. A failed or partial board read SHALL NOT establish that a card is off the board or not Done.

#### Scenario: Prune after the retention period

- **WHEN** a claim's card was observed Done more than 14 days ago under the default retention and nothing still needs its evidence
- **THEN** the next tick removes its logs, session state, agent output, and any eval candidate worktrees
- **AND** its outcome or result records, issue input, candidate branches, PRs, and committed eval results remain

#### Scenario: Skip a Done claim with a pending sync

- **WHEN** a fix claim's card is Done but its post-merge sync has not completed
- **THEN** its evidence is not pruned however old the claim is

#### Scenario: Prune a cancelled claim that recorded a PR

- **WHEN** a cancelled fix claim recorded a PR and the abandoned retention period has elapsed since the factory first observed it cancelled
- **THEN** its evidence is pruned without waiting on a post-merge sync, which only settled claims receive, and without waiting for its card to reach Done

#### Scenario: Prune a superseded claim outside Done

- **WHEN** a superseded eval claim's card now belongs to the fresh claim that replaced it and the abandoned retention period has elapsed since the factory first observed it superseded
- **THEN** the superseded claim's evidence is pruned, judged only on its own runs and reporting
- **AND** the replacing claim's evidence is untouched

#### Scenario: Prune a settled claim left in Review

- **WHEN** a settled eval claim's card has stayed in Review for longer than the settled retention period since the factory first observed it settled, and its results are committed to the configured results repository
- **THEN** its logs, session state, agent output, and candidate worktrees are pruned without the card moving to Done

#### Scenario: Keep evidence whose results are not yet captured

- **WHEN** an otherwise eligible eval claim has a repetition whose curated files have not yet been committed to the configured results repository
- **THEN** its evidence is not pruned until that commit succeeds

#### Scenario: Keep evidence of work that may resume

- **WHEN** a claim is active, waiting, or blocked, however old it is and whatever its card's status
- **THEN** none of its evidence is pruned

#### Scenario: Reach Done after a long time

- **WHEN** a settled claim whose evidence has not yet been pruned is moved to Done today
- **THEN** its remaining evidence is retained for the full evidence retention period from today's Done observation

#### Scenario: Sweep pre-existing history

- **WHEN** the factory first runs with this rule against a database of old terminal claims that have no recorded Done or terminal observation
- **THEN** it records those observations on that poll and prunes those claims only after the applicable period from that observation, never retroactively

#### Scenario: Prune a claim whose card left the board

- **WHEN** a superseded claim's card is no longer on the board and the abandoned retention period has elapsed since its terminal observation
- **THEN** a successful poll prunes its evidence
- **AND** a failed or partial board read prunes nothing because of missing cards

#### Scenario: Fail to prune

- **WHEN** removal of an eligible claim's evidence fails partway
- **THEN** the factory records the failure and remaining work and retries on later polls without blocking other jobs

### Requirement: Expose current operational status

`agent-factory status` SHALL show the following:

- per work kind, the slot holder and its progress;
- waiting work and why it waits;
- blocked fix and feature claims;
- settled fix and feature claims with eligible review comments waiting for their kind's slot;
- pending merge syncs and their last failure reason;
- the pause state;
- blocking conditions;
- the next permitted start time, when it can be determined.

For an eval attempt under Fly execution, status SHALL show:

- the Machine identity;
- the Machine's state, including whether it is stopped for a quota hold;
- the attempt's recorded deadline.

It SHALL also list Machines that reconciliation reported as unknown to the store, or as failed cleanup.

**Cleanup summary.** Status SHALL summarise terminal-claim cleanup in aggregate rather than claim by claim:

- the number of terminal claims whose workspace release, evidence pruning, or registry image deletion is still pending;
- an estimate of the local space held by those pending claims that have been measured, together with the number of pending claims not yet measured, so the figure is never presented as complete while some claims are unmeasured;
- the number of claims whose evidence has been pruned.

Each claim with a recorded cleanup, pruning, or registry image deletion failure SHALL be listed individually with its failure reason.

The factory's cycles SHALL measure the space estimate and keep it with each claim. `status` SHALL only read the recorded estimate and SHALL NOT walk the file system.

**Which claims are listed.** Status SHALL list only claims that are:

- running, waiting, blocked, or held;
- in Review;
- pending a merge sync;
- holding a recorded cleanup failure.

Claims whose card is Done with nothing pending, and superseded claims, SHALL be omitted unless `--all` is given, which lists every saved claim.

**What status must distinguish.** Status SHALL expose enough saved state to distinguish:

- active execution;
- an admission-window wait;
- a usage hold;
- a memory or disk hold;
- an unavailable prerequisite;
- a blocked claim;
- a waiting review round;
- unfinished reporting.

Status SHALL remain usable while execution is active. It SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite, memory, or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

#### Scenario: Inspect both slots

- **WHEN** an eval is running and a fix is blocked awaiting input with the `needs-input` label
- **THEN** status shows the eval slot's holder, the fix slot as free, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but its kind's slot is busy
- **THEN** status names the claim, the PR, and that it waits for the slot

#### Scenario: Inspect an installation with history

- **WHEN** the database holds many Done and superseded claims and one running claim
- **THEN** status lists the running claim and none of the settled ones
- **AND** `status --all` lists every saved claim

#### Scenario: Inspect a Fly attempt

- **WHEN** the operator requests status while an eval repetition runs in, or is stopped in, a Fly Machine
- **THEN** status shows the Machine identity, whether it is running or stopped for a quota hold, and the attempt's deadline

#### Scenario: Inspect a reconciliation finding

- **WHEN** reconciliation has reported a Machine unknown to the store or a cleanup that could not be verified
- **THEN** status lists that Machine and the reported reason until it is resolved

#### Scenario: Inspect a blocked feature

- **WHEN** a feature claim stopped during definition
- **THEN** status shows the feature slot as free and the blocked feature with its stop reason and pushed branch

#### Scenario: Inspect cleanup of many terminal claims

- **WHEN** dozens of terminal claims are awaiting or have completed cleanup and one of them has a recorded pruning failure
- **THEN** status shows the count of claims pending cleanup with an estimate of the space they hold and the count of pruned claims
- **AND** it lists only the failing claim individually, with its failure reason

#### Scenario: Show a partly measured cleanup backlog

- **WHEN** 12 terminal claims are pending cleanup and only 7 of them have a recorded space estimate
- **THEN** status shows the summed estimate of the 7 measured claims and states that 5 are not yet measured
