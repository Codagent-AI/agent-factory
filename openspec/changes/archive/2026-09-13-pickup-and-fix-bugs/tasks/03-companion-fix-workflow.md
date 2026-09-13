# Task: Deliver the headless `factory-fix` workflow in agent-runner

## Goal

Add a named, versioned, headless Agent Runner workflow, `core/factory-fix-v1.0.yaml`, that takes an issue file, a branch name, and a contract version, decides whether the bug is safely fixable, and either implements the fix with tests, runs the validator, pushes the branch, and opens a ready-for-review PR, or declines with typed reasons, writing exactly one structured outcome to `/artifacts/fix-outcome.json`. This is the companion change the factory's fix work kind invokes; it must ship on `agent-runner` `main` before the factory can execute a fix.

## Background

This task is delivered in the sibling repository `agent-runner` (working checkout `/Users/paul/codagent/agent-runner`, remote `Codagent-AI/agent-runner`), following that repository's workflow conventions and test layout. Planning sources are in `agent-factory` at `openspec/changes/pickup-and-fix-bugs/proposal.md`, `design.md` (sections "The companion workflow", "Outcome contract", "Launch" for the container invocation, and "Decisions: One companion workflow" and "One CI fix cycle for factory runs"), `specs/factory-fix-execution/spec.md`, and `test-plan.md`.

Agent Runner already has the pieces: `core/implement-task`, `core/run-validator-v1.0` (three repair cycles), `core/finalize-pr-v1.0` (push, ready-for-review, three CI fix cycles), autonomous headless steps with `capture`, and `skip_if: 'sh: …'` against captured values. Inspect the current versions of those workflows and match their parameter and step conventions. The factory invokes the workflow inside the Runner sandbox as:

```
cd /workspace/repo
AGENT_RUNNER_NO_TUI=1 agent-runner run core/factory-fix \
   --param issue_file=/artifacts/input/issue.json \
   --param branch_name=factory/fix-<issue>-<claim8> \
   --param contract_version=factory-fix/1
```

with `/workspace/repo` a fresh clone of the target repository at a recorded commit (clean tree, detached), `/workspace/skills` a read-only Agent Skills clone already installed, `GH_TOKEN` in the environment for `gh` and a Git askpass helper, and role profiles supplied through `/workspace/repo/.agent-runner/config.yaml` written by the factory. The workflow contributes all LLM-driven decisions; the factory never inspects the diff.

### Workflow shape

The YAML starts with the literal line `# factory-contract: factory-fix/1`; the factory's `doctor` greps this line at the resolved Runner commit with `git show <sha>:workflows/core/factory-fix-v1.0.yaml`. Use whatever directory the repository already uses for `core/*` workflows and make the contract line the first line of the file.

```
params: issue_file, branch_name, contract_version
 1 check-contract     command   contract_version == "factory-fix/1" else fail
 2 check-clean-tree   command   clean tree at the recorded commit
 3 triage             lead, autonomous, capture: decision   closed JSON
                      {fixable, reasons, plan}
 4 record-triage      script    if not fixable → write /artifacts/fix-outcome.json
                                {contract, outcome: "needs-input", reasons}; capture fixable
 5 implement          sub-workflow, skip_if: 'sh: test {{fixable}} != true'
    5a create-branch  command   git checkout -b {{branch_name}}
    5b fix            lead, autonomous  codagent:implement-with-tdd on the triage plan;
                                regression test; repo conventions; commit
    5c run-validator  core/run-validator-v1.0
    5d test-flows     tester, autonomous  codagent:test-flows on the changed flow
    5e address        lead, autonomous, skip_if no findings  fix, targeted checks, commit
    5f verify-clean   command
    5g finalize-pr    core/finalize-pr-v1.0 with ci_fix_cycles=1
 6 record-outcome     script    read PR url and final CI marker → fix-outcome.json
                                {contract, outcome: "pull-request" | "failed", pr, ci,
                                validator, reasons}
 7 verify-outcome     command   fix-outcome.json present with a valid outcome
```

Triage prompt content: the lead reads `issue_file` (repository, number, title, body, attempt number, prior factory PR if any, and eligible comments) and returns closed JSON `{fixable, reasons, plan}`. It must decline (`fixable: false`) rather than guess when: the problem stems from a CLI, platform, or dependency limitation the agent cannot change; several viable solutions require a human choice (name the options and what needs deciding); the report lacks enough detail to reproduce; the fix would require changes outside the target repository; or the fix would require a non-trivial specification change, which belongs in a Feature (say "belongs in a Feature" in the reason). Trivial inline specification updates are permitted within a fix. On a re-attempt, the prompt must treat the supplied writer comments as the answers to any prior decline.

Fix step content: implement with `codagent:implement-with-tdd` on the triage plan, add a regression test, follow the repository's conventions, commit. A validator still red after 5c's repair cycles ends the sub-workflow before 5g with outcome `failed` and no push. `finalize-pr` pushes, marks ready for review, and runs one CI wait-and-fix cycle: add a `ci_fix_cycles` parameter to `core/finalize-pr-v1.0.yaml` with default 3 so interactive use is unchanged, and pass 1 here. The PR body must contain `Refs #<n>` (no closing keyword), a stable claim marker taken from `issue_file` (an HTML comment such as `<!-- agent-factory:claim:<claim id> -->`), the triage summary, and the validator result. CI still red after the loop returns `failed` with the failing checks and leaves the PR open.

`--until triage` must run triage alone for prompt tuning.

### Outcome contract

`/artifacts/fix-outcome.json`:

```json
{"contract": "factory-fix/1",
 "outcome": "pull-request" | "needs-input" | "failed",
 "reasons": ["…"],
 "pr": {"url": "…", "number": 214, "branch": "factory/fix-212-1a2b3c4d", "head_sha": "…"},
 "validator": {"status": "passed" | "failed" | "skipped"},
 "ci": {"status": "passed" | "failed" | "pending"}}
```

`pr` is present only for `pull-request` and for `failed` after a push. Exactly one outcome file per run; the file is authoritative even if the process exit code is non-zero. Absence of the file is a technical failure on the factory side, so every exit path after step 3 must write it or fail loudly.

Constraints: compose existing sub-workflows rather than adding a second push path; no factory-side logic; the workflow must run with `AGENT_RUNNER_NO_TUI=1` and no interactive prompts; do not require any environment variable other than `GH_TOKEN` and the provider auth mounts the sandbox already provides.

## Spec

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-fix-execution/spec.md`.

### Requirement: Fix or decline autonomously

The fix workflow SHALL read the issue and its supplied comments and decide whether the bug is safely fixable without human input. It SHALL return `needs-input` with specific reasons instead of guessing when the problem stems from a CLI, platform, or dependency limitation the agent cannot change, when several viable solutions require a human choice, when the report lacks enough detail to reproduce, when the fix would require changes outside the target repository, or when the fix would require a non-trivial specification change, which belongs in a Feature rather than a bug fix. Trivial inline specification updates are permitted within a fix. Otherwise it SHALL implement the fix with tests following the repository's conventions, run the validator, push the branch, open the pull request as its last step, wait for CI and address failures within the existing bounded loop, and mark the PR ready for review. A validator that remains red after its repair cycles SHALL return `failed` with reasons before any push or PR; CI that remains red after the loop SHALL return `failed` with reasons while leaving that PR open. The PR SHALL be on a deterministic branch named from the issue and claim, SHALL reference the issue without a closing keyword, and SHALL identify the factory claim in a stable marker.

#### Scenario: Fix a reproducible bug

- **WHEN** the issue describes a reproducible defect within the target repository
- **THEN** the workflow produces a PR with tests, a validator run, and CI addressed, and returns `pull-request`

#### Scenario: Decline a bug needing a decision

- **WHEN** the issue admits several viable solutions that change behavior differently
- **THEN** the workflow returns `needs-input` naming the options and what it needs decided
- **AND** it pushes no branch and opens no PR

#### Scenario: Fail CI after the bounded loop

- **WHEN** CI remains red after the workflow's fix cycles
- **THEN** the workflow returns `failed` with the failing checks and leaves the PR open for a human

### Requirement: Invoke the versioned fix workflow (workflow-side portion)

The workflow contract SHALL be versioned; the factory SHALL refuse to launch when the workflow at the recorded Runner commit does not declare a compatible contract version and SHALL report this as a readiness problem. The workflow SHALL return exactly one structured outcome: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. The outcome SHALL be written to `fix-outcome.json` in the attempt's artifact directory and SHALL declare its contract version; absence of a structured outcome SHALL be treated as a technical failure.

## Test Plan

The test plan records this as a cross-repository obligation tested in `agent-runner` under that repository's conventions, not as a numbered `INT-*` or `E2E-*` item. Required automated coverage in `agent-runner`:

- Workflow validation for `core/factory-fix-v1.0.yaml` and the modified `core/finalize-pr-v1.0.yaml` through the repository's existing workflow-validation tests.
- A fixture-issue run with `--until triage` for both a fixable case and a declined case, using the repository's existing controlled-agent or recorded-response mechanism (no live model calls in the suite).
- Outcome-file production for the `needs-input` path from record-triage, and for the `pull-request` and `failed` paths from record-outcome with controlled sub-workflow results; assert the file matches the contract above and that no outcome is written twice.
- `finalize-pr` with `ci_fix_cycles` defaulting to 3 and honoring 1.

## Done When

- `core/factory-fix-v1.0.yaml` exists on `agent-runner` `main` with `# factory-contract: factory-fix/1` as its first line, the step structure above, and `--until triage` usable for prompt tuning.
- `core/finalize-pr-v1.0.yaml` accepts `ci_fix_cycles` (default 3) and the fix workflow passes 1.
- Every exit path after triage writes exactly one `/artifacts/fix-outcome.json` conforming to the contract; the `pull-request` path has pushed the branch and marked the PR ready with `Refs #<n>`, the claim marker, triage summary, and validator result in the body; the `needs-input` path has pushed nothing.
- The agent-runner test suite passes with the new validation, triage, and outcome tests, and the change is merged to `main` so the factory can resolve it from the configured Runner branch.
