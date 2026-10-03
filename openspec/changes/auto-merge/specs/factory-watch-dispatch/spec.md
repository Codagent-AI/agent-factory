## MODIFIED Requirements

### Requirement: Check a ready pull request for factory defects

A `PR-READY` dispatch with a parseable pull request URL SHALL start one headless session that checks what the run exposed about the factory itself. The session's brief SHALL name the pull request, repository, issue, attempt kind, attempt reason, attempt id, and the attempt's evidence path, SHALL list the configured fix-target repositories, and SHALL state whether auto-merge is on. The session SHALL mine the pull request description's red and orange attention items, and the attempt's evidence as needed, for defects in the factory stack: Agent Factory, the workflows and Agent Runner it runs, Agent Skills, and Agent Validator as the factory uses it. A defect in the product code the pull request changes is not a factory defect. For each factory defect it SHALL file an issue as defined in "File factory defects as issues". When auto-merge is off, it SHALL NOT review the pull request's code. When auto-merge is on, it SHALL also rate the pull request's risk as defined in "Rate a ready pull request's risk".

The session SHALL NOT post a review or any comment on the pull request, SHALL NOT approve, request changes, merge, or close it, and SHALL NOT create branches, commit, push, or open pull requests. It SHALL NOT ask questions. It SHALL return a result holding a short summary, the issues it filed, and the existing issues it added evidence to, and, when auto-merge is on, its risk rating. When auto-merge is off, the factory SHALL post no comment for a completed check. Status SHALL show the issues it filed or updated. A `PR-READY` dispatch for a pull request SHALL stay `pending` while another dispatch for the same pull request is `launched`.

#### Scenario: A ready pull request has no parseable URL

- **WHEN** a `PR-READY` dispatch has no parseable pull request URL
- **THEN** the event is logged and recorded `logged`, and no session starts or counts against the budget

#### Scenario: One check session per ready pull request

- **WHEN** a fix attempt completes with a pull request
- **THEN** exactly one PR-READY check session starts for that pull request

#### Scenario: An orange item exposes a factory defect

- **WHEN** auto-merge is off, a pull request's description has an orange item showing that a Runner workflow step misfired, and no open issue describes it
- **THEN** the session files a Bug issue in Agent Runner with the pull request link, the item, and a log excerpt, assigned to the factory; the dispatch completes; no comment or review is posted on the pull request; and status shows the pull request with the issue link

#### Scenario: Only product findings

- **WHEN** auto-merge is off and every red and orange item concerns the product change or is a false alarm
- **THEN** the session files no issue, and nothing is posted on the pull request or the claim's issue

#### Scenario: A review round finishes while its pull request is being checked

- **WHEN** a review-round attempt on a pull request completes while that pull request's earlier check session is still running
- **THEN** the new `PR-READY` dispatch waits and starts after the earlier session ends

### Requirement: Deliver dispatch comments exactly once

Every comment the factory posts for a dispatch SHALL carry a marker unique to that dispatch and that comment's purpose. Before posting, the factory SHALL look for a comment by the factory bot on the same issue or pull request that already carries the marker, and adopt it instead of posting again. A delivery that fails SHALL be recorded with its reason and retried in a later cycle. Every dispatch comment SHALL go to the claim's issue, except the risk-verdict comment, which SHALL go to the dispatch's pull request. Across restarts and retries, each such comment SHALL be delivered at most once, and it SHALL be retried until delivered.

#### Scenario: A restart after posting

- **WHEN** the factory posts a triage comment and restarts before recording the delivery
- **THEN** the next cycle finds the comment by its marker and records it, and no second comment is posted

#### Scenario: GitHub is unavailable

- **WHEN** posting a triage comment on the claim's issue fails
- **THEN** the failure and its reason are recorded, and a later cycle posts the comment once

#### Scenario: A restart after posting a risk verdict

- **WHEN** the factory posts a risk-verdict comment on a pull request and restarts before recording the delivery
- **THEN** the next cycle finds the comment on the pull request by its marker and records it, and no second comment is posted

## ADDED Requirements

### Requirement: Rate a ready pull request's risk

When auto-merge is on, the PR-READY check session SHALL read the pull request's diff, description, and, as needed, the attempt's evidence, and SHALL rate the pull request `low`, `medium`, or `high`. Its result SHALL hold the rating, the head commit it rated, and reasons that name each criterion that kept it from `low`, or confirm each criterion for `low`, including every orange item it judged harmless and why.

Either kind of pull request SHALL NOT be rated `low` when any of these holds:

- the description has a red attention item;
- it changes authentication, permissions, tokens, or credentials;
- it changes CI configuration or deploy or release scripts;
- it changes workflow definitions, such as Runner workflow YAML the factory ships;
- it changes a database schema or migration;
- it changes pinned versions or refs in committed configuration;
- it changes a public CLI or API interface, such as flags, output formats, or result schemas;
- it adds or upgrades a dependency.

A fix pull request MAY be rated `low` only when, in addition:

- every behavior change is needed to fix the defect the issue describes, with no refactoring, unrelated cleanup, or new feature;
- a test it adds or updates fails without the fix and passes with it;
- it changes at most 300 lines outside test files, counting added plus deleted lines and excluding generated lockfiles;
- every orange item was checked against the diff and evidence and judged harmless.

A feature pull request MAY be rated `low` only when, in addition:

- its specification changes only add requirements, with none modified or removed, and existing behavior is unchanged or the new behavior is off unless a setting enables it;
- it changes at most 150 lines outside test files, counting added plus deleted lines and excluding generated lockfiles;
- its description has no orange attention item;
- every added specification scenario is covered by a test.

When the session cannot establish a criterion, it SHALL NOT rate the pull request `low`.

#### Scenario: A narrow fix with a regression test

- **WHEN** auto-merge is on and a fix pull request changes 40 non-test lines to correct the reported off-by-one, adds a test that fails without the fix, touches no sensitive area, and has no red or orange items
- **THEN** the session rates it `low` with reasons confirming each criterion

#### Scenario: A fix that also edits a deploy script

- **WHEN** an otherwise narrow, tested fix pull request also changes `scripts/deploy.sh`
- **THEN** the session rates it `medium` or `high` and names the deploy-script change as the reason

#### Scenario: A red attention item

- **WHEN** a fix pull request's description has a red item
- **THEN** the session does not rate it `low` and names the item

#### Scenario: A feature with an orange item

- **WHEN** a small, additive, fully tested feature pull request has one orange attention item
- **THEN** the session does not rate it `low` and names the orange item

#### Scenario: A feature that modifies a requirement

- **WHEN** a feature pull request's specification changes modify an existing requirement
- **THEN** the session does not rate it `low`

#### Scenario: Auto-merge is off

- **WHEN** auto-merge is off and a PR-READY check runs
- **THEN** the session returns no rating, and the factory neither comments on nor merges the pull request

### Requirement: Merge a low-risk pull request

When a PR-READY check completes with a valid `low` rating, the factory itself, not the session, SHALL merge the pull request when all of these gates pass at the time of the merge:

- watching and auto-merge are on;
- the factory is not paused;
- the pull request URL's owner, repository, and number match the dispatch's repository and pull request number;
- the pull request's repository is a configured fix target and its base is that target's branch;
- the pull request is open, not a draft, and has no merge conflict;
- its head commit is the commit the session rated;
- at least one check or commit status is reported on that head, and every one has completed successfully;
- it has no unresolved review thread, and no writer's latest submitted review requests changes.

Before evaluating the gates, the factory SHALL check whether the pull request is already merged with the rated head; if it is, it SHALL record the merge as merged and SHALL NOT send another merge request, so a restart after a successful merge is never reported as not merged. The merge SHALL be a merge commit made as the factory bot and pinned to the rated head, so GitHub refuses it if the head moved. The factory SHALL NOT approve the pull request, bypass branch protection, or delete its branch. While checks are still running, no check or commit status has been reported yet, or the factory is paused, the merge SHALL wait and be re-evaluated each cycle until 60 minutes after the check completed; then it SHALL end not merged with that reason. When any other gate fails, or GitHub rejects the merge, the merge SHALL end not merged with the gate or GitHub's reason, and SHALL NOT be retried for that dispatch. A `medium` or `high` rating SHALL end not merged with the rating as the reason. Each dispatch SHALL attempt at most one merge. After a merge, the existing post-merge sync SHALL update the working clone and close the issue.

#### Scenario: Merge a low-risk fix

- **WHEN** auto-merge is on, a fix pull request in a fix-target repository is rated `low`, its checks are green, its head is unchanged, and it has no open review threads
- **THEN** the factory merges it with a merge commit at the rated head, the risk-verdict comment says it was merged automatically, and the post-merge sync later closes the issue

#### Scenario: Checks still running

- **WHEN** a pull request is rated `low` while one check is still running, and the check passes 20 minutes later
- **THEN** the factory waits, merges it in the first cycle after the check passes, and posts one risk-verdict comment

#### Scenario: Checks never finish

- **WHEN** a pull request rated `low` still has a running check 60 minutes after its PR-READY check completed
- **THEN** the factory does not merge it and the risk-verdict comment names the unfinished check

#### Scenario: No checks are reported

- **WHEN** a pull request rated `low` has no check or commit status on its head 60 minutes after its PR-READY check completed
- **THEN** the factory does not merge it and the risk-verdict comment says no checks were reported

#### Scenario: A restart after the merge

- **WHEN** the factory merges a pull request and restarts before recording the merge
- **THEN** the next cycle finds the pull request merged with the rated head, records it merged without another merge request, and the risk-verdict comment says it was merged automatically

#### Scenario: The pull request URL names another repository

- **WHEN** a dispatch's pull request URL names a repository other than the dispatch's repository
- **THEN** the factory does not merge and names the mismatch

#### Scenario: The head moved after the rating

- **WHEN** a review round pushes a new commit after the session rated the previous head `low`
- **THEN** the factory does not merge for that dispatch, names the moved head, and the review round's own `PR-READY` check rates the new head

#### Scenario: A writer requested changes

- **WHEN** a pull request rated `low` has a writer's latest review requesting changes
- **THEN** the factory does not merge it and names the outstanding change request

#### Scenario: Branch protection requires an approval

- **WHEN** GitHub rejects the merge because the base branch requires an approving review
- **THEN** the merge ends not merged with GitHub's reason in the risk-verdict comment, and no retry is made for that dispatch

#### Scenario: Paused factory

- **WHEN** the factory is paused while a `low`-rated pull request passes every other gate, and resumed 10 minutes later
- **THEN** no merge happens while paused, and the first cycle after the resume merges it

#### Scenario: Medium risk

- **WHEN** the session rates a pull request `medium`
- **THEN** the factory does not merge it and the risk-verdict comment gives the rating and reasons

### Requirement: Post the risk verdict on the pull request

For each PR-READY check that completed with a rating, once its merge has ended, the factory SHALL queue exactly one risk-verdict comment on the pull request as the factory bot, delivered as defined in "Deliver dispatch comments exactly once". The comment SHALL give the rating, the session's reasons, the rated head commit, and either that the pull request was merged automatically or the gate or reason that kept it from merging. A check that ended without a valid result SHALL NOT produce a risk-verdict comment, and SHALL NOT merge.

#### Scenario: Not merged because of the rating

- **WHEN** a check rates a pull request `high` because it changes a migration
- **THEN** the pull request gets one factory-bot comment giving `high`, the migration reason, the head commit, and that it was not merged

#### Scenario: Interrupted check

- **WHEN** a PR-READY check session is recorded `interrupted`
- **THEN** no risk-verdict comment is posted, the pull request is not merged, and the usual alert goes to the claim's issue
