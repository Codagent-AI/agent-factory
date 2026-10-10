## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration and end-to-end obligations, the acceptance testing envelope, and exceptional human-only
obligations.

The risk sits where scripts meet: `annotate-pr.py` builds the description, `mark-later-commits.py`
edits it as a subprocess, `review-description.sh` restores and republishes it, and `pr_description.py`
must be staged and importable by each of them. The unit tests for `pr_description.py` (rendering,
measuring, fitting, masking) come from the specifications and design and are not listed here. The
integration tests below run the real scripts as subprocesses in a temporary git repository with the
existing `gh` stubs (`GH_STUB`, `REVIEW_GH_STUB`) that capture the PATCHed body. That is how the
current annotation and review-round tests in `tests/integration/test_feature_workflow_scripts.py`
work.

No new end-to-end test is warranted. `tests/e2e/test_feature_cycle.py` does not exercise the
annotation step. A real publish needs a live GitHub pull request, and the integration tests already
run every script the workflow runs, on the exact body it publishes. The only part they cannot show
is GitHub's own rendering and size counting, which is left to the acceptance pass (rendering) and
to the conservative measure in the design (counting).

## Integration Tests

### INT-001: Annotation inlines the proposal verbatim around the existing report
- Covers: "Inline the proposal in the feature pull request description" (read the proposal, nest
  headings, keep review context); the modified "Annotate the feature pull request by review
  attention" (clean pull request scenario).
- Boundary: `annotate-pr.sh` → `annotate-pr.py` → `pr_description` import → `mark-later-commits.py`
  subprocess → `gh api` PATCH (stub).
- Setup: temporary repository with an archived change directory containing a `proposal.md` with
  Why, What Changes, Capabilities, Technical Approach, Out of Scope, and Impact sections, including
  a fenced code block with a `## ` line; a `review-attention.json` with red, orange, and yellow
  items; a `decisions.md`; a commit after acceptance.
- Action: run `annotate-pr.sh` as the existing tests do.
- Assertions: the PATCHed body has the issue line first, then "Review first", then exactly one
  `Closes #N`, the claim marker, `## Change summary` with the four artifact links, then the
  proposal region between the start and end markers, then the collapsed sections, decisions, and
  acceptance evidence. Each proposal section's text matches the file except that `## X` headings
  are `### X`. The fenced `## ` line is unchanged. No line of the region equals `## Change summary`.
  The proposal is not inside a `<details>` element. The region's first line begins with the literal
  `<!-- agent-factory:proposal:start ` that the documented rollback rule searches for.
- Execution: `tests/integration/test_feature_workflow_scripts.py`, `uv run pytest tests/integration`.

### INT-002: Report lines quoted in a proposal are neither read nor edited
- Covers: "Inline the proposal in the feature pull request description" (a proposal quotes report
  lines).
- Boundary: `annotate-pr.py` → `mark-later-commits.py` on the real body, then a review round
  through `review-description.sh` → `mark-later-commits.py`.
- Setup: the proposal contains, in a fenced block and in plain text, an
  ``Acceptance ran against `<other sha>`.`` line, a `Later commits: ` line, a
  `- [Commits after acceptance](...)` item, and copies of the region's start, section, and end
  marker forms (including an end marker placed before the quoted report lines). The repository has two commits after the real accepted
  commit, one of them changing a file a yellow item links to.
- Action: annotate, then run a review round (save, add a commit, restore).
- Assertions: the real acceptance evidence names the real accepted commit and lists every later
  commit. The orange "Commits after acceptance" item in "Review first" lists them. The yellow item
  is marked as possibly fixed. The region spans the whole proposal. Its text is byte-identical
  before and after both marker runs.
- Execution: `tests/integration/test_feature_workflow_scripts.py`.

### INT-003: A large proposal is shortened by whole sections at annotation
- Covers: "Keep the feature pull request description within GitHub's size limit" (fits after
  omitting sections; only the key sections fit; no proposal section fits; within the limit
  unchanged).
- Boundary: `annotate-pr.py` → marker → `fit` → PATCH (stub).
- Setup: proposals generated with section sizes chosen so that the full description is (a) within
  the limit, (b) over it but fits without Technical Approach and Impact, (c) fits only with Why and
  What Changes, (d) does not fit even with Why alone. Report content is held constant.
- Action: run `annotate-pr.sh` once per case.
- Additional case: a large proposal that quotes the section and end marker forms inside one of
  its sections; the omitted and kept sections are complete original sections.
- Assertions: every PATCHed body measures within 65,536 under the design's measure (UTF-8 bytes,
  CRLF line endings). The kept sections are verbatim. The notice names exactly the omitted sections
  in proposal order and links the full proposal. In case (d) the notice says the proposal was
  omitted for length. Decisions, risk items, and acceptance evidence are identical across cases.
  In case (a) no notice appears.
- Execution: `tests/integration/test_feature_workflow_scripts.py`.

### INT-004: A review round that outgrows the limit is shortened and published
- Covers: "Keep the feature pull request description within GitHub's size limit" (a review round
  pushes the description over the limit); "Inline the proposal…" (a review round inserts its
  section).
- Boundary: `review-description.sh` (staged from `REVIEW_WORKFLOW_SCRIPTS`) importing
  `pr_description` from the staged directory → `mark-later-commits.py` → PATCH (stub).
- Setup: a saved description produced by `annotate-pr.sh` with a full proposal, sized so it fits
  but leaves less room than one round's section and its marked commits need. A round with several
  commits and addressed feedback.
- Action: save, add the round's commits, restore.
- Assertions: the restore exits 0 and writes no `description-restore-failed`. The PATCHed body
  measures within the limit. The round's section precedes `## Change summary` and is complete. The
  commits after acceptance are complete. Whole lower-priority proposal sections are omitted and
  named. A second case with room to spare shows the region byte-identical to the saved one.
- Execution: `tests/integration/test_feature_workflow_scripts.py`, extending the existing
  review-round harness.

### INT-005: A missing proposal and a pre-change description keep today's behavior
- Covers: "Inline the proposal…" (archived proposal missing); migration of descriptions written
  before this change.
- Boundary: `annotate-pr.py` without `proposal.md`; `review-description.sh` and
  `mark-later-commits.py` on a saved description without region markers.
- Setup: an archived change directory without `proposal.md`; a saved description from today's
  format.
- Action: annotate; run a review round on the old-format description.
- Assertions: the annotation exits 0, keeps the links, and states that the proposal could not be
  included. The old-format round output equals what the round produces without this change: same
  section placement, same later-commit marking, no notice or markers added.
- Execution: `tests/integration/test_feature_workflow_scripts.py`.

### INT-006: The helper is staged for every run that imports it
- Covers: design staging; prerequisite for every requirement above in a real run.
- Boundary: `FEATURE_STAGED_FILES` and `REVIEW_WORKFLOW_SCRIPTS` → catalog staging → script import.
- Setup: the existing catalog tests in `tests/integration/test_feature_workflow_catalog.py`.
- Action: stage the feature and review workflow files as the launcher does.
- Assertions: `pr_description.py` is present in both staged sets, and INT-001 and INT-004 run from
  the staged copies, not the package directory.
- Execution: `tests/integration/test_feature_workflow_catalog.py` and
  `tests/integration/test_feature_workflow_scripts.py`.

## End-to-End Tests

None. See Coverage Strategy.

## Acceptance Testing Envelope

- Environments and sandboxes: this worktree and temporary git repositories under the test's own
  temporary directory. The scripts may be run directly with the `gh` stubs from the integration
  tests, or with a `gh` shim that records PATCH bodies to a file.
- Credentials and secrets: the operator's `gh` login on this Mac exists. It may be used only for
  the read-only Markdown render endpoint (`gh api --method POST /markdown` with `mode=gfm`), which
  creates nothing, to check how a produced body renders: the proposal expanded, headings nested,
  hidden markers invisible, and collapsed sections still collapsed.
- Authorized effects: none on GitHub beyond that render call. No cost.
- Off limits: creating, editing, or commenting on any real pull request or issue; the live factory
  service, its releases, `~/.agent-factory` state other than this attempt's artifact directory,
  and Paul's checkout; deploying.
- Permitted substitutes: the `gh` stubs for every PATCH and PR lookup; synthetic proposals to reach
  the size limit; archived proposals in `openspec/changes/archive/` as realistic input.
- Known risk areas: rollback to a release without proposal regions (contained by the
  operational rule in the design's Migration Plan, not by code); `mark-later-commits.py` first-match parsing (the defect class behind finding
  PR-002); review-round restore and republish (`description-restore-failed`); CRLF descriptions,
  which the review round preserves; unbalanced HTML in proposals (an accepted limitation); closing
  keywords such as `Fixes #N` inside a proposal, which are left verbatim and would link that issue
  (an accepted limitation); setext headings, which are not demoted (an accepted limitation).

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Annotate the feature pull request by review attention (modified: inline proposal in change summary) | INT-001 | — | — |
| Inline the proposal in the feature pull request description | INT-001, INT-002, INT-004, INT-005 | — | — |
| Keep the feature pull request description within GitHub's size limit | INT-003, INT-004 | — | — |
| Descriptions written before this change | INT-005 | — | — |
| Staging of the shared helper | INT-006 | — | — |
