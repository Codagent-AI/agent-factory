## Coverage Strategy

The specifications remain the source of unit-test requirements. That covers:

- the gate script's result mapping from review records;
- the reuse rules (`openspec/` paths, tasks-hash normalization);
- the outcome's status mapping;
- the annotation item rules;
- the comment sentence.

This plan records only the integration and end-to-end obligations those unit tests cannot catch,
the acceptance testing envelope, and human-only checks, of which there are none.

The highest risks sit at real boundaries:

- whether an isolated clone really escapes the claim clone's trust ledger with the real
  `agent-validator`;
- whether the merge-base diff and the review records behave as the design reads them;
- whether the Runner accepts the new workflow loops and session;
- whether the script chain from task-compliance record to PR description to outcome to issue
  comment keeps the counts consistent and never reports an unqualified pass.

The integration tests follow the existing conventions:

- script tests use real git repositories in `tests/integration/test_feature_workflow_scripts.py`
  with a fake `gh` on `PATH`;
- Runner catalog validation lives in `tests/integration/test_feature_workflow_catalog.py` and skips
  when no suitable Runner is installed;
- the stub-runner harness in `tests/e2e/test_feature_cycle.py` covers the factory end to end.

No obligation calls a real model.

## Integration Tests

### INT-001: An isolated review gets past a trusted claim clone with the real validator

- Covers: "Gate the feature on a bound task-compliance verdict": a trusted tree and a committed
  implementation are still reviewed, and the review is unaffected by the claim clone's trust and
  review history.
- Boundary: `task-compliance-gate.py`, real `git`, and the real installed `agent-validator`
  (`review`, `list`, and the trust ledger). The reviewer adapter CLI is a stub.
- Setup:
  - A temporary repository has a `.validator/config.yml` with one entry point, a check-free config,
    `all-reviewers` disabled, and `task-compliance: {builtin: task-compliance, enabled: false}`.
  - A target commit is followed by a branch commit that changes a source file, and a `tasks.md`
    sits under `openspec/changes/<change>/`.
  - In the claim clone, `agent-validator skip` (or a passing `run`) has made the head trusted. A
    plain `agent-validator run --enable-review task-compliance` there must report `Trusted`, which
    proves the precondition.
  - A stub executable named after the configured reviewer adapter is first on `PATH`. It returns a
    fixed review answer: one violation in one variant and none in the other, and it appends the
    prompt it received to a file.
  - A third variant passes with `max_previous_logs: 0` committed in the config, so the validator
    deletes its logs after the pass.
  - The test skips when `agent-validator` is not installed or its review adapter cannot be stubbed,
    naming the reason.
- Action: run the gate script with `phase=implemented`, `target_head=<target commit>`, and the
  tasks file.
- Assertions:
  - `task-compliance.json` has `result` `failed` with the stub's violation in the first variant, and
    `passed` in the second and third. This proves the passing result survives the real validator's
    auto-clean, both rotation into `previous/` and deletion;
  - `reviewed_head` equals `HEAD`, and `base` equals `git merge-base <target> HEAD`;
  - the prompt the stub received contains the tasks content and the changed source file, and no file
    from the target side only;
  - in the failing variant the evidence directory holds a copied `review_*task-compliance*.json` with
    status `fail`. In the rotating passing variant the copied record comes from `previous/`. In the
    deleting variant the recorded evidence includes the captured `[PASS] review:.:task-compliance`
    dispatch line;
  - the claim clone's `validator_logs` and the ledger under its git dir are unchanged;
  - no temporary review clone remains;
  - the script exits 1 for `failed` and 0 for `passed`.
- Execution: `tests/integration/test_task_compliance_gate.py`, in the default `uv run pytest` run.

### INT-002: Gate decisions across git history with a stub validator

- Covers:
  - "Gate the feature on a bound task-compliance verdict": no-verdict cases, error retry, reuse
    and re-review rules, resume merges, changed tasks, `not-declared`, and an unreadable
    declaration;
  - "Qualify the validator status in the feature outcome": the record fields it relies on.
- Boundary: `task-compliance-gate.py` with real git repositories and merges, and a fake
  `agent-validator` on `PATH`. The fake answers `list` from a fixture and, for `review`, writes
  review record JSON copied from real runs (`pass`, `fail`, `error`, `preserved_one_shot`,
  `skipped_prior_pass`, or none) and prints a status line.
- Setup:
  - a target branch and a claim branch;
  - a scripted sequence of fake responses per invocation, covering:
    - records in the log root or only in `previous/`;
    - a stdout `[PASS]` job line with no record (logs deleted);
    - a `[PASS]` line whose message says the state was preserved;
  - `list` fixtures with a root entry point declaring task-compliance and with only a `packages/app`
    entry point declaring it;
  - an invocation log recording each call's working directory and arguments.
- Action: run the gate in sequence:
  - `implemented`, then commit only under `openspec/` (archive move and checkbox tick), then
    `verified`;
  - then commit a source change and run `verified`;
  - then, as a resume would, merge a newer target head, write `base-merge.json` with it as
    `base_head`, and run `verified` with the older admission `target_head`;
  - with the `packages/app`-only declaration, change files under both `packages/app` and
    `packages/lib`;
  - separately, with an `error` followed by `pass`, with `error` twice, with no record and exit 0,
    with `list` lacking task-compliance, and with `list` failing.
- Assertions:
  - The run after the `openspec/`-only commit makes no `review` call and keeps the verdict.
  - A source change triggers a review.
  - After the resume merge, the review's `--base-branch` is the merged `base_head` from
    `base-merge.json`, not the admission `target_head`. The recorded `base` is the merge base with it,
    and no target-only file is in the reviewed diff.
  - A record found only in `previous/`, or a `[PASS]` dispatch line with no record, gives `passed`.
    A preserved-state `[PASS]` line gives `not-run`.
  - The `packages/app`-only declaration with a `packages/lib` change gives `not-run`, naming the
    `packages/lib` files, even though the `packages/app` job passed. With a root declaration, the same
    change gives `passed`.
  - `error` then `pass` gives `passed` after exactly two calls, and `error` twice gives `not-run`
    with the error.
  - A run with no record and exit 0 gives `not-run` with the printed status, never `passed`.
  - `preserved_one_shot` and `skipped_prior_pass` alone give `not-run`.
  - A missing declaration gives `not-declared` with no `review` call. A failing `list` gives
    `not-run`.
  - A tasks file changed beyond its checkboxes triggers a review.
  - Every `review` call's working directory is a separate clone, not the claim clone.
- Execution: `tests/integration/test_task_compliance_gate.py`.

### INT-003: The feature workflow wires both gates and the Runner accepts it

- Covers:
  - "Gate the feature on a bound task-compliance verdict": the gates' placement, the repair bound,
    and gates that never stop the workflow;
  - "Classify review attention without blocking": the classifier is told not to add
    task-compliance items.
- Boundary: the packaged `factory-feature-v1.0.yaml` with the staged catalog, the installed Agent
  Runner's `-validate`, and the YAML step graph.
- Setup: extend `test_feature_catalog_validates_and_preserves_prepopulated_session_dir` and add a
  structural test beside `test_int009_feature_merge_steps_and_staged_catalog`. The gate script must
  be listed among the staged feature files.
- Action: validate the catalog, then load the workflow's steps.
- Assertions:
  - The Runner validates the workflow.
  - The `implemented` gate loop sits after `implement` and before `complete-task`, and has
    `implement`'s skip condition.
  - The `verified` gate sits after the verify status steps and before `classify`, and is skipped
    only when an outcome exists or `validator_status` is not `passed`.
  - Each loop has `max: 3`, a `break_if: success` review step, and a repair step on an
    `implementor-agent` session, followed by one verification-only run.
  - No gate step captures `validator_status`, and `continue_on_failure` lets a `failed` result
    proceed.
  - The `classify` prompt says that task-compliance items are added by the workflow.
  - `record-outcome` receives the task-compliance record path.
  - The staged catalog contains `task-compliance-gate.py`.
- Execution: `tests/integration/test_feature_workflow_catalog.py`, which skips without a suitable
  Runner as today, and `tests/integration/test_feature_workflow_scripts.py` for the structural
  assertions, which always run.

### INT-004: From task-compliance record to PR description, outcome, and parser

- Covers:
  - "Classify review attention without blocking": the task-compliance tiers, exactly one item, and
    the uncovered commits;
  - "Report the task-compliance result": the description and counts;
  - "Qualify the validator status in the feature outcome".
- Boundary: `annotate-pr.py`, then `record-outcome.sh`, then `outcome.read_interpreted_outcome`,
  with a real git branch and the existing fake `gh` that captures the PR body.
- Setup:
  - A feature branch has accepted, reviewed, and two later CI-repair commits.
  - `review-attention.json` comes from a classifier that wrongly added its own red `Task-compliance
    did not run` item and no other task-compliance item.
  - `task-compliance.json` is parametrized over `not-run` (reason `Trusted`), `failed` (two
    violations), `not-declared`, `passed` with `reviewed_head` before the CI commits, and missing.
- Action: run `annotate-pr.py`, then `record-outcome.sh` with `validator_status=passed`, a passing
  CI status, PR details, and the task-compliance path. Then parse the outcome with `outcome.py`.
- Assertions:
  - Exactly one task-compliance item appears, in the specified tier with its fixed title. A missing
    record gives red `no task-compliance record`.
  - The red count in the description heading equals the `red` count in the outcome's
    `review_attention_counts`.
  - The `Commits after acceptance` item names both CI commits as not covered by task-compliance.
  - The outcome is `pull-request`, with `validator.checks` `passed` and `validator.status`:
    `incomplete` for `not-run` and for a missing record, `review-failed` for `failed`, and `passed` for
    `passed` and `not-declared`.
  - A missing record gives `task_compliance` `{result: not-run, reason: no task-compliance record}`,
    never an outcome without `task_compliance`.
  - `task_compliance.result` and `reviewed_head` match the record, and `outcome.py` accepts the file.
  - With `validator_status=failed`, the outcome stays `failed` with `validator.status` `failed`.
  - A fix-contract and a task-contract invocation without the new input produce byte-identical
    outcomes to today's.
- Execution: `tests/integration/test_feature_workflow_scripts.py`.

## End-to-End Tests

### E2E-001: A feature PR whose task-compliance did not run reaches Review with a qualified report

- Covers:
  - "Report the task-compliance result": the issue comment and red count;
  - "Qualify the validator status in the feature outcome": the outcome is unchanged and nothing
    blocks.
- Surface: the factory's `tick` over the stub-runner harness (`fix_cycle.Harness`, host execution),
  as in `test_feature_happy_path`.
- Setup: a Feature card, feature config, and the stub runner. The attempt finishes with a
  `pull-request` outcome that has `review_attention_counts` `{red: 1, orange: 0, yellow: 0}`,
  `validator: {status: incomplete, checks: passed}`, and `task_compliance: {result: not-run, reason:
  Trusted, …}`. A second variant uses `failed` with `review-failed`.
- Journey: admit, then finish, then tick.
- Assertions:
  - The card moves to Review with `pending-human-review`.
  - The PR comment contains `1 red flags` and `Task-compliance did not run: Trusted.` (or
    `Task-compliance violations remain.` for the failed variant).
  - A variant with `task_compliance.result` `passed` adds no task-compliance sentence.
  - No `infra-error` or technical failure is recorded.
- Execution: `tests/e2e/test_feature_cycle.py`.

## Acceptance Testing Envelope

- Environments and sandboxes:
  - temporary git repositories and clones under the test's temp directory;
  - a scratch checkout of this branch;
  - the installed Agent Runner and Agent Validator on `PATH`;
  - the stub-runner harness.

  The pass may run `task-compliance-gate.py` directly against scratch repositories, including one
  copied from a real feature branch in this repository, to exercise `Trusted`,
  `no_applicable_gates`, committed-work, merge, and openspec-only-change paths.
- Credentials and secrets: the host's logged-in reviewer CLIs (`claude`, `codex`) and the operator's
  `gh` login exist. The pass may use the reviewer CLIs. It may use `gh` only for read-only calls.
- Authorized effects: real task-compliance reviews against scratch repositories through the host
  reviewer CLIs. Each costs one model dispatch of a few minutes. Keep it to about six dispatches in
  total. Temporary clones and repositories must be removed afterwards.
- Off limits:
  - the live service: the LaunchAgent, `launchctl`, `scripts/deploy.sh`, and anything under
    `~/.agent-factory/releases`;
  - `~/.agent-factory/config.toml` and the live store;
  - other claims' clones and their trust ledgers or `validator_logs` under `~/.agent-factory/clones`
    (copy, never modify);
  - creating, editing, or commenting on GitHub issues, PRs, or project cards in real repositories;
  - pushing to any remote other than the claim's own branch through the workflow;
  - `config/codagent.toml` pins.
- Permitted substitutes:
  - a stub reviewer CLI or stub `agent-validator`, when model quota or the CLIs are unavailable, or
    to force `error`, `fail`, and `preserved_one_shot` responses;
  - the fake `gh` from the test suite, for PR description rendering.
- Known risk areas:
  - Parsing coupled to the validator: `agent-validator list` text (review gates and entry points),
    review record file names and `status` values, console job lines, auto-clean rotation, and the
    three-dot `--base-branch` semantics. Any drift must produce `not-run`,
    never `passed`.
  - Checkbox-normalized tasks hashing across the implemented checkpoint and archive.
  - The resume paths: resume at archive, verify, or finalize skips the first gate, and a merged
    target head on resume.
  - `record-outcome.sh` is shared with the fix and task contracts. Their outcomes must not change.
  - Removing the temporary clone on errors and interrupts.
  - Repeated items in prior annotation work (deduplication by title, and later commits a
    classifier item already names).
  - Accepted limitations:
    - CI-repair commits after the last review are reported as not covered rather than reviewed
      again;
    - the implementation step's own review is never reused;
    - each declaring run costs one or two extra reviews.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Gate the feature on a bound task-compliance verdict (isolated run past trust, pass surviving auto-clean) | INT-001 | — | — |
| Gate the feature on a bound task-compliance verdict (reuse, re-review, resume merge base, retry, declaration, entry-point coverage) | INT-002 | — | — |
| Gate placement, repair bound, and non-blocking workflow wiring | INT-003 | — | — |
| Classify review attention without blocking (task-compliance items) | INT-003, INT-004 | — | — |
| Qualify the validator status in the feature outcome | INT-004 | E2E-001 | — |
| Report the task-compliance result (description and counts) | INT-004 | — | — |
| Report the task-compliance result (issue comment) | — | E2E-001 | — |
