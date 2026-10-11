## Why

A Factory feature pull request's description says what to review first (red and orange items),
but it explains the change itself in one line: the issue title followed by links to the archived
proposal, specifications, design, and test plan. To learn why the change exists, what it covers,
and what it excludes, a reviewer has to open Files Changed or follow those links into the archived
OpenSpec files. Paul reviews every feature PR the factory opens (issue #71) and wants to understand
the change from the description alone. A generated summary of the proposal does not meet that
need: it can drift from what was approved and turns precise scope statements into vague prose.

The proposal is the right content to inline. It is the definition artifact written for a human
reader, it states the motivation, scope, out-of-scope boundary, and impact, and the definition
workflow already reviews it before anything is built. The annotation step already has the archived
change directory at hand, so the content is available where the description is assembled.

Verdict: **go**. The change is small, sits inside one existing script, and directly removes a
recurring review cost. The caveat is size: the largest archived proposals approach 33 KB and
GitHub rejects a description over 65,536 characters, and review rounds and later-commit marking
grow the description after it is first written, so inlining needs a size fallback that applies at
every publication. A second caveat is that copied text must stay out of reach of the scripts that
parse and edit the description's report lines.

## What Changes

- The feature pull request description carries the archived proposal's content inline, word for
  word, in the change summary section below "Review first". It is shown expanded rather than
  collapsed, so a reviewer reads it without clicking or navigating away.
- The whole proposal is included when it fits. Whenever the factory publishes the description
  (first annotation and every review-round update) and the final body would exceed GitHub's size
  limit, the description drops whole proposal sections, lowest priority first, keeping Why, What
  Changes, and Out of Scope longest. It states which sections were left out and links the full
  file. If no complete section fits, the proposal is replaced by that notice and link. Proposal
  text is never cut mid-section, summarized, or refreshed; report content (risk items, decisions,
  assumptions, acceptance evidence, review-round history) is never shortened to make room.
- The inlined proposal sits in a clearly delimited region that the factory treats as opaque.
  Scripts that read or update the description's report lines (later-commit marking and the
  review-round update) skip that region, so a proposal that quotes report text, such as a fenced
  example of "Acceptance ran against" or "Commits after acceptance", is neither rewritten nor
  mistaken for the real evidence.
- The proposal's headings are nested under the change summary so the description keeps one
  coherent outline, and the existing links to the proposal, specifications, design, and test plan
  remain.
- Everything else stays as it is: the opening issue line, "Review first" with red and orange
  items, the single closing keyword, the claim marker, the collapsed orange and yellow overflow,
  the collapsed decisions and assumptions, the acceptance evidence, and the review-round section
  that is inserted before the change summary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-feature-reporting`: the requirement "Annotate the feature pull request by review
  attention" changes from a short summary with artifact links to a summary that also carries the
  archived proposal's content inline and verbatim, with a defined fallback when the description
  would exceed GitHub's size limit.

## Technical Approach

The feature workflow's `annotate-pr` step (`annotate-pr.py`) already builds the description from
the archived change directory and publishes it through the REST API. It will read
`<archive>/proposal.md`, drop or demote its top-level headings so they sit under
`## Change summary`, and place the text right after the artifact links. No new workflow step,
input, or agent is needed, and the content comes from the committed file, not from a model.

Key decisions, to be detailed in design:

- **Verbatim, not summarized.** The issue rejects generated summaries; copying the file is also
  deterministic and testable.
- **Expanded, not in `<details>`.** The goal is reading the change from the description itself.
  Red and orange items still come first, so the review order does not change.
- **Size budget on the final body, at every publication.** Initial annotation runs
  `mark-later-commits.py` after building the body, and each review round inserts commit and
  feedback lines and re-runs that marker in `review-description.sh` before publishing without a
  size check. A description that fits at first can therefore outgrow the limit later, and a
  failed update records `description-restore-failed`. The fit check therefore runs on the final
  text just before each PATCH, through one shared deterministic helper that shortens only the
  proposal region (dropping whole sections, then falling back to the notice and link). This
  handles accumulated rounds rather than relying on a fixed reserve. The exact budget, section
  order, and helper placement belong in design.
- **The proposal region is opaque.** The proposal is wrapped in stable start and end markers.
  `mark-later-commits.py` currently treats the whole body as report text: it takes the first
  "Acceptance ran against" line, the first "Commits after acceptance" item, and the first "Later
  commits" line anywhere in it. It must skip the delimited region, so marker-like lines inside the
  proposal cannot select the acceptance commit, receive commit annotations, or prevent updates to
  the real evidence. Heading demotion alone does not provide this.
- **No heading that collides with existing anchors.** The review-round update in
  `review-description.sh` inserts its section before the line `## Change summary`. The inlined
  proposal must not contain that exact line, so demoted headings never become a second insertion
  point.
- **A missing proposal does not fail the step.** The definition workflow requires `proposal.md`,
  but if the file is absent the description falls back to today's links-only summary.

Review rounds keep the description they started with, so the inlined proposal shows the version
archived when the PR was first annotated. A round that changes the proposal does not refresh it;
a round may only shorten the original inlined text under the size rule above.

## Out of Scope

- Inlining the specifications, design, test plan, or tasks.
- Design diagrams, or adopting design-skill enhancements in the description.
- Fix and task pull request descriptions, which have no OpenSpec proposal.
- Rewriting, summarizing, or rewording proposal content with a model.
- Refreshing the inlined proposal during a review round.
- Changing the issue comment that links the pull request.

## Impact

- Code: in `src/agent_factory/work_kinds/pull_request/workflow/`: `annotate-pr.py` (inlining and
  the initial size check), `mark-later-commits.py` (skip the proposal region),
  `review-description.sh` (size check on the restored description before publishing), and a new
  shared helper module for the region and the size fallback, staged through `kinds.py` and
  `launch.py`. Integration tests in `tests/integration/test_feature_workflow_scripts.py`,
  including a proposal that quotes report lines and a description that fits at first but grows
  past the limit after a review round.
- Documentation: `docs/operations.md` gains a rollback rule for pull requests whose description
  has an inlined proposal, since older scripts would read it as report text.
- Specification: `factory-feature-reporting` (delta on the annotation requirement).
- Users: reviewers of feature PRs get longer descriptions with the proposal readable in place.
  Existing PRs are unchanged until their next annotation.
- No change to persisted data, configuration, public CLI, or other repositories. Takes effect for
  new feature attempts after the next factory deploy.
