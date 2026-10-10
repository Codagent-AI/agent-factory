- [x] Record a run-ending REPAIR_BLOCKED in any feature step as a needs-input outcome, end to end

## Task: Record REPAIR_BLOCKED in any feature step as needs-input (#142)

Implement the whole change described in these files in the change directory:

- `proposal.md`.
- The delta specs under `specs/`:
  - `factory-feature-execution`: the MODIFIED "Invoke the versioned feature workflow" and the ADDED
    "Record a repair-blocked step as needs-input", with every scenario.
  - `factory-feature-reporting`: the MODIFIED "Comment on feature activity".
- `design.md`. It is authoritative for these details:
  - the `repair-block.py` layout, its two contexts (`run`, `archive`), and its supersession scopes
    (`path`, `subtree`);
  - the causal-chain rule;
  - `attempt-start.json`;
  - the resume-point and branch-state computation;
  - the continuation push;
  - the outcome shape (`blocked_step`, optional `stopped_step` and `branch`);
  - the wrapper invocation;
  - the stub agent harness.
- `decisions.md`. Where entries differ, later entries override earlier ones. In particular:
  - D8 supersedes D5;
  - D10 corrects D6's evidence claim;
  - D23 to D25 (approach review) override the design and test-plan text they revise. The archive
    context keeps today's subtree-wide stale rule, INT-007 runs the installed Runner, and INT-008
    runs the resumed definition through plan publication.
- The automated obligations in `test-plan.md`: INT-001 to INT-008 and E2E-001.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or anything under `/Users/paul/codagent/*`. Never run
`scripts/deploy.sh` or `launchctl`. Tests must never call a real model CLI, push to a real remote,
or touch GitHub. Use fake Runners, stub agent CLIs, temporary bare repositories, and the existing
fake GitHub harness.

### Scope

1. **Shared recorder** (`src/agent_factory/work_kinds/pull_request/workflow/repair-block.py`, new,
   stdlib only; design "Audit parsing", "Run context", "Archive context"):
   - audit-line parsing and step-path ancestry, including loop `:N` suffixes;
   - `blocked_response(events, endpoint, scope)` with the `path` and `subtree` scopes. A malformed
     payload counts as no declaration, and an empty explanation yields nothing;
   - the `run` context:
     - it writes nothing when an outcome already exists;
     - it requires a failed `run_end` that is not `infrastructure`;
     - it follows the causal chain of failed ends and requires the check's `repair_blocked: true`
       plus an unsuperseded declaration on that exact path;
   - the `archive` context, with endpoint `archive, sub:archive-change` and `subtree` scope, no
     `run_end`, and the existing archive summary and `stopped_step: archive`, plus `blocked_step`;
   - outcome building and writing. The direction summary names the blocked step and the resume
     step, or says the next attempt starts a fresh definition;
   - any exception writes nothing.
   - Add the script to the packaged workflow file list in `kinds.py`.
2. **Resume point and branch state** (design "Resume point and branch state"):
   - `prepare-branch.sh` writes `<artifact_dir>/attempt-start.json` with `resume_from`, `head`, and
     `prior_head` when it continues a prior branch, and is otherwise unchanged;
   - the recorder computes the later, in `factory-resume-skip.sh` order, of `resume_from` and the
     step after the newest `Factory-Checkpoint` in
     `git log --first-parent refs/remotes/origin/<branch> ^<head>`;
   - branch state comes from `git ls-remote --exit-code`, with the remote-tracking-ref fallback;
   - when there is a resume point but the claim's branch is unpublished, push `prior_head` to the
     claim's branch. If that push fails, write nothing.
3. **Archive hook**: `record-archive-block.sh` keeps its step, inputs, payload and positional
   invocation, and error messages, and delegates to `repair-block.py archive`. All existing
   archive tests pass unchanged, including
   `test_record_archive_block_rejects_stale_declaration_after_later_failure`.
4. **Host wrapper** (`launch.host_script`):
   - only for the feature kind with a non-review contract, and only after a non-zero Runner exit;
   - run the staged `repair-block.py run` with `sys.executable -I`, the session directory, the
     artifact directory, and the branch, ending with `|| true`;
   - run it before the post-run audit, with the attempt's credentials still exported;
   - the wrapper's exit status stays the Runner's.
5. **Outcome contract** (`outcome.py`, `workflow/verify-feature-outcome.py`):
   - `blocked_step`, when present, must be a non-empty string;
   - a `needs-input` outcome carrying `blocked_step` may omit `stopped_step` and `branch`;
   - every other outcome keeps today's validation.
6. **Reporting** (`handler._feature_stop_message`): when there is no `branch` and the stop is not
   `preflight`, add "No branch was published; the next attempt starts fresh." Confirm that
   `feature_resume_point` already yields `""` for a missing `stopped_step`, and that the next
   attempt's admission comment says it starts fresh without reporting an unavailable resume point.
7. **Tests**:
   - implement INT-001 to INT-008 and E2E-001 at the locations and with the setup `test-plan.md`
     gives, including the committed sanitized real-Runner audit fixture with its source revision;
   - add unit tests for the parser, the causal chain and supersession (both scopes), explanation
     stripping, resume-order arithmetic, outcome validation, and stop-message text;
   - build the stub agent harness (design "Stub agent harness") so that the runtime parts of
     INT-007 and INT-008 run on a machine with an installed Runner, and skip only when no Runner is
     installed;
   - the suite must stay within the validator's test timeout.
8. **Documentation**: in `docs/operations.md`, add a short note on where feature failures are
   described. A run-ending `REPAIR_BLOCKED` becomes a `needs-input` card that names the blocked
   check and the resume step, and other failures still use the single recovery retry. Do not
   change `AGENTS.md` unless a statement there becomes false.
9. **Pull request description**:
   - **Orange items:**
     - the recorder depends on the installed Runner's audit format and degrades to recovery if the
       format changes;
     - a first-attempt definition block restarts definition, and unpushed work is not preserved
       (D6, D8, D10);
     - the recorder pushes prior work for a continuation whose own branch was never pushed (D19);
     - a definition-step block is reachable today only through future repair-bearing checks, so
       the acceptance test simulates it (D20);
     - the runtime tests depend on a stub agent CLI (D25).
   - **Follow-up issues to suggest:** verify and finalize blocks (D3), fix workflows (D4), and
     preserving unpushed work (D10).

### Done when

- Every requirement and scenario in both delta specs is implemented.
- A feature attempt whose run ends through a check's `REPAIR_BLOCKED` writes a valid `needs-input`
  outcome that has:
  - the exact blocked check path;
  - the explanation without the marker;
  - the resume point computed from published progress, or none for a fresh definition;
  - the branch when it is published.
- The supervisor records that outcome, no recovery run is created, the stop comment carries the
  explanation, and the next attempt resumes at the recorded step or starts fresh without a
  fallback.
- Plain failures, superseded blocks, empty explanations, infrastructure failures, limits, and
  attempts that already wrote an outcome behave exactly as before.
- verify's and finalize's `failed` outcomes, including #141's repair reason, are unchanged.
- The archive stop's outcome and stale-declaration behavior are unchanged apart from the added
  `blocked_step`.
- No partial work is committed or pushed. Only an unpushed continuation's prior head is pushed.
- INT-001 to INT-008 and E2E-001 pass, and the full suite passes under `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build` pass, and
  `agent-validator run` passes.
- No changes are made outside this repository.
