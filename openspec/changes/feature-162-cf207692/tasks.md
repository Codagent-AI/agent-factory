- [ ] Report a deterministic lines-of-code count in every factory fix and feature pull request description

## Task: Lines-of-code section in factory PR descriptions (#162)

Implement the whole change described in these files, in the change directory
`openspec/changes/feature-162-cf207692/`:

- `proposal.md`;
- the delta specs under `specs/`:
  - `factory-pull-request-size` (new): every ADDED requirement and scenario;
  - `factory-feature-reporting`: ADDED "Report the lines-of-code count in the feature pull request
    description";
  - `factory-fix-reporting`: ADDED "Report the lines-of-code count in the fix pull request
    description";
  - `factory-review-execution`: ADDED "Refresh the lines-of-code section after a review round";
- `design.md`, which is authoritative for the script name, its modes, the diff command and
  environment, binary detection, the parser state machine, rendering, live-delimiter rules, upsert
  anchors, and the wiring of each workflow;
- `decisions.md`. Where entries differ, later entries override earlier ones. In particular:
  - proposal-review PR-001 and PR-002 (count the published PR head; register for staging);
  - approach-review AR-001 (fixed diff policy and content-based binary detection);
  - approach-review AR-002 (live delimiters only);
  - approach-review AR-003 (refresh even when the pre-round save failed);
- the automated obligations in `test-plan.md`: INT-001 to INT-006.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or anything under `/Users/paul/codagent/*`. Never run `scripts/deploy.sh`
or `launchctl`. Tests and any manual checks must never PATCH, push to, or edit a real GitHub pull
request. Use a `gh` stub on `PATH` for every write path.

### Scope

1. **`count-loc.py`** (new): `src/agent_factory/work_kinds/pull_request/workflow/count-loc.py`,
   with a `#!/usr/bin/env python3` shebang, standard library only (design "`count-loc.py`"):
   - **Patterns:** `EXCLUDE` and `TEST` module constants holding the spec's initial lists. These are
     the single place to tune them.
   - **Classification:** `classify` uses `fnmatch.fnmatchcase` on the full path and on the base
     name, and checks exclusion first.
   - **Counting:** `count(diff_text)` is a state-machine parser:
     - `---`/`+++` are headers only before a file's first `@@`;
     - C-quoted paths are unquoted;
     - the old path is used when the new path is `/dev/null`;
     - hunk math follows the spec, with an omitted count meaning 1;
     - `\ No newline` lines are ignored;
     - the patch is split on `\n` only.
   - **Binary detection by content:** read the full blob ids from `index` lines (`--full-index`).
     A file is binary when either non-zero blob has a NUL byte in its first 8000 bytes (read with
     `git cat-file blob`); binary files and mode-only changes contribute nothing.
   - **Measurement:** `measure(base_ref, head_sha)`:
     - fetch `+refs/heads/<base>:refs/remotes/origin/<base>`;
     - fetch the head SHA only if it is missing;
     - take the merge base;
     - run the exact diff command and environment in the design (`GIT_CONFIG_NOSYSTEM=1`,
       `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_ATTR_NOSYSTEM=1`, `-c core.attributesFile=/dev/null`,
       `-c core.quotePath=false`, `-U0 --inter-hunk-context=0 --diff-algorithm=myers
       --indent-heuristic --text --full-index --no-renames --no-color --no-ext-diff --no-textconv
       --src-prefix=a/ --dst-prefix=b/`).

     Local HEAD is never the head. Any failure returns an unavailable result with a one-line reason.
   - **PR state:** `pr_state(number=None)` reads `gh api repos/{owner}/{repo}/pulls/N` (`body`,
     `base.ref`, `head.sha`). Without a number, it finds the open PR for the current branch.
   - **Rendering:** `render` produces the exact section format in the design: markers, the
     `### Lines of code` heading, the table with both rows always present, `+N −M` (U+2212), the
     `Measured` line, and the exclusion note. The unavailable form is one escaped line.
   - **Upsert:** `upsert(body, section)` acts only on live delimiters: lines that are exactly a
     marker, outside backtick and tilde fences.
     - It replaces the first block and removes duplicates.
     - It cleans up a dangling start marker.
     - It inserts at the anchors in order: after the first non-empty line following
       `## Change summary`, then after the claim-marker line, then at the end.
     - It preserves CRLF and leaves all other text byte-for-byte unchanged.
   - **Modes:**
     - `section --base-ref R --head SHA`, which always exits 0;
     - `refresh-file BODY --number N`, which always exits 0, renders the unavailable form when the
       PR read fails, and edits in place;
     - `refresh-pr [--number N] [--artifact-dir D]`, also accepting a stdin JSON payload
       `{"mode": "refresh-pr", "artifact_dir": ...}`. It writes `D/pr-body-loc.md` and PATCHes
       through REST with `-F body=@file`, three attempts as in `annotate-pr.py`, only when the body
       changed. It exits 0 when there is no open PR, and 1 when the PR cannot be read or the PATCH
       fails.
2. **Feature annotation** (`annotate-pr.py`):
   - add `baseRefName` to the `gh pr list --json` fields;
   - after the change-summary links line, insert the output of `count-loc.py section --base-ref
     <baseRefName or ''> --head <headRefOid>`, surrounded by blank lines;
   - the review-attention tiers and counts must be unchanged.
3. **Fix workflow** (`factory-fix-v1.0.yaml`): add the `refresh-loc` step directly after
   `annotate-pr` inside `implement`. It is `script: count-loc.py` with `mode: refresh-pr` and
   `artifact_dir`, and `continue_on_failure: true`.
4. **Review rounds** (`review-description.sh`): dispatch by kind.
   - **task:** exit 0, unchanged.
   - **fix:** `save` does nothing, and `restore` runs `refresh-pr --number N --artifact-dir
     <artifacts>`.
   - **feature, with a non-empty saved body:** in `rebuilt()`, run `refresh-file <updated> --number
     N` after `mark-later-commits.py`.
   - **feature, with a missing or empty saved body:** run `refresh-pr` on the current description.

   Any refresh failure writes `description-restore-failed` and exits nonzero. Keep the header
   comment accurate.
5. **Staging:** add `count-loc.py` to `FIX.staged_files` and `FEATURE_STAGED_FILES` in `kinds.py`,
   and to `REVIEW_WORKFLOW_SCRIPTS` in `launch.py`.
6. **Documentation:** in `docs/operations.md`, near "Reviewing a factory fix PR", add a short note:
   - fix and feature PR descriptions carry a script-computed "Lines of code" section (code and
     tests, excluding OpenSpec, docs, and configuration), measured from the merge base with the PR's
     base branch to its published head and refreshed on each attempt and review round;
   - the patterns live in `count-loc.py`.
7. **Tests:**
   - unit tests in `tests/unit/test_count_loc.py`, loading the script with
     `importlib.util.spec_from_file_location`. Cover every spec scenario and the design's "Testing
     Strategy" unit list: hunk math, new, deleted, renamed, binary, and mode-only files,
     classification precedence, header-like content lines, quoted paths, the `\n`-only split, the
     issue's reference diffs, rendering (zeros and unavailable), and every upsert case including
     inline and fenced marker examples;
   - INT-001 to INT-006 at the locations `test-plan.md` gives;
   - update existing `annotate-pr.py` and review-description tests only as far as the new section
     requires (for example stubs that return `baseRefName`). All other existing tests must pass
     unchanged.

### Done when

- Every requirement and scenario in the four delta specs is implemented.
- A fix PR, a feature PR, and fix and feature review rounds that run their implementation step each
  leave exactly one live "Lines of code" section, counted at the PR's published head. Task PRs and
  task rounds gain no section.
- Counts exclude target-branch merges and unpushed commits. They are identical under conflicting
  Git config and attributes. A binary file is skipped by content.
- A PR that changes only excluded files shows both rows at zero.
- A count failure shows the unavailable note and never changes an attempt's or round's outcome.
- Literal marker examples in a description (inline or fenced) are preserved byte-for-byte.
- `count-loc.py` is staged for fix, feature, and review attempts and runs from a staged catalog.
- INT-001 to INT-006 pass, and the full suite passes under `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build` pass, and
  `agent-validator run` passes.
- No persisted data format, outcome file, or contract version changes. No changes are made outside
  this repository.
