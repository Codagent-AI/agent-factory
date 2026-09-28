## Context

The factory releases a claim's files only on board gestures today.

- **Eval worktrees.** `WorktreeCleanup.reconcile`
  (`src/agent_factory/suites/and_scene/__init__.py`) releases them only for a `settled`
  claim, after Review and then Done.
- **Fix and feature files.** `PullRequestCleanup.reconcile`
  (`src/agent_factory/work_kinds/pull_request/cleanup.py`) releases clones, Docker run
  images, and credential copies after Review and then Done. For a `cancelled` claim it does
  so as soon as execution stops. It removes only the clone set in
  `claim.preparation["clones"]`, which each new preparation overwrites, so earlier
  attempts' `<root>/clones/<claim>/<n>` directories (and empty claim directories) leak.
  Once `cleanup["complete"]` is true it never runs again, so a later review round's
  clones leak as well.
- **Evidence.** `retention.reconcile` (`src/agent_factory/retention.py`) prunes only while
  the card is currently Done.
- **The tick loop** (`runtime.cycle`) visits claims only through cards that
  `list_project_items` returns. It `continue`s past superseded claims before
  `handler.cleanup`, so a superseded claim is never released.
- **Fly eval images.** `build_claim_image` (`fly/transport.py`) pushes
  `registry.fly.io/<app>:claim-<12 characters of the claim id>` for each eval claim. The
  supervisor copies `{repository, tag, digest}` into `run.progress["image_build"]` in
  SQLite. Nothing ever deletes it.
- **Timestamps.** `claim.updated_at` is bumped by every claim write (`set_claim_lifecycle`,
  `set_preparation`, `set_cleanup`, `_set_reporting`). No write sets it into the past.
- **Reports.** Reports are durable events in `claim.reporting["events"]`, delivered by
  `Controller.deliver_reports`. That method is idempotent, using a marker search
  (`_marker`), and records `delivery_failures`. `store.pending_events` lists undelivered
  events.
- **Fly Machines.** Machine ownership lives in `settings` rows `fly:machine:<run_id>`. Failed
  Machine cleanup lives in `fly:cleanup-failed`, owned by `FlyMachineBackend.reconcile`.
- **Schema.** SQLite is at schema version 4, and a newer database makes an older controller
  refuse to start (`_migrate`). The bundled SQLite (3.53) has JSON1.

**Registry probe, 2026-09-28.** This ran against the live `agent-factory-sandbox` registry
with the factory's deploy token and HTTP basic auth, using a disposable image pushed under
two tags, `factory-probe-e86f65e7-a` and `-b`. Both tags were removed afterwards.

- `GET /v2/<repo>/tags/list` returns every tag. The response names the repository by an
  internal id (`w3pgqe0zr65l1dv5`), so the factory must not compare that name.
- `DELETE /v2/<repo>/manifests/<tag>` returned `202` and removed **only that tag**. The
  manifest stayed reachable by digest and through the other tag.
- `DELETE /v2/<repo>/manifests/<digest>` returned `202` and removed the manifest and
  **every tag** that pointed to it. Afterwards, a GET by either tag or by the digest
  returned `404`.
- A repeated delete by digest returned `404 MANIFEST_UNKNOWN`.
- On the day of the probe, the nine existing tags all had distinct digests.

These results resolve the spec's deferred marker. Deletion by digest works end to end, and
it is exactly as wide as "every tag pointing at this digest". That is why the shared-digest
guard is mandatory. The probe says nothing about blob garbage collection, which remains
Fly's concern.

## Goals / Non-Goals

**Goals:**

- Release and prune terminal claims (`settled`, `cancelled`, `superseded`) without waiting
  for Done. Never release active, waiting, or blocked claims.
- Anchor every age clock on a durable terminal time. Backfill existing rows once, never
  earlier than the real transition.
- Keep review paths intact:
  - reopen cleanup when a new run starts;
  - release every attempt's clones;
  - deliver the eval human-review expiry before releasing its worktree.
- Delete each finished Fly eval claim's own manifest, guarded against shared digests, and
  isolated from Machine disposal.
- Show every pending failure in `status`.

**Non-Goals:**

- Orphaned files with no claim record, mirrors, and Docker Desktop's disk image and build
  cache.
- Registry tags the factory did not record, and registry blob garbage collection.
- Disk-pressure eviction, and any change to `doctor`.
- A schema version bump. See decision D1.

## Approach

### Components

```
runtime.cycle
 ├─ per-card loop (unchanged shape)
 │   ├─ retention.observe_done(claim, board_status)   ← record/reset Done observation only
 │   ├─ _report(...)                                   ← unchanged
 │   └─ handler.cleanup(claim, board_status)           ← Review→Done path, unchanged
 │   (collects seen[claim_id] = board_status for every claim on a returned card)
 └─ terminal.sweep(store, controller, client, handlers, local, seen, now) ← NEW, after the loop
     for claim in store.terminal_claims():            (settled | cancelled | superseded)
       if cleanup["sweep_complete"]: continue
       terminal_at = terminal.terminal_time(store, claim)          (backfill once)
       quiescent  = terminal.quiescent(store, claim)
       status     = seen.get(claim.id)                              (None = off-board)
       1. release   : handler.release(claim) when terminal.release_due(...)
                      (eval expiry event first; see "Unreviewed eval expiry")
       2. prune     : retention.prune_due(...) → retention._prune(...)
       3. registry  : fly.registry_cleanup.reconcile_claim_image(...) for eval claims
                      with recorded image builds (isolated try/except per claim)
       4. deliver   : controller.deliver_reports(claim.id) if claim has pending events
                      and was not on a returned card (on-board claims deliver via _report)
       set cleanup["sweep_complete"] when release, pruning and registry are all done
```

A new module, `src/agent_factory/terminal.py`, owns the sweep, the clocks, and eligibility.
`retention.py` keeps pruning mechanics and gains the two age paths. Each work kind's
existing cleanup class gains a `release` entry point that shares its removal code with the
Done path.

### Terminal time (`store.py`)

- Add `TERMINAL_LIFECYCLES = frozenset({"settled", "cancelled", "superseded"})`.
- `set_claim_lifecycle` handles a terminal `lifecycle` by writing `terminal_at` in the same
  `UPDATE` statement, and only when the persisted lifecycle differs:

  ```sql
  cleanup_json = CASE WHEN lifecycle = :lifecycle THEN cleanup_json
                      ELSE json_set(cleanup_json, '$.terminal_at', :now) END
  ```

  SQLite evaluates `lifecycle` in a `SET` expression against the row's old value. This
  matters because `review.process_review_claim` calls
  `set_claim_lifecycle(claim.id, claim.lifecycle, ...)` on a settled claim on every tick
  while its review waits for the slot. A settled-to-settled outcome update therefore never
  moves the clock. For any other lifecycle, `set_claim_lifecycle` leaves `terminal_at`
  alone, and reserving a run (below) clears it.
- `supersede_and_create` writes `terminal_at` in its superseding `UPDATE`. Settled or
  blocked to superseded is always a lifecycle change.
- `terminal.terminal_time(store, claim)` returns `cleanup["terminal_at"]`. When a terminal
  claim has no `terminal_at`, the function writes `claim.updated_at`, as read, into
  `cleanup["terminal_at"]` and marks it `terminal_at_backfilled: true` for traceability.
  `updated_at` is at or after the transition, because the transition itself wrote it and
  later writes only move it forward.
- Run finish times are never used.

### Reopening on a new run (`store.reserve_run`)

`reserve_run` updates the claim's `cleanup_json` in the same transaction as the run insert.
It sets `complete = false` and `review_observed = false`, and removes `terminal_at`,
`sweep_complete`, `retention.pruned_at`, and `expiry`. The historical
`retention.removed`, `retention.errors`, and `registry` records are kept, so the record of
earlier removals survives. Eval claims never start runs after settling (a fresh request
creates a fresh claim), so in practice this affects pull-request review rounds.

### Release eligibility (`terminal.release_due`)

All of the following must hold:

- The lifecycle is in `TERMINAL_LIFECYCLES`.
- **Quiescent:**
  - no run is in `NONTERMINAL_RUN_STATUSES`. That set also covers unverified ownership:
    `store` keeps a run whose ownership or completion is ambiguous in `observing`, which
    is one of those statuses;
  - `store.pending_events(claim.id)` is empty and there are no
    `reporting["delivery_failures"]`;
  - a settled pull-request claim has no pending post-merge sync, judged by
    `terminal.sync_pending(claim, client, cache)`. That check returns false when:
    - the claim has no recorded PR;
    - its sync has completed;
    - `client.get_pull_request` shows the PR open, or closed with no `merged_at`.

    It returns true when the PR has merged but its sync has not completed, and also when
    the PR read fails. The read happens only for a claim that is otherwise due, and at most
    once per claim per tick. The existing `pending_sync`, which treats an open PR as
    pending, is kept for `status`. Retention's gates use `sync_pending` instead, including
    the Done path, so a Done claim whose PR closed without merging is no longer held
    forever. The sweep never calls `merge_sync`. A settled claim whose card is off the
    Project and whose merged PR is unsynced keeps its files, and `status` keeps listing
    its pending sync.
- The release is not already complete (`cleanup["complete"] is not True`).
- It is due:
  - for `cancelled` or `superseded`, immediately;
  - for `settled`, when `seen.get(claim.id) != "Done"` and
    `now - terminal_at >= unreviewed_retention_days`.

A settled claim whose card is Done keeps using the existing Review-then-Done path. That
path still requires `review_observed`, which is unchanged behavior. It also gains the
reporting gate. `WorktreeCleanup.reconcile` and `PullRequestCleanup.reconcile` return
without removing anything while `store.pending_events(claim_id)` is non-empty or
`reporting["delivery_failures"]` is set. A failed human-review-command post followed by a
Done move therefore keeps the worktree until the command is delivered. `_report` delivers
pending events before `handler.cleanup` in the per-card loop, so removal follows on the
same tick as the successful delivery, or on the next.

`handler.release(claim) -> bool` is a new `WorkKindHandler` protocol method. It returns
true when everything is released.

- **Eval** (`EvalHandler.release`): calls `WorktreeCleanup.release(claim_id)`. That method
  is the removal half of today's `reconcile`, which removes the recorded worktrees and
  `remove_images(run_image_tags(...))` and sets `complete` and `last_error`. `reconcile`
  now calls it too. A claim that never recorded worktrees (for example one cancelled before
  preparation) is marked `complete: true` with nothing removed.
- **Pull request** (`PullRequestHandler.release`): calls `PullRequestCleanup._release`,
  extended to also remove the whole factory-owned claim clone directory,
  `PullRequestWorkspace.claim_directory(claim_id)` (`<root>/clones/<safe claim id>`). This
  is a new helper next to `attempt_directory`, using the same `_safe` naming.
  That covers every attempt's `<n>` directory and the leaked empty directories. It uses the
  same `_remove_tree` read-only-tolerant removal. `PullRequestCleanup.reconcile`'s existing
  cancelled branch is kept, but it now also requires the quiescence conditions above.
  Otherwise the Done path and the sweep could disagree.

### Unreviewed eval expiry (`EvalHandler`)

- **The handoff was published** when any event whose key ends with `:review-command` has a
  delivered `comment_id`.
- **Before release**, when a settled eval claim is due by the unreviewed path and the
  handoff was published, the sweep calls `store.record_event(claim.id, "review-expired",
  body)`. That call is idempotent by key. The sweep then does not release the claim on this
  pass.
- **The release gate** already requires no pending events. Release therefore happens on
  the first pass after `review-expired` has been acknowledged. That is the same tick when
  delivery succeeds, because the sweep delivers and then re-checks, or a later tick. A
  failed delivery is recorded by `deliver_reports` in `delivery_failures`, and `status`
  already surfaces those.
- **The body** comes from `EvalHandler.expiry_message(claim)`. It includes:
  - the expired commands;
  - a note that the suite worktree is being removed;
  - the results-capture links recorded by `publication.py` in the claim, when present,
    otherwise the retained result records on the Mac;
  - a note that a new request is needed to review a fresh run.
- **The card** is untouched: the expiry sets no status, no Verdict, and no label.
- **The handoff text.** `review_handoff` in the `and-scene` adapter replaces "Moving the item
  to Done releases the retained suite worktree, reviewed or not." with text that names both
  triggers and the configured number of days. `EvalHandler` passes
  `local.limits.unreviewed_retention_days` into the adapter.

### Pruning (`retention.py`)

- **Split the current `reconcile`:**
  - `observe_done(store, claim, board_status, now)` is called from the per-card loop for
    every claim, as the Done bookkeeping is today.
  - `prune_due(store, claim, board_status_or_none, now, local, handler)` implements the
    three spec paths.
- **Common gates,** already present:
  - `retention.pruned_at` is not set;
  - no non-terminal or unverified run;
  - no pending events or `delivery_failures`;
  - no pending post-merge sync for a settled claim, judged by `terminal.sync_pending`, so
    an open PR, or one closed without merging, does not block pruning.
- **Done path:** unchanged. It requires `board_status == "Done"` on this poll,
  `done_observed_at` older than `evidence_retention_days`, and `cleanup.complete`.
- **Cancelled or superseded path:** `terminal_at` older than `evidence_retention_days` and
  `cleanup.complete`.
- **Unreviewed path:** settled, `board_status != "Done"` (including `None`), `terminal_at`
  older than `unreviewed_retention_days`, and `cleanup.complete`.
- **Mechanics:** `_prune` and the removal targets are unchanged.
- **Removed:** the per-card `retention.reconcile` call. The sweep performs pruning for
  terminal claims. Non-terminal claims are never pruned.

### Registry image deletion (`fly/registry_cleanup.py`, `fly/api.py`)

`FlyMachinesClient` gains two methods. Both use the existing basic auth, `_OPENER`, and the
repository-prefix stripping of `resolve_manifest`.

- `list_tags(repository) -> list[str]`: `GET /v2/<repo>/tags/list`, following
  `Link: <...>; rel="next"` pagination.
- `delete_manifest(repository, digest) -> bool`: `DELETE /v2/<repo>/manifests/<digest>`.
  It returns true on `200`/`202` and false on `404`. It raises `FlyApiError` with the status
  otherwise. It refuses any reference that is not `sha256:` plus 64 hex characters, which
  enforces "never delete by tag" in code.

`resolve_manifest` already uses GET. A 404 from it is surfaced as
`FlyApiError(status=404)`.

`reconcile_claim_image(store, claim, client, now)` handles one claim:

1. Collect the distinct `(repository, tag, digest)` records from
   `run.progress["image_build"]` over `store.runs_for_claim`. If there are none, return
   without recording anything.
2. If `cleanup["registry"]["deleted_at"]` is set for every record, return.
3. **Gate:** all of these must hold, otherwise return without a request.
   - The lifecycle is terminal.
   - No run is in `NONTERMINAL_RUN_STATUSES`.
   - No `fly:machine:<run_id>` setting exists for any of the claim's runs.
   - None of those runs' Machine ids appears in `fly:cleanup-failed`.
4. `tags = list_tags(repo)`. For each tag other than the claim's own `claim-` tag, it takes
   `resolve_manifest(repo:tag)` and builds a digest → tags map. The per-tick cache is
   shared across claims.
5. Resolve the claim's own tag with `resolve_manifest(repo:claim tag)`. A 404 means the tag
   is absent. Then handle each recorded digest `D`, deciding in this order:
   - **Proven.** If the claim tag resolves to exactly `D` and no other tag maps to `D`,
     call `delete_manifest(repo, D)`.
     - A `true` result records `deleted_at`.
     - A `false` (404) result re-resolves the claim tag and `D`. If both are absent it
       records `deleted_at`; otherwise it records an error: "delete returned not-found but
       the image still resolves".
   - **Gone.** If the claim tag is absent, or resolves elsewhere, and GET of `D` returns
     404, record `deleted_at` without any DELETE.
   - **Not provable.** Record a skip, with a reason, and never DELETE:
     - another tag maps to `D`: "shared with <tag>";
     - the claim tag resolves to a different digest: "claim tag now points to <digest>".
       This covers an older build whose tag moved on;
     - the claim tag is absent while `D` still resolves: "untagged; ownership cannot be
       proven".

   A skip is re-evaluated each tick, because tags can change. It stays visible in `status`
   until the digest is deleted or is found gone.
6. **Errors.** Any `FlyApiError`, `OSError`, or unreadable token is recorded as
   `{"error": "<status or reason>", "at": now}`. A `405` or `UNSUPPORTED` answer is recorded
   the same way, as an error, not as success. The next tick retries.

Records live at `cleanup["registry"][<digest>]`. `reconcile_claim_image` is called in its
own `try`/`except Exception` inside the sweep, so it can never interrupt release, pruning,
other claims, or the cycle. It never writes `fly:cleanup-failed`, and
`FlyMachineBackend.reconcile` is untouched.

The sweep builds the client once per tick, lazily, from `local.fly` (`app`, `token_file`).
A claim with recorded images while no `[fly]` section is configured gets the error "no Fly
configuration to delete registry image". That error stays visible in `status`.

### Status (`operations.py`)

- `_needs_attention(claim)` additionally returns true when any of these hold:
  - a `cleanup["registry"]` record has an `error` or a `skipped`;
  - a `review-expired` event is pending or has a delivery failure;
  - a terminal claim that is due has `cleanup["last_error"]`.
- Superseded claims are no longer omitted when they need attention.
- `_cleanup_lines` adds `registry image <tag>@<short digest>: <reason>` and
  `review expiry not delivered: <reason>` lines. Superseded claims with nothing pending are
  still hidden without `--all`.

### Configuration and docs

- `LimitsConfig.unreviewed_retention_days: int = 30`, parsed with
  `_optional_positive_int(limits, "unreviewed_retention_days", "limits", 30)`. It is local
  configuration.
- `docs/operations.md` updates "Service management and storage" and "Evidence retention",
  and adds the registry cleanup and expiry comment. `docs/installation.md` lists the new
  limit.

## Decisions

- **D1. Keep `terminal_at` in `cleanup_json`, written with `json_set` in the lifecycle
  `UPDATE`, instead of adding a column.** A new column means schema version 5. Older
  releases then refuse the database (`version > SCHEMA_VERSION`), so `scripts/deploy.sh`'s
  automatic rollback to the previous release, and any still-running old-release process,
  would break. `json_set` inside the same statement keeps the write atomic. Older code
  preserves unknown keys, because every `set_cleanup` caller copies `claim.cleanup`.
- **D2. Delete by digest, never by tag, with a mandatory shared-digest guard.** A tag
  delete would only untag, leaving the manifest reachable and billable, and the probe
  showed Fly supports it only as untagging. A digest delete removes the manifest and every
  tag pointing at it. Checking all other tags first confines it to the claim's own image.
  The cost is at most one GET per tag per tick, and only while some claim still has an
  image pending deletion.
- **D3. Run registry deletion from the terminal sweep, not from
  `FlyMachineBackend.reconcile`.** This keeps Machine disposal free of registry latency
  and failures, as the spec requires, and ties deletion to claim state, which is where the
  gates live.
- **D4. Remove the whole `<root>/clones/<claim>/` directory on pull-request release.** It
  is factory-owned and per-claim, and the only way to reach earlier attempts' clones.
  Today only the last preparation's paths are recorded.
- **D5. A store-driven sweep after the per-card loop, with a `sweep_complete` flag.** This
  covers superseded and off-board claims. It costs one JSON flag check per finished claim
  per tick, and there are fewer than 100 claims.
- **D6. The expiry comment is an ordinary durable event.** It reuses the marker-based
  idempotent delivery and the existing "no pending events" gate, so no new ordering
  mechanism is needed.
- **D7. Drop the per-card `retention.reconcile` pruning call and prune only from the
  sweep.** This leaves one place that decides eligibility, and the per-card loop still
  records and resets Done observations.
- **D8. Write `terminal_at` only on a real lifecycle change, using a `CASE` on the old
  row value.** Same-lifecycle outcome updates (review activity recorded while a round waits
  for its slot) must not keep resetting the 30-day clock.
- **D9. Delete a digest only on current proof of ownership.** The claim tag must resolve to
  exactly that digest and no other tag may use it. Anything else is a visible skip, unless
  GET shows the image is already gone. A 404 on DELETE counts as gone only after the
  re-resolve confirms it. An older build whose tag moved on stays in place, visibly.
- **D10. For cleanup and retention, a sync is pending only after the PR has merged, and the
  sweep never runs a sync.** An open PR can still be reviewed in fresh clones, and a PR
  closed without merging never syncs, so neither should pin files. Running `merge_sync`
  for off-board claims would post comments on and close issues the operator removed from
  the Project, which is a new external effect this change does not need.
- **D11. The Done path gets the same undelivered-reporting gate as the sweep.** Otherwise a
  Done move right after a failed post would publish a review command for a worktree that
  has already been removed.

## Risks / Trade-offs

- **First-deploy burst.** The first tick may release and prune about 20 cancelled and
  superseded claims and delete about seven registry images. This is bounded by the claim
  count, and failures are per item and retried. Rollback is safe (D1). Deleted files cannot
  be restored, which is intended.
- **Backfill from `updated_at` can overstate age only in one direction.** It makes a claim
  look younger, never older. The worst case is a later release than ideal.
- **Tag listing cost.** The per-tag GETs grow with tag count. Deleting old tags shrinks the
  list, so this is self-limiting. The cache is per tick.
- **Registry behavior could change.** A future `405` or `UNSUPPORTED` is recorded as a
  visible error and retried each tick. That is noisy but safe. No deletion by tag or
  broader deletion can occur, because the client rejects non-digest references.
- **Ambiguous run ownership.** Every gate uses `NONTERMINAL_RUN_STATUSES`, which includes
  `observing`, the status the store keeps while ownership or completion is ambiguous. A
  run that is still unverified therefore always blocks release, pruning, and registry
  deletion.
- **Alternatives considered:**
  - a separate nightly job, rejected because it duplicates claim-state logic outside the
    factory;
  - schema v5, rejected under D1;
  - untagging only, rejected under D2;
  - registry deletion inside Fly reconciliation, rejected under D3.

## Migration Plan

1. Deploy with `scripts/deploy.sh`. No schema migration runs. On the first tick:
   - every terminal claim without `terminal_at` is backfilled from `updated_at`;
   - claims already past their period are released and pruned in that tick or the next;
   - eligible registry images are deleted;
   - settled `pending-human-review` evals older than 30 days outside Done get one expiry
     comment, and their worktrees are released on the following pass.
2. To delay the release of old Review evals, set `[limits] unreviewed_retention_days` in
   `~/.agent-factory/config.toml` to a larger value before deploying.
3. Rollback: redeploy the previous release. The extra `cleanup_json` keys are ignored, and
   anything already removed stays removed.
4. After deploy, check `agent-factory status` for cleanup, registry, or expiry lines, and
   use `du -sh ~/.agent-factory/*` to confirm the reduction.

## Open Questions

None. The registry behavior that the spec deferred was confirmed by the 2026-09-28 probe
described in Context.
