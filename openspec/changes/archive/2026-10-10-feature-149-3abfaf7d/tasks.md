- [x] Classify feature review attention after finalization, with a durable outcome when classification fails

## Task: Classify review attention after finalization (#149)

Implement the whole change described in these files in the change directory:

- `proposal.md`.
- The delta spec `specs/factory-feature-execution/spec.md`:
  - MODIFIED "Classify review attention without blocking";
  - MODIFIED "Finalize the feature pull request";
  - ADDED "Record a durable outcome when classification fails after finalization".
- `design.md`. It is authoritative for the step order, the step table (ids, commands, captures,
  `skip_if`, `continue_on_failure`), the `record-classification-failure.py` behavior and reason
  texts, and the outcome wiring.
- `decisions.md`. Where entries differ, later entries override earlier ones. In particular:
  - D20 (AR-001) overrides D15's delete fallback. The counts input comes from
    `classification_counts`, which is cleared on failure, and the rename is best effort with no
    deletion.
  - D21 (AR-002) and D22 (AR-003) override the earlier test-plan text.
- The automated obligations in `test-plan.md`: INT-001, INT-002, and INT-003.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or anything under `/Users/paul/codagent/*`. Never run `scripts/deploy.sh`,
`launchctl`, or a factory `tick`. Automated tests must never run a real model CLI, open or edit a
GitHub pull request or issue, or use Fly or Docker. Use the stub `claude` executable and project
agent profile that the test plan describes.

### Scope

1. **Workflow** (`src/agent_factory/work_kinds/pull_request/workflow/factory-feature-v1.0.yaml`):
   - Move `classify` and `verify-classification` after `finalize` and `mark-ci-failed`.
   - Add the new steps in the order and with the definitions in design "New step order" and its
     table: `seed-classification-reasons`, `seed-classification-counts`,
     `seed-classification-status`, `mark-classify-failed`, `mark-classification-invalid`,
     `record-classification-failure`, and `clear-classification-counts`.
   - Add `continue_on_failure: true` to `classify`, to `verify-classification` (keep its inline
     repair and prompt unchanged), and to `record-classification-failure`.
   - Extend the `skip_if` of `verify-classification` and `annotate-pr` to skip on a non-`passed`
     `classification_status`.
   - `seed-annotation-status` derives `failed` from a non-`passed` `classification_status`.
   - `record-outcome` takes `reasons: "{{classification_reasons}}"` and
     `review_attention_counts: "{{classification_counts}}"`.
   - Keep the classify prompt text unchanged. It gets no new rule about push or CI state.
   - Keep the comment above `seed-verify-status`.
2. **Script** (new
   `src/agent_factory/work_kinds/pull_request/workflow/record-classification-failure.py`, which must
   be executable):
   - Follow design "`record-classification-failure.py`": a best-effort rename to
     `review-attention.rejected.json`, deleting nothing, with a failed move named in the reason.
   - Use the exact reason texts for `session-failed` and `invalid`, including the no-file and
     move-failed variants.
   - Append `CI did not pass within its fix cycle` when `ci_status` is `failed`.
   - Write the JSON array to stdout in one final write, and exit 0.
   - Read script inputs the way sibling scripts do: `script_inputs` arrive as JSON on stdin.
3. **Staging**: add `record-classification-failure.py` to `FEATURE_STAGED_FILES` in
   `src/agent_factory/work_kinds/pull_request/kinds.py`, and check any other staged-file list
   or doctor check that enumerates feature workflow files.
4. **Tests**:
   - INT-001: a static test in `tests/integration/test_feature_workflow_catalog.py` with no Runner
     or platform skip, asserting every item listed in the test plan. Update existing assertions in
     that file that depend on the old position of `classify` or `verify-classification`.
   - Runner suitability: change `suitable_runner()` so it probes a small known-good fixture
     workflow, not `factory-feature-v1.0.yaml`. When `FEATURE_REQUIRE_RUNNER=1`, a missing or
     incompatible Runner fails instead of skipping. Apply this to the existing Runner-validated
     catalog test as well.
   - INT-002: `tests/integration/test_feature_classification_order.py`, covering cases (a) to (h)
     with the real Runner, the real `classify` session and inline repair driven by a stub `claude`,
     and the extraction and substitution rules in the test plan.
   - INT-003: in `tests/integration/test_feature_workflow_scripts.py`, covering every
     bad-classification shape, the CI status combinations, the fault-injected rename failure, and
     the regression check that empty reasons leave outcomes unchanged.
   - Add unit tests for the script's reason construction where useful. Existing tests must pass
     unchanged apart from the order-dependent assertions noted above.
5. **Acceptance run**: during acceptance, run
   `FEATURE_REQUIRE_RUNNER=1 uv run pytest tests/integration/test_feature_classification_order.py tests/integration/test_feature_workflow_catalog.py`
   against the installed Agent Runner, and record its output in the acceptance evidence. CI does
   not provision a Runner.
6. **Pull request description**: include an orange item stating that CI does not run the
   Runner-semantics tests (INT-002 and the Runner-validated catalog test), and that the
   `FEATURE_REQUIRE_RUNNER=1` acceptance run is their proof (D21).

### Done when

- On a feature attempt whose validator passed, `classify` runs only after `finalize` (and
  `mark-ci-failed`), and `annotate-pr` runs only after a classification that passed verification.
  This holds whether CI passed or failed.
- Commits that finalization adds appear in the classification's `later_commits` and are named by
  an orange item that states their diff size and has a `Tests:` sentence. The annotation's
  later-commit safety net still works.
- Each of these produces a verified `failed` outcome with the PR reference, `ci.status`, a reason
  naming the classification failure (plus the CI reason when CI failed), and no
  `review_attention_counts`, with no annotation: a classify session failure; a classification still
  invalid after repair; a missing, truncated, non-object, or structurally invalid record; and a
  failed rename of the rejected file.
- The rejected classification is kept as `review-attention.rejected.json` whenever the move
  succeeds. Nothing is deleted.
- With `classification_reasons` set to `[]`, the passing, CI-failed, annotation-failed,
  definition-stop, and validator-failed outcomes are unchanged.
- INT-001 and INT-003 pass in `uv run pytest`. INT-002 and the Runner-validated catalog test pass
  with `FEATURE_REQUIRE_RUNNER=1` against the installed Runner.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and the full
  `uv run pytest` suite pass, `openspec validate --specs --strict` passes, and
  `agent-validator run` passes.
- The fix and task workflows, Agent Runner, and every file outside this repository are unchanged.
