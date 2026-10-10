## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration and end-to-end obligations, the acceptance testing envelope, and exceptional human-only
obligations.

This change reorders steps in the feature workflow YAML and adds one deterministic script. The
risks are at three boundaries:

- whether the staged catalog still validates with the real Agent Runner, and whether the step order
  and flags are as designed;
- whether the Runner's `skip_if: previous_success` and `continue_on_failure` semantics give the
  `classification_status` and `annotation_status` values the design expects on each path;
- whether the new script, `record-outcome.sh`, and `verify-feature-outcome.py`, composed as real
  subprocesses, always produce a verified `failed` outcome when classification fails.

These are integration obligations. No new E2E obligation is warranted. The existing feature-cycle
E2E (`tests/e2e/test_feature_cycle.py`) drives the controller with a substitute Runner, not the
workflow YAML. Running the real feature workflow end to end needs live model sessions, GitHub, and
CI, so it belongs to the acceptance pass and the first real feature attempt after deploy. The
existing E2E must still pass unchanged.

## Integration Tests

### INT-001: The feature workflow's step order and wiring (static, always runs)
- Covers: "Classify review attention without blocking" (classification follows finalization);
  "Finalize the feature pull request" (classification and annotation follow it); "Record a durable
  outcome when classification fails after finalization" (the wiring).
- Boundary: the staged `.agent-runner/workflows` catalog as YAML, plus file staging and modes.
- Setup: a new test in `tests/integration/test_feature_workflow_catalog.py` with no Runner-based
  or platform skip. Add `record-classification-failure.py` to the feature's staged files.
- Action: stage the catalog and parse `factory-feature-v1.0.yaml`.
- Assertions:
  - The top-level step indices are ordered `finalize` < `mark-ci-failed` < `classify` <
    `mark-classify-failed` < `verify-classification` < `mark-classification-invalid` <
    `record-classification-failure` < `clear-classification-counts` < `seed-annotation-status` <
    `annotate-pr` < `record-outcome`.
  - `seed-classification-status` comes immediately before `classify`.
  - `classify`, `verify-classification`, and `record-classification-failure` have
    `continue_on_failure: true`.
  - `verify-classification` keeps its inline repair.
  - `verify-classification` and `annotate-pr` skip on a non-`passed` `classification_status`.
  - `seed-annotation-status` derives its value from `classification_status`.
  - `record-outcome` takes `reasons: {{classification_reasons}}` and
    `review_attention_counts: {{classification_counts}}`.
  - The new script is staged and executable.
  - The classify prompt's existing text assertions (`git diff --shortstat`, `Tests:`, and the rest)
    still hold.
- Execution: `uv run pytest tests/integration/test_feature_workflow_catalog.py` (CI `check`).

### INT-002: The real Runner validates the catalog and runs the closing steps with a stub agent
- Covers:
  - "Record a durable outcome when classification fails after finalization": session failure, a
    repair that leaves the classification invalid, a skipped verifier.
  - "Classify review attention without blocking": CI red still classifies and annotates; a repair
    that fixes the classification reaches annotation; a definition stop or validator failure leaves
    these steps inert.
  - "Finalize the feature pull request": `pull-request` only when classification and annotation
    succeed.
- Boundary: the real Agent Runner validates the staged catalog and runs the feature workflow's
  steps from `seed-ci-status` through `verify-outcome`. It uses real `skip_if`,
  `continue_on_failure`, `capture`, and inline-repair semantics. The real `classify` session step
  and the real `verify-classification` repair run against a stub `claude` CLI.
- Setup:
  - Runner suitability is probed with a small known-good fixture workflow, never with the workflow
    under review, so a regression in that workflow fails the test instead of skipping it.
  - A helper extracts that step range from `factory-feature-v1.0.yaml` with `skip_if`, `capture`,
    `continue_on_failure`, `repair`, and session bindings unchanged. It replaces only `finalize`
    (a command that exits 0 or 1), `annotate-pr` (a command that writes a marker), and `pr-details`
    (fixed PR JSON).
  - `verify-classification.py`, `record-classification-failure.py`, `record-outcome.sh`, and
    `verify-feature-outcome.py` run for real.
  - A project `.agent-runner/config.yaml` maps the `lead` agent to a stub `claude` on `PATH`. The
    stub is driven by environment variables per call: write a valid classification, write a
    truncated one, write one missing a later-commit item, write a valid one, or exit 1. This
    follows the stub pattern in the Runner's own tests.
- Action: run the fragment for each case:
  - (a) everything passes;
  - (b) the classify session fails;
  - (c) classify writes truncated JSON and the repair leaves it invalid;
  - (d) classify writes a file missing a later-commit item and the repair fixes it;
  - (e) finalize fails while the classification is valid;
  - (f) finalize fails and classification stays invalid after repair;
  - (g) `feature-outcome.json` is pre-seeded, as after a definition stop;
  - (h) the validator status is `failed`.
- Assertions:
  - (a) and (d) The annotate marker exists, and the outcome is `pull-request` with counts. In (d),
    the stub's repair invocation happened.
  - (e) Classification and annotation ran, the counts are retained, and the outcome is `failed` with
    `ci.status=failed` and the CI reason.
  - (b), (c), and (f) There is no annotate marker. The outcome is `failed` with the PR reference, a
    reason naming the classification failure (and the CI reason in (f)), `ci.status` matching
    finalize, and no `review_attention_counts`. `review-attention.rejected.json` exists whenever a
    file existed, and `verify-outcome` passes.
  - (b) Specifically, the verifier did not run.
  - (g) and (h) Neither classification nor annotation ran, and the pre-existing or
    validator-failure outcome is unchanged.
- Execution: `tests/integration/test_feature_classification_order.py`, which is macOS-gated.
  - CI does not provision Agent Runner, so the test skips when no Runner is configured.
  - With `FEATURE_REQUIRE_RUNNER=1` set, a missing or incompatible Runner fails the test instead.
  - Acceptance must run `FEATURE_REQUIRE_RUNNER=1 uv run pytest tests/integration/test_feature_classification_order.py tests/integration/test_feature_workflow_catalog.py`
    against the installed Runner (`FEATURE_TEST_RUNNER` or the default `agent-runner` on `PATH`),
    and record the output in the acceptance evidence as a completion obligation.

### INT-003: The failure script, outcome recording, and verification compose for every bad classification
- Covers: "Record a durable outcome when classification fails after finalization" (malformed
  record, invalid after repair, classification and CI both failing, evidence that cannot be moved).
- Boundary: real subprocesses of `record-classification-failure.py`, `record-outcome.sh`, and
  `verify-feature-outcome.py`, sharing a temporary artifact directory.
- Setup:
  - Write `review-attention.json` as: missing; truncated JSON; a JSON array (not an object); an
    object without tier lists; or a valid object (for the session-failure status).
  - Use `classification_status` values `session-failed` and `invalid`, and `ci_status` values
    `passed` and `failed`.
  - A fault-injected case makes the rename fail, for example with a read-only artifact directory,
    or with a pre-existing directory at the rejected-copy path.
- Action: run the script, then pass its stdout to `record-outcome.sh` as `reasons` with
  `annotation_status=failed`, an empty `review_attention_counts` (as `clear-classification-counts`
  provides), and fixed `pr_details`. Then run `verify-feature-outcome.py`.
- Assertions:
  - Each command exits 0.
  - The outcome is `failed`, with `pr`, `branch`, and `ci.status`, and no
    `review_attention_counts`.
  - The reasons name the classification failure, and include `CI did not pass within its fix
    cycle` exactly when CI failed.
  - The rejected copy exists, with the original bytes, whenever a file existed and the rename
    worked.
  - In the fault-injected case, the original file is untouched, the reason states that it could not
    be moved, and the verified failed outcome is still written.
  - A regression case passes `reasons="[]"` with `annotation_status` `passed` and `failed`, and
    with CI `passed` and `failed`. Each produces the same outcome as before this change.
- Execution: `tests/integration/test_feature_workflow_scripts.py` (CI `check`).

## End-to-End Tests

None new. The reasons are in the Coverage Strategy. `tests/e2e/test_feature_cycle.py` must still
pass unchanged.

## Acceptance Testing Envelope

- Environments and sandboxes: the claim's clone on Paul's Mac, temporary directories, and temporary
  git repositories. The pass may build a staged catalog and run the installed `agent-runner` (for
  `-validate`, and to execute workflow fragments whose session and network steps are replaced, as
  in INT-002). It may also run the workflow scripts directly.
- Credentials and secrets: the operator `gh` login and model CLI credentials exist on the host, but
  this pass needs neither. Do not use them to create pull requests or sessions.
- Authorized effects: local files under temporary directories and the claim's artifact directory.
  No external effects are authorized and none need cleanup.
- Off limits:
  - The live factory: `~/.agent-factory/releases`, the service clone, the LaunchAgent,
    `config.toml`, deploys, and `tick`.
  - Real GitHub pull requests, issues, or project cards.
  - Fly and Docker.
  - Paul's checkout.
  - Pushing anything other than the claim branch, through the workflow's own steps.
- Required run: `FEATURE_REQUIRE_RUNNER=1` INT-001 and INT-002 against the installed Agent Runner.
  Record the output in the acceptance evidence. CI does not provision a Runner, so this run is
  where the Runner semantics are proven.
- Permitted substitutes:
  - Shell-command stand-ins for `finalize`, `annotate-pr`, and `pr-details` when executing workflow
    fragments.
  - A stub `claude` CLI behind a project agent profile, for `classify` and its repair.
  - A prepared `review-attention.json` in place of a model-written one.
  - A fixed PR JSON in place of `gh pr list`.
- Known risk areas:
  - `skip_if: previous_success` after skipped top-level steps. The design relies on a skipped
    top-level step not resetting the previous outcome.
  - The interaction between explicit `reasons` and `record-outcome.sh`'s default reasons on the
    CI-failed and annotation-failed branches.
  - A stale `review-attention.json` from an earlier step on the same artifact path.
  - Classify-prompt assertions in the catalog test that may be coupled to the old position.
  - Accepted limitations: a ready PR without annotation on classification failure; an attempt limit
    expiring during classification still ending technically.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Classify review attention without blocking: classification follows finalization | INT-001, INT-002 (a) | — | — |
| Classify review attention without blocking: CI red still classifies and annotates | INT-002 (e) | — | — |
| Classify review attention without blocking: a repair corrects the classification | INT-002 (d) | — | — |
| Classify review attention without blocking: definition stop or validator failure leaves the tail inert | INT-002 (g), (h) | — | — |
| Finalize the feature pull request: `pull-request` only when classification and annotation succeed | INT-002 (a), (e) | — | — |
| Record a durable outcome when classification fails after finalization: session failure | INT-002 (b), INT-003 | — | — |
| Record a durable outcome when classification fails after finalization: invalid after repair | INT-002 (c), INT-003 | — | — |
| Record a durable outcome when classification fails after finalization: classification and CI both fail | INT-002 (f), INT-003 | — | — |
| Record a durable outcome when classification fails after finalization: malformed record, or evidence that cannot be moved | INT-003 | — | — |
| Wiring of the step order, guards, and inputs | INT-001 | — | — |
