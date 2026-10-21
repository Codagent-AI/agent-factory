## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration and end-to-end obligations, the acceptance testing envelope, and exceptional human-only
obligations.

Most of the risk is in pure logic: hunk math, classification, parsing, rendering, and insertion.
Unit tests in `tests/unit/test_count_loc.py` cover it, following the spec scenarios and the design's
Testing Strategy. The integration obligations below cover what unit tests cannot prove:

- real `git` behavior (merge bases, fetches, published versus local heads);
- the `gh` boundary that reads and PATCHes descriptions;
- the wiring of the script into the fix, feature, and review workflows and the staged catalog.

No automated end-to-end obligation is added. A real factory journey (Runner plus model sessions plus
GitHub) cannot be run deterministically in CI. The existing `tests/e2e` cycle tests stub the
workflow itself, so they would not exercise this change. Running the packaged scripts against real
repositories and a `gh` stub reaches the same public surface, which is the PR description the
scripts publish.

## Integration Tests

### INT-001: Count measures the published head against its merge base with the base branch
- Covers: factory-pull-request-size "Count the code and test lines a pull request changes"
  (target-branch merge, local commits that were not pushed, new and deleted files, binary file).
- Boundary: `count-loc.py` running real `git fetch`, `git merge-base`, and `git diff` against a bare
  `origin` repository.
- Setup: a temporary bare `origin` with `main` and a PR branch.
  - The PR branch adds a code file, deletes a code file, modifies a test file, adds a binary file
    and an `openspec/` file, and merges a later `main` commit that changes 400 code lines.
  - A clone has one extra local commit that is not pushed.
- Action:
  - Run `count-loc.py section --base-ref main --head <pushed head SHA>` from the clone.
  - Repeat with the clone's repository config set to `diff.interHunkContext=5`,
    `diff.algorithm=patience`, and a diff driver whose `diff.<driver>.algorithm` is set.
  - Repeat with an uncommitted `.gitattributes`, and with `.git/info/attributes`, marking the
    modified code file `-diff`.
  - Repeat on a variant branch that commits `sample.py -diff` for a text code file.
- Assertions:
  - every run produces counts identical to the default run (the text file is counted, and a hunk
    split by unchanged lines stays two hunks);
  - a file containing NUL bytes is not counted in any run;
  - the table counts only the PR's own code and test changes, with the exact expected numbers;
  - the merged `main` lines, the unpushed commit, the binary file, and the `openspec/` file are not
    counted;
  - the section names `<merge base[:12]>..<pushed head[:12]>`.
- Execution: `tests/integration/test_count_loc.py`, run by `uv run pytest` (validator `test`
  check, CI).

### INT-002: The head commit is missing locally, or the base cannot be fetched
- Covers: "Count the code and test lines" (head fetched by SHA); "Report an unavailable count
  without blocking the pull request".
- Boundary: `count-loc.py` and `git fetch` against a bare `origin`.
- Setup:
  - (a) a clone that does not yet have the pushed head commit;
  - (b) a base ref name that does not exist on `origin`.
- Action: run `section` mode in each case.
- Assertions:
  - (a) the head is fetched and counted;
  - (b) the output has the markers and `Lines-of-code count unavailable:` followed by a one-line
    reason, and the exit status is 0.
- Execution: `tests/integration/test_count_loc.py`.

### INT-003: `refresh-pr` reads, inserts or replaces, and PATCHes the live description
- Covers: factory-pull-request-size "Render the lines-of-code section" (refresh after new commits,
  section removed by a rewrite, exactly one section); factory-fix-reporting "Report the
  lines-of-code count in the fix pull request description".
- Boundary: `count-loc.py refresh-pr`, with both stdin JSON and command-line arguments, against
  real git and a `gh` stub on `PATH`. The stub serves `pr list`, `api repos/{owner}/{repo}/pulls/N`
  (body, `base.ref`, `head.sha`), and records `PATCH` calls.
- Setup: the INT-001 repositories. The stub serves the following bodies in turn:
  - a fix description with the `Refs #N` and claim-marker header and no section;
  - a body that already has a stale section;
  - a body with two sections;
  - a body identical to what a refresh would produce;
  - no open PR;
  - a PATCH that fails three times.
- Action: run `refresh-pr` for each case.
- Assertions:
  - the section is inserted below the claim marker, or replaced in place;
  - duplicates collapse to one, and the text outside the markers is byte-identical;
  - an unchanged body produces no PATCH;
  - no open PR gives exit 0 and no PATCH;
  - repeated PATCH failure gives exit 1 after three attempts;
  - `pr-body-loc.md` is written to the artifact directory.
- Execution: `tests/integration/test_count_loc.py`.

### INT-004: Feature annotation includes the section
- Covers: factory-feature-reporting "Report the lines-of-code count in the feature pull request
  description".
- Boundary: `annotate-pr.py` running `count-loc.py` as a subprocess, with real git and the existing
  `gh` stub pattern in `tests/integration/test_feature_workflow_scripts.py`, extended to return
  `baseRefName`.
- Setup: the existing annotate-pr fixtures, plus a bare `origin` and a feature branch whose only
  changes are OpenSpec and Markdown files, and one whose changes include code. The change's
  `decisions.md` quotes the section markers inline and inside a fenced block. Use this change's own
  `decisions.md` as the fixture.
- Action: run `annotate-pr.sh`.
- Assertions:
  - the published body has exactly one section, below the change-summary links line;
  - the excluded-only branch shows zeros in both rows;
  - the red and orange counts and items are unchanged;
  - with no `baseRefName`, the section shows the unavailable form and the description is still
    published;
  - the embedded decision log's marker examples are byte-identical, and there is exactly one live
    section.
- Execution: `tests/integration/test_feature_workflow_scripts.py`.

### INT-005: Review rounds refresh the section by kind
- Covers: factory-review-execution "Refresh the lines-of-code section after a review round".
- Boundary: `review-description.sh` (`save` then `restore`) running `count-loc.py`, with real git
  and a `gh` stub.
- Setup: review files of kind `fix`, `feature`, and `task`.
  - The feature case includes a round that merged a target commit.
  - Another case leaves local commits that are not pushed (published head unchanged).
  - Two more feature cases have a `save` that failed (stub error) or saved an empty body, followed
    by a working `gh` at `restore`.
  - The feature description embeds a decision log with inline and fenced marker examples.
- Action: run `save`, change the stubbed body as finalization would (drop the section), then run
  `restore`.
- Assertions:
  - fix: one section at the published head, and the rest of the current body is unchanged;
  - feature: the restored body contains the round section and one lines-of-code section, without
    the merged target lines;
  - task: no section is added and no PATCH is made;
  - an unpushed round counts the published head only;
  - a failed or empty save still yields one section for the current published head, and the rest
    of the current description is unchanged;
  - the embedded marker examples survive the restore unchanged;
  - a fix refresh failure, or a feature refresh failure, writes `description-restore-failed`.
- Execution: `tests/integration/test_count_loc.py` or the existing review-contract tests
  (`tests/integration/test_review_contract.py`).

### INT-006: Workflow wiring and the staged catalog
- Covers: the fix, feature, and review insertion points (proposal-review PR-002).
- Boundary: the packaged YAML, `kinds.py`, and `launch.py` staging into a catalog directory.
- Setup: `launch.stage_workflow_into` a temporary catalog for the fix, feature, and review
  workflows.
- Action: inspect the parsed `factory-fix-v1.0.yaml`, and run the staged `count-loc.py` (`section`
  mode) from the catalog against an INT-001 repository.
- Assertions:
  - `refresh-loc` directly follows `annotate-pr` inside `implement`, has `continue_on_failure: true`,
    and is a bare-name `script:` with `mode: refresh-pr`;
  - `count-loc.py` is in `FIX.staged_files`, `FEATURE_STAGED_FILES`, `REVIEW_WORKFLOW_SCRIPTS`, and
    `STAGED_FILES`, and is staged as executable;
  - the staged copy runs successfully;
  - when a suitable Runner is installed, the existing `-validate` catalog test passes with the
    updated fix workflow.
- Execution: `tests/integration/test_fix_workflow.py` and
  `tests/integration/test_feature_workflow_catalog.py`.

## End-to-End Tests

None. A full factory run needs model sessions and live GitHub. The INT-003 to INT-006 obligations
exercise the published description through the packaged scripts and staged catalog.

## Acceptance Testing Envelope

- Environments and sandboxes:
  - temporary local git repositories, with bare `origin` remotes, under the session's temporary
    directory;
  - scratch clones of public Codagent repositories (for example `Codagent-AI/agent-runner`) under a
    temporary directory;
  - the change's own worktree, including its venv (`uv run`).
- Credentials and secrets: the operator's existing `gh` login, for read-only GitHub calls only. No
  other credentials are needed.
- Authorized effects:
  - read-only `gh api`/`gh pr view` calls and `git fetch` against Codagent repositories. These are
    free and need no cleanup beyond removing scratch directories.
  - Running `count-loc.py section` against agent-runner PRs #232, #242, and #239 to compare with
    the issue's reference results (#232 code 55/17/10, tests 306/32/1; #242 code 19/4/1, tests
    130/0/0; #239 no code) is authorized and encouraged.
- Off limits:
  - editing any real pull request's description or title (no `refresh-pr` or `annotate-pr`
    against real PRs; point `gh` at a stub for those modes);
  - pushing to any real repository;
  - the live service (`~/.agent-factory` releases, config, store, LaunchAgent), `scripts/deploy.sh`,
    Paul's checkout, and the factory board.
- Permitted substitutes: a `gh` stub on `PATH` for every write path, and for any read path whose
  real target is unavailable.
- Known risk areas:
  - Git config and attributes in the checkout (inter-hunk context, diff algorithm, `-diff`) changing
    counts;
  - literal marker examples in embedded decision logs;
  - a failed best-effort description save before a review round;
  - model-written fix descriptions that move or mangle markers (dangling start marker, duplicate
    blocks);
  - CRLF bodies;
  - content lines that look like `+++` or `---` headers;
  - quoted paths with spaces or tabs;
  - target-branch merges into feature branches;
  - failed finalization or rounds that leave local commits that were never pushed;
  - `annotate-pr.py` stubs that lack `baseRefName`.

  Accepted limitations: generated or vendored code counts as code, and a submodule pointer change
  counts as one modified line.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| pull-request-size: Count the code and test lines a pull request changes | INT-001, INT-002 | — | — |
| pull-request-size: Render the lines-of-code section | INT-003 | — | — |
| pull-request-size: Report an unavailable count without blocking the pull request | INT-002, INT-004 | — | — |
| feature-reporting: Report the lines-of-code count in the feature description | INT-004 | — | — |
| fix-reporting: Report the lines-of-code count in the fix description | INT-003, INT-006 | — | — |
| review-execution: Refresh the lines-of-code section after a review round | INT-005 | — | — |
| Staged catalog and workflow wiring | INT-006 | — | — |
