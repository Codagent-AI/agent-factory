## ADDED Requirements

### Requirement: Refresh the lines-of-code section after a review round

When a review round on a fix or feature pull request runs its implementation step, the workflow SHALL refresh the description's "Lines of code" section, as defined by `factory-pull-request-size`, after the step ends, whether or not the round pushed. The refreshed section SHALL count the pull request's published head at that time. On a feature pull request, the refresh SHALL be part of bringing the restored description up to date with the round. When the description saved before the round is missing or empty, the refresh SHALL still run and SHALL replace or add only the section in the current description, without rebuilding the rest. A refresh failure SHALL be reported in the round's reply and SHALL NOT change the round's outcome. On a fix pull request, it SHALL replace or add only the section and leave the rest of the current description unchanged. A review round on a task pull request SHALL NOT add or change the section. A round that skips its implementation step SHALL leave the description unchanged.

#### Scenario: Fix review round pushes changes

- **WHEN** a review round on a fix pull request pushes new commits
- **THEN** the description's lines-of-code section reflects the new published head, and the rest of the description is unchanged

#### Scenario: Feature review round pushes changes

- **WHEN** a review round on a feature pull request pushes new commits, including a merge of the target branch
- **THEN** the restored description contains one lines-of-code section for the new published head
- **AND** the merged target-branch changes are not counted

#### Scenario: Feature round whose description could not be saved

- **WHEN** saving a feature pull request's description before the round fails, or saves an empty body, and the round then pushes new commits
- **THEN** the current description gains or updates exactly one lines-of-code section for the new published head, and the rest of it is left unchanged

#### Scenario: Review round fails before pushing

- **WHEN** a review round's validator stays red, so nothing is pushed
- **THEN** the refreshed section still counts the pull request's published head and none of the round's unpushed commits

#### Scenario: Task review round

- **WHEN** a review round on a task pull request pushes new commits
- **THEN** the description gains no lines-of-code section
