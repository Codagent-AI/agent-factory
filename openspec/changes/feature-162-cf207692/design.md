## Context

Factory pull request descriptions are written in three places, all of them scripts in
`src/agent_factory/work_kinds/pull_request/workflow/` that are staged into each attempt's Runner
catalog:

- **Feature:** `annotate-pr.py` (through `annotate-pr.sh`) builds the whole description after
  `finalize`. It writes `feature-pr-body.md` in the artifact directory and publishes it with a REST
  `PATCH`. It already reads the PR's `headRefOid` with `gh pr list --head <branch>`.
- **Fix:** Agent Runner's `builtin:core/finalize-pr` (a model) writes the description. The fix
  workflow's inline `annotate-pr` step then prepends `Refs #N` and the claim marker when the marker
  is missing. That step is the only factory-owned edit, and it is skipped when the marker is
  already present.
- **Review rounds:** `review-description.sh` saves the description before the round and restores it
  afterwards. It runs only when `push_round` is true, which is also the condition for the
  implementation step. Today it handles feature PRs only and exits for every other kind.

The factory stages workflow files from explicit lists, not by copying the whole directory:
`FEATURE_STAGED_FILES` and `FIX.staged_files` in `kinds.py`, and `REVIEW_WORKFLOW_SCRIPTS` in
`launch.py`, combined into `STAGED_FILES`. Fix attempts can run in the Docker sandbox, where the
catalog lives under `$HOME/.agent-runner/workflows` rather than in the clone. Fix workflow steps
therefore have to use `script:` steps, which the Runner resolves next to the workflow, not
`.agent-runner/workflows/...` paths. Workflow scripts may only assume `python3`, `git`, and `gh`.
A `script:` step passes its `script_inputs` as JSON on stdin (see `task-compliance-gate.py`).

## Goals / Non-Goals

**Goals:**
- One self-contained script that counts deterministically, holds the pattern lists, renders the
  section, and inserts or replaces it in a description.
- The count covers the published PR head and its merge base with the PR's base branch, never local
  work that was not pushed.
- Every fix and feature PR description carries exactly one current section, after every attempt
  and every review round that runs its implementation step.
- A count failure never changes an outcome or blocks the PR.

**Non-Goals:**
- Task PRs, risk classification and triage limits, per-target pattern configuration, and
  language-aware counting (see the proposal's Out of Scope).

## Approach

### `count-loc.py` (new, in `workflow/`)

A single Python 3 file with no third-party imports, in four layers:

1. **Patterns and classification.** Module constants `EXCLUDE` and `TEST` start with the spec's
   lists. They are the only place to tune them. `classify(path) -> "excluded" | "tests" | "code"`
   applies `fnmatch.fnmatchcase` to the full path and to the base name, and checks exclusion first.
   `fnmatchcase` is case-sensitive on every platform, so the result is the same on macOS and in the
   Linux sandbox.
2. **Diff parsing and counting (pure).** `count(diff_text) -> Counts` walks a `git diff -U0` patch
   as a state machine:
   - `diff --git ` starts a file and resets the path. It is unambiguous at the start of a line,
     because content lines always begin with `+`, `-`, a space, or `\`.
   - While in a file's header (before its first `@@`), `--- ` and `+++ ` set the old and new path.
     The new path is used unless it is `/dev/null`, in which case the old path is used. The `a/` or
     `b/` prefix is stripped, and C-quoted paths (`"a/x\ty"`) are unquoted. After the first `@@`,
     lines starting with `--- ` or `+++ ` are content, not headers. The issue's prototype misreads
     an added line whose text starts with `++ ` as a header, and the state machine avoids that.
   - Each `@@ -a[,old] +b[,new] @@` header adds to the file's kind, with a count of 1 when it is
     omitted: `mod = min(old, new)`, `added += new - mod`, `deleted += old - mod`,
     `modified += mod`. The path is recorded in the kind's file set. Hunk bodies are not otherwise
     read, and `\ No newline at end of file` lines are ignored.
   - Binary files and mode-only changes have no `@@` hunk, so they add nothing.

   `Counts` holds, per kind, `files` (the size of the path set) and `added`, `modified`, and
   `deleted`. The +/- values are derived when rendering: `+(added+modified)` and
   `−(deleted+modified)`.
3. **Git and GitHub layer.** `measure(base_ref, head_sha) -> Counts | Unavailable(reason)`:
   - `git fetch --no-tags origin +refs/heads/<base>:refs/remotes/origin/<base>`.
   - If `git cat-file -e <head>^{commit}` fails, `git fetch --no-tags origin <head>`. GitHub serves
     reachable SHAs, and the factory's branch is in the same repository.
   - `mb = git merge-base refs/remotes/origin/<base> <head>`.
   - The diff runs with a fixed policy, so settings in the checkout cannot change the counts
     (approach-review AR-001):
     - The environment sets `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, and
       `GIT_ATTR_NOSYSTEM=1`.
     - The command is `git -c core.quotePath=false -c core.attributesFile=/dev/null diff -U0
       --inter-hunk-context=0 --diff-algorithm=myers --indent-heuristic --text --full-index
       --no-renames --no-color --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ <mb>
       <head>`.
     - Every option that affects hunk shape is set on the command line. Command-line options beat
       repository config (`diff.interHunkContext`, `diff.algorithm`, `diff.indentHeuristic`) and a
       driver's `diff.<driver>.algorithm`; this was checked with git 2.50.
     - `--text` overrides `-diff`/`binary` attributes from any source: committed, uncommitted
       worktree, or `.git/info/attributes`. So attributes cannot hide a text file or change how it
       is diffed.
   - **Binary detection by content, not attributes.** For each file in the patch, the parser takes
     the old and new blob ids from the `index <old>..<new>` line (full ids, because of
     `--full-index`). It reads the first 8000 bytes of each non-zero blob with `git cat-file blob`.
     If either contains a NUL byte, which is Git's own default heuristic, the file is binary and is
     skipped. A committed `-diff` attribute therefore does not exclude a text file, and a binary
     file is excluded whatever its attributes say. A mode-only change has no `index` blobs and no
     hunks, so it contributes nothing.
   - The patch is read as bytes, decoded with `surrogateescape`, and split on `\n` only. Python's
     `splitlines` would also split on form feeds and other separators found in source or binary
     text.
   - Any failing command returns `Unavailable` with a one-line reason naming the command and its
     trimmed stderr. Local HEAD is never used as the head. Because `<head>` is GitHub's head SHA,
     unpushed local commits are excluded automatically.

   `pr_state(number=None)` reads `gh api repos/{owner}/{repo}/pulls/<n>` and returns `body`,
   `base.ref`, and `head.sha`. Without a number it first finds the open PR for the current branch
   (`gh pr list --head <branch> --state open --json number --limit 1`).
4. **Rendering and insertion (pure).**
   - `render(result, base_ref, mb, head) -> str` produces:

     ```
     <!-- agent-factory:loc -->
     ### Lines of code

     | kind | files | added | modified | deleted | +/- |
     |---|---|---|---|---|---|
     | code | 4 | 55 | 17 | 10 | +72 −27 |
     | tests | 3 | 306 | 32 | 1 | +338 −33 |

     Measured `<mb[:12]>..<head[:12]>` (merge base with `<base>` to the pull request head).
     OpenSpec specifications, other documentation, and configuration are excluded.
     <!-- agent-factory:loc-end -->
     ```

     An `Unavailable` result renders the same markers and heading followed by one line:
     `Lines-of-code count unavailable: <reason>.` The reason is collapsed to one line and
     HTML-escaped, so it cannot open a section or forge a marker.
   - **Live delimiters only** (AR-002). The scanner tracks fenced code blocks: a line opening with
     at least three backticks or tildes, which is closed by a matching fence. A line is a live
     delimiter only when, outside a fence and after trailing whitespace or `\r` is stripped, it
     consists of exactly `<!-- agent-factory:loc -->` or `<!-- agent-factory:loc-end -->`. Inline
     mentions (for example in the embedded `decisions.md`) and fenced examples are never matched,
     replaced, or removed.
   - `upsert(body, section) -> str` keeps the body's line ending (CRLF if the body has any CRLF).
     All of the following operates on live delimiters only:
     - Every complete marker block (start marker through the next end marker) is found. The first
       one is replaced by the section and the rest are removed.
     - A dangling start marker (no end marker after it) is removed together with the section's
       known lines that follow it: the `### Lines of code` heading, table rows (`|`), blank lines
       inside the block, and the `Measured`, `OpenSpec`, or `unavailable` line.
     - With no marker, the section is inserted at the first anchor that exists:
       1. after the first non-empty line following `## Change summary` (a feature description's
          artifact links line);
       2. after the claim-marker line (`<!-- agent-factory:claim:`), which is the fix description's
          header;
       3. at the end of the body.

       The section is surrounded by one blank line on each side.
     - Text outside the replaced or inserted block is left byte-for-byte unchanged.

**Command-line modes** (the script's `main`):

| Mode | Used by | Behavior | Exit |
|---|---|---|---|
| `section --base-ref R --head SHA` | `annotate-pr.py` | print the rendered section (or the unavailable form) | 0 |
| `refresh-file BODY --number N` | `review-description.sh` (feature) | read `base.ref` and `head.sha` with `pr_state(N)`, measure, upsert into BODY in place; a failed PR read renders the unavailable form | 0 |
| `refresh-pr [--number N] [--artifact-dir D]` or stdin JSON `{"mode": "refresh-pr", "artifact_dir": ...}` | fix workflow step, `review-description.sh` (fix) | read the PR, measure, upsert into its body, write `D/pr-body-loc.md`, and `PATCH` through REST (`-F body=@file`, with the same three-attempt retry as `annotate-pr.py`) only if the body changed | 0 on success or when no open PR exists; 1 when the PR cannot be read or the PATCH fails |

`refresh-pr` exits 1 only when it could not write anything at all. Every workflow step that runs it
sets `continue_on_failure: true`, so the outcome does not change.

### Feature: `annotate-pr.py`

Add `baseRefName` to its existing `gh pr list ... --json` fields. After the change-summary links
line, it runs `count-loc.py section --base-ref <baseRefName> --head <headRefOid>` and appends the
output, with a blank line before and after it. If `baseRefName` is missing, it passes an empty base
ref, and the script renders "unavailable: pull request base branch unknown". `annotate-pr.py`
already runs on every attempt that finalizes, including resumes, so the section is current after
each one. `mark-later-commits.py` then edits the body. It ignores the section, because
`### Lines of code` matches neither its tier pattern nor its item pattern.

### Fix: new `refresh-loc` step in `factory-fix-v1.0.yaml`

The new step goes immediately after `annotate-pr`, inside the `implement` group:

```yaml
      - id: refresh-loc
        continue_on_failure: true
        script: count-loc.py
        script_inputs:
          mode: refresh-pr
          artifact_dir: "{{artifact_dir}}"
```

It runs regardless of the claim marker, because the marker check belongs only to `annotate-pr`'s
header insertion. When finalization opened no PR, `refresh-pr` finds none and exits 0. Because it
measures GitHub's head SHA, a failed `finalize-pr` that left local commits never counts them.

### Review rounds: `review-description.sh`

The early `kind != "feature"` exit becomes per-kind dispatch:

- **task:** exit 0, as today.
- **fix:** `save` does nothing. `restore` runs `count-loc.py refresh-pr --number <N> --artifact-dir
  <artifacts>`. A failure writes `description-restore-failed` with the reason, which the existing
  `respond` prompt already reports, and the script exits nonzero. The step is already
  `continue_on_failure`.
- **feature:** when a saved body exists and is non-empty, the existing restore runs, and
  `rebuilt()` runs `count-loc.py refresh-file <updated> --number <N>` after `mark-later-commits.py`
  and before reading `updated` back. The existing `current != wanted` comparison then publishes it.
  When the saved body is missing or empty (the best-effort `save` failed), the script no longer does
  nothing. It runs `count-loc.py refresh-pr --number <N> --artifact-dir <artifacts>`, which replaces
  or adds only the section in the current published description and invents no restored content
  (AR-003). A failure in either path writes `description-restore-failed` and exits nonzero, and the
  `respond` step reports it. The step is `continue_on_failure`, so the round's outcome does not
  change.

`restore-description` already uses the same `skip_if` as `implement`
(`push_round != true`). The refresh therefore runs whenever the round ran its implementation step,
pushed or not, and never for answer-only rounds that merged nothing.

### Staging

Add `count-loc.py` to `FIX.staged_files` and `FEATURE_STAGED_FILES` in `kinds.py`, and to
`REVIEW_WORKFLOW_SCRIPTS` in `launch.py`. `STAGED_FILES` deduplicates it. The file starts with
`#!/usr/bin/env python3`, so staging marks it executable (`launch.py` checks for `#!`).

## Decisions

- **One script, invoked as a subprocess.** This matches `mark-later-commits.py`. The hyphenated name
  follows the other workflow scripts, and tests load it with `importlib.util.spec_from_file_location`
  (as `test_task_compliance_gate.py` does) to exercise the pure functions. An importable module
  (`count_loc.py`) imported from `annotate-pr.py` was rejected: it would need `sys.path` changes in
  every caller for no benefit.
- **GitHub's head SHA and base ref are authoritative** (proposal-review PR-001). Comparing against
  local HEAD was rejected, because failed finalizations and failed review rounds leave unpushed
  commits.
- **A fresh base-ref fetch on each refresh.** The merge base is stable as long as the target was not
  merged into the branch. Fetching keeps it correct when the PR branch merged newer target commits.
  When the fetch fails, the count is reported as unavailable. Using a stale `origin/<base>` was
  rejected, because the spec makes a failed fetch an unavailable count.
- **A state-machine parser rather than the prototype's line matching.** It gives the same counts as
  the prototype on normal diffs, and is correct for content lines that look like headers and for
  quoted paths.
- **Placement anchors.** Feature PRs place the section under the change-summary links. Fix PRs place
  it below the claim-marker header, falling back to the end of the body. Replacing in place keeps a
  moved section where a writer put it.

## Risks / Trade-offs

- **Content-based binary detection** ignores a committed `-diff` or `binary` attribute on a text
  file, and counts that file as text. This is deliberate, because attributes in a checkout must not
  change the count. Large generated text files marked `-diff` are therefore counted, unless their
  path matches an exclusion pattern.
- **Pattern heuristics:** generated or vendored code counts as code. This is accepted, and the lists
  are in one place.
- **Submodule pointer changes** show as a one-line `Subproject commit` hunk and count as one
  modified code line. This is rare in factory targets and accepted.
- **Model-written fix descriptions** can reword around the markers or delete them. Each refresh
  replaces or re-inserts the section, and a dangling start marker is cleaned up.
- **Extra GitHub reads:** one `gh api` read and at most two fetches per refresh, which is negligible
  next to the existing finalization traffic.
- **Existing `annotate-pr.py` tests:** their `gh` stubs return no `baseRefName`, so those tests will
  render the unavailable form. Tests that assert the whole body need the stub or the expectation
  updated.

## Testing Strategy

- **Unit (`tests/unit/test_count_loc.py`):**
  - hunk math (replace, add-only `-a,0`, delete-only `+b,0`, omitted counts);
  - new and deleted files, renames under `--no-renames`, binary and mode-only changes;
  - classification precedence and representative paths;
  - a content line starting with `+++ ` or `--- ` inside a hunk;
  - quoted paths;
  - the issue's reference diffs, reproduced as fixtures;
  - `render` with zeros and with an unavailable result;
  - `upsert` cases: insert at each anchor, replace, collapse duplicates, a dangling start marker,
    CRLF, and text outside the section unchanged;
  - inline-quoted markers and markers inside backtick and tilde fences are preserved, including
    when the live section is missing.
  - Patches containing form feeds or `\x0b` are split on `\n` only.
- **Integration (`tests/integration/test_count_loc.py`):** real temporary repositories with a bare
  `origin`:
  - a target-branch merge into the PR branch is not counted;
  - a local commit ahead of the published head is not counted;
  - an unreachable base ref renders the unavailable form;
  - `refresh-pr` with a `gh` stub PATCHes only when the body changed, and exits 0 when there is no
    PR.
- **Workflow scripts:**
  - the fix workflow has `refresh-loc` after `annotate-pr`, with `continue_on_failure`;
  - `review-description.sh` refreshes a fix PR, leaves a task PR untouched, and includes the
    section in a restored feature description;
  - `annotate-pr.py` output contains the section below the links.
- **Staged catalog:**
  - `count-loc.py` is in `FIX.staged_files`, `FEATURE_STAGED_FILES`, `REVIEW_WORKFLOW_SCRIPTS`, and
    `STAGED_FILES`;
  - running it from a staged catalog directory (`section` mode against a temporary repository)
    works.

## Migration Plan

The change is additive. It reaches the live service with the next deploy. Claims admitted earlier
keep their staged catalog, so their PRs gain the section only from a later attempt or review round
on a new release. A PR description without markers simply gets the section inserted. Rollback
leaves existing sections in place as inert Markdown, and older releases neither read nor refresh
them.

## Open Questions

None.
