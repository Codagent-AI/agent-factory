## MODIFIED Requirements

### Requirement: Annotate the feature pull request by review attention

The feature pull request's description SHALL be maintained in place rather than as comments and SHALL open with a line naming the issue number and title, followed by a "Review first" section that lists every red item, then the orange items described below, one line each, from the classification defined by `factory-feature-execution`. The orange items shown SHALL be every orange item that names a commit added after acceptance, followed by the classification's most important other orange items in its order, up to five orange items in total; the orange heading SHALL state the total orange count, and, when any orange or yellow items are collapsed, the section SHALL state how many further orange items and how many yellow items are collapsed below the change summary. Each item SHALL link to its detail: a file committed on the feature branch links to that file on GitHub, at the cited line or range where the classification cites one, and detail that exists only in the attempt's evidence links to the acceptance evidence in the description, never to a path on the host. Red, orange, and yellow SHALL be visually distinct; red and orange SHALL each be shown even when empty, stating that the tier has no items. Below that section the description SHALL carry the issue reference exactly once with a closing keyword, the claim marker, a short summary of the change with links to the archived proposal, specifications, design, and test plan, followed by the archived proposal's content shown inline and expanded as defined in "Inline the proposal in the feature pull request description", the orange items beyond the first five together with every yellow item, the change's decision log together with the assumptions that assumption review left unresolved, and the acceptance evidence, with those orange and yellow items, white items, the decision log, and the unresolved assumptions collapsed by default. The acceptance evidence SHALL name the commit acceptance ran against and, when later commits exist, SHALL list every commit on the branch after it. Each red, orange, or yellow item whose linked file a later commit changed SHALL say it may be fixed by that commit. A review round SHALL NOT re-run acceptance; the evidence SHALL keep naming the commit it describes, and the round's completion comment SHALL state that acceptance was not re-run. A review round SHALL keep the description the round started with, restoring it when the round's finalization rewrote it, and SHALL then bring it up to date with the round: a section before the change summary lists the round's commits and, when the round's changes passed validation and were pushed, the feedback they addressed by source and id without the triage-time reply text, or otherwise states that the commits were not pushed, and the later commits and the items they may have fixed are refreshed as above. When the round pushes a merge of the target branch, it is listed with the round's commits, and the round's completion comment SHALL also name that merge commit as added after acceptance and not covered by the acceptance evidence, linking the commit and the acceptance evidence in the description.

#### Scenario: Review a pull request with red flags

- **WHEN** a feature attempt returns `pull-request` after an acceptance criterion could not be verified
- **THEN** the "Review first" section lists that criterion as red above every orange item

#### Scenario: Review a clean pull request

- **WHEN** a feature attempt returns `pull-request` with every criterion passed and no decision-bearing assumption
- **THEN** the description opens with the issue number and title, and the "Review first" section states that the red and orange tiers are empty and how many yellow items are collapsed
- **AND** the yellow assumptions, the passed criteria, and their evidence are collapsed below it
- **AND** the change summary below the "Review first" section shows the archived proposal's content without being collapsed

#### Scenario: Review a pull request with many orange items

- **WHEN** a feature attempt returns `pull-request` with seven orange items, one of them for commits added after acceptance, and two yellow items
- **THEN** the "Review first" section shows the commits after acceptance and the four most important other orange items, gives the orange count as seven, and states that two more orange items and two yellow items are collapsed
- **AND** the two other orange items and the yellow items are collapsed below the change summary

#### Scenario: Link review items a reviewer can open

- **WHEN** a red item's detail is in a committed specification file and an orange item's detail is only in the attempt's session evidence
- **THEN** the red item links to the cited lines of that file on the feature branch on GitHub and the orange item links to the acceptance evidence in the description

#### Scenario: Repair CI after acceptance

- **WHEN** the finalization loop pushes commits to fix CI after acceptance ran
- **THEN** those commits appear as an orange item and the evidence names the accepted commit and lists the later commits
- **AND** the orange count in the description and in the issue comment includes that item

#### Scenario: Complete a review round on a feature

- **WHEN** a review round on a feature pull request completes
- **THEN** the acceptance evidence still names the commit it describes and the completion comment states that acceptance was not re-run
- **AND** the description is the one the round started with, even when finalization rewrote it during the round, with a section listing the round's commits and, when they passed validation and were pushed, the feedback they addressed
- **AND** the acceptance evidence and the "Commits after acceptance" item list every commit after acceptance, including the round's
- **AND** an item whose linked file a round commit changed says it may be fixed by that commit

#### Scenario: A commit after classification changes an item's linked file

- **WHEN** a commit made after acceptance changes the file a red, orange, or yellow item links to
- **THEN** that item in the description says it may be fixed by that commit

#### Scenario: Complete a review round that merged the target branch

- **WHEN** a review round on a feature pull request pushes a merge of the target branch
- **THEN** the description is the one the round started with, and its section of the round's commits and its "Commits after acceptance" item list the merge commit
- **AND** the completion comment names the merge commit, linked, as added after acceptance and links the acceptance evidence in the description

## ADDED Requirements

### Requirement: Inline the proposal in the feature pull request description

The feature pull request's description SHALL show the archived change's proposal inline in the change summary, after the links to the archived artifacts and before the collapsed orange and yellow items, so that a reviewer can read the proposal without opening Files Changed or the archived files. The proposal content SHALL be shown expanded, not inside a collapsed section. The proposal's text SHALL be copied verbatim from the archived proposal: the factory SHALL NOT summarize, reword, or generate it, and SHALL change only heading levels, so that every proposal heading nests below the change summary heading. No line of the inlined proposal SHALL equal the change summary heading, so that a review round's section is inserted before the change summary and never inside the proposal.

The inlined proposal SHALL be bounded by hidden start and end markers that stay the same across review rounds and that no line of the proposal can match, even when the proposal quotes the factory's own markers. Every factory update of the description, including the marking of commits added after acceptance and a review round's update, SHALL treat the text between the markers as opaque: it SHALL NOT take the accepted commit, the commits after acceptance, the later commits, or any red, orange, or yellow item from that text, and SHALL NOT add, change, or remove text inside it except to shorten it as defined in "Keep the feature pull request description within GitHub's size limit".

When the archived proposal is missing or cannot be read, the description SHALL keep the links to the archived artifacts, SHALL state that the proposal could not be included inline, and the annotation SHALL otherwise complete as before.

A review round SHALL keep the inlined proposal of the description it started with and SHALL NOT refresh it from a proposal the round changed; it MAY only shorten it as defined in "Keep the feature pull request description within GitHub's size limit".

#### Scenario: Read the proposal in the description

- **WHEN** a feature attempt returns `pull-request` and its archived proposal has Why, What Changes, Capabilities, Technical Approach, Out of Scope, and Impact sections
- **THEN** the description's change summary shows all six sections, expanded, with their text identical to the archived proposal's
- **AND** the links to the archived proposal, specifications, design, and test plan are still shown, and the "Review first" section still comes first

#### Scenario: Nest the proposal's headings under the change summary

- **WHEN** the archived proposal's sections use second-level headings such as `## Why`
- **THEN** the description shows those headings below the level of the change summary heading, with the heading text unchanged

#### Scenario: Keep existing review context around the proposal

- **WHEN** a feature attempt returns `pull-request` with red, orange, and yellow items, a decision log, unresolved assumptions, and acceptance evidence
- **THEN** the description keeps the issue line, the "Review first" section, the closing keyword once, the claim marker, the collapsed orange and yellow items, the collapsed decision log and assumptions, and the acceptance evidence, alongside the inlined proposal

#### Scenario: A proposal quotes report lines

- **WHEN** the archived proposal contains example lines reading `Acceptance ran against`, `Later commits:`, and a `Commits after acceptance` item, and commits are added after acceptance
- **THEN** the description's acceptance evidence names the commit acceptance actually ran against and lists the commits added after it
- **AND** the "Commits after acceptance" item in the "Review first" section lists those commits
- **AND** the example lines inside the inlined proposal are unchanged

#### Scenario: A proposal quotes the factory's markers

- **WHEN** the archived proposal quotes the hidden markers that bound and divide the inlined proposal, in a fenced example and in plain text, followed by an `Acceptance ran against` line
- **THEN** the inlined proposal shows the whole proposal unchanged, and the acceptance evidence names the commit acceptance actually ran against
- **AND** when the description must be shortened, only whole sections of the original proposal are omitted

#### Scenario: A review round inserts its section

- **WHEN** a review round on a feature pull request with an inlined proposal completes
- **THEN** the round's section appears before the change summary and the inlined proposal is unchanged

#### Scenario: A review round changes the proposal

- **WHEN** a review round commits a change to the archived proposal
- **THEN** the description keeps the inlined proposal it had when the round started

#### Scenario: The archived proposal is missing

- **WHEN** the annotation runs and the archived change has no readable proposal
- **THEN** the description shows the links to the archived artifacts and a statement that the proposal could not be included inline
- **AND** the rest of the description is published as before

### Requirement: Keep the feature pull request description within GitHub's size limit

Each time the factory publishes a feature pull request's description, at annotation and at every review-round update, it SHALL check the final text it is about to publish, after every other change including the marking of commits added after acceptance and the review round's section. When that text exceeds GitHub's limit of 65,536 characters for a pull request description, the factory SHALL shorten only the inlined proposal, by omitting whole proposal sections until the description fits. It SHALL omit sections other than Why, What Changes, and Out of Scope first, last-appearing first, then Out of Scope, then What Changes, then Why, and SHALL never cut a section part way through. In place of omitted sections it SHALL state which sections were omitted and link the full archived proposal. When no whole section fits, the inlined proposal SHALL be replaced by that statement and link. The factory SHALL NOT shorten the issue line, the "Review first" section, the closing keyword, the claim marker, the orange and yellow items, the decision log, the unresolved assumptions, the acceptance evidence, or any review round's section to make room. A description that fits SHALL be published with its inlined proposal unchanged. When the description still exceeds the limit with every proposal section omitted, the factory SHALL attempt to publish it with the proposal omitted and SHALL report a failed update as it does for any other rejected description update.

#### Scenario: A large proposal fits after omitting sections

- **WHEN** a feature attempt returns `pull-request` and the description with the full proposal would exceed 65,536 characters, but fits without the Technical Approach and Impact sections
- **THEN** the published description fits within the limit, shows the Why, What Changes, Capabilities, and Out of Scope sections verbatim, and states that Technical Approach and Impact were omitted, with a link to the full proposal
- **AND** the risk items, decision log, assumptions, and acceptance evidence are complete

#### Scenario: Only the key sections fit

- **WHEN** the description fits only after every section except Why and What Changes is omitted
- **THEN** the description shows Why and What Changes verbatim and names every omitted section, with a link to the full proposal

#### Scenario: No proposal section fits

- **WHEN** the description exceeds the limit even with only the Why section inlined
- **THEN** the description shows no proposal section, states that the proposal was omitted for length, and links the full proposal

#### Scenario: A review round pushes the description over the limit

- **WHEN** a feature pull request's description fits with its full proposal, and a later review round's section and the marking of its commits would push it past 65,536 characters
- **THEN** the round publishes a description within the limit, with whole proposal sections omitted and named, and with the round's section, the commits after acceptance, and the acceptance evidence complete
- **AND** the round does not record a failure to restore the description because of its length

#### Scenario: A description within the limit is unchanged

- **WHEN** the description with the full proposal is within 65,536 characters
- **THEN** the full proposal is published and no omission statement appears

#### Scenario: The description is too large without the proposal

- **WHEN** the description exceeds 65,536 characters even with every proposal section omitted
- **THEN** the factory attempts to publish it with the proposal omitted and no report content shortened
- **AND** a rejected update is reported as a failed description update, as for any other rejected update
