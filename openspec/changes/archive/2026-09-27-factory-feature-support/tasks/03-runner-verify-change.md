# Task: Carve `core/verify-change` out of Agent Runner's `core/implement-change`

## Goal

Give Agent Runner a reusable builtin, `core/verify-change`, holding the autonomous tail of
`core/implement-change` (assumption review through the acceptance handoff), so the Agent
Factory's feature workflow can verify an archived OpenSpec change without re-implementing
those steps. While moving the tail, make acceptance preparation reliable: the tester runs as
a workflow step in a bounded loop instead of through `call_agent`, and Agent Validator runs
only as the `run-validator` workflow step, never from a step prompt or a skill.

The factory's feature kind cannot be enabled until a Runner build containing this builtin
is installed on the factory host, and the factory's doctor will check for it.

## Background

This work is in the Agent Runner repository, not in Agent Factory. Work in a new git
worktree of `/Users/paul/codagent/agent-runner` (under its `worktrees/` directory) on a new
branch from `origin/main`, and open the pull request against `main`. Read
`/Users/paul/codagent/agent-runner/AGENTS.md` first (test-driven development; `make test`
runs `go test -tags dev_audit ./...`; do not run `make build` unless asked). The factory's
deploy script (`scripts/deploy.sh` in Agent Factory) merges Runner `origin/main` into `dev`
and builds the host Runner from `dev`, but skips that merge with a warning when it would
conflict. The design's migration plan requires the change on both `main` and `dev` before
the feature kind is enabled, so check whether the branch also merges cleanly into
`origin/dev` and state the result in the pull request description.

Read `/Users/paul/codagent/agent-factory.features/openspec/changes/factory-feature-support/design.md`,
section "C. Agent Runner prerequisite" and Decisions 9 and 10; the proposal's "What Changes"
part 4 states the scope.

Current state:

- `workflows/core/implement-change-v1.0.yaml` (used by `openspec:change` v2,
  `spec-driven:change` v2, `openspec:implement-change` v2, and
  `spec-driven:implement-change` v2; the v1 change workflows use their own older files)
  runs `validate-change-name`, `validate-skip-validator`, `check-plan`, `implement-tasks`,
  `complete-task-index`, `verify-task-index`, then the tail: `review-assumptions` (writes
  `output/acceptance-assumptions.md`), `verify-assumptions-handoff`, `simplify`,
  `run-validator` (`workflow: ../core/run-validator-v1.0.yaml`), `verify-clean-for-pr`,
  `open-draft-pr` (its prompt invokes `codagent:push-pr`, which runs validator detection and
  the `validator-run` skill), `verify-draft-pr` (captures `pr_url`), `prepare-acceptance`
  (`tools: [call_agent]`; the lead calls the `acceptance-tester` session up to three times
  and writes `output/acceptance-preparation-status.txt` as `ACCEPTANCE_COMPLETE` or
  `ACCEPTANCE_FAILED`), and `verify-acceptance-handoff`, which accepts either marker.
- Sub-workflows share the run's `{{session_dir}}` and named sessions, receive only the
  params passed to them, and cannot return captures, so results come back through files.
  `skip_if` supports `previous_success` and `sh: <command>`. Loops support `max_param` and
  `break_if`.
- Existing tests that cover these workflows include `workflows/embed_test.go` and
  `workflows/pull_request_test.go`, plus the gate-script tests beside them
  (`reacceptance_status_gate_test.go`, `ci_status_gate_test.go`).

Decisions to implement:

1. New `workflows/core/verify-change-v1.0.yaml` with params `change_name`, `change_dir`,
   `change_label`, `artifact_validation_instruction` (required), `skip_validator` (default
   `"false"`), and `acceptance_rounds` (default `"3"`); sessions `lead-agent: lead` and
   `acceptance-tester: tester` (named sessions are shared across the run, preserving
   continuity with the caller). Steps: `validate-change-name`, `validate-skip-validator`,
   then `review-assumptions`, `verify-assumptions-handoff`, `simplify`, `run-validator`,
   `verify-clean-for-pr`, `open-draft-pr`, `verify-draft-pr` moved verbatim from
   `implement-change` (except `open-draft-pr`, below), then the restructured acceptance,
   then `verify-acceptance-handoff` verbatim.
2. Acceptance without `call_agent`, with the validator as a step: a `loop` with
   `max_param: acceptance_rounds`, whose body is
   (a) `acceptance-test` in session `acceptance-tester`, autonomous, using
   `codagent:prepare-acceptance` with the same inputs and evidence directory as today and,
   from the second round, `acceptance-impact-scope.md` naming the affected flows;
   (b) `acceptance-gate`, a script that exits 0 when the tester's evidence shows convergence,
   with `break_if: success`;
   (c) `acceptance-fix` in session `lead-agent`, which fixes reported defects with
   `codagent:implement-with-tdd`, runs the artifact validation instruction and the narrowest
   relevant checks, commits with a `[{{step_id}}]` prefix, and writes the impact scope for
   the next round, but does not run Agent Validator or push;
   (d) `acceptance-validator`, `workflow: run-validator-v1.0.yaml`, skipped when
   `skip_validator` is `true`;
   (e) `acceptance-push`, a script that pushes and verifies local `HEAD` equals the draft
   pull request head.
   Steps (c) to (e) are skipped on the last round, so no unverified fix is made after the
   final test. After the loop, `write-acceptance-status` writes `ACCEPTANCE_COMPLETE` or
   `ACCEPTANCE_FAILED` to `output/acceptance-preparation-status.txt` and ensures
   `acceptance-handoff.md` exists (on failure it lists the open defects and evidence).
3. `open-draft-pr` no longer invokes `codagent:push-pr`: its prompt pushes the branch and
   opens the draft pull request directly, and the existing `verify-draft-pr` script checks
   the result. No step prompt in `verify-change` runs Agent Validator, directly or through a
   skill; validation is always the `run-validator` workflow as its own step.
4. `core/implement-change` keeps its head (through `verify-task-index`) and calls
   `verify-change-v1.0.yaml` with its params, leaving `acceptance_rounds` at the default.
   The four v2 callers behave as before, apart from the tester running as a workflow step.
5. Out of scope: restructuring `core/finalize-pr` (its `push-pr` and `fix-pr` steps still
   run the validator through skills; that is a follow-up), and any change to the v1 change
   workflows.

## Spec

Agent Factory's specifications consume this builtin; the requirement it must satisfy there:

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Verify the change and open a draft pull request

After archiving, the feature workflow SHALL run the Runner's `core/verify-change` builtin workflow given the archived change directory: assumption review, simplify, the validator, the clean-tree check, a draft pull request, and acceptance preparation against the test plan. A validator that remains red after its bounded repair SHALL return `failed` with reasons; the branch SHALL be pushed and no pull request opened.

#### Scenario: Open a draft pull request

- **WHEN** the validator passes after simplify
- **THEN** the workflow pushes the branch, opens a draft pull request, and prepares acceptance evidence against the test plan

#### Scenario: Stay red after validator repair

- **WHEN** the validator remains red after its bounded repair
- **THEN** the workflow pushes the branch, opens no pull request, and returns `failed` with the failing checks


For the Runner itself, behavior is defined by the decisions above: `core/implement-change`
v2 callers keep their observable behavior (same outputs, same `ACCEPTANCE_COMPLETE` /
`ACCEPTANCE_FAILED` markers, same draft pull request), and `core/verify-change` is callable
by an external project workflow as `workflow: builtin:core/verify-change-v1.0.yaml` with the params
above.

## Test Plan

The factory change's test plan assigns no `INT-*` or `E2E-*` obligation here; the Runner
pull request carries its own tests. Add, test-first, in the Runner repository:

- workflow validation of `verify-change-v1.0.yaml` and the modified
  `implement-change-v1.0.yaml` (they load, validate, and embed);
- composition: `core/implement-change` calls `core/verify-change` with its params and the
  v2 change and implement-change workflows still resolve;
- static checks that no step in `verify-change` declares `tools: [call_agent]` and no step
  prompt invokes Agent Validator or a validator-running skill (`codagent:push-pr`,
  `validator-run`);
- `acceptance-gate` and `acceptance-push` script tests (convergence and non-convergence
  evidence; push verified against the draft pull request head, and a mismatch fails);
- loop behavior: `acceptance_rounds` bounds the loop, the gate breaks it on convergence,
  steps (c) to (e) are skipped on the last round, and `write-acceptance-status` writes the
  right marker and `acceptance-handoff.md` in both cases.

## Done When

- `workflows/core/verify-change-v1.0.yaml` exists with the params, sessions, and steps
  above, and `core/implement-change` delegates its tail to it.
- The tests listed above pass, and `make test` passes in the Runner worktree.
- `./dev.sh -validate` (or the Runner's equivalent validation command) accepts every
  modified workflow.
- The pull request description states whether the branch merges cleanly into
  `origin/dev` (checked with a dry-run merge in a scratch worktree, pushing nothing to
  `dev`).
- A pull request against Agent Runner `main` is open, describing the carve-out, the
  acceptance loop, and the removal of `call_agent` and prompt-driven validation from the
  tail, and noting that `core/finalize-pr` restructuring is a follow-up.

## Delivery

This task's code lives in Agent Runner, so it has no Agent Factory commit of its own:

- https://github.com/Codagent-AI/agent-runner/pull/155 (merged): the `core/verify-change`
  carve-out, the bounded acceptance loop, and removal of `call_agent` and prompt-driven
  validation from the tail.
- https://github.com/Codagent-AI/agent-runner/pull/156: stop `verify-change` before opening a
  pull request when the validator stays red after repair (the "Stay red after validator
  repair" scenario), and read `run-validator.sh` inputs once.
