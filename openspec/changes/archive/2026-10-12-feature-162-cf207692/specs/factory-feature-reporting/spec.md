## ADDED Requirements

### Requirement: Report the lines-of-code count in the feature pull request description

Each time a feature attempt annotates its pull request description, including on every resume that re-annotates it, the description SHALL include the "Lines of code" section defined by `factory-pull-request-size`, placed in the change summary below the links to the archived artifacts, and computed for the pull request's published head at that time. The section SHALL NOT add red, orange, yellow, or white review items, and SHALL NOT change the counts of the "Review first" section.

#### Scenario: Open a feature pull request

- **WHEN** a feature attempt returns `pull-request`
- **THEN** the description's change summary contains the lines-of-code section for the pull request's published head

#### Scenario: Resume a feature that updates its pull request

- **WHEN** a later attempt on the same feature pushes new commits and re-annotates the pull request
- **THEN** the description contains one lines-of-code section, counted at the new published head

#### Scenario: Feature with only definition artifacts and docs

- **WHEN** a feature pull request changes only OpenSpec artifacts and documentation
- **THEN** the section shows zero code and zero tests
