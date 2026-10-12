# Decisions

## propose: Verdict go

- **Decision:** Proceed (go). The issue gives a verified prototype, and PR descriptions are already rendered by deterministic workflow scripts.
- **Alternatives considered:** no-go (rely on GitHub +/-); go with caveats on model-written fix descriptions (folded into the approach as replace-after-finalize).
- **Decision-bearing:** no

## propose: Scope to fix and feature PRs; task PRs excluded

- **Decision:** Add the section to fix and feature PRs and their review rounds only. Task (chore) PRs are out of scope.
- **Alternatives considered:** also cover task PRs through `annotate-chore-pr.sh`.
- **Rationale:** the issue body and acceptance criteria name "fix and feature" explicitly, although the task kind already existed when the issue was filed. Task descriptions run under the task guard.
- **Decision-bearing:** yes

## propose: A single workflow script owns the patterns

- **Decision:** `count-loc.py` in `work_kinds/pull_request/workflow/` holds the exclusion and test patterns as constants. Other scripts call it.
- **Alternatives considered:** shared factory configuration (`config/codagent.toml`); per-target configuration.
- **Rationale:** the issue asks for "one place, easy to tune". The scripts run inside the target checkout, which has no access to the factory configuration, and per-target tuning was not requested.
- **Decision-bearing:** no

## propose: Merge base from the PR's base branch on origin

- **Decision:** Measure `git merge-base origin/<PR base> HEAD`..HEAD after fetching the base ref.
- **Alternatives considered:** the admission-frozen target commit; local `main`.
- **Rationale:** the issue specifies `merge-base(target, head)`. The PR's base is what reviewers compare against, and the merge base excludes target merges into feature branches.
- **Decision-bearing:** no

## propose: Marker-delimited section, insert or replace, best effort

- **Decision:** The section sits between `<!-- agent-factory:loc -->` and `<!-- agent-factory:loc-end -->` and is replaced in place on every refresh. A computation failure renders an "unavailable" note and never changes the attempt outcome.
- **Alternatives considered:** append on each update; fail the attempt when counting fails.
- **Rationale:** a model-written fix description or a review round's finalization can rewrite the body. Replacing in place keeps exactly one current copy, and the existing description annotation is best effort.
- **Decision-bearing:** no

## propose: Fix review rounds refresh the section

- **Decision:** `review-description.sh` refreshes the section for fix PRs as well as feature PRs after a round that pushes. Task rounds are unchanged.
- **Alternatives considered:** refresh feature PRs only (the current description-restore scope).
- **Rationale:** the acceptance criteria require a refresh "when a review round or later attempt updates the PR" for fix and feature PRs alike.
- **Decision-bearing:** no

## proposal-review: PR-001 (measure the published PR head): applied

- **Decision:** Measure `merge-base(origin/<PR base>, <published headRefOid>)..<published head>`, using the base and head as GitHub reports them, and fetch them when needed. Use local HEAD only when it equals the published head. If the objects cannot be obtained, show the "count unavailable" note. Tests cover a failed finalization and a failed review round that leave local HEAD ahead of the PR.
- **Alternatives considered:** local HEAD (rejected: it can include unpushed repair or review commits after a failed finalization or round).
- **Decision-bearing:** no. This refines the earlier merge-base decision within the issue's stated intent.

## proposal-review: PR-002 (explicit staging registration): applied

- **Decision:** Register `count-loc.py` in the fix and feature kinds' `staged_files` (`kinds.py`) and in `REVIEW_WORKFLOW_SCRIPTS` (`launch.py`). Name this in Impact, and add a staged-catalog integration check for fix, feature, and review attempts.
- **Alternatives considered:** stage the whole workflow directory (rejected: it changes the existing explicit staging approach).
- **Decision-bearing:** no

## spec: Modified capabilities expressed as ADDED requirements

- **Decision:** Add new requirements to `factory-feature-reporting`, `factory-fix-reporting`, and `factory-review-execution`. Do not modify their existing requirement blocks.
- **Alternatives considered:** a MODIFIED copy of "Annotate the feature pull request by review attention" with the section inlined.
- **Rationale:** the behavior is additive. A separate requirement keeps the large annotation requirement stable and the lines-of-code behavior testable on its own.
- **Decision-bearing:** no

## spec: Section format details

- **Decision:** The heading is "Lines of code". The table has the columns kind, files, added, modified, deleted, and +/-, and always two rows (`code`, `tests`). The +/- cell uses `+N −M`, with the minus sign from the issue's example. The section names the measured base and head commits. The markers are `<!-- agent-factory:loc -->` and `<!-- agent-factory:loc-end -->`. If duplicate sections exist, a refresh collapses them to one.
- **Alternatives considered:** omit zero rows (rejected by the issue's acceptance criteria); omit the measured commits.
- **Decision-bearing:** no

## spec: File counting and edge cases

- **Decision:** "files" is the number of distinct paths with at least one counted hunk. Binary files and mode-only changes contribute no file and no lines. Renames count as a delete plus an add. Exclusion is checked before the test patterns. Patterns match the full path or the base name, and `*` crosses `/`. These follow the issue's prototype.
- **Alternatives considered:** count binary files as changed files; let test classification win over exclusion.
- **Decision-bearing:** no

## spec: Review-round refresh trigger and placement

- **Decision:** Refresh whenever a fix or feature review round runs its implementation step, even if nothing was pushed, because the count always measures the published head. Rounds that skip implementation leave the description unchanged. Task rounds never add the section. In a feature description the section goes in the change summary below the artifact links. In a fix description it goes below the factory header.
- **Alternatives considered:** refresh only after a successful push; refresh on every round, including answer-only rounds.
- **Decision-bearing:** no

## design: Script interface and invocation

- **Decision:** `count-loc.py` has the modes `section`, `refresh-file`, and `refresh-pr` (the last also takes stdin JSON for `script:` steps). It is invoked as a subprocess from `annotate-pr.py` and `review-description.sh`, and as a `script:` step in the fix workflow. Tests load it with importlib.
- **Alternatives considered:** an importable `count_loc.py` module imported by callers; an inline Python step in the YAML.
- **Rationale:** this matches the existing `mark-later-commits.py` and `task-compliance-gate.py` patterns. `script:` steps resolve in the Docker sandbox catalog, while `.agent-runner/workflows` paths do not.
- **Decision-bearing:** no

## design: State-machine diff parser

- **Decision:** Treat `---`/`+++` as file headers only before a file's first `@@`. Unquote C-quoted paths. Force the diff format with `--src-prefix/--dst-prefix`, `--no-ext-diff`, `--no-textconv`, `--no-color`, and `core.quotePath=false`.
- **Alternatives considered:** the issue's prototype parser as is (it miscounts content lines that start with `++ ` or `-- `).
- **Decision-bearing:** no

## design: Fetch base each refresh; failure is unavailable

- **Decision:** Fetch `origin/<base>` on each refresh, and fetch the head SHA only if it is missing. Any failure renders the unavailable form.
- **Alternatives considered:** fall back to an existing stale `origin/<base>` ref.
- **Rationale:** the spec makes a failed fetch an unavailable count. A fresh base keeps the merge base correct after target merges.
- **Decision-bearing:** no

## design: Fix review rounds use refresh-pr; feature rounds use refresh-file inside the restore

- **Decision:** `review-description.sh` dispatches by kind: task exits, fix runs `refresh-pr` in restore mode, and feature adds `refresh-file` in `rebuilt()`. A fix refresh failure writes `description-restore-failed` so the round's reply reports it.
- **Alternatives considered:** a separate review-workflow step for every kind.
- **Rationale:** the restore step already has the right trigger (`push_round`) and already owns writing the description.
- **Decision-bearing:** no

## test-plan: No automated E2E; integration obligations through packaged scripts

- **Decision:** Six integration obligations use real git with bare remotes and a `gh` stub, plus the staged-catalog wiring checks. There is no new E2E test.
- **Alternatives considered:** extend the `tests/e2e` cycle tests (they stub the workflow, so they would not exercise this change); a live Runner run (needs model sessions and live GitHub, which is not deterministic).
- **Decision-bearing:** no

## test-plan: Acceptance may read real GitHub PRs but never write

- **Decision:** The exploratory pass may run `count-loc.py section` read-only against agent-runner PRs #232, #242, and #239 to compare with the issue's reference numbers. All description writes go through a `gh` stub. The live service, deploys, and pushes are off limits.
- **Alternatives considered:** fully stubbed acceptance (loses the issue's real-world reference check).
- **Decision-bearing:** no

## approach-review: AR-001 (deterministic diff policy): applied

- **Decision:** Fix every option that shapes hunks on the command line (`--inter-hunk-context=0 --diff-algorithm=myers --indent-heuristic`). Ignore system and global config and the global attributes file. Pass `--text` so attributes cannot hide text files. Detect binary files by content: a NUL byte in the first 8000 bytes of either blob. The spec now states that local Git config and attributes do not change the count. INT-001 is extended with conflicting config and attributes. This was verified with git 2.50: `diff.interHunkContext=5` merged two one-line hunks into one five-line hunk, and `--inter-hunk-context=0` and `--text` restored the expected hunks.
- **Alternatives considered:** `--attr-source=<head>` so that committed attributes are honored (still affected by `.git/info/attributes`, and needs git 2.40 or later); an isolated temporary repository (heavier, and still needs a policy for committed attributes).
- **Decision-bearing:** no. A committed `-diff` on a text file is now counted, which is consistent with "content decides" and recorded under the design's Risks.

## approach-review: AR-002 (live delimiters only): applied

- **Decision:** Only a line consisting of exactly a marker, outside fenced code blocks, is a section delimiter. Inline and fenced marker examples (for example in this change's own `decisions.md`, which `annotate-pr.py` embeds) are preserved byte-for-byte. Added to the spec, the design, the unit cases, and INT-004 and INT-005.
- **Alternatives considered:** change the marker text so it is unlikely to be quoted (fragile: any decision log about the feature quotes it).
- **Decision-bearing:** no

## approach-review: AR-003 (refresh independent of the saved description): applied

- **Decision:** When a feature round's saved description is missing or empty, `review-description.sh restore` runs `refresh-pr` on the current published description. It replaces or adds only the section and does not rebuild acceptance content. A failure writes `description-restore-failed`, and the round's outcome is unchanged. Added a review-execution scenario and INT-005 cases for a failed save and an empty save.
- **Alternatives considered:** make `save` mandatory (it would turn a transient GitHub error into a failed round).
- **Decision-bearing:** no

## tasks: One implementation task; operations note included

- **Decision:** One task covers the script, the three wiring points, staging, tests, and a short note in `docs/operations.md`.
- **Alternatives considered:** no documentation change (the issue asks to note the exclusions; the PR section itself states them, and the operations note helps an operator tuning the patterns).
- **Decision-bearing:** no
