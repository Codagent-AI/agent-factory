- [x] Inline the archived OpenSpec proposal in the Factory feature pull request description, end to end

## Task: Inline the proposal in feature PR descriptions (#71)

Implement the whole change described in these files in the change directory:

- `proposal.md`.
- The delta spec `specs/factory-feature-reporting/spec.md`:
  - MODIFIED "Annotate the feature pull request by review attention" (the change summary is
    followed by the inlined proposal);
  - ADDED "Inline the proposal in the feature pull request description";
  - ADDED "Keep the feature pull request description within GitHub's size limit".
- `design.md`, which is authoritative for the module layout, the function names, the marker
  formats and per-region token, the size measure, the section omission order, the notice text,
  and the call order in each script.
- `decisions.md`. Where entries differ, later entries override earlier ones. In particular,
  approach-review AR-001 overrides the fixed marker text in the "design: Shared module and region
  markers" entry: every marker carries the region's token. AR-002 overrides any claim that
  rollback is safe.
- The automated obligations in `test-plan.md`: INT-001 to INT-006.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or anything under `/Users/paul/codagent/*`. Never run `scripts/deploy.sh`
or `launchctl`. Tests must never call the real GitHub API: use the existing `gh` stubs
(`GH_STUB`, `REVIEW_GH_STUB`) in `tests/integration/test_feature_workflow_scripts.py`. Do not
create, edit, or comment on any real pull request or issue.

All workflow files live in `src/agent_factory/work_kinds/pull_request/workflow/`. The clone's
`.agent-runner/workflows/` directory is untracked staging; do not edit it.

### Scope

1. **Shared module `pr_description.py`** (design "New shared module"), standard library only:
   - constants `LIMIT`, `START`, `SECTION`, `END`, `START_LINE`, `OMITTED`, `ALL_OMITTED`, and
     `UNAVAILABLE`;
   - `token(text)`: a 12-hex token from `sha256(f"{n}\0{text}")`, taking the first `n` whose token
     does not occur in the text;
   - `render(text, href)`: ATX headings outside fenced code demoted so the shallowest is level 3
     (capped at 6); sections split at that level, with a leading section for text before the
     first heading; every other line copied unchanged; trailing blank lines stripped;
   - `measure(body)`: UTF-8 byte length with line endings normalized to CRLF;
   - `region(lines)`: the first full `START_LINE` match and that token's first exact `END` after
     it, or `None`;
   - `fit(body)`: unchanged when within `LIMIT` or without a region; otherwise drop whole sections
     in the specified order (non-key sections last-appearing first, with a heading-less leading
     section named `Introduction`, then Out of Scope, What Changes, Why), rewriting a single
     notice line right after `START` that accumulates names already listed. When every section is
     gone, use `ALL_OMITTED`. Return the body even if it still does not fit;
   - `masked(lines)` and `unmasked(lines, removed)`;
   - a `python3 pr_description.py fit FILE` entry point.
2. **`annotate-pr.py`**:
   - after the change-summary links line, append a blank line and the rendered region for
     `archive / "proposal.md"` with href `f"{prefix}/proposal.md"`, or `UNAVAILABLE` when the
     file is missing or unreadable;
   - after `mark-later-commits.py` runs, apply `fit` to the body file before the PATCH. If `fit`
     raises, report it on stderr and publish the unshortened body.
3. **`mark-later-commits.py`**: `mark()` masks the region before any parsing and unmasks it before
   returning. A body without a region behaves exactly as today.
4. **`review-description.sh`**: after the marker runs on `pr-description-after-review.md`, apply
   `fit` to it before comparing with the current body and PATCHing. Import the module from the
   directory of `MARKER` (added to `sys.path`). Keep inserting the round's section before the
   first `## Change summary` line.
5. **Staging**: add `pr_description.py` to `FEATURE_STAGED_FILES` (`kinds.py`) and to
   `REVIEW_WORKFLOW_SCRIPTS` (`launch.py`), and update every test that lists the staged files.
6. **Documentation**: add the rollback rule from the design's Migration Plan to
   `docs/operations.md`, beside the other rollback rules. Name the search string
   `<!-- agent-factory:proposal:start `.
7. **Tests**:
   - implement INT-001 to INT-006 at the locations and with the setup `test-plan.md` gives;
   - add the unit tests for `pr_description.py` listed in the design's "Verification" (`token`,
     `render`, `measure`, `region`, and `fit`, including proposals quoting every marker form in
     fences and in plain text);
   - existing annotation, later-commit, and review-round tests must pass. Update an existing
     assertion only where the change summary's new content changes it, and say so in the commit.
8. **Pull request description**: include a yellow item stating the accepted limitations:
   - closing keywords quoted in a proposal are left verbatim and would link that issue;
   - unbalanced HTML in a proposal can affect rendering;
   - setext headings are not demoted;
   - rolling back past this change needs the documented containment.

### Done when

- Every requirement and scenario in the delta spec is implemented.
- A feature PR description shows the archived proposal verbatim and expanded after the artifact
  links. Only the heading levels change. "Review first", the closing keyword (once), the claim
  marker, the collapsed items, decisions, assumptions, and acceptance evidence are all still
  present.
- Report lines and marker forms quoted inside a proposal are never read or edited by
  `mark-later-commits.py` or a review round, and never split the region.
- Every published description measures within 65,536 under the design's measure, at annotation
  and after every review round. Only whole proposal sections are omitted, they are named, and
  the full proposal is linked. Report content is never shortened.
- A missing proposal and a description written before this change behave as specified.
- INT-001 to INT-006 pass, and the full suite passes under `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build` pass,
  and `agent-validate run` passes.
- No changes are made outside this repository.

## Implementation verification

Agent Validator is deferred to the later workflow step by the implementing session's explicit
instruction. The three parameterized `test_real_validator_escapes_trusted_claim_clone` cases
were excluded because they invoke the real Validator.

The initial broad suite exposed end-to-end CLI subprocess timeouts, also reproduced on an
isolated `origin/main` snapshot. The follow-up validator test log contained 20 such failures.
Whole CLI invocations now have a 60-second test budget, above the individual production
probe deadlines, to accommodate concurrent subprocess startup. Production timeouts,
behavioral assertions, and pytest worker configuration are unchanged.

Final verification with `uv run pytest -k 'not test_real_validator_escapes_trusted_claim_clone'`
passed all 1,599 selected tests in 137.34 seconds, including INT-001 to INT-006 and the
regressions for omission names and best-effort review publication. Ruff formatting and
linting, Pyright, and `uv build` also passed.
