## Context

The feature pull request description is built and published by three scripts in
`src/agent_factory/work_kinds/pull_request/workflow/`:

- `annotate-pr.py` (run by `annotate-pr.sh` in the feature workflow's `annotate-pr` step) builds the
  whole description into `<artifact_dir>/feature-pr-body.md`, runs `mark-later-commits.py` on that
  file, then publishes it with `gh api --method PATCH repos/{owner}/{repo}/pulls/<n> -F body=@<file>`
  (three tries). The change summary is today one line: the feature title plus links to
  `<prefix>/proposal.md`, `specs/`, `design.md`, and `test-plan.md`, where `prefix` is the archived
  change directory on the feature branch on GitHub (or the local archive path without a
  repository). The archived change directory is the `archive` path the script already receives.
- `mark-later-commits.py BODY_FILE` edits a description in place. It reads the first line matching
  `Acceptance ran against `<sha>`.` anywhere in the body, tracks red/orange/yellow tiers from
  `### <icon> <Tier> (<n>)` headings (any other `#` line or `</details>` ends a tier), marks items
  whose linked file a later commit changed, extends the first `- [Commits after acceptance](` line,
  and extends or inserts the first `Later commits: ` line.
- `review-description.sh` (feature review rounds, `factory-review-v1.0.yaml`) saves the description
  before a round and, on restore, inserts the round's section before the first line equal to
  `## Change summary`, runs `mark-later-commits.py` (path in the `MARKER` environment variable) on
  `pr-description-after-review.md`, and PATCHes it. A failed PATCH writes
  `description-restore-failed` and exits non-zero.

None of these checks the description's size. GitHub rejects a body over 65,536 characters with
HTTP 422 (`body is too long (maximum is 65536 characters)`). Archived proposals in this repository
reach about 33 KB; decisions, acceptance evidence, and later review rounds add more.

The files a run uses are staged from the package into the clone's untracked
`.agent-runner/workflows/` by the lists `FEATURE_STAGED_FILES` (`kinds.py`) and
`REVIEW_WORKFLOW_SCRIPTS` (`launch.py`). The staged copies are not part of the repository.

## Goals / Non-Goals

**Goals:**

- Show the archived proposal verbatim and expanded in the change summary.
- Keep every factory reader and updater of the description from reading or editing the proposal
  text, except the size fallback.
- Keep every published description, at annotation and at each review round, within GitHub's limit
  by shortening only the proposal, whole sections at a time.
- Leave descriptions written before this change working exactly as they do today.

**Non-Goals:**

- Inlining specifications, design, test plan, or tasks; fix and task pull requests; the issue
  comment; refreshing the proposal during a review round.
- Shortening any report content, or making a description fit when it is too large without the
  proposal.

## Approach

### New shared module `pr_description.py`

A new module in the workflow directory, staged with the feature files and the review files, owns
the proposal region. It has no dependencies beyond the standard library, matching
`decision_json.py`.

Constants:

```python
LIMIT = 65_536
START = "<!-- agent-factory:proposal:start {token} {href} -->"  # href = full proposal URL, HTML-escaped
SECTION = "<!-- agent-factory:proposal:{token}:section -->"
END = "<!-- agent-factory:proposal:{token}:end -->"
START_LINE = re.compile(r"^<!-- agent-factory:proposal:start ([0-9a-f]{12}) (\S*) -->$")
OMITTED = "_Omitted for length: {names}. Read the [full proposal]({href})._"
ALL_OMITTED = "_The proposal was omitted for length. Read the [full proposal]({href})._"
UNAVAILABLE = "_The proposal could not be included inline; open the Proposal link above._"
```

Every region has its own delimiter token, so text copied from the proposal can never be read as
a delimiter, even when the proposal quotes the factory's markers in a fence or in plain text:

- `token(text: str) -> str` returns the first 12 hex characters of the SHA-256 of the proposal
  text plus a counter, `sha256(f"{n}\0{text}")` for `n = 0, 1, ...`, taking the first value that
  does not occur anywhere in the proposal text. The token is deterministic for a given proposal,
  needs no state, and cannot appear in any proposal line, so no proposal line can equal the
  region's `SECTION` or `END` marker.
- The token is written once, in the `START` line. A review round reads it back from there and
  never recomputes it, so the region keeps its delimiters across rounds even after sections were
  omitted.

Functions:

- `render(text: str, href: str) -> list[str]` turns the proposal file's text into region lines:
  `START` with the region's token, then for each section that token's `SECTION` line, a blank
  line, and the section's lines, then that token's `END`. It:
  - finds ATX headings (`^#{1,6}[ \t]`) outside fenced code blocks (lines opening and closing
    with ``` or `~~~`);
  - demotes them by `3 - min_level` when the shallowest heading is above level 3, capping at 6, so
    `## Why` becomes `### Why`. Heading text and every other line are copied unchanged;
  - splits sections at headings of the (demoted) shallowest level. Text before the first heading
    is its own leading section. A proposal with no headings is one section;
  - strips trailing blank lines from the file but keeps everything else, including fenced code,
    HTML, and lines that look like report text.
- `measure(body: str) -> int` returns the UTF-8 byte length of the body with every line ending
  normalized to `\r\n`. This is never less than GitHub's character count however it counts
  (code points or UTF-16 units, with or without CRLF normalization), so a body that measures
  within `LIMIT` is accepted. The cost is that bodies heavy in non-ASCII text are shortened a
  little earlier than strictly needed.
- `region(lines) -> tuple[int, int, str, str] | None` returns the index of the first line that
  matches `START_LINE` in full, the index of the first line after it that equals that token's
  `END` exactly, the token, and the href, or `None` when either line is missing. Only that token's
  `SECTION` lines inside the region are section boundaries. Without a closed region the body is
  treated as having none. The first `START_LINE` match is the real one: everything before the
  region is factory-written (the issue line, the "Review first" items, which are single lines
  beginning with `- `, the review-round section, whose agent text is escaped, and the
  change-summary links), and a proposal's quoted `START` line comes after the real one.
- `fit(body: str) -> str` returns the body unchanged when `measure(body) <= LIMIT` or it has no
  region. Otherwise it reads the sections inside the region and the names already listed in an
  existing `OMITTED` line (the line right after `START`), and drops sections one at a time in
  this order until the body fits or none remain:
  1. sections whose heading text is not `Why`, `What Changes`, or `Out of Scope`
     (case-insensitive, trimmed), last-appearing first, including a leading section without a
     heading (named `Introduction` in the notice);
  2. `Out of Scope`, then `What Changes`, then `Why`.

  After each drop it rewrites the notice line, right after `START`: `OMITTED` with the dropped
  section names in proposal order, or `ALL_OMITTED` once every section is gone. Sections that
  remain are not touched. If the body still exceeds the limit with every section dropped, `fit`
  returns that body. Publication then fails as it does today, with GitHub's error recorded.
- `masked(lines) -> tuple[list[str], list[str] | None]` replaces the region with its `START` line
  alone and returns the removed region; `unmasked(lines, removed)` puts it back where that `START`
  line now is. Both are used by `mark-later-commits.py`. The placeholder is the region's own `START` line, which
is unique in the masked body.
- `main()` (`python3 pr_description.py fit FILE`) applies `fit` to the file in place, for
  shell callers.

### `annotate-pr.py`

- After the change-summary links line, read `archive / "proposal.md"`. If it is readable, append
  a blank line and the lines of `render(text, f"{prefix}/proposal.md")`. If it is missing or
  unreadable (`OSError`, `UnicodeDecodeError`), append `UNAVAILABLE` instead and continue.
  Everything after the change summary (collapsed orange and yellow items, decisions,
  assumptions, acceptance evidence) follows as today.
- After `mark-later-commits.py` runs, read the body file, apply `pr_description.fit`, and write
  it back before the PATCH. If the fit step raises, report it on stderr and publish the
  unshortened body (best effort, like the marker).
- The import works because Python puts the script's directory first on `sys.path`.

### `mark-later-commits.py`

`mark()` masks the region before any parsing and unmasks it before returning. The placeholder
`START` line matches none of its patterns, is not a heading, and is not `</details>`. Tier
tracking, the acceptance line, the first `Commits after acceptance` item, and the first
`Later commits:` line are therefore found only outside the proposal, and nothing is inserted
inside it. Line insertions before the placeholder are handled by locating it by value when
unmasking. A body with no region behaves exactly as today.

### `review-description.sh`

The round section is still inserted before the first `## Change summary` line. The proposal
region always follows that heading and its headings are demoted below level 2, so the first
match is the factory's heading. After the marker runs on `pr-description-after-review.md`, the
script applies `pr_description.fit` to it (importing the module from the directory of
`MARKER`, added to `sys.path`) before comparing it with the current body and PATCHing it. A
description saved before this change has no region, and the round behaves as today.

### Data flow

```text
annotate-pr.py:  build lines (+ render(proposal) | UNAVAILABLE) -> feature-pr-body.md
                 -> mark-later-commits.py (region masked) -> fit() -> PATCH
review round:    saved body -> insert round section before "## Change summary"
                 -> mark-later-commits.py (region masked) -> fit() -> compare -> PATCH
```

### Staging

Add `pr_description.py` to `FEATURE_STAGED_FILES` in `kinds.py` and to `REVIEW_WORKFLOW_SCRIPTS`
in `launch.py`. Tests that list the staged files must be updated with it.

### Documentation

`docs/operations.md` gains the rollback rule in the Migration Plan below.

## Decisions

- **One shared module, not logic copied into each script.** The annotation and the review
  round must agree on the region format and drop order. A module beside the scripts, staged
  like `decision_json.py`, keeps one definition.
- **Hidden markers, not re-parsing headings, to find sections later.** A review round may need
  to shorten a description built in an earlier attempt. Parsing Markdown again would need
  fence tracking and could misread a fenced `###` line. `SECTION` lines make the boundaries
  exact, and HTML comments do not render.
- **A per-region token absent from the proposal, not fixed marker text** (finding AR-001). With
  fixed markers, a proposal quoting `<!-- agent-factory:proposal:end -->` would close the region
  early and expose the rest to report parsing, and a quoted section marker would let fitting drop
  part of a section. Fence parsing alone cannot fix this, because plain-text quotes must also stay
  verbatim. A token chosen so it does not occur in the proposal text makes a collision
  impossible by construction, with no rewriting of proposal text.
- **The full-proposal link travels in the `START` marker.** `fit` runs in two scripts. Carrying
  the link in the region means the review round needs no knowledge of the archive path.
- **Fit runs last, on the exact text being published.** The marker and the round section both
  grow the body. Checking anything earlier would leave the published body unbounded (finding
  PR-001).
- **Conservative byte measure, no extra margin.** GitHub does not document how it counts. The
  UTF-8 length with CRLF line endings is an upper bound on any of its plausible counts, so no
  arbitrary margin is needed.
- **Too large without the proposal: publish and fail as today.** Shortening report content is
  ruled out by the specification. The existing failure reporting (annotation failure outcome,
  `description-restore-failed`) already covers an oversized body.
- **Missing proposal is not a failure.** The description keeps the links and says the proposal
  could not be included. The risk report is more important than the inline proposal.
- **Demotion limited to ATX headings outside fences.** Setext headings (`Heading` over `===`) are
  copied unchanged. Proposals written from the template use ATX headings, and changing setext
  underlines would alter non-heading lines.

## Risks / Trade-offs

- **Closing keywords inside a proposal.** GitHub reads closing keywords such as `Fixes #12`
  anywhere in a description. A proposal containing one would link, and on merge close, that
  issue. No archived proposal in this repository contains one, and rewriting the text would
  break the verbatim requirement. This is accepted and recorded. The "exactly one closing
  keyword" test covers the factory's own line only.
- **Unbalanced HTML in a proposal** (`<details>` without its closing tag) could change how the
  rest of the description renders. This is accepted as verbatim content. The factory's own
  parsing is unaffected because the region is masked.
- **Longer descriptions.** Reviewers scroll past the proposal to reach the collapsed sections.
  "Review first" stays at the top, so the review order is unchanged.
- **A hand-edited region** (markers removed) is treated as having no region. The description is
  then published unshortened and parsed as before this change.

## Migration Plan

No data migration. The change takes effect for feature attempts and review rounds started from
a release that contains it, after `scripts/deploy.sh`. Pull requests annotated earlier have no
region, and their review rounds behave as before.

Rolling back is not safe without containment (finding AR-002). A release without this change
runs the old `mark-later-commits.py` and `review-description.sh`. On a description with a
proposal region, the old marker reads the whole body. When the proposal quotes report lines, it
can take a quoted acceptance commit and edit a quoted `Later commits:` line instead of the real
evidence. The old review round also publishes without the size check, so a near-limit
description can fail to restore. The rule, added to `docs/operations.md` beside the other
rollback rules:

- Before rolling back to a release without proposal regions, list open factory feature pull
  requests whose description contains `<!-- agent-factory:proposal:start `.
- For each one, settle or cancel its open review claim, and do not request review rounds on it,
  until the factory runs a release with this change again. A writer may instead remove the region
  (the lines from its `START` through its `END`) from the description by hand before a round runs
  on the older release. The description then keeps only the links, as before this change.
- A hand rollback bypasses nothing extra: no deploy guard is added for this rule. The hazard
  needs a review round on an affected pull request, and review rounds are admitted only on a
  writer's request.

INT-001 asserts the literal `START` prefix, so the documented search string stays accurate.

## Verification

Unit tests for `pr_description.py`, imported with `importlib` as `annotate_pr` is in
`tests/integration/test_feature_workflow_scripts.py`:

- `render`: demotion from `##` and from `#`; fenced `##` lines unchanged; a leading section
  without a heading; no headings; text otherwise identical to the file.
- `measure`: LF and CRLF bodies, and multi-byte text.
- `token`: deterministic for the same text; changes when the text contains the first candidate;
  never occurs in the text.
- `region` and `fit` with a proposal that quotes `START`, `SECTION`, and `END` lines (fixed-text
  and with the region's own prefix) in a fence and in plain text: the region spans the whole
  proposal, the proposal is unchanged when it fits, and shortening removes only complete original
  sections.
- `fit`: within the limit unchanged; drops Impact, then Technical Approach, then Capabilities,
  keeping Why, What Changes, and Out of Scope; only Why and What Changes left; every section
  dropped (`ALL_OMITTED`); still oversized after dropping everything (returned without error);
  an earlier `OMITTED` notice extended rather than duplicated; a body without a region
  unchanged.

Integration tests using the existing `annotate-pr.sh` and review-description harnesses:

- The annotation shows all six template sections verbatim and expanded after the artifact links,
  with "Review first" first and every existing section present.
- A proposal quoting `Acceptance ran against`, `Later commits:`, a `Commits after acceptance`
  item, and the region's start, section, and end marker forms, in fences and in plain text: the
  real evidence and the orange item list the later commits, and the quoted lines are unchanged.
- A missing proposal: `UNAVAILABLE` is shown and the PATCH succeeds.
- A large proposal: the PATCHed body measures within the limit and names the omitted sections.
- A review round whose section and marked commits push a description that fit past the limit:
  the round PATCHes a shortened body and writes no `description-restore-failed`.
- A review round on a description with a region: its section appears before the change summary,
  and the region is unchanged when it fits.
- A pre-change description without a region: round and marker output unchanged.
