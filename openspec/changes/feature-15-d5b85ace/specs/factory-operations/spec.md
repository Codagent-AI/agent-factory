## MODIFIED Requirements

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done,
the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees. For
a fix or feature it SHALL instead remove the per-attempt clones of every attempt of the
claim and, for sandbox attempts, run-specific images. It SHALL preserve results, logs,
SQLite history, candidate branches, PRs, and mirrors, subject to the evidence retention
requirement. Worktrees and clones SHALL remain available while work is running, waiting, or
blocked. They SHALL also remain available while a settled claim is in Review, until the
terminal release below applies. When verified running work is dragged to Done,
reconciliation SHALL restore Running before cleanup is considered, and that edit SHALL NOT
remove worktrees. Done cleanup SHALL also wait until all of the claim's reporting has been
delivered, so that a human-review command is never published after its worktree has been
removed.

**Terminal release.** The factory SHALL release a claim outside the Review-then-Done path
when it is quiescent: its lifecycle is `settled`, `cancelled`, or `superseded`, no run of
the claim is non-terminal or of unverified ownership, all of the claim's reporting has been
delivered, and, for a settled claim, no post-merge sync is pending. Releasing a claim SHALL
remove the same recorded factory-owned worktrees or clones, run-specific images, and
credential copies that Done cleanup removes. It SHALL happen as follows:

- A `cancelled` or `superseded` claim SHALL be released on the first poll on which it is
  quiescent, whatever its card's status and whether or not its card is still on the
  Project. Its card may never pass through Review or Done.
- A `settled` claim whose card is not observed as Done on the current poll, including a
  claim whose card is no longer on the Project, SHALL be released once the unreviewed
  retention period has elapsed since the claim's recorded terminal time. That period is the
  local setting `[limits] unreviewed_retention_days`, a positive integer that defaults to
  30. Before releasing a settled eval claim that posted a human-review command, the factory
  SHALL first deliver that claim's human-review expiry report, as defined in
  `factory-eval-reporting`.

In both cases the claim's evidence SHALL remain subject to the retention requirement.

**Pending post-merge sync.** For cleanup and retention, a settled pull-request claim has a
pending post-merge sync only when both hold:

- its recorded PR has been observed merged;
- its post-merge sync has not completed.

A PR that is still open, or that was closed without merging, leaves no sync pending for
cleanup or retention. If a merge happens later, the post-merge sync still runs as before.
When the PR's state cannot be read on a poll, the sync SHALL be treated as pending for that
poll. Terminal release and pruning SHALL NOT themselves run a post-merge sync. A claim whose
card is no longer on the Project, and whose merged PR has not been synced, therefore keeps
its files, and `status` continues to show it as pending a sync.

**Reopened cleanup.** When a new run starts on a claim whose cleanup had completed, for
example a review round on a settled pull-request claim, the factory SHALL mark that claim's
cleanup incomplete. The new run's clones, images, and credential copies SHALL then be
released under these same rules once the claim is quiescent again.

**Sweep and safety.** The factory SHALL evaluate Done cleanup and terminal release on every
tick. The terminal release SHALL cover every terminal claim in its store whose release is
incomplete, whether or not the Project query returns its card. The factory SHALL persist
cleanup progress and failures, retry incomplete cleanup on later polls, and continue
processing other jobs and claims. Repeated cleanup and controller restarts SHALL tolerate
already-removed owned worktrees, clones, images, and credential copies. Cleanup SHALL
operate only on recorded factory-owned worktrees, clones, images, and credential copies. It
SHALL NOT remove shared source checkouts, the operator's working clones, mirrors, or
another item's worktrees. A claim that is `active`, `waiting`, or `blocked` SHALL NOT be
released.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned worktrees or clones and any run-specific images are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, or blocked, including an active item incorrectly dragged to Done
- **THEN** its worktrees or clones remain available for execution and recovery, however long ago the claim was admitted

#### Scenario: Keep a settled claim in Review within the period

- **WHEN** a settled eval claim has been in Review for 20 days under the default unreviewed retention period
- **THEN** its evals worktree and retained artifacts remain and its human-review command still works

#### Scenario: Release a cancelled fix claim

- **WHEN** a fix claim's issue is closed while an attempt is running and the attempt has since stopped
- **THEN** the next poll removes its clones, run-specific images, and credential copies without waiting for Review or Done
- **AND** its evidence remains until the retention rule removes it
- **AND** a contradictory Done edit follows the existing Running correction policy

#### Scenario: Release a cancelled eval claim

- **WHEN** an eval claim is cancelled by issue closure and its last repetition has stopped and its cancellation comment has been delivered
- **THEN** the next poll removes its Runner, Skills, and evals worktrees whatever its card's status

#### Scenario: Release a superseded claim

- **WHEN** a writer re-requests a settled claim's issue, a fresh claim supersedes it, and the superseded claim has no non-terminal run
- **THEN** the next poll removes the superseded claim's recorded worktrees or clones, run-specific images, and credential copies
- **AND** the fresh claim's worktrees and clones are untouched

#### Scenario: Release a settled claim left in Review

- **WHEN** a settled fix claim with an open PR has been in Review for longer than 30 days since it settled under the default unreviewed retention period, and its reporting is delivered
- **THEN** the next poll removes the clones of every attempt, any run-specific images, and credential copies
- **AND** its PR, branch, and outcome record remain

#### Scenario: Keep a merged claim whose sync failed

- **WHEN** a settled fix claim's PR merged, its post-merge sync is blocked by uncommitted changes in the working clone, and it has been outside Done for longer than the unreviewed retention period
- **THEN** its clones and evidence are kept, whether or not its card is still on the Project, and status shows the pending sync
- **AND** once a later poll completes the sync for a card still on the Project, the claim is released and pruned under the same rules

#### Scenario: Release a claim whose PR was closed without merging

- **WHEN** a settled fix claim's PR was closed without merging and it has been outside Done for longer than the unreviewed retention period with its reporting delivered
- **THEN** its clones are released and its evidence is pruned without waiting for a sync

#### Scenario: Hold Done cleanup for an undelivered review command

- **WHEN** posting a settled eval's human-review command failed and its card is then moved to Done
- **THEN** its worktrees are kept until a later poll delivers the command, and they are removed on that poll or the next

#### Scenario: Wait for undelivered reporting

- **WHEN** a claim is otherwise eligible for terminal release but a report for it has not yet been delivered
- **THEN** nothing is released until that report is delivered on a later poll

#### Scenario: Release a claim whose card left the Project

- **WHEN** a cancelled claim's card has been removed from the Project
- **THEN** the tick still releases the claim's recorded worktrees or clones from the store's record of the claim

#### Scenario: Reopen cleanup for a review round

- **WHEN** a settled fix claim's clones were released after the unreviewed period and a writer then posts eligible review comments on its still-open PR
- **THEN** the review round runs in fresh clones
- **AND** once the round's run ends and the claim is quiescent, those new clones are released under the same rules

#### Scenario: Retry incomplete cleanup

- **WHEN** removal of a reviewed Done item's or a terminal claim's worktrees fails, or the controller restarts partway through cleanup
- **THEN** the factory records the remaining cleanup and retries on later polls without blocking other jobs
- **AND** already-removed worktrees do not cause a new failure or affect retained evidence

### Requirement: Retain evidence for a bounded period

The factory SHALL prune the attempt evidence of terminal claims after configurable retention
periods. The retention period is `[limits] evidence_retention_days`, default 14 days. The
unreviewed retention period is `[limits] unreviewed_retention_days`, default 30 days. Both
periods SHALL be local configuration.

A claim's evidence SHALL be eligible for pruning only when all of these hold:

- no run of the claim is non-terminal or of unverified ownership;
- all of the claim's reporting has been delivered;
- for a settled claim, no post-merge sync is pending, as defined in "Clean up worktrees after
  review";
- one of these paths applies:
  - **Done path.** The claim is settled and its card is observed as Done on the poll that
    prunes. The retention period has elapsed since the factory first durably recorded that
    Done observation, and the claim's worktree, clone, image, and credential cleanup has
    completed.
  - **Cancelled or superseded path.** The claim is `cancelled` or `superseded`, the
    retention period has elapsed since its recorded terminal time, and its terminal release
    has completed. This path applies whatever the card's status and whether or not the card
    is still on the Project.
  - **Unreviewed path.** The claim is settled, its card is not observed as Done on the poll
    that prunes (including a card no longer on the Project), the unreviewed retention period
    has elapsed since its recorded terminal time, and its terminal release has completed.

The Done path SHALL be established from the board observation of the poll that prunes. A
card observed in any other state SHALL reset the recorded Done observation.

Pruning SHALL remove logs, Runner session state, agent session state, and agent output under
the attempt's artifact directory and, for a host attempt, its recorded Runner session
directory. It SHALL remove only enumerated evidence paths and SHALL keep any file or
directory it does not recognise. Pruning SHALL keep the fix or feature outcome, eval result
and provenance records, and the attempt's issue input. It SHALL NOT touch candidate
branches, PRs, mirrors, SQLite history, or the operator's working clones.

The factory SHALL record what it removed and any failures, and retry failed pruning on later
polls. Pruning SHALL run as a sweep on each tick over every terminal claim whose pruning is
incomplete, whether or not the Project query returns its card, so evidence that predates
this rule is covered. When a new run starts on a claim whose evidence was already pruned,
the new run's evidence SHALL become subject to this rule again. The earlier removal record
SHALL be kept.

#### Scenario: Prune after the retention period

- **WHEN** a claim's card was observed Done more than 14 days ago under the default retention and nothing still needs its evidence
- **THEN** the next tick removes its logs, session state, and agent output
- **AND** its outcome or result records, issue input, candidate branches, and PRs remain

#### Scenario: Skip a Done claim with a pending sync

- **WHEN** a fix claim's card is Done and its PR merged, but its post-merge sync has not completed
- **THEN** its evidence is not pruned however old the claim is

#### Scenario: Prune a cancelled claim that recorded a PR

- **WHEN** a cancelled fix claim recorded a PR and more than 14 days have passed since it was cancelled
- **THEN** its evidence is pruned whatever its card's status, without waiting on a post-merge sync, which only settled claims receive

#### Scenario: Prune a superseded claim outside Done

- **WHEN** a claim was superseded 15 days ago, its card is in Review for the fresh claim, and its terminal release has completed
- **THEN** the superseded claim's evidence is pruned and the fresh claim's evidence is untouched

#### Scenario: Keep a recently superseded claim whose runs finished long ago

- **WHEN** a claim's last run finished 40 days ago and the claim was superseded 3 days ago
- **THEN** its evidence is kept until 14 days after it was superseded

#### Scenario: Prune a settled claim left in Review

- **WHEN** a settled claim's card has stayed in Review for more than 30 days since it settled, its reporting is delivered, and its terminal release has completed
- **THEN** its evidence is pruned
- **AND** its outcome or result records and issue input remain

#### Scenario: Move a Review card to Done late

- **WHEN** a settled claim's card moves from Review to Done 25 days after it settled
- **THEN** the unreviewed path no longer applies and its evidence is kept until 14 days after that Done observation

#### Scenario: Reach Done after a long time

- **WHEN** a claim that was settled only today, after months of work, is moved to Done today
- **THEN** its evidence is retained for the full retention period from today's Done observation

#### Scenario: Sweep pre-existing history

- **WHEN** the factory first runs with this rule against a database whose old Done claims have no recorded Done observation
- **THEN** it records the observation on that poll and prunes those claims only after the retention period from that observation
- **AND** old cancelled, superseded, and unreviewed settled claims are judged from the terminal time recorded for them under `factory-claim-lifecycle`

#### Scenario: Fail to prune

- **WHEN** removal of an eligible claim's evidence fails partway
- **THEN** the factory records the failure and remaining work and retries on later polls without blocking other jobs

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind:

- the slot holder and progress;
- waiting work and why it waits;
- blocked fix and feature claims;
- settled fix and feature claims with eligible review comments waiting for their kind's slot;
- pending merge syncs and their last failure reason;
- pause state and blocking conditions;
- the next permitted start time, when it can be determined.

For an eval attempt under Fly execution it SHALL show the Machine identity, the Machine's
state including whether it is stopped for a quota hold, and the attempt's recorded deadline.
It SHALL also list Machines that reconciliation reported as unknown to the store or as failed
cleanup.

It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a
merge sync, or holding a recorded cleanup failure. A recorded cleanup failure includes a
failed terminal release, an undelivered human-review expiry report, and a failed,
refused, or skipped registry image deletion. For each such failure it SHALL show the
claim, the failed item, and the last reason, whether the claim is settled, cancelled, or
superseded. Claims whose card is Done with nothing pending, and superseded claims with
nothing pending, SHALL be omitted unless `--all` is given, which lists every saved claim.

It SHALL expose enough saved state to distinguish active execution, an admission-window
wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, a
waiting review round, and unfinished reporting. Status SHALL remain usable while execution
is active and SHALL NOT start work or change execution controls.

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

#### Scenario: Inspect a failed registry image deletion

- **WHEN** a superseded eval claim's registry image deletion failed on the last poll
- **THEN** plain `status` lists that claim with the image tag and the registry's reason until a later poll deletes the image
