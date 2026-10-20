## Why

Agent Evals is moving the `and-scene` suite to start from definition artifacts and run task planning before implementation (Codagent-AI/agent-evals#87, Agent Evals change `start-from-definition`). Planning's task review calls a fresh `crosscheck` agent, so the suite's `run.sh` will require a fourth role profile, `--crosscheck-cli/-model/-effort`. The factory today accepts and passes only lead, implementor, and tester. Eval admission fetches agent-evals `main`, so once that change merges, every newly admitted eval fails unless the factory passes crosscheck.

## What Changes

- **Eval intake:**
  - Accept a `crosscheck` key in the `eval` block, as a complete `cli:model:effort` triple like the other roles.
  - Add `crosscheck = "codex:gpt-6.1-sol:high"` to `[eval.defaults]` in `config/codagent.toml`. This matches the factory's feature defaults and the `crosscheck` agent in the maintainer's Agent Runner default profile.
  - Include crosscheck in the Fly Cursor check.
  - Freeze the effective crosscheck profile with the claim's other role profiles.
- **Suite invocation (`suites/and_scene`):**
  - Pass `--crosscheck-cli/-model/-effort` when the claim's frozen settings include crosscheck. Claims frozen without it keep their three-role invocation.
  - Include crosscheck in readiness and in the Fly launcher's credential-mount compatibility check, so the widest mount shape stays covered.
  - Hold readiness with an actionable message when a frozen crosscheck profile meets a retained harness that does not accept `--crosscheck-cli`.
- **Role handling elsewhere:** extend every role loop that serves eval execution to cover crosscheck.
- **Remove workflow knowledge:** drop the readiness check for `workflows/core/implement-change-v1.0.yaml` (`suites/and_scene/__init__.py:255-256`). The factory has no knowledge of Runner workflows; the suite's preflight owns workflow selection and verification, and it fails before any model call.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `factory-eval-intake`: the `crosscheck` eval key, its configured default, and the Fly Cursor check.
- `factory-eval-execution`: pass the frozen crosscheck profile to the suite, add it to Fly compatibility, keep claims frozen before crosscheck on three roles, and hold readiness when the harness cannot accept it.

## Out of Scope

- Any change to fix, feature, or task work kinds, which already have their own role sets.
- Choosing which eval role profiles are best.
- The `and-scene-define` suite.

## Impact

- **Order:** deploy immediately after Agent Evals `start-from-definition` merges, and admit no eval in between. That change requires Agent Runner change `plan-and-implement-change` on Runner `main` first.
- **Code:**
  - `src/agent_factory/suites/and_scene/__init__.py`: `_roles`, `plan`, and the Fly compatibility dry run.
  - Eval intake parsing and validation.
  - Role loops in `src/agent_factory/operations.py:147`, `fly/backend.py:131-136`, `work_kinds/eval/__init__.py`, and `work_kinds/eval/handler.py:107`.
  - The role-order comment in `fly/launcher.py:80`.
  - `config/codagent.toml`.
- **Tests and docs:** the and-scene adapter tests and golden eval-input fixtures (`tests/fixtures/eval_inputs_golden/`), plus the eval request documentation.
