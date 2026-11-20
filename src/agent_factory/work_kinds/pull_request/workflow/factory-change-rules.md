## Factory implementation constraints

These rules bind every implementation, audit, and validator-repair step of this change.

- Decide and record. Never stop to ask the user mid-run: settle each ordinary question yourself, and record any choice that could change behavior, scope, or integration in the decision record the workflow names.
- Do not use test-driven development and do not require a failing test first. Verify with automated tests and inspection only. Never operate the product by hand: manual testing happens once, in acceptance.
- Validator repairs fix the code. They never loosen or raise a test timeout, skip, delete, or weaken a test, or add, remove, upgrade, or redirect a dependency (git, URL, fork, tarball, registry, override, or resolution) to make a check pass.
- A check that already fails at the merge base with the target branch, or whose only remedy is one of the changes above, is not this change's to fix. Leave it failing and report it under the heading "Out-of-scope failures needing a human decision", stating what failed, why it is out of scope, and the remedy a human would need to approve so it can be filed as a separate issue.
