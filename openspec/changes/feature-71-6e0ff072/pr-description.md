Feature PR descriptions now show the archived proposal expanded after the artifact links, with ATX headings nested under the change summary. Tokenized regions keep quoted report lines and markers out of commit marking, and annotation and review rounds omit only whole proposal sections when the final description exceeds the conservative 65,536-byte limit.

The shared helper is staged for feature and review workflows. Missing or unreadable proposals leave a notice and the artifact links. Operations documentation records the containment required before rollback.

- 🟡 Accepted limitations: closing keywords quoted in a proposal remain verbatim and would link that issue; unbalanced HTML can affect rendering; setext headings are not demoted; rolling back past this change requires the documented containment for open feature PRs and review claims.

Validation: all 168 focused tests pass; unit tests and INT-001 through INT-006 exercise the real staged scripts with GitHub stubs. Ruff formatting and linting, Pyright, and `uv build` pass. All 20 existing archived proposals render without text loss, and the shared helper is included in the wheel and source distribution.

The broad suite is blocked by end-to-end failures, mostly CLI subprocess timeouts; the first timeout also reproduces on `origin/main`. Agent Validator and the three tests that invoke the real Validator are intentionally deferred to the next workflow step. See `tasks.md` for the run counts and verification limit.
