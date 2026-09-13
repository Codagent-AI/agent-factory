# Handoff: after pickup-and-fix-bugs

Written 2026-09-12 at the end of the review-and-refine step, for whoever plans the next change. It records what shipped, what is not yet proven, what Paul observed during review that he does not want, and what the next change should be about. Where this document says a behaviour is undesired without saying what the right behaviour is, that is deliberate: Paul will decide it when planning the next change.

## Headline for the next change: run on the Mac without Docker

Docker on the mini will not work well until an external drive arrives. The host volume is 228 GiB; each sandbox image build takes several gigabytes, Docker's disk file only shrinks after a manual trim, and Docker Desktop crashed once during this change when the volume filled. Paul wants the factory to start running before the drive exists.

The next change should add a setting that lets the factory run work directly on the Mac, with no container. Nothing about that design is decided. Facts the design has to contend with:

- Both work kinds assume Docker today. Evals launch the and-scene suite through the Runner sandbox script, and fixes launch the companion Runner workflow the same way. The supervisor discovers the container to observe progress and records the image tag on the run. Cleanup removes per-run images at Done for both kinds.
- The sandbox currently provides: a fresh filesystem per attempt, a curated environment where only the fix credential enters and the App token never does, model authentication mounted from the operator's own CLI logins, a per-run HOME so agent sessions cannot leak between attempts, and a writable target clone with a read-only Skills mount. A host mode needs to say which of these it keeps, which it drops, and which it replaces.
- Readiness and doctor are Docker-shaped: the Docker check, the `fix docker memory` headroom line, `limits.memory_reservation_gib`, and the launcher-flag checks on `sandbox-run.sh` all assume a container. The disk floor `limits.minimum_free_gib` reads host free space and is currently set to 20 GiB in production.
- The container start-up script writes the Runner settings, git identity, askpass helper, and profile config into the attempt's HOME and clone. A host mode needs equivalents that do not touch Paul's own home directory or CLI configuration.
- The fix contract (`factory-fix/1`, `fix-outcome.json`) and the companion workflow do not themselves depend on Docker. The image tag hint and container discovery do.

## What shipped in pickup-and-fix-bugs

- Bug issues are routed to the factory and admitted as a second work kind with its own execution slot, concurrent with evals.
- Admission requires the issue author to be a repo writer or above. Branches, not commit pins, are resolved at admission and frozen on the claim.
- Fixes run from a mirror of the target repo and per-attempt clones of target, Runner, and Skills, in the Docker sandbox, via the companion Runner workflow `core/factory-fix-v1.0.yaml`.
- Side effects are reconciled before every attempt: an existing open factory PR settles the claim instead of launching a duplicate.
- A declined bug stays in Running with the `needs-input` label and is re-admitted by a newer writer comment or a drag back to Ready.
- After a fix PR merges, the factory fast-forwards the operator's working clone, closes the issue, and moves the card to Done. Cleanup at Done removes clones and per-run images.
- Doctor gained a fix section: launcher, credential, workflow contract, mirrors, Docker memory, and token identity checks.
- Branch `fix-bugs`, draft PR https://github.com/Codagent-AI/agent-factory/pull/5, head `3000aef` at the time of writing.

## What is verified and what is not

- Automated suite on the head: 282 passed, 2 skipped; Ruff and Pyright clean.
- Real-Docker launch test (E2E-004): two concurrent sandbox launches from factory-prepared clones, with the credential present and the planted default secrets absent, and image removal at cleanup. Passed on 2026-09-12 with a model-free stand-in workflow and a placeholder token.
- Never exercised: a real push and PR from inside the sandbox with a real credential (AT-002), the post-merge sync and cleanup journey against a real PR (AT-004), live routing from a caller repository (AT-001), and the direct-push rejection with real rulesets (AT-005). Acceptance status for this change is still `ACCEPTANCE_FAILED` for the deployment reasons below, not for a known code defect.
- The first real Bug fed to the factory will be the first end-to-end proof.

## Deployment steps still outstanding (Paul)

1. Merge the companion workflow: https://github.com/Codagent-AI/agent-runner/pull/89.
2. Create the fix credential. Plan: a fine-grained personal access token from the `codagent` machine-user account, member of the org with write on the target repos but not an owner, scoped to Contents, Pull requests, and Issues. Write it to a private file with exactly one line `GH_TOKEN=<token>` and point `credentials.fix_environment` at it. Doctor checks the shape, that it differs from the App token, and its identity and access.
3. Add rulesets on the target repos that block direct pushes to main so the token can only land work through a PR.
4. Replace the production shared config. Production points at `/Users/paul/codagent/agent-factory/config/codagent.toml`, which is the old file: it has no `[fix]` section and still uses `eval.harness_sha`. This change's `config/codagent.toml` has the `[fix]` section and `eval.harness_ref`. Doctor fails on the old harness key but is silent about a missing `[fix]` section.
5. Configure `[repositories.working_clones]` in the local config for each fix target so the merge sync can fast-forward.
6. Bump the caller workflow pins to the published routing revision.
7. Rerun acceptance for AT-001 through AT-005.

## Observed during review and not wanted

These are recorded as undesired. The correct behaviour is not specified here.

- `agent-factory status` lists every claim ever saved, including superseded and long-settled ones, so a fresh install with history reads as a wall of old claims. Paul does not want the whole history in status. What status should show instead is his call.
- Doctor prints a repair action on lines that passed. The three repository lines and the suite entry point line say OK and then print "Clone or repair..." or "Install the pinned...". The `fix sandbox launch` OK line prints an empty action.
- Evidence under `<storage_root>/artifacts/` is never pruned. It is already about 2 GB from past evals and is the only unbounded growth on the volume.
- The disk floor reads host free space, which stays low after image removal until Docker's disk file is trimmed, so the factory refuses admissions that would actually fit. The Playwright base image is re-pulled by every Runner image build, so removing it by hand does not save space for long.
- `docs/github-setup.md` still describes the harness setting as an immutable SHA pin. It is a branch now.

## Ideas raised but not decided

- A fix-only GitHub App instead of a machine-user token would give app-style permission control and a distinct bot identity, but installation tokens expire after an hour and a fix attempt can run longer. It needs a refresh path that does not put the App key in the sandbox.

## Where the evidence lives

- Run output directory: `/Users/paul/.agent-runner/projects/-Users-paul-codagent-agent-factory-fix-bugs/runs/change-2026-09-12T00-08-45-132917Z/output/`.
- Read `acceptance-handoff.md` (use the latest sections), `acceptance-flow-evidence.md`, `acceptance-findings.md`, and `acceptance-assumptions.md` there. Historical sections are retained; the resolve-assumptions outcome at the end of the handoff is current.
- Approved requirements for this change: `openspec/changes/pickup-and-fix-bugs/specs/`.
