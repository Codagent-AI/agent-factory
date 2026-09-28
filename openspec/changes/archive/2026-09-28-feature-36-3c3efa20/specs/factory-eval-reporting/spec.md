## MODIFIED Requirements

### Requirement: Report traceable inputs and per-repetition results

The factory SHALL post the frozen evaluation inputs and report each repetition independently. Reporting SHALL include:

- claim and suite run identities;
- suite identity;
- full Runner and Skills revisions;
- the deployed harness revision;
- the Agent Validator revision;
- accepted role and validator settings;
- repetition count;
- relevant pinned evaluation inputs.

When the claim recorded a Validator revision, reporting SHALL give its full SHA. When it did not, because the claim was admitted under Docker execution or before the factory recorded Validator revisions, reporting SHALL state that the Validator was the published npm release and was not pinned. The Project's `Refs` field SHALL display `runner@<7> skills@<7> evals@<7> validator@<7>` when the claim recorded a Validator revision, and `runner@<7> skills@<7> evals@<7>` otherwise, using the first seven characters of each recorded commit SHA. The harness revision SHALL be identified as the test environment version.

For each repetition, results SHALL include execution status, established product verdict, available automated subtotal out of 70, duration, available costs, artifact location, and recorded candidate branch and draft-PR links. Unstarted, interrupted, completed, and failed repetitions SHALL remain distinguishable. Missing scores, costs, or other result data SHALL be described as unavailable rather than zero or fabricated values. Reporting SHALL NOT present absolute results as evidence of improvement over a baseline.

#### Scenario: Report mixed repetition outcomes

- **WHEN** one repetition is ready for human review, another has a product failure, and another remains unstarted
- **THEN** the issue shows each repetition's own status, available score, evidence location, and candidate links
- **AND** the aggregate board verdict does not hide those distinctions

#### Scenario: Omit unavailable cost data honestly

- **WHEN** a completed repetition lacks cost data
- **THEN** its result reports cost as unavailable while retaining its other available results

#### Scenario: Report a pinned Validator

- **WHEN** a Fly eval claim recorded Validator revision `C`
- **THEN** the posted inputs give the full SHA `C`, and the `Refs` field ends with `validator@` followed by the first seven characters of `C`

#### Scenario: Report an unpinned Validator

- **WHEN** a claim recorded no Validator revision
- **THEN** the posted inputs state that the Validator was the published npm release and was not pinned, and the `Refs` field is `runner@<7> skills@<7> evals@<7>` as before this change
