## MODIFIED Requirements

### Requirement: Configure service-driven watching

The shared configuration SHALL accept an optional `[watch]` section with these settings:

- `enabled` (default false);
- the factory `repository` (`owner/name`) that dispatched sessions check out, required when watching is enabled;
- the default dispatch `agent` profile in `cli:model:effort` form, required when watching is enabled;
- optional per-event profiles for `PR-READY` and `FAILURE`;
- the concurrency cap (default 2, at least 1);
- the failure grace period in minutes (default 7, zero or more);
- the session timeout in minutes (default 90, at least 1);
- `auto_merge`, a boolean (default false) that turns on risk rating and auto-merge of low-risk pull requests.

When watching is enabled, configuration loading SHALL fail on a missing repository or default profile, a profile that is not in `cli:model:effort` form, an unknown event name, a value out of range, or an `auto_merge` that is not a boolean, and the failure SHALL name the setting. A missing section, or `enabled = false`, SHALL keep today's behavior, with auto-merge off. The Codagent example configuration SHALL enable watching with the default profile `claude:claude-sonnet-5-5:medium` and `auto_merge = true`. Each cycle SHALL read the watch settings from the configuration it loads, so a changed profile, concurrency cap, grace period, timeout, or `auto_merge` applies to dispatches that start after the change. A session that is already running SHALL keep its profile and timeout. A merge that is waiting SHALL use the `auto_merge` value of the cycle that evaluates it, so turning it off stops every waiting merge. The checks a merge requires SHALL come from each repository's GitHub rulesets, not from this configuration.

The section SHALL have no per-day session budget. A `daily_sessions` key left in the section SHALL NOT fail configuration loading and SHALL have no effect.

#### Scenario: Configure the dispatch model and budget

- **WHEN** the shared configuration enables watching with the agent `claude:claude-sonnet-5-5:medium` and a concurrency cap of 3, and sets no session budget because none exists
- **THEN** dispatched sessions run with that profile, no more than 3 run at once, and no event is skipped because of how many sessions started that day

#### Scenario: A leftover budget setting

- **WHEN** watching is enabled and the `[watch]` section still sets `daily_sessions = 5`
- **THEN** configuration loads, and a sixth event in a local day starts a session like any other

#### Scenario: Reject an invalid profile

- **WHEN** watching is enabled with the agent `sonnet`
- **THEN** configuration loading fails and names the `[watch]` agent setting

#### Scenario: Leave watching unconfigured

- **WHEN** the shared configuration has no `[watch]` section
- **THEN** the factory detects no watch event, starts no session, and shows watching as disabled in status

#### Scenario: Reject a non-boolean auto-merge

- **WHEN** watching is enabled with `auto_merge = "yes"`
- **THEN** configuration loading fails and names the `[watch]` auto_merge setting

#### Scenario: Turn auto-merge off while a merge waits

- **WHEN** a `low`-rated pull request waits for a running check and `auto_merge` is changed to false before the next cycle
- **THEN** the next cycle does not merge it and ends its merge with the reason that auto-merge is off

### Requirement: Report watch dispatches in status

When watching is enabled, `agent-factory status` SHALL show a watch section with:

- whether watching is enabled, and whether auto-merge is on;
- the time of the last detection pass;
- each `launched` dispatch with its event, claim, pull request when there is one, model profile, and elapsed time;
- the number of `pending` dispatches, and why they wait: the concurrency cap, a failing watch doctor group, or a running check of the same pull request;
- each ended dispatch whose usage delivery to the development-audit destination did not succeed;
- the number of sessions started today and today's known estimated cost, with no budget;
- every dispatch recorded `interrupted`, `timed-out`, `launch-failed`, or `budget-exhausted` whose claim is not yet observed Done, cancelled, or superseded;
- each undelivered dispatch comment with its last failure reason;
- for each completed dispatch whose claim is not yet observed Done, cancelled, or superseded, the factory issues its session filed or updated, with the pull request, or the claim's issue for a triage;
- for each completed PR-READY check with a rating whose claim is not yet observed Done, cancelled, or superseded, the pull request, its rating, and its merge state: waiting with what it waits for, merged, or not merged with the reason.

When watching is disabled, status SHALL show one line saying so, and it SHALL still list `launched` and `pending` dispatches. Status SHALL NOT start or change any dispatch, and SHALL NOT merge.

#### Scenario: Inspect a running triage

- **WHEN** a triage session is running and one PR-READY check dispatch waits for the cap
- **THEN** status shows the triage session's event, claim, profile, and elapsed time, and one pending dispatch waiting for the concurrency cap

#### Scenario: Inspect the day's spend

- **WHEN** seven sessions have started today with known costs
- **THEN** status shows seven sessions started today and the sum of their estimated costs, and shows no session budget

#### Scenario: Inspect a dispatch an earlier release skipped for budget

- **WHEN** a `FAILURE` dispatch that an earlier release recorded `budget-exhausted` belongs to a claim not yet observed Done, cancelled, or superseded
- **THEN** status lists that dispatch as `budget-exhausted`

#### Scenario: Inspect a failed dispatch

- **WHEN** a PR-READY check dispatch timed out for a claim that is still in Review
- **THEN** status lists that dispatch as `timed-out` with its pull request and evidence path

#### Scenario: Inspect the issues a check filed

- **WHEN** a PR-READY check completed and filed one factory issue for a claim still in Review
- **THEN** status shows the pull request and the filed issue's URL on one line

#### Scenario: Inspect a merge that waits

- **WHEN** a pull request rated `low` waits for a running check
- **THEN** status shows the pull request, `low`, and that the merge waits for checks

#### Scenario: Inspect a pull request left for review

- **WHEN** a pull request was rated `medium` for a claim still in Review
- **THEN** status shows the pull request, `medium`, and not merged because of the rating

### Requirement: Document the service-driven watcher

The operations documentation SHALL describe service-driven watching as the normal mode, and SHALL state that the watcher's job is to make sure the factory itself works, and, when auto-merge is on, to merge the factory's low-risk pull requests:

- the two events and what each one does;
- the `[watch]` settings and their defaults, and how to escalate a failure to a stronger model;
- the concurrency behavior; that no event is skipped because of session volume, which the factory job cap bounds instead; and that `budget-exhausted` dispatches from earlier releases remain in history and can be redispatched;
- the actions a dispatched session may and may not take, including that it files issues for factory defects and never fixes or merges anything;
- auto-merge: the risk bars for fixes, tasks, and features, the gates the factory checks before it merges, the risk-verdict comment on the pull request, the 60-minute wait, and how to turn it off;
- that auto-merge requires a GitHub ruleset requiring status checks on each fix target's base branch, that a repository without one never auto-merges, and that branch protection requiring an approving review blocks auto-merge in that repository;
- that triage runs after a failed claim's automatic retry and does not hold it;
- the watch doctor group, including the `gh` login that files issues;
- the watch section of status;
- `watch redispatch`;
- how to find a dispatch's evidence and usage.

They SHALL state that no interactive watcher session is used. An on-demand `factory-status` skill SHALL report the factory's state once when asked, without watching or polling, including pull requests the factory merged automatically. A `factory-triage` skill SHALL hold the failure-handling procedure and both headless procedures that the watch workflow's sessions follow: the PR-READY check, including the risk rating, and the failure triage. Its standing rules SHALL allow the resident's auto-merge while still forbidding an agent session to merge. The factory PR review skill and its reviewer agent SHALL have no headless mode; they review a pull request only when the operator asks.

#### Scenario: Operate the service watcher

- **WHEN** an operator follows the documentation to enable watching
- **THEN** they can set the profile and concurrency cap, pass the watch doctor group, find running and failed dispatches and the issues they filed in status, and redispatch a failed one

#### Scenario: Turn off auto-merge

- **WHEN** an operator follows the documentation to stop auto-merging
- **THEN** they set `[watch] auto_merge = false` in committed configuration; sessions launched after the change do not rate risk, no pull request is merged after the change, and a rating from a session already running gets a risk-verdict comment saying it was not merged because auto-merge is off

#### Scenario: Enable auto-merge for a repository

- **WHEN** an operator follows the documentation to let a fix target auto-merge
- **THEN** they add a ruleset on its base branch that requires its CI status checks, and the factory then merges `low`-rated pull requests there once those checks pass

#### Scenario: Ask for a factory update

- **WHEN** the operator asks an agent for a factory update
- **THEN** the agent follows `factory-status`, reports once what waits on the operator, what is running, what failed, and what merged automatically, and starts no watcher

#### Scenario: Ask for a PR review

- **WHEN** the operator asks an agent to review a factory pull request
- **THEN** the agent follows `factory-pr-review` interactively; no watch session reviews it for the operator
