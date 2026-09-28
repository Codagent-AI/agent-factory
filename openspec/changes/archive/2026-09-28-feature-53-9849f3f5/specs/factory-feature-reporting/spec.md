## MODIFIED Requirements

### Requirement: Annotate the feature pull request by review attention

The feature pull request's description SHALL be maintained in place rather than as comments and SHALL open with a line naming the issue number and title, followed by a "Review first" section that lists every red item, then the orange items described below, one line each, from the classification defined by `factory-feature-execution`. The orange items shown SHALL be every orange item that names a commit added after acceptance, followed by the classification's most important other orange items in its order, up to five orange items in total; the orange heading SHALL state the total orange count, and, when any orange or yellow items are collapsed, the section SHALL state how many further orange items and how many yellow items are collapsed below the change summary. Each item SHALL link to its detail: a file committed on the feature branch links to that file on GitHub, at the cited line or range where the classification cites one, and detail that exists only in the attempt's evidence links to the acceptance evidence in the description, never to a path on the host. Red, orange, and yellow SHALL be visually distinct; red and orange SHALL each be shown even when empty, stating that the tier has no items. Below that section the description SHALL carry the issue reference without a closing keyword, the claim marker, a short summary of the change with links to the archived proposal, specifications, design, and test plan, the orange items beyond the first five together with every yellow item, the change's decision log together with the assumptions that assumption review left unresolved, and the acceptance evidence, with those orange and yellow items, white items, the decision log, and the unresolved assumptions collapsed by default. The acceptance evidence SHALL name the commit acceptance ran against and, when later commits from the finalization loop exist, SHALL list them. A review round SHALL NOT re-run acceptance; the evidence SHALL keep naming the commit it describes, and the round's completion comment SHALL state that acceptance was not re-run. A review round SHALL leave the description as it was before the round, restoring it when the round's finalization rewrote it. Commits a review round pushes, including a merge of the target branch, are therefore not added to the description's orange items. The round's completion comment SHALL instead name each such merge commit as added after acceptance and not covered by the acceptance evidence, linking the commit and the acceptance evidence in the description.

#### Scenario: Review a pull request with red flags

- **WHEN** a feature attempt returns `pull-request` after an acceptance criterion could not be verified
- **THEN** the "Review first" section lists that criterion as red above every orange item

#### Scenario: Review a clean pull request

- **WHEN** a feature attempt returns `pull-request` with every criterion passed and no decision-bearing assumption
- **THEN** the description opens with the issue number and title, and the "Review first" section states that the red and orange tiers are empty and how many yellow items are collapsed
- **AND** the yellow assumptions, the passed criteria, and their evidence are collapsed below it

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
- **AND** the description is the one the round started with, even when finalization rewrote it during the round

#### Scenario: Complete a review round that merged the target branch

- **WHEN** a review round on a feature pull request pushes a merge of the target branch
- **THEN** the description is the one the round started with and its orange items are unchanged
- **AND** the completion comment names the merge commit, linked, as added after acceptance and links the acceptance evidence in the description
