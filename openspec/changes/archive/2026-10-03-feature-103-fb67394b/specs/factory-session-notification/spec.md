## ADDED Requirements

### Requirement: Record the originating session when the board skill creates an issue

When the `codagent-github-project` skill creates an issue from a Claude session, it SHALL record that session on the issue as a single session marker in the issue body. The marker SHALL be an HTML comment, so GitHub does not render it, and it SHALL hold:

- the session UUID from `CLAUDE_CODE_SESSION_ID`;
- the session's name at recording time, when the session can determine it;
- the recording time.

The marker SHALL be a single line of the form `<!-- codagent-session: {"session_id":"<uuid>","name":"<name>","recorded_at":"<UTC ISO-8601>"} -->`, appended after a blank line. `name` SHALL contain only `A-Z`, `a-z`, `0-9`, `.`, `_`, and single `-` characters, and SHALL be omitted when nothing remains. Readers SHALL treat the last such marker whose `session_id` is a UUID as the issue's marker, and anything else as no marker. The skill SHALL leave the rest of the body as Paul wrote it, and the marker SHALL NOT add a fenced code block. When `CLAUDE_CODE_SESSION_ID` is not set, the skill SHALL create the issue without a marker. When the skill updates an existing issue's body, it SHALL keep the existing marker unchanged. Only this skill and `factory-assign` SHALL write a marker. Issues created any other way, including by routing, the factory, or watch sessions, SHALL carry no marker.

#### Scenario: Create an issue from a Claude session

- **WHEN** a Claude session with `CLAUDE_CODE_SESSION_ID` set creates a Feature through the board skill
- **THEN** the issue body ends with one hidden session marker holding that UUID and the recording time, and the rendered issue shows only Paul's text

#### Scenario: Create an issue outside a Claude session

- **WHEN** the board skill's steps run without `CLAUDE_CODE_SESSION_ID` set
- **THEN** the issue is created with no session marker

#### Scenario: Edit an issue that has a marker

- **WHEN** the board skill rewrites the body of an issue that carries a session marker
- **THEN** the marker is still present and unchanged after the edit

#### Scenario: An eval request still parses

- **WHEN** the board skill creates an executable Eval whose body carries a session marker
- **THEN** the body still has exactly one fenced eval TOML block, and admission parses the request

### Requirement: Replace the recorded session when assigning to the factory

When `factory-assign` successfully applies a handoff (`--apply`) from a Claude session, it SHALL record its own session on the issue. It SHALL replace an existing marker, or add one when none exists, so the issue carries exactly one marker and the latest assigning session wins. It SHALL change nothing else in the body. A read-only check, an `--apply` that is refused (for example an issue that is already claimed, closed, or labelled `needs-input`), and a run without `CLAUDE_CODE_SESSION_ID` SHALL leave the marker unchanged. The read-back SHALL show which session the issue now records.

#### Scenario: Assign from a different session

- **WHEN** session B runs `factory-assign --apply feature` on an issue whose marker records session A
- **THEN** the issue's single marker records session B, and the rest of the body is unchanged

#### Scenario: Assign an issue without a marker

- **WHEN** a Claude session applies `factory-assign` to an issue created by hand
- **THEN** the issue gains one marker recording that session

#### Scenario: A refused handoff

- **WHEN** `factory-assign --apply` refuses an issue that is already claimed
- **THEN** the issue's marker is unchanged

### Requirement: Detect when the factory stops progressing on a marked issue

When notifications are enabled, every factory cycle, whether started by the resident or by `tick`, paused or not, SHALL evaluate the latest claim of each issue that carries a session marker. It SHALL use the claims and runs in the factory store, not only the cards present in the board snapshot. An issue SHALL count as stopped when all of the following hold:

- no run of its latest claim is unfinished;
- the claim is `settled`, `blocked`, `cancelled`, or `superseded`. Otherwise its card is closed, its Owner is not `factory`, or it is no longer on the Project;
- no review round is pending: no repository writer other than the factory has reviewed or commented on the claim's pull request since its latest run finished. Activity that does not start a review round, such as a non-writer comment, does not hold a stop;
- the card is not queued again: it is not open with `Owner=factory`, `Status=Ready`, and no `needs-input` label;
- the watch gate below is clear.

It SHALL also have stayed stopped, with no change to the cause of the stop, for the configured settle period, observed by successful reads throughout. An issue absent from a successful board snapshot SHALL be read directly and confirmed to exist and to be off the Project before it is classified. An issue that still reports Project membership while missing from the snapshot is unknown. Each stop SHALL be classified, from the claim and its latest run, as exactly one of:

- `pull-request`: a pull request was opened or updated;
- `needs-input`;
- `failed`: a failure status, or a completed run with the `failed` outcome;
- `settled`: an eval claim settled;
- `cancelled`: the claim is cancelled or superseded;
- `not-queued`: none of the above, and the card left the queue.

Only claims whose latest run finished after notifications were last enabled and within the last 7 days SHALL be evaluated. A claim with no finished run is not evaluated. An issue that was never claimed SHALL NOT notify. When the factory cannot read the issue or its card from GitHub, the issue's state is unknown. It SHALL NOT count as stopped in that cycle, and the settle period SHALL restart from the first successful read afterwards, so an outage longer than the settle period never shortens it.

The enablement time SHALL be recorded before the first enabled cycle consumes attempt results, so a run that finishes during that cycle is eligible.

#### Scenario: A feature opens a pull request

- **WHEN** a feature run on a marked issue completes with a pull request, the claim settles in Review, and no review, watch dispatch, or re-queue follows within the settle period
- **THEN** one `pull-request` stop is detected for that run

#### Scenario: A review round follows quickly

- **WHEN** a marked fix's pull request receives a human review comment before the settle period ends, and a review-round run starts
- **THEN** no stop is detected until that review-round run has finished and the issue has stayed stopped for the settle period

#### Scenario: A question for Paul

- **WHEN** a marked feature's run completes with `needs-input` and the claim is blocked with the `needs-input` label
- **THEN** one `needs-input` stop is detected after the settle period

#### Scenario: An eval between repetitions

- **WHEN** a marked eval's repetition finishes and the claim is still active, with its card open and `Owner=factory`
- **THEN** no stop is detected

#### Scenario: An eval settles

- **WHEN** a marked eval claim settles after its last repetition
- **THEN** one `settled` stop is detected after the settle period

#### Scenario: The card is removed from the Project

- **WHEN** a marked fix claim's card is removed from the Project while the claim waits, so the card is absent from the board snapshot
- **THEN** the factory reads the issue directly, finds that it is not on the Project, and detects one `not-queued` stop after the settle period

#### Scenario: GitHub cannot be read

- **WHEN** reading a marked issue that is absent from the snapshot fails
- **THEN** no stop is detected in that cycle, and a later cycle evaluates the issue again

#### Scenario: An outage outlasts the settle period

- **WHEN** a marked issue starts settling, and GitHub reads then fail for longer than the settle period before succeeding again
- **THEN** no notification is sent on the first successful read, and one is sent only after a full settle period of successful reads

#### Scenario: A run finishes in the first enabled cycle

- **WHEN** notifications are enabled and, in that same first enabled cycle, a marked fix run's result is consumed with a pull request and its claim settles
- **THEN** that stop is notified once its settle period has passed

#### Scenario: Work from before enablement

- **WHEN** notifications are enabled on an installation whose marked issues have runs that finished earlier
- **THEN** no stop is detected for those runs

### Requirement: Wait for the service watcher before notifying

When watching is enabled and the latest run of a stopped issue is one that the watcher detects (a `PR-READY` or `FAILURE` event, as `factory-watch-dispatch` defines them), the stop SHALL NOT be notified until one of these holds:

- the watch dispatch for that run exists and has ended (`completed`, `interrupted`, `timed-out`, `launch-failed`, `budget-exhausted`, or `logged`);
- no dispatch for that run has been launched within the configured watch wait since the run finished. The stop is then notified and recorded as `watch event missed` when no dispatch exists, or as `watch dispatch waiting` when a dispatch is still pending.

A launched dispatch SHALL be waited for until it ends. Its own session timeout bounds that wait. When watching is disabled, this gate SHALL be skipped.

#### Scenario: A failed run is triaged first

- **WHEN** a marked fix run fails with watching enabled and the failure grace period has not passed
- **THEN** no notification is sent until the FAILURE triage dispatch for that run has ended

#### Scenario: The watcher misses an event

- **WHEN** a marked feature run completes with a pull request and no PR-READY dispatch appears within the watch wait
- **THEN** the stop is notified and recorded as `watch event missed`

#### Scenario: Watching is disabled

- **WHEN** watching is disabled and a marked fix run fails
- **THEN** the stop is notified once the settle period has passed, without waiting for a dispatch

### Requirement: Resolve the recorded session to one live local session

For each stop, the factory SHALL read the issue's session marker as it is at that moment. It SHALL take the marker from the board snapshot when the card is present, and otherwise fetch the issue by repository and number. It SHALL resolve the marker's UUID through Claude Code's local session registry (`~/.claude/sessions`). The resolution SHALL require an entry whose session UUID matches and whose process is still the same live process, identified by both its pid and its process start time. It SHALL read only the session UUID, name, pid, and process start time from the registry, and SHALL never read or print the registry's key files or tokens. It SHALL repeat this check immediately before it starts delivery.

The delivery session SHALL list live sessions immediately before sending and SHALL require exactly one listed session with the resolved name. It SHALL send to that session's freshly listed `name [ref]`. When the marker is absent or malformed, no entry matches, the process has exited or been replaced, or the listing shows no match or several matches, nothing SHALL be sent and the outcome SHALL be `no-session`. Between the last check and the send, another session could take the target's name. That window lasts seconds. This SHALL be documented as a delivery limitation.

#### Scenario: The session was renamed

- **WHEN** the recorded session has been renamed since the marker was written and is still running
- **THEN** the message is sent to the session under its current name

#### Scenario: The session has ended

- **WHEN** the recorded session's process has exited
- **THEN** nothing is sent and the outcome is `no-session`

#### Scenario: The pid was reused

- **WHEN** the registry entry's pid now belongs to a different process with a different start time
- **THEN** nothing is sent and the outcome is `no-session`

#### Scenario: Two sessions share the name

- **WHEN** the listing shows two live sessions with the resolved name
- **THEN** nothing is sent and the outcome is `no-session`

#### Scenario: The marker was overwritten

- **WHEN** session A created the issue, session B later assigned it, and both are running when the factory stops
- **THEN** only session B receives the message

### Requirement: Deliver one notify-only message per stop

For each detected stop that resolves to a session, the factory SHALL start one bounded headless Claude CLI session. That session SHALL use the configured notify profile, have only the `ListAgents` and `SendMessage` tools available, and be bounded by the configured timeout. It SHALL report its outcome as a schema-validated structured result (`sent`, `no-session`, or `failed`, with a detail). A missing or invalid result SHALL count as `failed`. It SHALL send exactly the message text the factory rendered. The message SHALL contain only:

- a first line that is a self-contained summary naming the issue (`owner/repo#N`), its kind, and what happened;
- the issue URL;
- the pull request URL, when there is one;
- the claim id;
- for `needs-input` and `failed`, a link to the factory's own reporting comment for that claim and run that explains the stop, when the factory recorded one. Any other comment, including a newer unrelated bot comment, SHALL NOT be linked; without a matching comment the link is omitted.

The message SHALL NOT ask, instruct, or suggest that the receiving agent take any action. Each stop, identified by claim, latest run, and stop kind, SHALL produce at most one delivery attempt, across restarts and across repeated cycles. A later run on the same issue that stops again SHALL be a new stop. The factory SHALL record each attempt's outcome: `sent`, `no-session`, `failed` (launch failure, timeout, or a send error), or `budget-exhausted`. It SHALL NOT retry any of them. Claude notifier sessions launched in one local day SHALL NOT exceed the configured daily cap. A stop that ends before launch (no marker, `no-session`, or a readiness failure) SHALL NOT count against it. A stop that would launch beyond the cap SHALL be recorded `budget-exhausted`. A successful send means the message reached the session. A session in a different permission mode may still hold it for its user's approval or refuse it, and the factory SHALL NOT treat that as a failure.

#### Scenario: Notify a pull request

- **WHEN** a marked feature on `Codagent-AI/agent-factory#103` stops with a pull request and its session is live
- **THEN** that session receives one message whose first line names `Codagent-AI/agent-factory#103`, the feature kind, and that a pull request was opened or updated, followed by the issue URL, the pull request URL, and the claim id, with no instruction

#### Scenario: The same stop is seen again

- **WHEN** the resident restarts after a stop was recorded `sent`, and the next cycle sees the same claim, run, and stop kind
- **THEN** no second message is sent

#### Scenario: A second stop after a review round

- **WHEN** a notified pull-request stop is followed by a review-round run that updates the pull request and stops again
- **THEN** a second message is sent for the new run

#### Scenario: Link only the run's own explanation

- **WHEN** a marked feature stops with `needs-input`, and a watch triage comment from the bot is posted after the factory's needs-input comment
- **THEN** the message's details link points to the needs-input comment for that run, not the newer comment

#### Scenario: The daily cap is spent

- **WHEN** the day's delivery attempts have reached the cap and another stop is detected
- **THEN** no session is started, and the stop is recorded `budget-exhausted`

### Requirement: Never let notification affect a claim

Notification SHALL be best effort. A failure anywhere in detection, marker reading, session resolution, launching, or sending SHALL be logged and recorded where possible. It SHALL NOT fail, block, delay, retry, or change any claim, run, card, label, or comment, and SHALL NOT stop the rest of the cycle. The notifier SHALL NOT post on the issue or pull request. Notification SHALL NOT wait for a delivery session within the cycle: a delivery session that is still running SHALL be supervised and its outcome recorded in a later cycle, and one that exceeds its timeout SHALL be stopped and recorded `failed`.

#### Scenario: The registry is unreadable

- **WHEN** the session registry directory is missing or unreadable during a cycle
- **THEN** the stop is recorded `no-session`, and the cycle admits, supervises, and reports claims normally

#### Scenario: The delivery session hangs

- **WHEN** a delivery session does not finish within its timeout
- **THEN** it is stopped, the attempt is recorded `failed`, and the claim is unchanged

### Requirement: Stop notifying without losing records

When notifications are disabled, no stop SHALL be detected and no delivery session SHALL start. Delivery sessions that are already running SHALL be supervised until they end, and their outcomes recorded. When notifications are enabled again, the enablement time SHALL become the current time, so stops of runs that finished earlier are not notified. An ended notification record and its evidence SHALL be pruned once it ended more than the evidence retention period ago, and never sooner than 8 days after it ended, so a pruned stop cannot be detected again.

#### Scenario: Disable while a delivery runs

- **WHEN** notifications are disabled while a delivery session is running
- **THEN** that session's outcome is still recorded, and no new stop is detected

#### Scenario: Re-enable after a pause

- **WHEN** notifications are re-enabled after a week disabled
- **THEN** no message is sent for stops of runs that finished during that week
