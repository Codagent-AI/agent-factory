Feature review attention previously described the branch before finalization pushed it and settled
CI. Classification and its verification now follow finalization, so they include CI-fix commits.
Classification failure preserves rejected evidence, skips annotation, and records a verified failed
outcome with the PR reference and CI status, without unverified tier counts. A failed evidence
rename leaves the original untouched and is named in the reason.

Review first:

- 🟠 CI does not run the Runner-semantics tests (INT-002 and the Runner-validated catalog test).
  The recorded `FEATURE_REQUIRE_RUNNER=1` acceptance run is their proof: 19 tests passed against
  the installed Runner. Tests: static wiring and script-composition tests run in CI; the real
  classify session and inline repair are exercised locally through a stub `claude`.

Acceptance output: [acceptance-run.txt](acceptance-run.txt).
