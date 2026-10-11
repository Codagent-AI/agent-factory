# Decisions

## proposal: Verdict

- Decision: go. Inline the archived proposal in the feature PR description.
- Alternatives considered: no-go (links are enough), which contradicts the issue.
- Decision-bearing: no.

## proposal: Content to inline

- Decision: the whole archived `proposal.md`, verbatim; under size pressure keep Why, What Changes, and Out of Scope verbatim and name what was left out, with a link to the full file.
- Alternatives considered: only selected sections always; a model-generated summary (rejected by the issue); inlining specs and design too (beyond the issue's scope).
- Decision-bearing: yes. The issue allows "most/all of it or the key relevant sections"; full when it fits satisfies both readings.

## proposal: Placement and visibility

- Decision: expanded under `## Change summary`, after the artifact links; "Review first" stays at the top.
- Alternatives considered: collapsed in `<details>` (still in the description but less direct to read); above "Review first" (changes the review order the reporting spec requires).
- Decision-bearing: yes. Chosen to meet "understand directly from the PR description" without moving red and orange items.

## proposal: Size limit

- Decision: GitHub's 65,536-character description limit is enforced by dropping lower-priority proposal sections whole, never cutting mid-section; exact budget is left to design. (Revised by PR-001: the check applies to the final body at every publication.)
- Alternatives considered: no handling (risks a failed description update for the largest proposals, up to about 33 KB); collapsing the proposal when large.
- Decision-bearing: no.

## proposal: Heading nesting and review-round insertion point

- Decision: demote proposal headings under `## Change summary` and ensure no inlined line equals `## Change summary`, which `review-description.sh` uses as its insertion anchor.
- Alternatives considered: inserting headings unchanged.
- Decision-bearing: no.

## proposal: Review rounds

- Decision: review rounds keep the description they started with; the inlined proposal is not refreshed by a round.
- Alternatives considered: re-reading the proposal each round (conflicts with the existing keep-the-description rule and widens scope).
- Decision-bearing: no.

## proposal-review: PR-001 (size fallback misses later description updates)

- Decision: applied. The size rule now applies to the final body just before every publication (initial annotation after later-commit marking, and each review-round update in `review-description.sh`), through one shared helper that shortens only the proposal region, with a notice-and-link fallback when no whole section fits. Report content is never shortened. `review-description.sh` added to scope, with a test for a body that outgrows the limit after a round.
- Alternatives considered: a fixed headroom reserve at annotation only (fails after enough rounds); trimming acceptance evidence or decisions (loses review context the issue requires preserved).
- Decision-bearing: no. Confirmed in code: `review-description.sh` publishes without a size check and records `description-restore-failed` on a failed PATCH.

## proposal-review: PR-002 (copied proposal text parsed as report lines)

- Decision: applied. The inlined proposal sits between stable start and end markers and is opaque to the description's readers and updaters; `mark-later-commits.py` skips that region. Added to scope, with a test for a proposal that quotes "Acceptance ran against" and "Commits after acceptance" lines.
- Alternatives considered: rewriting or escaping marker-like lines in the proposal (breaks the verbatim requirement); relying on heading demotion (does not stop line-based matching).
- Decision-bearing: no. Confirmed in code: `mark()` takes the first matching line anywhere in the body.

## specs: Shape of the delta

- Decision: modify "Annotate the feature pull request by review attention" only to say the summary is followed by the inlined proposal; add two requirements, "Inline the proposal in the feature pull request description" and "Keep the feature pull request description within GitHub's size limit".
- Alternatives considered: folding everything into the existing requirement (already very long and harder to test).
- Decision-bearing: no.

## specs: Section omission order

- Decision: omit sections other than Why, What Changes, and Out of Scope first, last-appearing first (Impact, then Technical Approach, then Capabilities in the template), then Out of Scope, What Changes, Why.
- Alternatives considered: dropping by size (largest first), which keeps less predictable content.
- Decision-bearing: no.

## specs: Missing proposal

- Decision: keep the artifact links, state the proposal could not be included, and publish the rest as before.
- Alternatives considered: failing the annotation step (would lose the risk report for a cosmetic gap).
- Decision-bearing: no.

## specs: Deferred to design

- Decision: how characters are counted against GitHub's limit, any safety margin, and what happens when the description is over the limit even with the proposal omitted are deferred to design.
- Alternatives considered: fixing them in the spec.
- Decision-bearing: no.

## design: Shared module and region markers

- Decision: a new standard-library module `pr_description.py` beside the workflow scripts owns rendering, masking, measuring, and fitting; the region is bounded by `<!-- agent-factory:proposal:start <href> -->` and `<!-- agent-factory:proposal:end -->`, with a `<!-- agent-factory:proposal-section -->` line before each section.
- Alternatives considered: duplicating logic in each script; re-parsing Markdown headings to find sections in a review round (fragile with fenced code).
- Decision-bearing: no.

## design: Size measure and oversized fallback

- Decision: measure UTF-8 bytes with CRLF line endings against 65,536, an upper bound on any plausible GitHub count, with no extra margin; if the description is still too large with the proposal fully omitted, attempt the publish and let existing failure reporting handle the rejection. Spec deferred marker resolved with this rule and a scenario.
- Alternatives considered: counting code points (could under-count); a fixed safety margin (arbitrary); shortening report content (ruled out by the spec).
- Decision-bearing: no.

## design: Closing keywords quoted in a proposal

- Decision: accepted risk, left verbatim. A proposal containing `Fixes #N` would link that issue; none of the archived proposals contains one.
- Alternatives considered: rewriting or escaping such text (breaks verbatim).
- Decision-bearing: no.

## design: Staged copies

- Decision: `.agent-runner/workflows/` is untracked staging, not a maintained copy; the proposal's Impact was corrected to name `kinds.py` and `launch.py` staging lists instead.
- Alternatives considered: none.
- Decision-bearing: no.

## test-plan: Test layers and acceptance envelope

- Decision: six integration obligations running the real scripts against a temporary repository with the existing `gh` stubs; no new end-to-end test, because the feature e2e cycle does not exercise annotation and a real publish needs a live pull request. The acceptance pass may use only the read-only GitHub Markdown render endpoint, and must not touch real pull requests, issues, or the live service.
- Alternatives considered: an end-to-end test publishing to a scratch pull request (outward-facing and redundant with integration coverage); no GitHub access at all (loses the rendering check).
- Decision-bearing: no.

## approach-review: AR-001 (proposal can quote the region delimiters)

- Decision: applied. Each region gets a deterministic 12-hex token, derived from the proposal's SHA-256 with a counter, chosen so it occurs nowhere in the proposal text; the start marker carries the token and href, and the section and end markers include it. Region detection and fitting use only that token's markers. Spec gained a scenario for a proposal that quotes the markers; unit tests, INT-002, and INT-003 cover quoted markers in fences and plain text.
- Alternatives considered: fence-aware parsing of fixed markers (plain-text quotes still collide); escaping quoted markers (breaks verbatim).
- Decision-bearing: no.

## approach-review: AR-002 (rollback reads new regions with old scripts)

- Decision: applied. Removed the rollback-safety claim. The Migration Plan now states an operational rule, to be added to `docs/operations.md`: before rolling back past this change, find open factory feature PRs whose description contains `<!-- agent-factory:proposal:start `, and settle or cancel their review claims and request no rounds until the factory is forward again, or remove the region by hand first. INT-001 pins the search string.
- Rejected part: a code-level compatibility test against the older release. The older release's scripts cannot be changed by this change, and review rounds are admitted only at a writer's request, so containment is operational, matching the existing rollback rules for fixtures and tasks. No deploy guard is added.
- Alternatives considered: placing the region after the acceptance evidence so old first-match readers still find real lines first (contradicts the spec's placement in the change summary); a deploy-script guard (disproportionate for a hazard that needs a quoted report line plus a writer-requested round).
- Decision-bearing: no.

## write-tasks: Single task

- Decision: one task covering the shared module, the three scripts, staging, the operations doc rollback rule, unit and INT-001 to INT-006 tests, and a yellow PR item listing the accepted limitations; AR-001 and AR-002 named as overriding earlier design-stage entries.
- Alternatives considered: splitting by script (the workflow requires exactly one task).
- Decision-bearing: no.
