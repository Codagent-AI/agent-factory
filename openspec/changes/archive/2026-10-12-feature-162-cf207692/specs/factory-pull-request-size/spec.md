## ADDED Requirements

### Requirement: Count the code and test lines a pull request changes

The factory SHALL compute a pull request's size with a script, not with a model estimate. The script SHALL diff the pull request's published head commit against the merge base of that head and the pull request's base branch, both as GitHub reports them, using a zero-context diff with rename detection (`-M`, Git’s default similarity threshold, with an unlimited rename limit via `-l0`). Commits merged into the pull request branch from the target branch SHALL therefore not be counted, and commits that exist only in the local checkout and were never pushed SHALL NOT be counted. For each diff hunk that replaces `old` lines with `new` lines, `min(old, new)` lines SHALL count as modified, `new - min(old, new)` as added, and `old - min(old, new)` as deleted. For each kind (code and tests) the script SHALL report the number of distinct files with at least one counted hunk; the added, modified, and deleted line totals; and the GitHub-style totals for the same files, where `+` is added plus modified and `−` is deleted plus modified. A new file SHALL count all of its lines as added and a deleted file all of its lines as deleted. A pure rename SHALL contribute no lines or files. A rename with edits SHALL count only its edited lines, classified by its new path. A file is binary when either its old or its new version contains a NUL byte in its first 8000 bytes. A binary file, or a change only to the file mode, SHALL contribute neither lines nor a file to the count. The same pull request state SHALL always produce the same counts. Git configuration (system, global, or repository) and Git attributes (committed, uncommitted, or local) in the checkout where the count runs SHALL NOT change the counts.

#### Scenario: A hunk that replaces lines

- **WHEN** a code file's only change is one hunk that replaces 3 lines with 5 lines
- **THEN** the code row shows 1 file, 2 added, 3 modified, and 0 deleted lines, and `+5 −3`

#### Scenario: A hunk that removes more than it adds

- **WHEN** a code file's only change is one hunk that replaces 4 lines with 1 line
- **THEN** the code row shows 1 file, 0 added, 1 modified, and 3 deleted lines, and `+1 −4`

#### Scenario: New and deleted files

- **WHEN** a pull request adds a 20-line code file and deletes a 7-line code file
- **THEN** the code row counts 2 files, 20 added and 7 deleted lines, and `+20 −7`

#### Scenario: Target-branch merge into the pull request branch

- **WHEN** a feature pull request branch has merged 400 changed code lines from the target branch and its own commits change 10 code lines
- **THEN** only the pull request's own 10 lines are counted

#### Scenario: Local commits that were not pushed

- **WHEN** finalization or a review round fails and leaves local commits that the pull request's published head does not contain
- **THEN** the count covers only the published head, and the unpushed commits are not counted

#### Scenario: Binary file

- **WHEN** a pull request changes only a binary file in a code path
- **THEN** the code row shows 0 files and 0 lines

#### Scenario: Local Git settings and attributes do not change the count

- **WHEN** the same published pull request is counted in a checkout whose Git configuration sets a non-zero inter-hunk context or another diff algorithm, or whose attributes (committed, uncommitted, or local) mark a text code file as `-diff` or `binary`
- **THEN** the counts are identical to those from a checkout with default settings, and the text file's lines are counted

### Requirement: Classify changed files as excluded, tests, or code

Each changed file SHALL be classified by its repository-relative path, using its new path for a rename. The exclusion patterns SHALL be an ordered mapping defined next to the test patterns in one place. Each category SHALL match either the full path or the base name, in this order: `OpenSpec changes` (`openspec/changes/*`), `OpenSpec specs` (`openspec/specs/*`), `docs` (`docs/*`, `*.md`, `*.mdx`, `*.txt`, `*.rst`, `LICENSE*`), then `config/data` (`*.yaml`, `*.yml`, `*.toml`, `*.json`, `*.ini`, `*.cfg`, `*.lock`, `go.sum`, `go.mod`, `.github/*`, `.validator/*`, `.claude/*`, `testdata/*`, `openspec/*`). The first matching category SHALL win, and its lines SHALL be counted separately from code and tests. Otherwise, a file whose full path or base name matches a test pattern SHALL count as tests, and every other file SHALL count as code. The test patterns SHALL initially be `*_test.go`, `tests/*`, `*/tests/*`, `test_*.py`, `*_test.py`, `*.test.ts`, and `*.spec.ts`. In these patterns `*` matches any characters, including `/`.

#### Scenario: Specifications, docs, and configuration are excluded

- **WHEN** a pull request changes `openspec/changes/x/proposal.md`, `docs/operations.md`, `config/codagent.toml`, `uv.lock`, and `.github/workflows/ci.yml`
- **THEN** none of those files counts as code or tests

#### Scenario: Test files by name and directory

- **WHEN** a pull request changes `runner/step_test.go`, `tests/unit/test_counter.py`, `src/pkg/tests/helpers.py`, and `web/app.spec.ts`
- **THEN** each of those files counts as tests

#### Scenario: Exclusion wins over the test patterns

- **WHEN** a pull request changes `tests/fixtures/data.json`
- **THEN** the file is excluded, not counted as tests

#### Scenario: Other source files are code

- **WHEN** a pull request changes `src/agent_factory/controller.py` and `scripts/deploy.sh`
- **THEN** both files count as code

### Requirement: Render the lines-of-code section

The pull request description SHALL carry exactly one "Lines of code" section, delimited by the stable markers `<!-- agent-factory:loc -->` and `<!-- agent-factory:loc-end -->`. Only a line consisting of exactly a marker, outside any fenced code block, SHALL count as a section delimiter. A marker quoted inline in text or shown inside a fenced code block is literal text and SHALL be left unchanged. The section SHALL contain a table with the columns kind, files, added, modified, deleted, and +/-, and exactly two rows in that table, `code` and `tests`, even when their counts are zero. Below it, a second table SHALL contain the columns part, files, and +/-, with one row for each excluded category in classification order, including zero rows, followed by a total row summing code, tests, and all excluded categories. Each +/- SHALL use added plus modified and deleted plus modified. The total SHALL equal the summed added and deleted columns of `git diff --numstat -M <merge-base> <head>` with the same diff algorithm options, omitting binary files, gitlinks, mode-only changes, and pure renames from every row. A note SHALL state that excluded parts are listed separately and do not count as code or tests. The section SHALL name the base and head commits that were measured. Refreshing the section SHALL replace everything between the markers and leave the rest of the description unchanged. When the markers are absent, the refresh SHALL add the section. When an earlier refresh left more than one marked section, the refresh SHALL leave exactly one.

#### Scenario: A pull request that changes only excluded files

- **WHEN** a pull request changes only OpenSpec artifacts and Markdown documentation
- **THEN** its description shows the section with both rows at 0 files and 0 lines, rather than omitting the section

#### Scenario: Refresh after new commits

- **WHEN** the section is refreshed after new commits are pushed to the pull request
- **THEN** the description contains one section, with counts and a head commit for the new published head
- **AND** the text outside the markers is unchanged

#### Scenario: Marker examples in the description are preserved

- **WHEN** a description embeds a decision log that quotes the markers inline and shows them in a fenced code block, and the section is refreshed
- **THEN** the description has exactly one live section, and the quoted and fenced marker examples are unchanged

#### Scenario: Section removed by a rewrite

- **WHEN** finalization rewrote the description without the section, and the section is then refreshed
- **THEN** the description again contains exactly one section


#### Scenario: Excluded parts are broken down in order

- **WHEN** a pull request edits `openspec/changes/archive/x/tasks.md`, `openspec/specs/x/spec.md`, `docs/x.md`, `config/codagent.toml`, `openspec/config.yaml`, and `tests/README.md`
- **THEN** the breakdown counts them under OpenSpec changes, OpenSpec specs, docs, config/data, config/data, and docs respectively, without counting any as code or tests

#### Scenario: Total includes every counted part

- **WHEN** a pull request changes code, tests, every excluded category, and a binary file
- **THEN** the total files and +/- equal the sum of all six parts, and its +/- equals the summed numeric numstat columns with rename detection enabled
- **AND** the binary file contributes neither lines nor files

#### Scenario: Pure rename

- **WHEN** a pull request renames a file without editing it
- **THEN** it contributes 0/0 and no file to any row

#### Scenario: Rename with edits between categories

- **WHEN** a pull request renames a code file to `tests/test_a.py` and replaces one line
- **THEN** the tests row counts one modified line and one file, with +1 −1, and the old code path contributes nothing

### Requirement: Report an unavailable count without blocking the pull request

When the count cannot be computed (for example, when the pull request's published head or base branch cannot be read or fetched, or when the diff fails), the section SHALL state that the count is unavailable and give the reason, instead of showing a table of guessed or partial numbers. A count failure SHALL NOT change the attempt's or round's outcome, and SHALL NOT prevent the pull request from being opened or its description from being updated.

#### Scenario: Base branch cannot be fetched

- **WHEN** fetching the pull request's base branch fails during a refresh
- **THEN** the section says the lines-of-code count is unavailable and gives that reason
- **AND** the attempt's outcome is the same as it would have been with a successful count
