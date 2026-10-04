## Why

Paul hands work to the factory from Claude sessions: one session files the issue with
`codagent-github-project`, and the same or another session queues it with `factory-assign`. That
session then has no way to learn when the factory is done with the issue. Today the only option is
to run `factory-watch` in that session: a background script that polls sqlite and `gh` for up to
12 hours, and that has to be started by hand for each issue. Usually nobody starts it, so a PR, a
`needs-input` question, or a failure waits until Paul next looks at the board.

The service watcher does not cover this. It was rescoped to factory health (`a830128`) and is
dispatched only for `PR-READY` and `FAILURE`. It never runs for `needs-input`, a settled eval, or a
cancellation, and it reports on the claim's issue, not to the session that asked for the work.

The pieces this needs already exist and were checked on this Mac while writing this proposal:

- A headless Claude session that the resident launches through Agent Runner (this definition run is
  one) gets a messaging socket, and `ListAgents` lists Paul's interactive sessions from it. So a
  service-launched session can send cross-session messages.
- Every live session is registered in `~/.claude/sessions/<pid>.json` with its `sessionId` (the
  `CLAUDE_CODE_SESSION_ID` UUID), its current `name`, and its `pid`. The registry maps a recorded
  UUID to the session's current name, which is the `SendMessage` address.
- `SendMessage` resolves a bare name. A `[ref]` resolves only when it was just read from a listing,
  so a recorded ref does not work as an address, and a recorded name goes stale when the session
  is renamed. The stable identifier is the session UUID.

Verdict: **go with caveats.** The value is direct: it removes most uses of `factory-watch` and gets
answers to `needs-input` questions sooner. The cost is one new resident step plus a very small,
cheap model session per notification. The caveats:

- delivery depends on Claude Code's cross-session messaging and its session registry, which this
  repository does not own and which may change;
- a receiving session in a different permission mode may hold the message for approval or refuse
  it;
- each delivery costs a short model session, because only a Claude session can call `SendMessage`.

Delivery is best effort, so each caveat degrades to "no message", never to a failed claim. One
exception remains: a seconds-long race between the last identity check and the send. If a session
takes over the target's name inside that window, the message can reach that session instead
(see Technical Approach). The
verdict would become no-go if a headless session could no longer send cross-session messages. The
check above shows that it can today.

## What Changes

- **Recording.** `codagent-github-project` records the creating session on each issue it creates,
  as a hidden marker in the issue body. The marker holds the session UUID, its name at the time, and
  when it was recorded. `factory-assign` replaces any marker with its own session's, or adds one.
  One marker per issue, and the latest one wins. Issues without a marker never notify.
- **Stop detection.** Each factory cycle starts from the store: the latest claim of each issue whose
  latest run finished recently and has not been notified. The board snapshot does not drive it. An
  issue stops in the same cases `factory-watch` reports a stop: the latest run finished with a pull
  request opened or updated, `needs-input`, a failure, or a settled eval, or the claim was cancelled
  or the card is no longer queued (including a card removed from the Project). It counts as stopped
  only when nothing is still moving, the same way `factory-watch` decides it:
  - no unfinished run;
  - no pending review round;
  - the card is not queued again;
  - no open watch dispatch;
  - when watching is enabled, no watch event that is expected but not yet detected. A run that
    should produce `PR-READY` or `FAILURE` waits until its dispatch exists and has ended, or until a
    bounded detection window (the failure grace period plus a margin, matching `factory-watch`'s 25
    minutes) has passed. In that case the notification records that the watch event was missed.
    When watching is disabled, this gate is skipped.

  The issue must also stay stopped for a settle period. Each stop is notified at most once, so a
  later review round or resume that stops again notifies again.
- **Delivery.** For each stop, the resident reads the issue's marker at that moment. It uses the card
  snapshot when the card is there, and otherwise fetches the issue by repository and number. It
  resolves the UUID to a live session through the registry: the UUID must match and the pid must
  still be the same process. It checks this again immediately before launch. If the session
  resolves, the resident launches a short, bounded headless Claude session whose only tools are
  `ListAgents` and `SendMessage`. That session calls `ListAgents` just before sending and requires
  exactly one live row with the resolved name. It then sends one fixed message to that row's freshly
  listed `name [ref]`. If there is no match or more than one, it sends nothing. The resident
  renders the message, which carries only the issue, the kind, what happened, and links to the issue,
  the PR, and the claim. It contains no instructions to the receiving agent. If the session cannot be
  found, or its identity cannot be confirmed, nothing is sent and the outcome is `no-session`. A
  GitHub read failure leaves the state unknown. It is neither a stop nor `no-session`, and a later
  cycle tries again. Every outcome (sent, no session, failed) is recorded, and none of them can
  block, fail, or retry the claim.
- **Configuration and operations.** A new shared `[notify]` section has `enabled`, the agent profile
  (a small model by default), a settle period, a daily cap, and a per-send timeout. `status` reports
  recent notifications and their outcomes, and `doctor` gets a `notify` group. `AGENTS.md`, the two
  skills, and `factory-watch` describe the new behavior. `factory-watch` remains available for
  following issues that carry no marker.

## Capabilities

### New Capabilities
- `factory-session-notification`: recording the originating session on an issue, detecting when the
  factory stops progressing on a marked issue, resolving the session, and delivering one best-effort,
  notify-only message per stop.

### Modified Capabilities
- `factory-operations`: the `[notify]` configuration, the `notify` doctor group, notifications in
  `status`, and documentation of session notifications in `AGENTS.md` and the operator skills.

## Technical Approach

- **Marker in the body, not a comment.** A comment by Paul's login would be read as an eligible
  writer comment. The factory reads those as answers to `needs-input` and as feature-definition
  input, so a marker comment could be mistaken for an answer. A hidden HTML comment in the body
  (for example `<!-- codagent-session: {...} -->`) is invisible on GitHub. It adds no fenced block,
  so eval requests still parse, and replacing it is a single edit. The skills already edit issues
  through Paul's `gh` login, and `factory-assign` changes only the marker and leaves the rest of
  the body alone. The resident already loads issue bodies in its card snapshot, so reading the
  marker needs no new GitHub call in the common case. Issues missing from the snapshot (closed or
  removed from the Project) are fetched directly, and their Project membership is checked.
- **One notifier for every stop event, separate from the watcher.** The watcher's purpose is factory
  health, and it fires only for two of the six stop cases. Using it for notifications would widen
  its purpose again and tie notifications to its budget and enablement. A small dedicated notifier
  handles every stop the same way. It runs no clone, no workflow, and no result schema. It is a
  single bounded headless Claude CLI session (`claude -p`; Agent Runner has no tool allowlist) with
  only two tools available, launched from a new
  `notify` step that runs after watch detection in every cycle, including while the factory is
  paused. When watching is enabled, a notification waits for the watcher's own dispatch for the
  run, so Paul's session hears about a failure after the factory-health triage, not before it.
- **Resolution in Python, sending by the model.** The resident resolves the UUID to the current name
  from the registry. It reads only the `sessionId`, `name`, `pid`, and `procStart` fields and never
  touches the `.key` files. It confirms that the pid is the same live process, and confirms it again
  just before launch. The model session then lists sessions, requires one unambiguous row with that
  name, and sends to the freshly listed `name [ref]`. The resident does not speak the messaging
  socket protocol directly: that protocol is undocumented and authenticated per peer.
- **Remaining race.** `ListAgents` shows names and refs, not UUIDs. A rename or exit, followed by
  another session taking the same name, can still slip in between the last registry check and the
  listing. That window lasts seconds. It is a documented delivery limitation, and the message
  carries only links and status, no instructions.
- **Durable record.** A new table keyed by issue and stop signature (claim, latest run, stop kind)
  records each notification and its outcome. It makes delivery exactly-once per stop across
  restarts, and evidence retention prunes it.

```
skill session ──writes marker──▶ issue body
                                     │
resident cycle: detect stop ─▶ read marker ─▶ registry: UUID→live name ─▶ headless notifier ─▶ SendMessage
                     │                                │ not found
                     └──── record outcome ◀───────────┘ (nothing sent)
```

## Out of Scope

- Notifying sessions on another machine, cloud sessions, or Remote Control sessions. Only sessions
  in this Mac's registry are resolved.
- Tracking more than one session per issue, or keeping a history of earlier sessions.
- Recording sessions for issues created by routing, the factory, the watcher, or by hand.
- Any instruction, question, or follow-up action in the message. Replies from the receiving session
  are not read.
- Retrying delivery, or waiting for a session that is busy, holding messages, or not yet started.
- Changing what the service watcher detects or does.
- Changes to Claude Code, Agent Runner, or other repositories.

## Impact

- **Code:** a new `notify` module (detection, registry resolution, launch, store), a resident and
  `tick` cycle step, new configuration and doctor and status entries, and one new table (additive;
  no existing persisted format changes).
- **Skills:** `.claude/skills/codagent-github-project` (write the marker on create) and
  `.claude/skills/factory-assign` (replace the marker; `assign.py` gains a marker write that leaves
  the rest of the body alone). `factory-watch` documentation notes that marked issues notify on their
  own.
- **Configuration:** `config/codagent.toml` gains `[notify]`. Enabling it on the live service needs a
  deploy.
- **Dependencies:** relies on Claude Code's cross-session messaging, the `~/.claude/sessions`
  registry, and `CLAUDE_CODE_SESSION_ID`, none of which this repository controls. If any of them
  changes, delivery stops (recorded as `no-session` or `failed`) and claims are unaffected.
- **Cost:** one short small-model session per stop on a marked issue, bounded by the daily cap.
- **Users:** Paul's interactive sessions receive `<cross-session-message>` notices. A session in a
  different permission mode may show them for approval first.
