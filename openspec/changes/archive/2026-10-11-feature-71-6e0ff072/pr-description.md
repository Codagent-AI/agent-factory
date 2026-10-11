Feature PR descriptions now show the archived proposal expanded after the artifact links, with ATX headings nested under the change summary. Tokenized regions keep quoted report lines and markers out of commit marking, and annotation and review rounds omit only whole proposal sections when the final description exceeds the conservative 65,536-byte limit.

The shared helper is staged for feature and review workflows. Missing or unreadable proposals leave a notice and the artifact links. Operations documentation records the containment required before rollback.

- 🟡 Accepted limitations: closing keywords quoted in a proposal remain verbatim and would link that issue; unbalanced HTML can affect rendering; setext headings are not demoted; rolling back past this change requires the documented containment for open feature PRs and review claims.

Validation: all 1,599 selected tests pass in 137.34 seconds; unit tests and INT-001 through INT-006 exercise the real staged scripts with GitHub stubs. Ruff formatting and linting, Pyright, and `uv build` pass. All 20 existing archived proposals render without text loss, and the shared helper is included in the wheel and source distribution.

End-to-end CLI test budgets now allow 60 seconds for whole invocations that execute several separately bounded probes. Production deadlines and behavioral assertions are unchanged. Agent Validator and the three tests that invoke the real Validator are intentionally deferred to the next workflow step. See `tasks.md` for the verification details.
