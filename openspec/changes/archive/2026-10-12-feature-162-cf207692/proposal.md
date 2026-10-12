## Why

Paul wants to see how big each factory change is at a glance, measured on code rather than on
specifications and documentation. The factory has no lines-of-code logic today. The 300- and
150-line limits in `.claude/skills/factory-triage/SKILL.md` are model estimates, and
`watch/result.py` only checks the shape of the risk result. GitHub's +/- totals do not help much.
On a factory feature PR they are dominated by OpenSpec artifacts (proposal, specs, design, test
plan, tasks, decisions), docs, and configuration, so a PR with a 30-line code change can show
+1,500.

A count that code computes from git, shown in the PR description, gives Paul a size he can trust
when he reviews a PR. Later it can also feed the risk classification and triage limits. That use is
out of scope here.

Verdict: **go.** The issue includes a prototype that Paul checked against three agent-runner PRs.
The factory already renders PR descriptions in deterministic scripts: `annotate-pr.py` for
features, the fix workflow's `annotate-pr` step, and `review-description.sh` for review rounds. The
work is one small, self-contained, testable script plus three insertion points. The caveats:

- Fix PR descriptions are written by a model in Agent Runner's `finalize-pr`, and a review round's
  finalization may rewrite any description. The section has to be inserted by script after each of
  those steps, and it is replaced, never appended, so a stale or forged copy cannot persist.
- The pattern lists are heuristics. Generated code, vendored files, or a non-standard test layout
  in a target is counted as code until someone tunes the lists. That is acceptable because the
  lists live in one place.

## What Changes

- **A deterministic counter.** A workflow script diffs `merge-base(base, head)..head` with
  `git diff -U0 --no-renames`. Here `head` is the PR's published head commit, and `base` is the
  PR's base branch, both as GitHub reports them. It is never unpushed local work. It drops excluded paths (OpenSpec, docs, configuration, lock files,
  repository metadata), sorts the remaining files into **code** and **tests**, and for each kind
  reports the number of files, added, modified, and deleted lines, plus the GitHub-style +/- for the
  same files. It uses the issue's hunk math: in each `-U0` hunk, `min(old, new)` lines count as
  modified and the remainder as added or deleted. The exclusion and test patterns are module
  constants in that one script, starting from the issue's lists.
- **A "Lines of code" section in the description.** It has a two-row table (code, tests), a note
  that specs, docs, and configuration are excluded, and the base and head commits it measured. The
  section sits between stable HTML markers, and each refresh replaces it in place. The head shown is
  always the published PR head. A PR that changes
  only excluded files shows zeros, not a missing section.
- **Every fix and feature PR, refreshed on every update:**
  - Feature: `annotate-pr.py` renders the section into the description it builds, on the first
    attempt and on every resume that re-runs it.
  - Fix: a new step after the fix workflow's `annotate-pr` inserts or replaces the section after
    `finalize-pr` writes the description. It runs on every attempt that opens or updates the PR.
  - Review rounds: after a round pushes, the section is recomputed for the new head. For a feature,
    `review-description.sh restore` does it as part of rebuilding the description. For a fix PR,
    whose description is otherwise left alone, it is inserted or replaced in the current
    description.
- **Unit tests** for hunk math, exclusions, test classification, new and deleted files, binary
  files, and merge-base handling (target-branch merges into the PR branch are not counted), plus
  workflow-script tests for inserting and replacing the section.

## Capabilities

### New Capabilities
- `factory-pull-request-size`: computing the code and test line counts for a factory pull request
  and keeping the "Lines of code" section in its description current.

### Modified Capabilities
- `factory-feature-reporting`: the annotated feature description includes the lines-of-code section.
- `factory-fix-reporting`: the fix PR description includes the lines-of-code section, inserted after
  finalization on every attempt.
- `factory-review-execution`: a review round that pushes refreshes the section on fix and feature
  PRs.

## Technical Approach

- **One standalone script, shipped with the workflows.** Add `count-loc.py` under
  `work_kinds/pull_request/workflow/`, so that it reaches each target's `.agent-runner/workflows/`
  like the other workflow scripts. It needs only `python3` and `git`, which every factory sandbox
  and host attempt already has, so it works in any target language. It has a pure parsing and
  counting core (diff text in, totals out), which the unit tests exercise. A thin git layer finds
  the merge base and runs the diff, and a renderer produces the delimited Markdown section. Other
  scripts import or call it. They do not copy the patterns. The factory stages workflow files from
  explicit lists, not from the whole directory: `STAGED_FILES` in `launch.py` combines each kind's
  `staged_files` in `kinds.py` with `REVIEW_WORKFLOW_SCRIPTS`. `count-loc.py` is added explicitly
  to the fix and feature kinds' lists and to the review scripts. An integration check runs the
  counter from the staged catalog that fix, feature, and review attempts receive. Without that
  check, best-effort handling could hide a missing script.
- **Measure the published PR, not local HEAD.** Annotation also runs when finalization or a
  review round failed before pushing (the fix workflow continues after `finalize-pr` fails, and
  `restore-description` runs after a failed implementation). In those cases local HEAD can hold
  unpushed commits. So the script reads the PR's base branch and published head SHA from GitHub
  (`baseRefName` and `headRefOid`, as `annotate-pr.py` already does for bot-review coverage). It
  fetches both when needed and measures `merge-base(origin/<base>, <published head>)..<published
  head>`. Local HEAD is used only when it equals the published head. If those objects cannot be
  obtained, the section shows the "count unavailable" note. A feature branch merges the target
  branch into itself on resume and in review rounds. Because the base is the merge base, those
  merged commits are not counted.
- **Insert or replace, never append.** The section sits between
  `<!-- agent-factory:loc -->` and `<!-- agent-factory:loc-end -->`. A refresh replaces whatever lies
  between the markers, and adds the section if the markers are missing. In a feature description it
  goes in the "Change summary" area. In a fix description it goes after the factory header. It
  contains no red or orange items, so the watcher's PR-READY mining and the auto-merge gates are
  unaffected.
- **Best effort, never blocking.** If the count cannot be computed (for example, the base ref cannot
  be fetched), the section says so and gives the reason. The attempt's outcome does not change, and
  the PR is still opened or updated. This follows the existing "description annotation is best
  effort" pattern in the fix workflow and in `review-description.sh`.
- **Deterministic and literal.** The script counts lines exactly as the issue defines them. It does
  not weight lines, strip whitespace or comments, or detect generated code. Binary files have no
  `-U0` hunks and contribute nothing. Renames are diffed as a delete and an add (`--no-renames`), as
  the issue specifies.

## Out of Scope

- Using the count in risk classification, auto-merge gates, triage size limits, or the
  `factory-triage` skill's 300- and 150-line rules.
- Task (chore) PRs. The issue names fix and feature PRs. Task descriptions are rendered by
  `annotate-chore-pr.sh` under the task guard, and adding the section there can follow if wanted.
- Per-target or per-repository pattern configuration. The lists are constants in one script, and
  tuning them is a code change.
- Language-aware counting (comments, blank lines, generated or vendored code).
- Issue comments, board fields, or `status` output carrying the count.

## Impact

- **Code:** new `src/agent_factory/work_kinds/pull_request/workflow/count-loc.py`, registered in the
  explicit staging lists (the fix and feature kinds' `staged_files` in `kinds.py` and
  `REVIEW_WORKFLOW_SCRIPTS` in `launch.py`). Changes to
  `annotate-pr.py` (feature description), `factory-fix-v1.0.yaml` (a new refresh step after
  `annotate-pr`), and `review-description.sh` (refresh for fix and feature rounds; task rounds are
  unchanged). New unit tests under `tests/unit`. New workflow-script tests under
  `tests/integration` cover these cases: a failed finalization and a failed review round that leave
  local HEAD ahead of the PR, which must not count unpushed commits; and the staged catalog, which
  must contain the counter for fix, feature, and review attempts.
- **Workflows:** the fix and review workflows gain or extend a step within their current contract
  versions (`factory-fix/1`, `factory-review/1`). The outcome files and the claim state are not
  changed.
- **Persisted data and interfaces:** none changed. The PR description gains one marker-delimited
  section. Existing parsers of the description (the claim marker, issue keywords, attention items)
  are unaffected.
- **Deploy:** reaches the live service with the next Agent Factory deploy. Claims admitted before
  the deploy keep their frozen workflow revisions.
- **Users:** Paul sees a code and test size table on every fix and feature PR. It is refreshed when
  a review round or later attempt updates the PR.
