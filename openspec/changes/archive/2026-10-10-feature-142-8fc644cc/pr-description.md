A feature run that ends because a check declares `REPAIR_BLOCKED` now records
`needs-input` before the host wrapper exits. The issue receives the check's exact
path and explanation, and a writer comment resumes from published progress or
starts a fresh definition. Explained blocks no longer consume the recovery retry.

The shared recorder serves both the host wrapper and the existing archive hook.
It follows the terminal audit failure chain, rejects stale or empty declarations,
and computes progress from `attempt-start.json` and published first-parent
checkpoints. For an unpublished continuation it pushes only the prior head.
Existing archive stale rules and verify/finalize failed outcomes are preserved.

Orange items:

- The recorder depends on the installed Runner's audit format. Format drift leaves
  the attempt on the existing recovery path; portable and installed-Runner tests
  exercise this boundary.
- A first-attempt definition block restarts definition. Untracked drafts and
  unpushed commits may be lost during clone slimming; partial work is not pushed.
- The recorder publishes prior work when a continuation's own branch was never
  pushed. A rejected push leaves the run on the recovery path.
- Definition checks do not currently declare repair; the acceptance journey
  simulates a future repair-bearing check with a fake Runner audit.
- Installed-Runner runtime tests emulate Claude's headless stream protocol with a
  stub CLI and skip only when Runner is absent. The definition tests also stub
  OpenSpec validation and Validator's plan-commit `skip` command.

Suggested follow-ups:

- Decide how verify and finalize repair blocks should differ from failed validation
  and CI (D3).
- Decide repair-block and resume behavior for fix workflows (D4).
- Preserve untracked drafts and unpushed commits before clone slimming (D10).

Tests: unit audit/contract tests, real git and wrapper integration tests, the
supervisor/claim/writer-comment journey, existing archive regressions, and runtime
tests using the installed Runner with a local stub agent CLI. Formatting, lint,
Pyright, and `uv build` pass. The broader pytest suite excludes the three existing
`test_real_validator_escapes_trusted_claim_clone` cases because they invoke installed
Validator reviews. Agent Validator is deferred to the later workflow step as requested.
