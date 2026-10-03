## MODIFIED Requirements

### Requirement: Report traceable inputs and per-repetition results

The factory SHALL post the frozen evaluation inputs and report each repetition independently. Reporting SHALL include:

- claim and suite run identities;
- suite identity;
- full Runner and Skills revisions;
- the deployed harness revision;
- the Agent Validator revision;
- the requested fixture ref and the full fixture revision, when the claim recorded one;
- accepted role and validator settings;
- repetition count;
- relevant pinned evaluation inputs.

When the claim recorded a Validator revision, reporting SHALL give its full SHA. When it did not, because the claim was admitted under Docker execution or before the factory recorded Validator revisions, reporting SHALL state that the Validator was the published npm release and was not pinned.

When the claim recorded a fixture revision:

- the posted frozen inputs SHALL state, outside the raw input listing, the requested fixture ref and the full fixture SHA, and that the fixture was selected by the request instead of the harness's pin;
- each repetition's report SHALL give the full fixture SHA.

When the claim recorded no fixture revision, the posted frozen inputs and repetition reports SHALL be exactly as before this change and SHALL NOT add any fixture statement.

The Project's `Refs` field SHALL display `runner@<7> skills@<7> evals@<7>`, followed by ` validator@<7>` when the claim recorded a Validator revision, followed by ` fixture@<7>` when the claim recorded a fixture revision. Each `<7>` is the first seven characters of the recorded commit SHA. The harness revision SHALL be identified as the test environment version.

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
- **THEN** the posted inputs give the full SHA `C`, and the `Refs` field contains `validator@` followed by the first seven characters of `C`

#### Scenario: Report an unpinned Validator

- **WHEN** a claim recorded no Validator revision and no fixture revision
- **THEN** the posted inputs state that the Validator was the published npm release and was not pinned, and the `Refs` field is `runner@<7> skills@<7> evals@<7>` as before this change

#### Scenario: Report a pinned fixture

- **WHEN** a Fly eval claim recorded Validator revision `C`, requested fixture ref `eval/fixture-sonnet-validator`, and fixture revision `b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a`
- **THEN** the posted inputs state the requested ref `eval/fixture-sonnet-validator` and the full SHA `b83deca4d3a8be7f70c97e6eabc25b79b6edeb2a`, and that the request selected the fixture instead of the harness's pin
- **AND** the `Refs` field ends with `validator@<first seven of C> fixture@b83deca`
- **AND** each repetition report gives the full fixture SHA

#### Scenario: Report a pinned fixture without a Validator revision

- **WHEN** a Docker eval claim recorded fixture revision `F` and no Validator revision
- **THEN** the `Refs` field is `runner@<7> skills@<7> evals@<7> fixture@<7>`, using the first seven characters of `F`

#### Scenario: Report a default fixture

- **WHEN** a claim recorded no fixture revision
- **THEN** its posted inputs, repetition reports, and `Refs` field contain no fixture statement and are identical to what the factory reported before this change
