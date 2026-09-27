## Why

Factory and eval runs leave disk and registry leftovers that are only released once a claim's card
reaches Done, and many cards never get there. A settled eval sits in Review. A cancelled claim's
card may be archived. A superseded claim's card now belongs to its replacement. Some leftovers are
never released at all:

- the harness worktrees of cancelled evals, because `WorktreeCleanup` handles only settled claims;
- the worktrees and clones of every superseded claim.

What this has cost:

- On 2026-09-23 an eval was not admitted because free disk space was 4.1 GiB, below the 5 GiB
  `minimum_free_gib` floor. It was admitted only after caches were cleared by hand.
- `~/.agent-factory/artifacts` grew from 3.3 GB on 2026-09-23 to about 6.1 GiB on 2026-09-25. It
  is 7.7 GiB on 2026-09-27, across 86 run directories created since 2026-09-09.
- The store holds 23 settled, 20 cancelled, and 3 superseded fix claims, and 8 settled,
  1 cancelled, and 8 superseded eval claims.
- About 6.1 GiB of the 7.7 GiB (≈ 79%) sits in the `.runtime/candidate-worktree` directories of
  26 eval repetitions. Each is roughly 230 MB, mostly `node_modules` and `skills`. The existing
  evidence-retention rule never removes these directories, even for claims in Done.
- The operator's nightly low-disk script cannot help, because it cannot tell which factory
  directories hold active or resumable claim state. Only the factory knows that, so the factory
  has to do the cleanup.
- Each Fly eval claim pushes a `claim-<12 chars>` tag to the sandbox app's registry, and nothing
  removes it. The registry currently holds six `claim-` tags. This is deferred work from
  agent-factory#14.

## What Changes

- **Include candidate worktrees in evidence pruning.** Evidence pruning also removes each eval
  repetition's `.runtime/candidate-worktree`, on the existing Done path and the new path below.
  - The curated result files (`result.json`, `report.html`, `implementation.diff`, and the rest)
    are already committed to the eval results repository.
  - Pruning waits until that capture has succeeded.
  - This one change reclaims about 6 GiB of today's 7.7 GiB once the affected claims become
    eligible.
- **Retention for finished claims outside Done.** Once a finished claim has been idle long enough,
  it releases its recorded worktrees, clones, per-run local images, and credential copies, and its
  evidence is pruned. The idle clock starts when the factory first durably observes the claim in
  its terminal lifecycle, whatever its card status.
  - A cancelled or superseded claim becomes eligible after a short period, 3 days by default.
    Nobody reviews these claims.
  - A settled claim whose card is outside Done becomes eligible after 14 days by default. That
    matches the Done evidence period and leaves room for optional human review of evals parked in
    Review.
  - The existing Done path is unchanged: cleanup on the next poll, pruning 14 days later.
- **Protect claims that may still run.** The factory never touches:
  - a claim that is active, waiting, or blocked;
  - any claim with a non-terminal or unverified run, undelivered reporting, an unfinished results
    capture, a pending post-merge sync, or a live or stopped Fly Machine.

  If a terminal claim becomes active again, its idle clock resets.
- **Close the existing gaps.** Cancelled evals and all superseded claims release their workspaces
  under the idle rule.
- **Reach claims whose card left the board.** After each successful poll, a bounded sweep driven by
  the claim store visits terminal claims whose card is no longer on the board. The board decides
  nothing about who owns local disk, so an archived card no longer leaks.
- **Make the eval review window visible.** The human-review handoff comment states that the
  command stays usable until the item moves to Done, or until the retention period after the
  request settles, whichever comes first. When the window lapses without Done, the factory
  records an issue event saying the retained worktree and evidence were released.
- **Clean up Fly image tags.** Once an eval claim no longer needs its image, the factory deletes
  that image from the sandbox app's registry. That point is reached when the claim is settled,
  cancelled, or superseded, no run is non-terminal, and every one of its Machines has been
  disposed.
  - The factory deletes only the digest that claim recorded for itself, and only after confirming
    that its `claim-` tag still resolves to it.
  - Registry failures are persisted and retried on later polls. A missing manifest counts as done.
  - Tag cleanup runs apart from Machine disposal and reconciliation, and never delays or fails
    either of them.
- **Report in aggregate.** `status` summarises the cleanup:
  - the number of claims awaiting cleanup and the bytes they hold;
  - the number of claims pruned;
  - each failing claim, listed individually with its error.

  It does not list every claim.

No public interface or persisted format changes incompatibly. The new configuration keys are
optional and have defaults. The new cleanup state is additive in the claim's existing cleanup
record.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-operations`:
  - "Clean up worktrees after review" gains release of idle terminal claims outside Done,
    including cancelled evals, superseded claims, and claims whose card has left the board.
  - "Retain evidence for a bounded period" gains the idle-terminal path alongside the Done path.
    It replaces the rules that pruning never touches a claim whose card is not Done or not on the
    board. It adds the candidate worktree to the pruned paths and the results-capture guard.
  - "Expose current operational status" gains the aggregate cleanup summary.
- `factory-eval-execution`:
  - "Preserve suite-owned evidence and candidate outputs" now keeps the suite worktree and
    repetition evidence until Done or until the settled-idle period elapses, whichever comes
    first.
- `factory-eval-reporting`:
  - "Provide executable human-review instructions" states the new end of the review window and
    records an event when it lapses.
- `factory-fly-execution`:
  - Gains a requirement to delete a claim's registry image once no active or resumable work can
    use it. Failures are retried and isolated from Machine disposal.

## Technical Approach

The work extends existing mechanisms rather than adding a new subsystem.

**Idle clock.** The first time cleanup sees a claim in a terminal lifecycle (`settled`,
`cancelled`, `superseded`), it writes `terminal_observed_at` in the claim's `cleanup` record. If
the claim is later seen in any other lifecycle, the entry is cleared.

- This mirrors how `done_observed_at` works today. It deliberately avoids `claim.updated_at`,
  which every cleanup write changes.
- History recorded before the upgrade gets its clock on the first tick after the upgrade. Nothing
  is cleaned up retroactively.

**Where it runs.** The per-card loop in `runtime.py` keeps calling `retention.reconcile` and each
handler's `cleanup`. After a successful poll, a new store-driven pass visits terminal claims whose
card was not in this poll's board snapshot and applies the same idle rule to them. The pass runs
only after a successful poll, so a failed board read cannot make every claim look off-board. Its
per-tick work is bounded.

**Eligibility.** Eligibility reuses the existing guards in `retention._eligible`: non-terminal
runs, pending events, delivery failures, and pending sync. It adds three:

- the idle period for the claim's lifecycle has elapsed;
- the claim has no Fly Machine record and no stop hold;
- the eval results capture has succeeded, or applies to no repetition.

**Release and pruning.** Each kind's existing removal code reads the recorded factory-owned paths:
`WorktreeCleanup` for evals, `PullRequestCleanup` for fixes and features. Each gains an entry path
that does not need a Review-then-Done observation. That entry path also covers cancelled and
superseded claims.

`_EVAL_REP_REMOVE` gains `.runtime/candidate-worktree`. That directory is a standalone clone, with
its own `.git` directory, so removing the tree leaves no dangling worktree metadata anywhere.

Releasing old clones is safe for settled fix and feature claims:

- A review round prepares fresh clones (`prepare_review`) rather than reusing old ones.
- Only blocked claims resume on recorded clones, and blocked claims are excluded.

**Configuration.** Two keys set the idle periods, with validation like `evidence_retention_days`:

| Key | Applies to | Default |
|---|---|---|
| `[limits] abandoned_retention_days` | cancelled and superseded claims | 3 days |
| `[limits] settled_retention_days` | settled claims outside Done | 14 days |

Disk estimate, from the artifact directory over 2026-09-09 to 2026-09-27:

- Average growth is about 0.43 GiB/day, with bursts of about 1.4 GiB/day.
- Retained evidence therefore settles at roughly 6 GiB on average, and about 20 GiB during a
  sustained burst. That is the same bound the Done path already implies.
- Current free space on the Mac is 43 GiB.
- Cancelled and superseded claims, over half of today's claims, stop contributing after 3 days.

**Fly tags.** The Fly registry client (`FlyMachinesClient`) gains a manifest DELETE by digest. The
digest comes from the claim's attempt record, `image-build.json`, which holds the repository, tag,
and digest. The DELETE uses the same basic-auth scheme that `resolve_manifest` already uses, and
its outcome is recorded under `cleanup["fly_image"]`.

- Before deleting, the factory resolves the tag and confirms it still points to the recorded
  digest. Otherwise it could remove a manifest that another tag shares.
- The step runs in the per-claim cleanup path, not in `FlyBackend.dispose` or `reconcile`, so
  registry errors cannot affect Machine disposal.
- Feasibility check, run on 2026-09-27:
  - A `DELETE /v2/agent-factory-sandbox/manifests/sha256:000…` request to `registry.fly.io`
    returned 404 `MANIFEST_UNKNOWN`. A registry with deletion disabled returns 405 `UNSUPPORTED`,
    so the route is handled.
  - `GET /v2/agent-factory-sandbox/tags/list` returned 200 and listed the six `claim-` tags.
- Deleting a real manifest has not been tried, because it cannot be undone. The first real
  deletion will confirm it.
- A 405 `UNSUPPORTED` reply is treated as a persistent failure. The claim records it once and it
  is not retried every tick, so an unexpected refusal stays visible without looping.

**Verdict: go.** The disk problem has already blocked admission. The fix mostly generalises code
that already exists, plus one missing path in the pruning list.

## Out of Scope

- Evidence or workspaces of active, waiting, or blocked claims, whatever their age or board
  presence.
- Mirrors, candidate branches, PRs, SQLite history, shared source checkouts, the operator's
  working clones, and the eval results repository.
- Releases under `~/.agent-factory/releases`, which `scripts/deploy.sh` manages.
- Docker Desktop's disk image and builder caches.
- The operator's external nightly low-disk script.
- Registry tags the factory did not record for a claim, such as `base` and `deployment-*`, and any
  sweep of the registry based on listing its tags.
- Changing the Done path's timing or the 14-day evidence period.
- Proactive cleanup driven by disk pressure that ignores the idle periods.

## Impact

- **Code:**
  - `src/agent_factory/retention.py`: idle clock, eligibility, candidate-worktree target, and the
    results-capture guard.
  - `src/agent_factory/runtime.py`: the store-driven pass over off-board terminal claims.
  - `src/agent_factory/work_kinds/pull_request/cleanup.py` and
    `src/agent_factory/suites/and_scene/__init__.py` (`WorktreeCleanup`, handoff text): the idle
    entry path, the cancelled-eval path, and the review-window wording.
  - `src/agent_factory/fly/api.py` and `src/agent_factory/fly/transport.py`: registry DELETE and
    reading the recorded digest.
  - Eval handler cleanup: tag deletion and the lapse event.
  - `src/agent_factory/config.py`: the two new limits.
  - `src/agent_factory/operations.py`: aggregate status.
- **Specs:** `factory-operations`, `factory-eval-execution`, `factory-eval-reporting`,
  `factory-fly-execution`.
- **Docs:** `docs/operations.md` ("Service management and storage", "Evidence retention") and the
  `AGENTS.md` notes that point at issue #15.
- **Operators:**
  - Optional human review of a settled eval left in Review ends 14 days after the request settles,
    and the card shows an event when it lapses.
  - Old cancelled and superseded claims free their disk space after 3 days without anyone moving
    cards.
- **Data:** only additive fields in `claim.cleanup`. There is no schema migration.
