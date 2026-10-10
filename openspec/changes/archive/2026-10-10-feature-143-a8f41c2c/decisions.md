# Decisions: feature-143-a8f41c2c

## propose

1. **Verdict: go with caveats.**
   - Alternatives: no-go, or a single global per-kind concurrency number.
   - Rationale: a concurrency number does not guarantee a slot for higher priorities, and the
     per-kind slot design extends cleanly to a (kind, priority) key.
   - Decision-bearing: yes.

2. **Urgent gets its own lane, the same as High, Medium, and Low.**
   - Alternatives: fold Urgent into High; ignore Urgent.
   - Rationale: the issue's open question says Urgent "presumably follows the same pattern as one
     more level", and the Priority field has four values.
   - Decision-bearing: yes.

3. **Cards with no Priority use the Low lane.**
   - Alternatives: a fifth "unset" lane below Low; refuse to admit unprioritized cards.
   - Rationale: unset already sorts last; one lane keeps peak concurrency at four per kind;
     `factory-assign` sets a default Priority anyway.
   - Decision-bearing: yes.

4. **A higher-lane block applies to new starts only.**
   - New starts: first admission, fresh re-admission, a blocked claim resuming, and a review
     round on a settled claim.
   - Continuations (the next unit of an `active` or `waiting` claim, such as the next eval
     repetition) need only their own lane free.
   - Alternatives: gate every reservation, which would stall a running eval between repetitions;
     exempt review rounds and resumes too.
   - Rationale: the issue says no *new issue* of lower priority is *admitted* and that "issues
     already running continue". A settled or blocked claim is not running.
   - Decision-bearing: yes.

5. **A run's lane is the card's current Priority when the attempt is reserved, recorded on the
   run.**
   - Alternatives: freeze the Priority at claim admission.
   - Rationale: it matches today's ranking by current Priority and lets an operator bump a
     claim's later attempts. Recording the lane keeps the guard and status stable.
   - Decision-bearing: no.

6. **No preemption, and reprioritizing never moves a running attempt.**
   - Alternatives: pause or cancel lower work.
   - Rationale: the issue's text, and the existing intake rule that reprioritizing does not
     interrupt active work.
   - Decision-bearing: no.

7. **The schema change is idempotent and outside the `user_version` bump.**
   - Change: a `run.priority` column and a unique index on `(kind, priority)`. Existing
     unfinished runs go to the Low lane.
   - Alternatives: bump `SCHEMA_VERSION` to 5.
   - Rationale: a version bump makes supervisors from the previous release fail to open the
     database during deploy ("database is newer than this controller"). The watch and notify
     schemas already set this precedent. This keeps the change additive, so it is not a breaking
     persisted-format change, and not a stop.
   - Decision-bearing: yes.

8. **Keep the `<kind> slot: ...` status summary line, and add lane detail beneath it.**
   - Alternatives: replace it with per-lane lines.
   - Rationale: `scripts/slots.sh` (deploy) and the skills parse `^<kind> slot: free$`, so a
     deploy script on either side of the change keeps working.
   - Decision-bearing: no.

9. **No configuration switch or per-kind lane cap. Lanes apply to every kind.**
   - Alternatives: an opt-in flag; a configurable lane count or concurrency cap.
   - Rationale: the issue asks for the same rule for every kind. The disk, memory, quota,
     readiness, and job-cap holds already bound load. A cap can follow if needed.
   - Decision-bearing: yes.

10. **Keep one admission per tick.**
    - Alternatives: fill every free lane in one tick.
    - Rationale: ticks are frequent, and it keeps the change small.
    - Decision-bearing: no.

11. **Kinds stay independent.** A busy higher lane blocks lower lanes of the same kind only.
    - Alternatives: cross-kind priority.
    - Rationale: the issue title and body scope lanes to "each work kind's lane".
    - Decision-bearing: no.

## proposal-review

12. **PR-001 (legacy runs migrated to Low undercut running higher-priority work): applied.**
    - Change: unfinished runs at upgrade get a legacy marker that occupies every lane of their
      kind until they settle.
    - Alternatives: look up the current Project Priority during migration (rejected: the store
      migration has no GitHub access and the result may not be verifiable); treat legacy runs as
      Urgent (equivalent, but less explicit).
    - Decision-bearing: yes.

13. **PR-002 (continuation decided by `active`/`waiting` lifecycle admits first attempts beside
    higher lanes): applied.**
    - Change: a reservation is a continuation only when the claim has a started attempt in its
      current execution episode. An episode begins at admission, fresh re-admission, unblock, or
      review round. A pre-launch failure keeps the episode's original category, and `reserve_run`
      decides from saved run history in the same transaction, never from a caller flag or the
      lifecycle.
    - Alternatives: gate every reservation (rejected: stalls running evals between repetitions,
      against "issues already running continue").
    - Decision-bearing: yes.

14. **PR-003 (replacing the per-kind index breaks the old release's atomic reservation contract
    on rollback): applied.**
    - Change: add a downgrade command that, in one transaction, verifies at most one unfinished
      run per kind and restores `one_nonterminal_run_per_kind`. `deploy.sh` runs it while paused
      before switching to a release without lanes, and refuses the rollback otherwise, mirroring
      the fixture rollback guard. `user_version` stays unchanged, so supervisors from either
      release keep working; verified that supervisors never call `reserve_run`.
    - Alternatives: a warning only, or relying on the old runtime's prefilter (rejected per the
      finding: neither is a database-level guarantee); bumping `user_version` (rejected: breaks
      surviving supervisors during a deploy).
    - Not direction-level: `deploy.sh` is in this repository and the fixture guard sets the
      precedent.
    - Decision-bearing: yes.

## spec

15. **The core lane rule lives in `factory-claim-lifecycle` "Prevent overlapping execution",
    which also defines "a kind's slot" as the lane the attempt would occupy.**
    - Effect: existing wording such as "release the fix slot" in the reporting specs and "its slot
      is free" in the job-cap notice keeps a precise meaning without being rewritten.
    - Alternatives: rewrite every requirement that says "slot" (rejected: many unchanged
      requirements would be copied for a wording change, with a higher risk of drift).
    - Decision-bearing: no.

16. **Review rounds, blocked-claim resumes, and Ready cards of one kind are considered in
    Priority order. A review round precedes Ready work only at the same Priority, and attempts
    reserved earlier in a cycle count as occupying their lane.**
    - Alternatives: keep "review rounds before all new Ready work of the kind" (rejected: a Low
      review round and a High bug arriving together would both start, which runs against "do not
      admit lower while higher runs").
    - Decision-bearing: yes.

17. **The eval intake gets an ADDED requirement; bug, feature, and task intake modify their
    selection requirement.**
    - Rationale: only the latter three contain "each kind fills only its own execution slot".
    - Decision-bearing: no.

18. **A card whose Priority is not one of the four known values uses the Low lane, the same as
    unset.**
    - Rationale: it matches the existing ranking, which sorts unrecognized values with unset ones.
    - Decision-bearing: no.

19. **`factory-review-execution` is unchanged.** It has no slot or admission wording; review-round
    admission is in `factory-pull-request-lifecycle`.
    - Decision-bearing: no.

20. **The rollback guard's command names, and how a target release reports lane support, are
    deferred to design.** The observable refusal and restore behavior is specified. A lanes
    release re-establishes lane enforcement when it next opens a database whose per-kind guard
    was restored, which covers a deploy that fails after restoring.
    - Decision-bearing: no.

21. **The status summary line `<kind> slot: ...` is kept, and reads exactly `<kind> slot: free`
    only when the kind has no unfinished attempt. Per-lane lines are added.**
    - Rationale: this keeps `scripts/slots.sh` and the skills working.
    - Decision-bearing: no.

22. **The resource-sizing documentation change is an ADDED documentation requirement, rather
    than a rewrite of "Keep local data under a configurable root".** Its "one eval and one fix"
    sizing stays true as a minimum.
    - Decision-bearing: no.

## design

23. **Lane mode is decided by which unique index exists (`one_nonterminal_run_per_lane` or
    `one_nonterminal_run_per_kind`). Only the resident's start, `lanes enable`, and
    `lanes downgrade` switch it.**
    - Alternatives: switch on every writable open (rejected: the new release's doctor would drop
      the per-kind index under the still-running older resident during a forward deploy); a
      settings marker (rejected: a second source of truth).
    - Spec implication applied: the claim-lifecycle and operations deltas now say that lanes take
      effect at resident start, and that a deploy which stops after the restore runs `lanes enable`.
      This replaces "the next time it opens the database", which the design showed was unsafe.
    - Decision-bearing: yes.

24. **Command names.** `agent-factory lanes supported` (no config; prints `priority-lanes`),
    `lanes downgrade [--check]`, and `lanes enable`. This resolves the spec's deferred-to-design
    marker. They follow the `honored-revisions` / `pinned-claims` precedent.
    - Decision-bearing: no.

25. **Continuations are classified by a pure function over the claim's runs.** Episode-start
    reasons are `review` and `unblock`. Pre-suite retries fold into their episode, and "started"
    means `started_at` is set. No new episode column.
    - Alternatives: persist an episode id per run (rejected: more schema for no added safety).
    - Decision-bearing: no.

26. **Review rounds and unblocks move from the runtime's first loop into the Priority-ordered
    admission pass, sorted re-entry-first within a Priority.**
    - Alternatives: a "higher Ready card pending" check in the first loop (rejected: it starves a
      Low review round behind an ineligible High card).
    - Decision-bearing: yes.

27. **Supersedes decision 10. Admission allows one reservation per kind per cycle, instead of one
    per cycle.**
    - Rationale: with lanes, a single global start per poll would slow multi-lane admission, and
      review rounds and Ready work of different kinds could already both start in one cycle.
      Sandbox memory is re-sampled per reservation. The proposal's Technical Approach and Out of
      Scope are updated to match.
    - Decision-bearing: yes.

28. **The run column is named `lane`, and `NULL` means a legacy holder.**
    - Rationale: no backfill is needed, and an older release's inserts become legacy holders
      automatically. The proposal is updated from `priority` to `lane`.
    - Decision-bearing: no.

29. **Deploy recovery after `lanes downgrade` uses an EXIT trap that runs `lanes enable` with the
    live release until `launchctl bootout`.** A killed deploy leaves a safe single-slot mode,
    visible in status.
    - Decision-bearing: no.

## test-plan

30. **Six integration obligations and one E2E (several priorities of the fix kind plus a task,
    through `agent-factory tick`).** Upgrade, rollback, and deploy are covered at the integration
    layer with the previous release's verbatim SQL and stub launchd tools, not by an E2E test.
    - Alternatives: an E2E per scenario (rejected: duplicates INT-003 at a higher cost);
      exercising the real LaunchAgent (rejected: it touches the live service).
    - Decision-bearing: no.

31. **The acceptance envelope forbids the live service, the real board, real deploys, and any
    model, Fly, Docker, or GitHub calls.** It allows throwaway release worktrees of this branch and
    `origin/main` under a temporary `HOME`, to check upgrade and rollback against the real previous
    code. Fly lane concurrency is not exercised live, which is an accepted limitation.
    - Decision-bearing: no.

32. **Human-only testing: none.** Every behavior is observable through the CLI, the store, and
    the fake board.
    - Decision-bearing: no.

## approach-review

33. **AR-001 (the ordered pass dropped review-blocked claims and fresh re-admission of settled
    claims): applied.**
    - Change: review candidates now include `blocked_by == "review"` claims, and keep the
      `prior_pull_request` fallback. Dispatch is separate from eligibility to reserve: a review
      candidate that reserves nothing falls through to the Ready path, so a settled claim dragged
      to Ready reaches its `fresh` gesture. INT-003 is extended for fix, feature, and task.
    - Verified against `review.py:98-110` and `handler.py:1178-1207`.
    - Decision-bearing: yes.

34. **AR-002 (the "concurrent reservations across lanes" scenario was stronger than an occupancy
    gate can enforce): applied.**
    - Change: the spec scenario, design testing, and INT-001 now state the serializable rule. High
      first refuses Low; Low first lets both succeed; no Low start takes effect while High is
      unfinished. INT-001 also tests both forced orders.
    - Alternatives: arbitrating pending requests so High always wins a simultaneous race
      (rejected: a materially different scheduler; the issue's "no preemption" keeps a committed
      Low).
    - Decision-bearing: yes.

35. **AR-003 (the admission sort used `lane_for`, putting an unprioritized re-entry ahead of an
    explicit Low card): applied.**
    - Change: the primary sort key is the existing Project Priority rank
      (`github._priority_rank`). `lane_for` is used only for occupancy. INT-003 adds an unset
      versus explicit Low case and an equal-Priority review precedence case.
    - Decision-bearing: no.

36. **AR-004 (the deploy trap was cleared at `bootout`, so a detected unload failure left the live
    lanes release in kind mode): applied.**
    - Change: the recovery boundary is confirmed removal of the resident, after the unload poll. On
      an unload failure the trap restores the LaunchAgent and `shared_config` pointers, and runs
      `lanes enable`. After a confirmed unload, failures keep the per-kind guard.
    - Two operations spec scenarios are added and INT-006 is extended.
    - Decision-bearing: yes.

## tasks

37. **One implementation task covers the whole change, as instructed.** The task lists the
    precedence among decisions (27 over 10, 23 over 20, AR-001 to AR-004 over the design-stage
    text). It names an orange PR attention item for the concurrency load and the rollback-only-via-
    deploy constraint.
    - Decision-bearing: no.
