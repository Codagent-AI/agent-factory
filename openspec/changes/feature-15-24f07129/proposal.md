## Why

The factory's disk and registry footprint grows without bound for every claim whose card
does not reach Done, and the growth is now blocking work:

- On 2026-09-23 an eval was not admitted because free space was 4.1 GiB, under the 5 GiB
  `minimum_free_gib` floor. It ran only after caches were cleared by hand. At that time
  `~/.agent-factory` held 3.3 GB of artifacts (44 run directories), 1.5 GB of clones and
  464 MB of worktrees, across 13 settled, 11 cancelled, 10 superseded and 1 blocked claims.
  By 2026-09-25 artifacts alone had reached about 6.1 GiB, with new eval repetition
  directories appearing several times a day.
- Every release path is gated on the board. Eval worktrees, fix and feature clones, run
  images, and credential copies are released only after the card goes Review then Done
  (`WorktreeCleanup`, `PullRequestCleanup`). Evidence pruning (`retention.py`) only runs
  while the card is currently Done, and the spec forbids pruning any claim whose card is
  anywhere else or has left the board. A superseded claim never gets a cleanup pass at
  all, and a cancelled eval claim's worktrees are never released. Cards that stay in Review
  or get parked elsewhere keep everything forever.
- The operator's nightly low-disk script deliberately leaves factory state alone, because
  only the factory knows which claims are active or resumable. Clearing it by hand is
  therefore unsafe as well as tedious, so the factory has to do it.
- Each Fly eval claim pushes a `claim-<12 characters of the claim id>` tag to the sandbox
  app's registry (agent-factory#14). The factory pins the digest for launches but never
  removes the tag. On 2026-09-28 the registry held seven `claim-` tags, each with its own
  distinct manifest, besides `base` and one `deployment-` tag. This cleanup was explicitly
  deferred from #14.

The audience is the operator of the factory Mac (Paul). The outcome is a factory that stays
under its disk floor without manual cleanup and removes the registry tags and manifests it
created once the claim that needed them is finished.

## What Changes

1. **Record when a claim became terminal.** Every transition into `settled`, `cancelled`,
   or `superseded` durably records its time on the claim (`terminal_at`), including a
   re-settlement after a pull-request review round. All age clocks in this change start
   at that time.
2. **Keep review paths intact.**
   - A settled fix or feature claim can still be resumed by a review round on its open PR
     (`review.py`). Each round cuts fresh per-attempt clones and reads only the kept outcome
     and PR records, so releasing earlier attempts' clones does not affect it. A new run on
     a claim reopens that claim's cleanup, so the new round's clones, images, and credential
     copies are released later rather than skipped by the `complete` short-circuit.
     Release covers every attempt's recorded clones, not only the latest preparation.
   - A settled eval claim awaiting human review has a published handoff that runs
     `human-review.sh` from its retained evals worktree. Its worktrees stay until the card
     reaches Done or the handoff expires (see 3). Release never runs while any of the
     claim's reporting, including that handoff, is still undelivered. This ensures a late
     handoff never points at a removed worktree.
3. **Release finished claims without waiting for Done.** A claim that cannot run again
   becomes eligible for release of its recorded factory-owned worktrees, clones, run images,
   and credential copies, whatever its card status. The claim must meet all of these:
   - its lifecycle is `settled`, `cancelled`, or `superseded`;
   - no run is non-terminal or of unverified ownership;
   - its reporting has been delivered;
   - no post-merge sync is pending. A sync counts as pending only once the PR has merged,
     so an open PR, or one closed without merging, does not pin files.

   Eligibility depends on the lifecycle:
   - `cancelled` and `superseded` claims: released on the first poll that meets these
     conditions. This extends the existing cancelled-fix rule to eval claims and adds the
     missing pass for superseded claims.
   - `settled` claims whose card is not Done (typically left in Review): released once a
     new **unreviewed retention period**, `[limits] unreviewed_retention_days` (default
     30), has elapsed since `terminal_at`. Before releasing a settled eval claim's
     worktrees, the factory posts an issue comment. It says the human-review command has
     expired and that a new request is needed to review a fresh run. The comment is posted
     first and is delivered like any other factory report, so a failed post delays release
     instead of hiding the expiry. The existing Review-then-Done release is unchanged and
     normally happens sooner.
4. **Prune evidence outside Done.** The existing evidence pruning (same enumerated paths,
   same kept records) also applies to:
   - cancelled and superseded claims once `evidence_retention_days` (default 14) has
     elapsed since `terminal_at`;
   - settled claims outside Done once the unreviewed retention period has elapsed and
     their release has completed.

   All of the existing guards still apply. The current-Done path is unchanged.
5. **Cover claims that are no longer on the board.** The release and pruning sweep runs over
   terminal claims recorded in the store, not only over cards returned by the project
   query. A card that was removed from the project no longer pins its claim's files
   forever.
6. **Delete a finished eval claim's Fly image tag and manifest.** Deletion happens once an
   eval claim meets all of these:
   - it is terminal;
   - it has no non-terminal run;
   - it holds no Machine.

   The factory then deletes the manifest it recorded for the claim's own `claim-` tag from
   the sandbox registry. It does so only after checking two things:
   - the tag still resolves to the recorded digest;
   - no other tag in the repository resolves to that digest.

   If another tag shares the digest, the factory skips the deletion and reports it; it
   never deletes a manifest that another tag still uses. Active, waiting, and blocked claims
   keep their image. The deletion is recorded and retried in the claim's own cleanup state,
   separately from Machine disposal. It never blocks or fails disposal, reconciliation,
   admission, or other claims. A failure, including a registry that refuses deletion, stays
   a visible cleanup failure in `status`. It is not recorded as success.
   Reclaiming the deleted blobs' storage is up to Fly's registry.
7. **Observability and docs.** `status` shows cleanup, handoff-expiry, and registry
   failures that are still pending. `docs/operations.md` ("Service management and
   storage", "Evidence retention") and `docs/installation.md` describe the new clocks,
   periods, and paths. `[limits] unreviewed_retention_days` is a new optional setting. It
   is additive and there are no breaking changes.

## Capabilities

### New Capabilities

None. Every behavior extends an existing capability.

### Modified Capabilities

- `factory-operations`:
  - "Clean up worktrees after review" gains:
    - release of terminal claims outside Done;
    - the reporting-delivered and reopen-on-new-run rules;
    - release of every attempt's clones;
    - a store-driven sweep that covers claims no longer on the board.
  - "Retain evidence for a bounded period" drops the currently-Done condition for
    cancelled, superseded, and expired unreviewed settled claims, and defines each clock
    from `terminal_at`.
  - "Expose current operational status" reports pending cleanup and registry failures.
  - The configuration surface gains `unreviewed_retention_days`.
- `factory-claim-lifecycle`: "Persist accepted work and execution history" records the time
  of each transition into a terminal lifecycle.
- `factory-eval-reporting`: a settled eval whose unreviewed period expires reports the
  expiry of its human-review command before its worktrees are released. The review
  command's comment states the expiry.
- `factory-eval-execution`: "Preserve suite-owned evidence and candidate outputs" keeps the
  suite worktree until Done or expiry, not only until Done.
- `factory-fly-execution`: a new requirement, "Remove a finished claim's registry image",
  deletes the claim's own `claim-` manifest once the claim is terminal and holds no
  Machine. It covers the deletion's guards, failures, and retries, and keeps them separate
  from Machine disposal and stale-Machine reconciliation. The build requirement is
  unchanged.

## Technical Approach

- **One release routine, two gates.** Keep the existing per-kind cleanup classes and
  `retention.py`. Give both a board-independent "terminal and quiescent" gate, which
  requires all of these:
  - the lifecycle is terminal;
  - no run is non-terminal or of unverified ownership;
  - no Fly Machine record is held;
  - no reporting is pending;
  - no sync is pending.

  The board-driven Review-then-Done path and the new age-based path call the same release
  routine, so what gets deleted and how failures are recorded (`claim.cleanup`) do not
  change. Release still touches only recorded factory-owned paths, image tags, and
  credential copies. It never touches mirrors, shared checkouts, the operator's working
  clones, branches, PRs, or SQLite history. Launching a new run on a claim resets its
  cleanup completion, so later review rounds are covered.
- **Clocks come from recorded transitions.** `terminal_at` is written in the same
  transaction as the lifecycle change: `set_claim_lifecycle`, and the superseding update in
  `supersede_and_create`. A historical row has no `terminal_at`, so the factory backfills
  it once from the claim's `updated_at`. That column was set by the transition itself and
  only moves forward afterwards, so it is never earlier than the real transition. It errs
  towards keeping files longer, and claims idle since they finished still become eligible
  soon after deploy. Run `finished_at` is not used, because a claim can finish its runs
  weeks before it is cancelled or superseded.
- **Store-driven sweep.** After the per-card loop, each tick iterates over terminal claims
  whose cleanup and pruning are not yet complete. That set is small and shrinks as claims
  finish. Board-derived state is not needed for the age-based path, so claims that left the
  board are included safely.
- **Registry deletion.** Extend the narrow Fly registry client (`fly/api.py`) with three
  operations, all using basic auth as `resolve_manifest` does:
  - tag listing (`GET /v2/<repo>/tags/list`);
  - a manifest GET;
  - manifest delete by digest (`DELETE /v2/<repo>/manifests/<digest>`).

  The evidence gathered on 2026-09-28 against the live `agent-factory-sandbox` registry,
  using only reads and one DELETE of a nonexistent digest:
  - tags/list works;
  - every existing tag has a distinct digest;
  - DELETE is accepted by the endpoint: it answered `404 MANIFEST_UNKNOWN` for an unknown
    digest, not `405 UNSUPPORTED`.

  During design, a disposable probe image confirmed the end-to-end effect: a delete by
  digest removes the manifest and every tag pointing at it, while a delete by tag only
  untags. See `design.md`, Context. The shared-digest guard is therefore mandatory, and
  deletes go by digest only. A 404 on delete counts as already deleted. A per-image record
  in `claim.cleanup["registry"]` makes retries idempotent across restarts. The factory
  never deletes `base`, `deployment-` tags, other claims' tags, or any manifest that another
  tag references.
- **Isolation from Machine disposal.** Tag deletion runs from per-claim cleanup, not from
  `FlyMachineBackend` reconciliation, and its errors go to `claim.cleanup`, not to
  `fly:cleanup-failed`. A registry outage therefore cannot hold a Machine, block admission,
  or change a result.

## Out of Scope

- Deleting whole artifact directories, outcome and result records, provenance, issue
  inputs, candidate branches, PRs, mirrors, or SQLite rows. Pruning stays enumerated.
- Files that no claim record references (orphans from crashes or manual runs), the Agent
  Runner build, Docker Desktop's disk image or build cache beyond per-run images, and
  `~/.agent-runner` state outside factory attempts.
- Mirror growth and garbage collection.
- Registry tags the factory did not create (`base`, `deployment-` tags, hand-pushed tags),
  tags that cannot be tied to a recorded claim digest, and Fly's own garbage collection of
  unreferenced blobs.
- Disk-pressure-driven eviction (deleting sooner because space is low). Cleanup stays
  age- and lifecycle-based.
- Changing `minimum_free_gib` or the operator's nightly low-disk script.

## Impact

- **Code:**
  - `src/agent_factory/store.py` (`terminal_at`, backfill);
  - `src/agent_factory/retention.py`;
  - `src/agent_factory/work_kinds/pull_request/cleanup.py`;
  - `WorktreeCleanup` in `src/agent_factory/suites/and_scene/__init__.py`;
  - the cleanup and reporting of the eval and pull-request handlers;
  - the tick loop in `src/agent_factory/runtime.py`;
  - `src/agent_factory/fly/api.py` (tag list, manifest delete);
  - `src/agent_factory/config.py` (`unreviewed_retention_days`);
  - `src/agent_factory/operations.py` (status lines).
- **Persisted state:** new optional keys: `terminal_at`, the registry deletion record, and
  the handoff-expiry record. They are additive, and existing rows remain valid through the
  one-time backfill.
- **Specs:** `factory-operations`, `factory-claim-lifecycle`, `factory-eval-reporting`,
  `factory-eval-execution`, and `factory-fly-execution`, plus `docs/operations.md` and
  `docs/installation.md`.
- **Operator:**
  - The first ticks after deploy release and prune cancelled and superseded claims that
    have been untouched for 14 days or more. Settled claims outside Done that have been
    untouched for more than 30 days are released too.
  - A settled eval left in Review gets an expiry comment, then loses its human-review
    worktree after the unreviewed period. The operator can lengthen that period or move
    the card to Done sooner.
  - Fix and feature review rounds keep working after release.
- **External systems:** authenticated tag-list, manifest GET, and manifest DELETE calls to
  `registry.fly.io` for the sandbox app, limited to the factory's own `claim-` manifests.
  Design pushed and then deleted one disposable probe image, under two `factory-probe-`
  tags, on 2026-09-28.
