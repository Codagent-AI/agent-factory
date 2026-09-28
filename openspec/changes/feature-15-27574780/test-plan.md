## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration and end-to-end obligations, the acceptance testing envelope, and exceptional
human-only obligations.

The change is mostly safety logic: what may be deleted, and when. Its failures appear at
four boundaries:

- the real SQLite store, where terminal times, reopening, and backfill must be atomic and
  must not break older releases;
- the real filesystem, where release must remove exactly the owned paths, including
  read-only trees and earlier attempts;
- the registry HTTP contract, where deletes go by digest only, the shared-digest guard
  applies, and error mapping must be right;
- the tick loop's wiring and ordering: the sweep over off-board and superseded claims,
  the expiry comment before release, and registry isolation from Machine disposal.

Unit tests cover the pure eligibility and clock decisions (`terminal.release_due`,
`retention.prune_due`, and the configuration default and validation), driven by the spec
scenarios.

- **Integration tests** use the existing patterns: a real `ClaimStore` on `tmp_path`, real
  git worktrees and clones, and the loopback `FakeMachinesApi` extended with registry
  endpoints.
- **E2E tests** reuse the CLI harnesses in `tests/e2e/test_factory_cycle.py`,
  `test_fix_cycle.py`, and `test_fly_eval_cycle.py`, which run real subprocess ticks
  against a stub GitHub and fake Fly. Time is advanced by rewriting the claim's recorded
  `terminal_at`, or its `done_observed_at`, in the test's own SQLite between ticks, so no
  test sleeps or depends on the wall clock.
- **CI command:** everything runs under `uv run pytest`, which is the validator's `test`
  check. No new Docker-marked test is required.

## Integration Tests

### INT-001: Terminal time is written atomically, reopened by a new run, and backfilled once
- Covers: `factory-claim-lifecycle` "Persist accepted work and execution history" (all new
  scenarios). The reopened-cleanup part of `factory-operations` "Clean up worktrees after
  review". Design D1 and D8.
- Boundary: `ClaimStore` on a real SQLite file: `set_claim_lifecycle`,
  `supersede_and_create`, and `reserve_run`, with `json_set` on `cleanup_json`.
- Setup: A temporary store with claims created through `ClaimDraft`. One claim's
  `cleanup_json` holds existing keys (`paths`, `review_observed`, and
  `retention.removed`). Another claim is inserted with a terminal lifecycle and no
  `terminal_at`, simulating a pre-change row with a known `updated_at`.
- Action:
  - Settle, cancel, and supersede claims.
  - Call `set_claim_lifecycle(claim, "settled", outcome)` five more times on an already
    settled claim, as `review.process_review_claim` does while a round waits for its slot.
    Then supersede that claim.
  - Reserve a new run on a settled claim that had `complete: true` and
    `retention.pruned_at`.
  - Read the pre-change row through `terminal.terminal_time`, twice, with an unrelated
    claim write in between.
  - Reopen the database with a fresh `ClaimStore`.
- Assertions:
  - `terminal_at` is present immediately after each terminal transition, with no separate
    write, and existing cleanup keys are preserved.
  - Re-settlement replaces `terminal_at`.
  - The repeated settled-to-settled updates leave `terminal_at` exactly as first written,
    while superseding then writes a new one.
  - `reserve_run` clears `complete`, `review_observed`, `terminal_at`, `sweep_complete`,
    and `retention.pruned_at`, and keeps `retention.removed` and `registry`.
  - The backfilled value equals the row's `updated_at` as first read and does not change
    on the second read.
  - `PRAGMA user_version` is still 4 after all operations.
  - A `set_cleanup` performed the way the current (pre-change) code calls it preserves
    `terminal_at`.
- Execution: `tests/integration/test_terminal_time.py`, run by `uv run pytest`.

### INT-002: Terminal release removes exactly the owned files, only when quiescent
- Covers: `factory-operations` "Clean up worktrees after review":
  - release a cancelled eval claim;
  - release a superseded claim;
  - release a settled claim left in Review;
  - wait for undelivered reporting;
  - retry incomplete cleanup;
  - preserve worktrees still in use.

  Also `factory-eval-execution` "Release an expired review worktree", and design D4.
- Boundary: `EvalHandler.release`, `WorktreeCleanup`, `GitWorktreeManager`,
  `PullRequestHandler.release`, `PullRequestCleanup`, `PullRequestWorkspace`, and the real
  filesystem and git.
- Setup:
  - Real source repositories with eval worktrees created by the factory's worktree manager.
  - A pull-request claim with three attempt directories under `<root>/clones/<claim>/`.
    Only the last is recorded in `preparation["clones"]`, and one contains a read-only
    tree as Go's module cache leaves it.
  - A private credential directory per run, a mirror, an operator working clone, and a
    second claim's clone directory.
  - Claims in each gate state:
    - a run in `observing`;
    - a run in `running`;
    - a pending report event;
    - a recorded `delivery_failures`;
    - settled pull-request claims past the unreviewed period whose PRs are, in the stub
      GitHub client:
      - open;
      - closed without merging;
      - merged with the sync incomplete;
      - unreadable (a raised `GitHubApiError`);
    - a settled eval and a settled fix claim whose cards are Done with `review_observed`,
      each holding a pending report event;
    - an `active`, a `waiting`, and a `blocked` claim, each whose card sits in Review.
- Action:
  - Call the sweep's release step (`terminal.sweep` with a `seen` map and the stub client)
    for each claim.
  - Call `handler.cleanup(claim, board_status="Done")` for the two Done claims, before and
    after acknowledging their event.
  - In one case, make a removal fail by denying permission on a parent, then restore it
    and sweep again.
- Assertions:
  - Eligible claims lose exactly their recorded worktrees, every attempt clone, the claim
    clone directory, run image tags (through a patched `agent_factory.work_kinds.images.subprocess.run`, as in `test_fix_cleanup.py`), and credential
    copies.
  - The mirror, the working clone, the shared source checkouts, and the other claim's
    directory are byte-for-byte untouched.
  - Every gated claim keeps everything. The claims whose PR is open or closed without
    merging are released. The merged-unsynced claim and the claim whose PR read failed are
    kept, and the sweep never attempts a sync.
  - The Done claims keep their files while the event is pending, and lose them only after
    it is acknowledged.
  - The permission failure is recorded in `cleanup.last_error` with `complete: false`,
    and the second sweep completes without a new error for the already-removed paths.
- Execution: `tests/integration/test_terminal_release.py`, run by `uv run pytest`.

### INT-003: Evidence retention follows the Done, cancelled or superseded, and unreviewed paths
- Covers: `factory-operations` "Retain evidence for a bounded period" (all scenarios).
  `factory-eval-execution` "Prune a settled eval's evidence".
- Boundary: `retention.observe_done`, `retention.prune_due`, and `_prune` over a real
  `ClaimStore` and real evidence trees, for eval repetitions and pull-request attempts.
- Setup: Extend `tests/integration/test_retention.py`. Claims have evidence trees and
  recorded terminal times or Done observations set relative to a fixed `now`. The cases
  include:
  - a claim superseded 3 days ago whose runs finished 40 days ago;
  - a claim whose card moved from Review to Done on day 25;
  - an off-board settled claim (`board_status=None`);
  - a claim whose release is incomplete;
  - a cancelled claim with a recorded PR.
- Action: Run `observe_done` and `prune_due` for the relevant polls.
- Assertions:
  - Each claim is pruned exactly when its path allows and never earlier.
  - The superseded 3-day claim is kept until day 14 after superseding.
  - The late-Done claim is kept until 14 days after the Done observation, even past day 30.
  - The off-board claim is pruned by the unreviewed path.
  - An incomplete release blocks pruning.
  - The kept records (outcome, result, provenance, and `input/issue.json`) and any
    unknown files survive.
  - A new run after pruning makes the new attempt's evidence prunable again, while the
    earlier `removed` record stays.
- Execution: `tests/integration/test_retention.py`, run by `uv run pytest`.

### INT-004: Registry client contract for tag listing, manifest resolution, and digest-only delete
- Covers: `factory-fly-execution` "Remove a finished claim's registry image" (the client
  side), and design D2.
- Boundary: `FlyMachinesClient.list_tags`, `resolve_manifest`, and `delete_manifest` over
  real HTTP against `FakeMachinesApi`. The fake is extended to hold tag → digest state,
  and it reproduces the semantics the 2026-09-28 probe observed:
  - a delete by digest removes the manifest and every tag pointing at it;
  - a delete by tag removes only that tag;
  - a repeated delete returns 404;
  - `tags/list` names the repository by an internal id and paginates with a `Link`
    header.
- Setup: The fake is seeded with `base`, a `deployment-` tag, two `claim-` tags, and an
  extra tag sharing one claim's digest. Scripted statuses cover 202, 404, 401, 405 with an
  `UNSUPPORTED` body, 500, and a connection reset.
- Action: List the tags across pages, resolve tags, delete by digest, delete again, and
  attempt a delete with a tag or a malformed reference.
- Assertions:
  - Requests carry basic auth, never bearer auth.
  - The pagination result is complete.
  - A delete returns true on 202 and false on 404.
  - 401, 405, and 500 raise `FlyApiError` carrying the status and reason.
  - A non-digest reference raises before any request is sent; the fake records no DELETE.
  - Cleartext non-loopback endpoints are refused, as for the other calls.
- Execution: `tests/integration/test_fly_api.py`, run by `uv run pytest`.

### INT-005: Claim image deletion is gated, guarded, idempotent, and isolated
- Covers: `factory-fly-execution` "Remove a finished claim's registry image" (all
  scenarios except the full-cycle outage, which E2E-002 covers). Design D3.
- Boundary: `fly.registry_cleanup.reconcile_claim_image` with a real `ClaimStore` (runs
  with `progress["image_build"]`, `fly:machine:<run>` settings, and `fly:cleanup-failed`)
  and `FakeMachinesApi`.
- Setup: Claims for these cases:
  - settled with Machines gone;
  - waiting with a stopped Machine record;
  - settled but a Machine id in `fly:cleanup-failed`;
  - superseded, with a fresh claim that has its own tag;
  - digest shared with another tag;
  - `claim-` tag moved to an unrecorded digest;
  - `claim-` tag absent while the recorded digest still resolves;
  - two distinct recorded digests with the claim tag pointing at the newer one;
  - tag and digest already gone;
  - a DELETE scripted to answer 404 while the fake still resolves the digest, and another
    answered 404 after the fake has removed it;
  - no recorded build;
  - two runs recording the same digest.
- Action:
  - Run the per-claim reconcile, then again after reopening the store.
  - Script a 500, then a 202.
  - Remove the `[fly]` configuration for one case.
- Assertions:
  - Only the eligible claims' recorded digests are deleted.
  - `base`, `deployment-`, the fresh claim's tag, and every shared or moved digest remain.
  - The untagged live digest and the older of the two builds are skipped with their
    reasons, and no DELETE is sent for either. The newer build is deleted.
  - The already-gone case records `deleted_at` with no DELETE sent.
  - The 404-but-still-resolves case records an error. The 404-and-gone case records
    `deleted_at`.
  - Skips and errors are recorded under `cleanup["registry"]`, with no `deleted_at`.
  - The 500 is retried and then recorded as deleted.
  - After `deleted_at`, a reopened store makes zero registry requests for that claim.
  - The no-build claim makes zero requests and records nothing.
  - The missing `[fly]` configuration records the visible error.
  - Nothing is ever written to `fly:cleanup-failed`.
- Execution: `tests/integration/test_fly_registry_cleanup.py`, run by `uv run pytest`.

### INT-006: `status` surfaces pending cleanup, expiry, and registry failures
- Covers: `factory-operations` "Expose current operational status", including "Inspect a
  failed registry image deletion" and the listing rules for superseded claims.
- Boundary: `agent-factory status` and `status --all` against a prepared real state
  database, through the CLI entry point as the existing status tests invoke it.
- Setup: A state database holding:
  - a superseded claim with a registry error;
  - a superseded claim with a registry skip for a shared digest;
  - a settled eval with an undelivered `review-expired` event and a delivery failure;
  - a cancelled claim with `cleanup.last_error`;
  - a superseded claim with nothing pending;
  - a Done claim with nothing pending.
- Action: Run `status`, then `status --all`.
- Assertions:
  - Plain `status` lists the four claims with failures, with the tag and short digest,
    the reason, and the expiry line.
  - It omits the clean superseded and Done claims.
  - `--all` lists all six.
  - No state changes.
- Execution: `tests/integration/test_operations_status_sync.py`, or a sibling file, run by
  `uv run pytest`.

## End-to-End Tests

### E2E-001: An unreviewed eval expires, is announced once, then is released and pruned
- Covers:
  - `factory-eval-reporting`: "Provide executable human-review instructions" and "Report
    the expiry of unreviewed human-review commands" (all scenarios);
  - `factory-eval-execution`: "Preserve suite-owned evidence and candidate outputs";
  - `factory-operations`: the settled-in-Review release and the unreviewed pruning path.
- Surface: the factory CLI `tick`, using the stub GitHub and eval harness of
  `tests/e2e/test_factory_cycle.py`.
- Setup:
  - An eval request settles `pending-human-review` in Review with a delivered review
    command.
  - A second eval settles `failed` with no review command.
  - A third settles `pending-human-review` and its card is moved to Done on day 20.
  - A fourth settles `pending-human-review` while the stub GitHub fails its review-command
    post. Its card is then moved to Done before the post is retried.
  - `[limits] unreviewed_retention_days` is left at its default.
- Journey:
  - Run the tick through settlement. The review-command comment is posted.
  - Age the first two claims' `terminal_at` past 30 days and tick.
  - Make the stub GitHub fail comment creation once and tick. Restore it and tick.
  - Age the Done claim past day 30 and tick.
  - For the fourth claim, tick with its card in Done while the post still fails, then
    restore GitHub and tick twice.
- Assertions:
  - The review-command comment states the Done-or-30-days expiry.
  - While the post fails, the reviewable claim keeps its worktrees and `status` shows the
    undelivered expiry.
  - After recovery, exactly one expiry comment exists, carrying the claim marker. Its
    worktrees are removed on that tick or the next, and its evidence is pruned. Its card
    status and Verdict are unchanged, and the issue stays open.
  - The failed eval gets no expiry comment but is still released.
  - The Done eval gets no expiry comment and keeps its evidence until 14 days after its
    Done observation.
  - The fourth eval keeps its worktrees while its review command is undelivered, even
    though its card is Done. After recovery the command is posted exactly once, and the
    worktrees are removed on that tick or the next.
- Execution: `tests/e2e/test_factory_cycle.py`, or `tests/e2e/test_terminal_cleanup.py`,
  run by `uv run pytest`.

### E2E-002: A finished Fly eval's image is deleted without affecting Machine disposal
- Covers: `factory-fly-execution` "Remove a finished claim's registry image": "Delete a
  settled claim's image", "Survive a registry outage", and "Restart after a deletion".
  Design D3.
- Surface: the factory CLI `tick`, run as in `tests/e2e/test_fly_eval_cycle.py`, with
  `FakeMachinesApi` (extended with the registry) and the `flyctl` double.
- Setup:
  - A Fly eval claim runs to settlement, which records the build digest.
  - The fake registry also holds `base`, a `deployment-` tag, and another claim's tag.
  - A second claim's Machine is left past its deadline so reconciliation must destroy it
    on the same tick.
  - The first registry DELETE is scripted to return 500.
- Journey: Tick after settlement, restart the controller, then tick again.
- Assertions:
  - On the first tick, the overdue Machine is destroyed and its record cleared.
  - The failed deletion is recorded on the claim and shown by `status`, and
    `fly:cleanup-failed` stays empty.
  - On the next tick, the claim's manifest is deleted by digest, after which neither its
    tag nor its digest resolves in the fake.
  - `base`, the `deployment-` tag, and the other claim's tag remain.
  - A further tick sends no registry request for the deleted image.
- Execution: `tests/e2e/test_fly_eval_cycle.py`, run by `uv run pytest`.

### E2E-003: Pull-request claims are released outside Done, and review rounds keep working
- Covers:
  - `factory-operations` "Clean up worktrees after review": "Release a settled claim left
    in Review", "Reopen cleanup for a review round", and "Release a superseded claim";
  - `factory-claim-lifecycle` "Re-settle after a review round".
- Surface: the factory CLI `tick`, using the host fix journey harness in
  `tests/e2e/test_fix_cycle.py`.
- Setup:
  - A bug is fixed through two attempts: a recovery retry, then a success with an open PR.
    It settles in Review.
  - A second bug's settled claim is superseded when a writer moves its card back to
    Ready.
  - A third bug settles with a PR that the stub GitHub then reports merged, while its
    working clone has uncommitted changes, so its post-merge sync stays blocked.
  - A fourth, like the third, has its card removed from the Project.
- Journey:
  - Age the first claim past 30 days and tick.
  - Post an eligible writer review comment on its open PR, and tick through the review
    round to re-settlement.
  - Age it again and tick.
  - Separately, tick after the second claim is superseded and its fresh claim is running.
  - Age the third and fourth claims past 30 days and tick.
  - Clean the third claim's working clone, tick, and tick again.
- Assertions:
  - Both attempt directories, the claim's clone directory, and the credential copies are
    removed after the first aging, while the PR, branch, and outcome remain.
  - The review round runs in fresh clones and pushes to the same PR.
  - After re-settlement, `terminal_at` is new and cleanup is incomplete. The round's clones
    are kept until the second aging, then removed.
  - The superseded claim's clones are removed while its fresh claim's clones stay intact
    and its run proceeds.
  - The first claim's release at 30 days happens with its PR still open, so an open PR
    does not block it.
  - The third and fourth claims keep their clones and evidence while their syncs are
    pending, and `status` shows both pending syncs.
  - After the third claim's working clone is cleaned, its sync completes and the claim is
    then released and pruned.
  - The fourth claim, off the Project, is never synced by the sweep and keeps its files.
- Execution: `tests/e2e/test_fix_cycle.py`, run by `uv run pytest`.

### E2E-004: The first tick after upgrade sweeps pre-existing history safely
- Covers:
  - the store-driven sweep in `factory-operations` "Clean up worktrees after review",
    including "Release a claim whose card left the Project";
  - "Sweep pre-existing history" in the retention requirement;
  - the backfill in `factory-claim-lifecycle`.
- Surface: the factory CLI `tick` against a state root prepared to look like one written
  by the previous release.
- Setup: A state root built with the harnesses and then stripped of the new keys: no
  `terminal_at`, and `updated_at` set in the past. It holds:
  - cancelled and superseded eval and fix claims with worktrees, clones, and evidence;
  - a settled claim whose card was removed from the Project;
  - an `active`, a `waiting`, and a `blocked` claim, each with files;
  - a recent cancelled claim (`updated_at` 2 days ago).
- Journey: Run one tick, then a second tick.
- Assertions:
  - Old cancelled and superseded claims are released and pruned.
  - The off-board settled claim older than 30 days is released and pruned, from the store
    record alone.
  - The recent cancelled claim is released but its evidence is kept.
  - The active, waiting, and blocked claims keep every file.
  - Every terminal claim now has `terminal_at` equal to its prior `updated_at`.
  - The second tick removes nothing new, sends no new request, and reports no error.
  - The schema version is unchanged.
- Execution: `tests/e2e/test_terminal_cleanup.py`, run by `uv run pytest`.

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - the change's worktree on the factory Mac, with `uv run` and pytest;
  - temporary storage roots under `tmp_path` or `mktemp -d`;
  - the stub GitHub, `FakeMachinesApi`, and `flyctl` doubles in `tests/`;
  - local Docker (29.x), available only for `docker rmi` of images the pass itself tagged.
- **Credentials and secrets:**
  - The factory's Fly deploy token is at `~/.agent-factory/credentials/fly-deploy-token`.
    Read it only into process memory for registry calls, and never print it or write it
    to Docker configuration.
  - The GitHub App key is in `~/.agent-factory/config.toml` `[credentials]`. It must not be
    used; use the stub GitHub instead.
- **Authorized effects:**
  - Against the live `registry.fly.io/agent-factory-sandbox`, read-only `tags/list` and
    manifest GETs are allowed.
  - The pass may push and then delete its own disposable images under tags that start
    with `factory-probe-`, using raw Registry v2 calls as the design probe did. This
    exercises the real `delete_manifest` and `list_tags` against Fly. Each probe tag must
    be deleted before the pass ends, verified with `tags/list`. The cost is negligible,
    because the images are a few hundred bytes.
- **Off limits:**
  - Deleting or overwriting any `claim-`, `base`, or `deployment-` tag.
  - Running `tick`, `resident`, or `deploy.sh` with the live configuration. That includes
    any command that reads `~/.agent-factory/config.toml` as its configuration, since it
    would act on the real Project and post real comments.
  - Modifying `~/.agent-factory/state.sqlite3`, `artifacts/`, `clones/`, `worktrees/`,
    `releases/`, or the LaunchAgent.
  - Posting to GitHub issues or PRs.
  - Creating Fly Machines.
- **Permitted substitutes:**
  - The stub GitHub and fake Fly APIs substitute for the real services in every
    lifecycle journey.
  - A copy of the live `state.sqlite3` may be opened read-only in a temporary directory to
    check how the new eligibility functions classify real historical claims (backfilled
    clocks, which claims would be released or pruned). Release, prune, and registry code
    must never run against that copy, because it records absolute paths into the live
    storage root.
- **Known risk areas:**
  - The reach of a digest delete: the probe showed it removes every tag pointing at the
    digest, so the shared-digest guard is the only safety net.
  - Comparing timezone-aware timestamps: `_now()` writes UTC ISO strings, while the cycle's
    `now` uses the schedule timezone.
  - Done flapping between polls.
  - Ordering of the expiry comment before release, and its idempotence under delivery
    failures.
  - Read-only trees in clones.
  - Superseded claims skipped by the per-card loop's `continue`.
  - `json_set` against the default `'{}'` and legacy rows.
  - Reopening on `reserve_run` must not wipe historical removal records.
  - Accepted limitations: orphan paths with no claim record, and registry tags without a
    recorded digest, are left alone. Fly's blob garbage collection is unverified.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| factory-claim-lifecycle: Persist accepted work and execution history (terminal time, backfill, re-settle) | INT-001 | E2E-003, E2E-004 | — |
| factory-operations: Clean up worktrees after review (terminal release, quiescence, reopen, sweep) | INT-001, INT-002 | E2E-001, E2E-003, E2E-004 | — |
| factory-operations: Retain evidence for a bounded period | INT-003 | E2E-001, E2E-004 | — |
| factory-operations: Expose current operational status | INT-006 | E2E-001, E2E-002 | — |
| factory-eval-reporting: Provide executable human-review instructions | — | E2E-001 | — |
| factory-eval-reporting: Report the expiry of unreviewed human-review commands | — | E2E-001 | — |
| factory-eval-execution: Preserve suite-owned evidence and candidate outputs | INT-002, INT-003 | E2E-001 | — |
| factory-fly-execution: Remove a finished claim's registry image | INT-004, INT-005 | E2E-002 | — |
