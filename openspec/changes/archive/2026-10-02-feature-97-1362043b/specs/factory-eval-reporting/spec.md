## ADDED Requirements

### Requirement: Report frozen inputs identically across releases

For every claim, this release's reporting SHALL be identical to what the previous release (the
release that introduced `fixture_ref`) reports for the same claim. This covers:

- the posted frozen-inputs comment, including the raw input listing, the Agent Validator line,
  and the fixture statement;
- each repetition's report, including the fixture line;
- the Project `Refs` field;
- the event recorded when a claim's saved revisions are missing or invalid.

This holds for claims without optional revisions, claims with a Validator revision, claims with a
fixture revision, claims with both, and claims admitted before the factory recorded Validator
revisions. Neither a deploy of this release nor a rollback from it SHALL change any of these for
an existing claim.

#### Scenario: Report a claim with both optional revisions

- **WHEN** a Fly eval claim recorded Validator revision `C`, requested fixture ref
  `eval/fixture-sonnet-validator`, and fixture revision `F`
- **THEN** its frozen-inputs comment, repetition reports, and `Refs` field
  (`runner@<7> skills@<7> evals@<7> validator@<7> fixture@<7>`) are byte-identical to those the
  previous release posts for the same claim

#### Scenario: Report a default claim

- **WHEN** a claim recorded no Validator revision and no fixture revision
- **THEN** its frozen-inputs comment states that the Validator was the published npm release and
  was not pinned, contains no fixture statement, and is byte-identical to the previous release's
  comment, and its `Refs` field is `runner@<7> skills@<7> evals@<7>`

#### Scenario: Report invalid saved revisions

- **WHEN** a claim's saved revisions lack a Runner, Skills, or harness revision, or record a
  Validator or fixture revision that is not a full commit SHA
- **THEN** no `Refs` text is produced, and the claim records the same invalid-revisions event,
  naming the same revisions in the same order, as the previous release records
