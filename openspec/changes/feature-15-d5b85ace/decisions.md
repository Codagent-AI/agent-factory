# Decisions

## propose

1. **Verdict: go.** The disk floor already blocked an eval admission, artifacts are still
   growing daily, and only the factory knows which claims are safe to clean. Alternatives:
   operator-side scripts, which the issue comment rejects because they cannot see claim
   state; lowering `minimum_free_gib`, which only postpones the problem. Decision-bearing:
   yes.
2. **Treat `settled`, `cancelled`, and `superseded` as the non-resumable lifecycles.
   `active`, `waiting`, and `blocked` count as active or resumable and are never touched.**
   Re-requesting settled work creates a fresh claim that supersedes the old one, and a
   blocked claim is re-admitted from a writer comment, so it is resumable. Alternative:
   also clean long-blocked claims, which the issue's "preserve active/resumable claims"
   rules out. Decision-bearing: yes.
3. **Release cancelled and superseded claims' worktrees, clones, images, and credential
   copies as soon as execution has stopped. Prune their evidence after
   `evidence_retention_days` from becoming terminal, whatever the card status.** This
   extends the existing cancelled-fix rule. Alternative: wait for Done, which is the
   current behavior and the source of the leak. Decision-bearing: yes.
4. **Settled claims outside Done get a separate, longer `[limits]
   unreviewed_retention_days` (default 30) before release and pruning.** A settled eval in
   Review still serves the human-review command, so it needs longer than cancelled work.
   The issue and comment explicitly ask for retention of settled claims outside Done.
   Alternatives: reuse the 14-day period (too aggressive for human review); never clean
   settled claims outside Done (contradicts the issue). Decision-bearing: yes (assumed
   default value).
5. **Start the age clock at the latest run's `finished_at`, falling back to the first
   observation of the terminal lifecycle.** This lets existing history become eligible on
   the first tick after deploy. Alternative: start at the first observation after upgrade,
   as the Done rule did, which delays relief by a full period while the disk is already
   under pressure. Decision-bearing: yes.
6. **Add a store-driven sweep over terminal claims, so claims whose cards left the board
   are also cleaned.** This reverses the current spec clause that forbids pruning
   off-board claims, but only on the age-based path, which does not rely on board state.
   Alternative: leave off-board claims alone, which leaks them permanently.
   Decision-bearing: yes.
7. **Delete a Fly `claim-` image only when the claim is terminal, has no non-terminal run,
   and holds no Machine. Delete by the recorded digest after confirming the claim's own tag
   still resolves to it. Record and retry the deletion in `claim.cleanup`, separate from
   `fly:cleanup-failed` and Machine reconciliation.** Alternatives: delete by tag (not
   supported by Registry v2 and unsafe with a mutable tag); run deletion inside Fly
   reconciliation (couples registry failures to Machine disposal, which the issue
   forbids). Decision-bearing: yes.
8. **Fly does not document registry deletion. The design must verify it against the live
   registry. An explicit "unsupported" answer is recorded once and surfaced, not retried
   every tick.** Not a stop: the Mac cleanup delivers most of the value, and the Fly part
   degrades to a visible, bounded no-op. Alternative: stop the definition until deletion
   support is confirmed. That was rejected because it is a feasibility risk the design
   step can resolve inside the repository, not a direction choice. Decision-bearing: yes.
9. **Out of scope: orphan files not referenced by any claim, mirrors, Docker Desktop's
   disk and build cache, disk-pressure eviction, and tags the factory did not record.**
   This keeps cleanup limited to recorded, factory-owned paths, matching the existing
   safety rule. Decision-bearing: no.

## proposal-review

10. **PR-1 (structural): applied.** Verified in code. `review.py` `process_review_claim`
    resumes settled claims whose PR is open, and the eval `report_events` handoff points
    at `human-review.sh` in the retained evals worktree. Changes:
    - a new run reopens the claim's cleanup, so the `complete` short-circuit no longer
      skips it;
    - release covers every attempt's clones;
    - release requires all reporting to be delivered;
    - a settled eval's human-review handoff is explicitly expired with an issue comment,
      delivered before its worktrees are released.

    Decision 4 is replaced: the 30-day unreviewed period stays, and expiry is now explicit
    instead of silent. Alternative: keep settled Review evals until Done. That was rejected
    because the issue comment explicitly asks for retention of settled claims outside Done.
    Fix and feature review rounds cut fresh clones, so they stay on the same claim.
    Decision-bearing: yes.
11. **PR-2 (significant): applied.** A claim can finish its runs long before it is
    cancelled or superseded. The factory now persists `terminal_at` in the same transaction
    as every transition into a terminal lifecycle, and all clocks start there. Historical
    rows are backfilled once from `updated_at`, which is never earlier than the transition.
    This supersedes decision 5 (latest run `finished_at`). Alternative: backfill from the
    first observation after deploy. That is also safe, but it delays disk relief by a full
    period. Decision-bearing: yes.
12. **PR-3 (structural): applied, with narrowed claims and a gate. Not a stop.**
    Read-only checks against the live `agent-factory-sandbox` registry on 2026-09-28
    showed:
    - `tags/list` works;
    - all nine tags have distinct digests;
    - a DELETE of a nonexistent digest returned `404 MANIFEST_UNKNOWN`, not
      `405 UNSUPPORTED`, so the endpoint accepts deletes.

    No real image was deleted. The proposal now:
    - checks that no other tag shares the digest before deleting;
    - treats refusal as a visible failure, not a success;
    - no longer promises storage reclamation, which is Fly's;
    - makes a disposable-image end-to-end probe the first design task.

    If the probe shows deletion is unsupported or has wider effects, that is a definition
    stop to revisit the Fly scope. Decision 8's "record unsupported once and stop retrying"
    is withdrawn. The disposable-image push was not done during proposal work, because it
    mutates production infrastructure. Alternatives: stop now (rejected: the evidence makes
    per-claim deletion likely feasible); rolling a single tag (rejected: it leaves untagged
    manifests, with no documented garbage collection). Decision-bearing: yes.

## spec

13. **Surface registry and expiry failures only in `status`, not in `doctor`.** `doctor`
    diagnoses readiness, and a failed cleanup does not block readiness. `status` already
    lists claims that hold a recorded cleanup failure. The proposal text is updated to
    match. Alternative: a new `doctor` check, which would widen the readiness requirement
    for no gain in admission safety. Decision-bearing: yes.
14. **Specify the Fly deletion as an ADDED requirement in `factory-fly-execution`, and
    leave "Build the sandbox image once per claim" unmodified.** The build behavior does
    not change. The end-to-end effect of the deletion is marked `deferred-to-design`,
    pending the disposable-image probe (decision 12). Decision-bearing: no.
15. **Add `factory-eval-execution` as a modified capability.** Its "Preserve suite-owned
    evidence" requirement promised the worktree until Done, which would contradict the
    expiry. Decision-bearing: no.
16. **The unreviewed path applies only while the card is not observed Done on the current
    poll.** A card that reaches Done switches the claim to the Done path, which retains
    evidence for the full period from the Done observation. This preserves the existing
    "Reach Done after a long time" behavior. Alternative: prune whichever path ends first,
    which would cut a late Done short. Decision-bearing: yes.
17. **Pruning on the cancelled and superseded paths now waits for the claim's terminal
    release to complete.** Before, cancelled and superseded claims were exempt from the
    cleanup condition. They now always get a release pass, so waiting on it keeps pruning
    and release ordered. Registry deletion is not a precondition. Decision-bearing: no.
18. **A new run on a pruned or released claim reopens its cleanup and its retention for the
    new run's evidence.** Earlier removal records are kept. This covers review rounds after
    a 30-day release. Decision-bearing: no.
19. **Terminal release now also requires all of the claim's reporting to be delivered,
    including for cancelled fix claims, which were previously released as soon as
    execution stopped.** This implements PR-1's delivery ordering. At most it delays
    release by the time needed to deliver a report. Decision-bearing: no.
20. **The expiry comment is posted only for settled eval claims that posted at least one
    human-review command.** Failed, cancelled, and superseded evals are released without
    a comment, because they published no review command. The card's status and Verdict are
    never changed. Decision-bearing: yes.
21. **`unreviewed_retention_days` is specified inside the retention and cleanup
    requirements as local configuration, and the large configuration requirement is not
    modified.** That requirement already lists "evidence retention" among the configurable
    items. Decision-bearing: no.

## design

22. **Ran the disposable registry probe during design, resolving decision 12's gate. Not a
    stop.** The probe pushed one unique image under two `factory-probe-` tags in
    `agent-factory-sandbox`, using raw Registry v2 calls with a temporary in-memory
    credential and no Docker configuration change. Results:
    - a delete by tag returned `202` and removed only that tag;
    - a delete by digest returned `202` and removed the manifest and both tags;
    - a repeated delete returned `404`.

    Both probe tags are gone and no other tag was touched. The spec's `deferred-to-design`
    marker was removed, and its scenario now states the confirmed effect. Alternative:
    leave the probe to implementation. That was rejected because the probe was cheap and
    confined to self-created tags, and it removes a stop risk before task planning.
    Decision-bearing: yes.
23. **D1: store `terminal_at` in `cleanup_json` with `json_set` in the lifecycle `UPDATE`,
    with no schema version bump.** A version 5 schema would make the previous release
    refuse the database, which breaks `deploy.sh` rollback and running old-release
    processes. Alternative: a new column. Decision-bearing: yes.
24. **D2: delete by digest only, with a mandatory shared-digest guard. The client rejects
    non-digest references.** Alternative: delete by tag, which only untags and leaves the
    manifest stored. Decision-bearing: yes.
25. **D3: registry deletion runs in the new terminal sweep, isolated per claim, and never in
    `FlyMachineBackend.reconcile` or `fly:cleanup-failed`.** Decision-bearing: no, because
    the spec mandates the isolation.
26. **D4: pull-request release removes the whole factory-owned `<root>/clones/<claim>/`
    directory.** Only the latest preparation's clones are recorded, and the probe of
    `~/.agent-factory/clones` showed leaked empty claim directories. Alternative: record
    every attempt's clones in the preparation, which leaves existing leaks behind.
    Decision-bearing: no.
27. **D5 and D7: a store-driven terminal sweep runs after the per-card loop. Pruning moves
    entirely into the sweep, and the per-card loop only records or resets Done
    observations.** Decision-bearing: no.
28. **D6: the human-review expiry is an ordinary durable report event
    (`review-expired`).** The existing "no pending events" gate orders it before release.
    Decision-bearing: no.
29. **Unverified ownership is the store's `observing` status, which is part of
    `NONTERMINAL_RUN_STATUSES`, so no separate predicate is needed.** Decision-bearing: no.
30. **Without a `[fly]` configuration, a claim with recorded images gets a visible error
    rather than a silent skip.** Decision-bearing: no.

## test-plan

31. **Advance time in E2E tests by rewriting `terminal_at` or `done_observed_at` in the test's
    own SQLite between CLI ticks, not by patching the clock or sleeping.** The E2E
    harnesses run `tick` as a subprocess, so patching the in-process clock is not possible.
    Rewriting the recorded time also exercises the real read path. Decision-bearing: no.
32. **The acceptance pass may push and delete its own `factory-probe-` images in the live
    `agent-factory-sandbox` registry, and may make read-only registry calls. It must not
    touch `claim-`, `base`, or `deployment-` tags, run `tick`/`resident`/`deploy.sh`
    against the live configuration, post to GitHub, or modify live factory state.** This
    mirrors the design probe's authorization, and it is the only way to check the real
    registry contract. Alternative: fakes only, which would leave the delete semantics
    untested against Fly after implementation. Decision-bearing: yes.
33. **A copy of the live `state.sqlite3` may be used only for read-only eligibility
    classification, never for release, prune, or registry code, because its paths are
    absolute live paths.** Decision-bearing: yes.
34. **Human-only testing: none.** Every check is observable through the CLI, SQLite, the
    filesystem, and the fakes. Decision-bearing: no.
35. **No new Docker-marked tests.** Docker image removal in release is exercised by patching
    `agent_factory.work_kinds.images.subprocess.run`, as `test_fix_cleanup.py` does, so the default
    `-m 'not docker'` suite covers it. Decision-bearing: no.

## approach-review

36. **AR-1 (high): applied.** Verified: `review.process_review_claim` calls
    `set_claim_lifecycle(claim.id, claim.lifecycle, ...)` on settled claims every tick
    while a review round waits for its slot. `terminal_at` is now written only when the
    persisted lifecycle differs from the new terminal one, using a SQL `CASE` on the old
    row value (design D8). The spec now says that a same-lifecycle update keeps the
    terminal time, and adds a scenario for it. INT-001 repeats settled-to-settled updates.
    Decision-bearing: yes.
37. **AR-2 (high): applied.** The spec and design now require current proof of ownership
    for each digest: the claim tag resolves to exactly that digest and no other tag uses
    it. Otherwise:
    - the image is treated as already deleted only when GET shows both the tag and the
      digest are absent;
    - an absent tag with a live digest is a visible skip;
    - an older build whose tag moved on is a visible skip;
    - a 404 on DELETE counts as deleted only after a re-resolve confirms both are gone.

    This is design D9. INT-005 adds the absent-tag, two-build, and 404 cases. For an older
    digest from a repeated build, the decision is to leave it in place and show it,
    because deleting it would need an ownership proof the registry cannot give. Recorded
    builds make this rare. Decision-bearing: yes.
38. **AR-3 (high): applied.** Verified that `WorktreeCleanup.reconcile` and
    `PullRequestCleanup.reconcile` remove files on Done without a reporting check. Both
    Done-path classes now wait while events are pending or delivery failed (design D11).
    The spec adds this to the Done path with a scenario, INT-002 covers it, and E2E-001
    now includes a failed review-command post followed by a Done move.
    Decision-bearing: yes.
39. **AR-4 (high): applied.** Verified that `pending_sync` is true for any settled claim
    with a PR until sync completes, and that `sync_claim` returns early for an unmerged
    PR. For cleanup and retention, a sync is now pending only once the PR is observed
    merged. An open PR, or one closed without merging, does not block, and a failed PR
    read blocks for that poll. The existing `pending_sync` stays for `status`.

    The sweep never runs `merge_sync`. An off-board claim whose merged PR is unsynced
    keeps its files, and `status` shows the pending sync. Alternative: have the sweep sync
    off-board claims. That was rejected because it would post comments on and close issues
    the operator removed from the Project, a new external effect (design D10). The spec
    gains the definition and scenarios. INT-002 covers open, closed, merged, and
    unreadable PRs, and E2E-003 covers merged-with-failed-sync on and off the board.
    Decision-bearing: yes.

## tasks

40. **One implementation task for the whole change, in the single-task format of the
    archived `code-review` change.** It links every definition artifact and states the
    scope, test obligations, safety constraints, and the rule for spec adjustments.
    Changes to what may be deleted, or when, return to definition. Decision-bearing: no.

## continuation review (feature-15-d5b85ace)

41. **No artifact revisions. The artifacts carried over from `feature-15-24f07129` still
    match the issue and its eligible comment.** The issue title, body, and single eligible
    comment (2026-09-25) are identical to the input of both attempts of the earlier claim.
    Each point in them maps to an existing artifact:
    - Mac cleanup outside Done: terminal release and the three pruning paths in
      `factory-operations`, with clocks from `terminal_at`;
    - retention for settled, cancelled, and superseded claims outside Done: proposal items
      3 and 4, decisions 3, 4, and 10;
    - preserving active and resumable claims: decision 2 and the `active`/`waiting`/`blocked`
      exclusions in both specs;
    - safe retries: persisted cleanup progress, the retry scenarios, and idempotent
      registry records;
    - "not permission to delete all artifacts": enumerated pruning, with whole directories
      and records out of scope;
    - Fly tag cleanup that keeps images for active or resumable claims and is isolated from
      Machine disposal: the ADDED `factory-fly-execution` requirement and design D3 and D9.

    The branch contains `origin/main`. Since planning, the base specs changed only by the
    removal of the stray delta header (#38). Every MODIFIED requirement header still
    matches its base spec, and `openspec validate --strict` passes. The design has no open
    questions. Alternative: redo definition from scratch, rejected because nothing in the
    input or the base specs changed. Decision-bearing: no.
