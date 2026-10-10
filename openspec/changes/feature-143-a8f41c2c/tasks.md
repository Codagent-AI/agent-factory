- [ ] Run one issue per Priority level in each work kind's lane, end to end

## Task: Priority lanes per work kind (#143)

Implement the whole change described in these files in the change directory:

- `proposal.md`.
- The delta specs under `specs/`:
  - `factory-claim-lifecycle`:
    - MODIFIED "Prevent overlapping execution";
    - ADDED "Assign each attempt a Priority lane";
    - ADDED "Classify new starts and continuations";
    - ADDED "Hold every lane for attempts that predate lanes".
  - `factory-eval-intake`: ADDED "Fill eval Priority lanes".
  - `factory-bug-intake`, `factory-feature-intake`, `factory-task-intake`: MODIFIED selection by
    Priority, with the lane rule.
  - `factory-task-execution`: MODIFIED "Treat task claims as pull-request claims in shared
    lifecycle rules".
  - `factory-pull-request-lifecycle`: MODIFIED "Re-admit a review round through the claim's kind
    slot".
  - `factory-operations`:
    - MODIFIED "Expose current operational status";
    - MODIFIED "Run an immediate normal cycle with tick";
    - ADDED "Restore the per-kind guard before rolling back past lanes";
    - ADDED "Document Priority lanes".
- `design.md`, which is authoritative for:
  - module layout and function names;
  - the `lane` column and the two index names;
  - lane mode, and who switches it;
  - the gate and its causes;
  - `classify_reservation`;
  - the ordered admission pass and its dispatch;
  - the CLI commands;
  - the status line formats;
  - `scripts/lane-guard.sh` and its EXIT trap.
- `decisions.md`. Where entries differ, later entries override earlier ones. In particular:
  - decision 27 (one reservation per kind per cycle) supersedes decision 10;
  - decision 23 supersedes decision 20 (lanes take effect at resident start, not on every open);
  - approach-review AR-001 to AR-004 (entries 33 to 36) override the design-stage text they revise:
    - review-blocked claims are review candidates, with the `prior_pull_request` fallback and a
      fall-through to the fresh Ready path;
    - concurrent reservations follow the serializable rule;
    - admission sorts by `github._priority_rank`, with `lane_for` used only for occupancy;
    - the deploy trap lasts until the resident's removal is confirmed.
- The automated obligations in `test-plan.md`: INT-001 to INT-006 and E2E-001.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or anything under `/Users/paul/codagent/*`. Never run the real
`scripts/deploy.sh` or `launchctl` against the user's domain. Deploy tests use the stub harness
only. Automated tests must not call GitHub, Fly, Docker, or any model.

### Scope

1. **Lane vocabulary** (`src/agent_factory/lanes.py`, new):
   - `LANES`, `lane_for`, and `rank`;
   - `github._PRIORITY_ORDER` derives from `LANES`;
   - unset and unknown values map to `"low"` for occupancy only.
2. **Schema** (`store.py`, design "Schema"):
   - `_ensure_lane_column()` on every writable open adds a nullable `run.lane` column, with
     `SCHEMA_VERSION` unchanged;
   - `enable_lanes()` swaps `one_nonterminal_run_per_kind` for `one_nonterminal_run_per_lane`
     (unique on `(kind, lane)` over reserved, running, and observing runs);
   - `restore_kind_guard(check_only)` reverses the swap atomically, and returns the offending runs
     instead when any kind has more than one unfinished run;
   - lane mode is read from which index exists.
3. **Reservation gate** (`store.py`, design "Reservation gate"):
   - `reserve_run(..., lane)` takes `lane` as a required argument;
   - `_lane_gate` runs inside the existing `BEGIN IMMEDIATE` and raises `LaneBusy(NonterminalRunError)`
     with cause `kind-mode`, `legacy`, `lane-busy`, or `higher-lane` and the blocking run;
   - the pure `classify_reservation(runs, reason)` treats `review` and `unblock` as episode starts,
     folds pre-suite retries into their episode, and treats `started_at` as "started";
   - read-only `lane_decision` and `lane_occupancy`;
   - every "is the slot free" use of `nonterminal_runs(kind=...)` is replaced.
4. **Callers**:
   - `controller.reserve_next(claim_id, *, lane, readiness)` returns `None` on `LaneBusy` without a
     hold;
   - `handler.unblock(..., lane=...)` and `handler.review_round(..., lane=...)` (`blocked.py`,
     `review.py`, and the handler protocol in `work_kinds/base.py` and its implementations) use
     `lane_decision` for their early gate and pass `lane` to `reserve_run`;
   - their non-admitting side effects stay unchanged: reconciliation and `waiting_review`.
5. **Admission** (`runtime.py`, design "Admission"):
   - Move review rounds and unblocks out of the first per-card loop into one ordered admission
     pass. Cancellation, merge sync, reporting, and cleanup stay in the first loop.
   - Sort by `(github._priority_rank(card.priority), re-entry first, original index)`.
   - Dispatch:
     - unblock candidates (blocked, not by review) go to `unblock`;
     - review candidates (settled, or `blocked_by == "review"`) go to `review_round`, then fall
       through to the Ready path when nothing was reserved;
     - everything else takes the Ready path.
   - The `ready` prefilter uses `lane_decision(kind, lane_for(card.priority), claim_id, next_reason)`
     before `prepare()`.
   - Allow one reservation per kind per cycle, re-sampling sandbox memory before each one.
   - The job-cap card check uses the same lane decision.
   - Each cycle rewrites the `lane-wait` settings namespace.
6. **CLI** (`cli.py`, design "CLI"):
   - `lanes supported`, which needs no configuration, prints `priority-lanes`, and exits 0;
   - `--config L lanes downgrade [--check]`, with the offender output format and exit codes;
   - `--config L lanes enable`;
   - `resident` calls `enable_lanes()` once at start;
   - `tick`, `doctor`, and `status` never switch the mode.
7. **Status** (`operations.py`, design "Status"):
   - the summary line `<kind> slot: free`, or `<kind> slot: busy (<lanes highest first>)`;
   - one line per busy lane, `<kind> lane <lane>: <repo>#<n> <unit> (<status>)`;
   - the legacy line `<kind> lane all (pre-lane attempt): ...`;
   - `lanes: off (...)` in kind mode;
   - lane wait reasons for claims, review rounds, and `lane-wait` cards.
8. **Deploy** (`scripts/lane-guard.sh`, new, sourced by `scripts/deploy.sh`, design "Deploy"):
   - `lane_guard before`, beside `fixture_guard ... before`;
   - `lane_guard restore` after `fixture_guard ... after` and before `point_at`;
   - the EXIT trap, which restores the plist and `shared_config` pointers when they were moved and
     runs `lanes enable` with the live executable, preserving the exit status;
   - clear the trap only after the unload poll confirms the old resident is gone;
   - `scripts/slots.sh` keeps working unchanged against the new status output.
9. **Skills and documentation**:
   - `.claude/skills/factory-status/SKILL.md`, `.claude/skills/factory-assign/SKILL.md`
     (including its status grep and the `fix slot:` example), and
     `.claude/skills/factory-watch/SKILL.md`;
   - `AGENTS.md` and `docs/operations.md`, covering every item in operations "Document Priority
     lanes": resource sizing for concurrent host lanes, the rollback procedure, and that a hand
     rollback bypasses the guard.
10. **Tests**:
    - implement INT-001 to INT-006 and E2E-001 at the locations and with the setup `test-plan.md`
      gives;
    - add the unit tests listed in its "Coverage Strategy";
    - update existing tests that call `reserve_run`, `reserve_next`, `review_round`, or `unblock`
      for the new `lane` argument without weakening their assertions;
    - `tests/integration/test_per_kind_slots.py` and the old-format `test_deploy_slots.py` cases
      must still pass.
11. **Pull request description**: include an orange attention item. It says that the change runs
    up to four concurrent attempts per kind, so host load and Fly spend can rise, and that rollback
    past this release goes only through `scripts/deploy.sh` (a hand rollback bypasses the guard).

### Done when

- Every requirement and scenario in all eight delta specs is implemented.
- Within each kind, at most one unfinished attempt runs per Priority lane. A new start never takes
  effect while a higher lane of its kind is busy, and running attempts are never stopped or moved.
- A continuation (a started attempt in the claim's current episode) needs only its own lane.
  Admissions, fresh re-admissions, unblocks, and review rounds are new starts. A claim that never
  launched is a new start.
- Unset and unknown Priority use the Low lane, but still sort after an explicit Low.
- An attempt with no recorded lane holds every lane of its kind.
- Lanes take effect only at resident start or with `lanes enable`, and `SCHEMA_VERSION` stays 4.
  The previous release's SQL and supervisors keep working against the database.
- `deploy.sh` refuses a rollback past lanes while any kind has more than one unfinished attempt,
  and restores the per-kind guard after pausing. Any failure before the old resident's removal is
  confirmed restores the pointers and re-enables lanes on the live release.
- `status` keeps `<kind> slot: free` for an idle kind and shows lanes, legacy holders, kind mode,
  and wait reasons. `slots_free` stays correct.
- INT-001 to INT-006 and E2E-001 pass, and the full suite passes under `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build` pass, and
  `agent-validate run` passes.
- No changes are made outside this repository, and `config/codagent.toml` gains no pins.
