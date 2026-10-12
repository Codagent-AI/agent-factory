## ADDED Requirements

### Requirement: Report the lines-of-code count in the fix pull request description

After finalization on every fix attempt that opens or updates a pull request, the factory SHALL refresh the description's "Lines of code" section, as defined by `factory-pull-request-size`. This SHALL happen even when finalization wrote or rewrote the description, and even when the factory header (the issue reference and claim marker) was already present. When the section is added, it SHALL be placed below that header. The rest of the description, as finalization wrote it, SHALL be left unchanged.

#### Scenario: Open a fix pull request

- **WHEN** a fix attempt opens a pull request
- **THEN** its description carries the issue reference, the claim marker, and one lines-of-code section for the published head

#### Scenario: Later fix attempt updates the pull request

- **WHEN** a later attempt on the same issue pushes to the existing pull request and finalization rewrites its description
- **THEN** the description again contains exactly one lines-of-code section, counted at the new published head

#### Scenario: Finalization fails after some commits

- **WHEN** finalization fails and the local branch holds commits that were never pushed
- **THEN** the section counts only the pull request's published head
