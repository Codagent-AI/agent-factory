# Handoff: preserve evaluation sessions across recovery containers

Implement the remaining agent-evals integration fix below, then return it for independent review by the agent working on Agent Factory. Follow the repository's AGENTS.md and use test-driven development. This is a focused recovery fix, not another evaluator or a redesign of Factory.

## Objective

An interrupted and-scene repetition must be able to resume its existing Agent Runner workflow and CLI-agent sessions in a replacement Docker container. Reusing the suite checkpoint alone is insufficient if the agent's actual session data disappeared with the original container.

## Current state and evidence

- Agent Factory repo: `/Users/paul/codagent/agent-factory`.
- Factory branch: `iteration-1`; commit `3d64da4134897c7afe4ab97108fb521e827391a1`.
- Factory PR: https://github.com/Codagent-AI/agent-factory/pull/3 — open, draft, base `main`.
- Factory now fixes your review's container-discovery, Claude-wait timer, and repetition-reporting defects. Full Validator passed; 112 automated tests passed, plus a separate real-Docker discovery/background-container/cancellation test. These are regression results, not a successful live acceptance run.
- Factory's deployed harness pin remains `01b5a0ef5ee600a96a11737f134df05fa30cd887`, published on `factory/linked-worktree-metadata` in agent-evals. The separately reviewed agent-evals `dev` revision was `2b1f52a`; do not assume the pinned revision contains that newer hardening.
- Live request: https://github.com/Codagent-AI/agent-evals/issues/7. Its initial attempt hit a Factory inactivity bug, now fixed. Its one normal recovery then failed with `thread/resume failed: no rollout found for thread id ...`.
- Claim ID: `eb4de363-eb30-4a3c-8968-abaf105ffaaf`.
- Retained artifacts: `/Users/paul/.agent-factory/artifacts/eb4de363-eb30-4a3c-8968-abaf105ffaaf-rep-1/`.
- Retained pinned worktrees: `/Users/paul/.agent-factory/worktrees/eb4de363-eb30-4a3c-8968-abaf105ffaaf/{runner,skills,evals}`.
- Working diagnosis: the pinned sandbox uses ephemeral `HOME=/workspace/home`; artifacts/checkpoints survive, while authentication bootstrap does not restore the missing Codex rollout. Verify the actual Runner-managed CLI homes and session locations before choosing the fix.

## Required behavior and boundaries

1. Preserve the private per-evaluation session state needed for genuine continuation across replacement containers, with stable paths where the CLI/Runner require them. Cover the supported role profiles; do not share mutable sessions between evaluations.
2. Resume the same workflow/session from its existing checkpoint. Do not silently launch a fresh workflow when a recorded rollout is missing. Preserve the current bounded recovery policy and finished work.
3. Keep authentication forwarding separate from retained session evidence. Do not copy the host's entire home or credential stores into evaluation artifacts, publication snapshots, logs, or Git. Private runtime state must remain outside the suite's curated published evidence.
4. Preserve existing wrapper arguments, source revision pinning, linked-worktree Git-metadata support, skip-validator behavior, and ordinary standalone suite execution. Factory should continue to use the supported wrapper interface.
5. Keep generic sandbox infrastructure in Agent Runner, as required by agent-evals' repository rules. Prefer existing supported launcher capabilities. If the correct fix requires a Runner change, identify that dependency rather than duplicating launcher infrastructure inside the suite.

The exact storage layout is not predetermined. A private per-evaluation runtime directory is a candidate approach; establish which files and paths actually need to survive before deciding. Do not mount an entire persistent HOME merely on assumption.

## Implementation and verification

- Work in an isolated checkout/worktree. Do not mutate existing checkouts that may be live bind mounts, or reuse/delete the retained failed acceptance artifacts.
- First add a failing regression showing the session-state loss across container replacement. Then implement the smallest fix and show it passing.
- Add practical model-free Docker coverage: container A writes representative agent session/rollout data through the real selected mount setup; remove A; container B launches in recovery mode and can access that same state at the expected paths. Verify a different evaluation cannot see or overwrite it, and runtime files are excluded from curated publication.
- Also test the actual resume wiring/checkpoint identity. A marker-file mount test alone does not prove that Runner and the CLI use the retained location. Clearly distinguish controlled filesystem/wiring evidence from a real paid CLI continuation.
- Run targeted tests and the repository's required `npm run check`; follow its commit/validation conventions.
- Return a reviewable commit or draft PR and a published candidate harness SHA that retains both the linked-worktree support from `01b5a0e` and the desired current suite hardening. Explain the branch ancestry and any additional Runner dependency. Do not update Factory's pin or merge Factory's PR yourself.

## Stop boundaries and return report

Do not start a paid evaluation, unpause Factory, reset the consumed retry budget, alter issue #7, or mark acceptance complete. Another live repetition requires separate authorization after review. Factory-side review, pin/config updates, and independent live acceptance will be coordinated by the Factory agent.

Return:
- Root cause and the exact session paths/data now preserved.
- Changed repositories/files, commit SHA(s), draft PR URL(s), and candidate harness SHA.
- Red/green regression evidence, real-Docker evidence, and full check results.
- Isolation, permissions, credential handling, and publication-exclusion behavior.
- Any limitation or unresolved decision, especially whether real CLI continuation is still unproven.

## Relevant code and durable context

- `/Users/paul/codagent/agent-evals/evals/agent-runner/and-scene/run.sh`
- `/Users/paul/codagent/agent-evals/evals/agent-runner/and-scene/controller.mjs`
- `/Users/paul/codagent/agent-evals/evals/agent-runner/and-scene/lib/checkpoint.mjs`
- `/Users/paul/codagent/agent-evals/evals/agent-runner/and-scene/lib/runner-state.mjs`
- `/Users/paul/codagent/agent-evals/evals/agent-runner/and-scene/lib/result.mjs` and `lib/publication.mjs`
- `/Users/paul/codagent/agent-runner/scripts/sandbox-run.sh` and the pinned Runner checkout's launcher/bootstrap code.
- Approved Factory requirements: `/Users/paul/codagent/agent-factory/openspec/changes/iteration-1/specs/`.
- Durable evidence directory: `/Users/paul/.agent-runner/projects/-Users-paul-codagent-agent-factory/runs/change-2026-09-08T23-55-23-195379Z/output/`.
- Within that directory, read `acceptance-findings.md` (F-004 and lead disposition), `acceptance-handoff.md`, `acceptance-assumptions.md`, and `integration-review-remediation.md`. Historical sections are retained: use the latest lead assessment. Acceptance is still failed; do not treat prior partial evidence as a full pass.
- Avoid dumping raw historical plan/transcript records: an earlier credential exposure was remediated locally, and source credentials must never be reproduced in output.
