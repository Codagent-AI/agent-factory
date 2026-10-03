## ADDED Requirements

### Requirement: Keep frozen evaluation inputs stable across deploys

A deploy or rollback SHALL NOT change a claim's frozen inputs or the reports rendered from them.

#### Scenario: Continue a claim across a deploy or rollback

- **WHEN** a claim with unfinished repetitions is continued after the factory is deployed or
  rolled back
- **THEN** its recorded settings, revisions, sources, and request fingerprint are unchanged, and
  its `Refs` field, frozen-inputs comment, and repetition reports are the same as before
