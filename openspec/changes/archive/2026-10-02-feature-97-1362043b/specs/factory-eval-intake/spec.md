## ADDED Requirements

### Requirement: Keep frozen evaluation inputs compatible across releases

A release SHALL accept the same eval requests, and freeze them in the same form, as the release
before it (the release that introduced `fixture_ref`). This covers each of these, for every
combination of revisions a claim can record (Runner, Skills, and harness, plus a Validator
revision, a fixture revision, or both):

- the set of accepted eval-block keys, and the messages that reject an unsupported key or an
  invalid ref value;
- the request fingerprint that decides whether an edited request continues the existing claim
  or is a fresh request;
- the claim's recorded settings, revisions, and Validator source, including their names and
  their order;
- the "waiting for revision readiness" reason for each revision that cannot be resolved.

A claim that either release admitted SHALL be continued by the other without being rejected,
re-admitted, or having its frozen inputs changed. Each repetition, retry, and resume SHALL invoke
the suite with the same arguments, and under Fly execution with the same revision, repository,
and Validator-source entries in its execution manifest. This applies equally after deploying this
release over unfinished claims and after rolling back to the previous release.

The `agent-factory honored-revisions` command SHALL print the same revision names, in the same
order, as the previous release: `runner`, `skills`, `evals`, `validator`, `fixture`. The deploy
fixture guard SHALL behave exactly as before.

#### Scenario: Continue a claim admitted by the previous release

- **WHEN** a claim that the previous release admitted with a Validator revision and a fixture
  revision has unfinished repetitions, and this release is deployed
- **THEN** its next repetition launches with the same frozen inputs, the same suite arguments
  (including `--fixture-ref <SHA> --repo https://github.com/Codagent-AI/and-scene.git`), and the
  same Fly execution manifest revision entries that the previous release would have used

#### Scenario: Roll back with a claim admitted by this release

- **WHEN** this release admits a claim with a fixture revision, and the factory is later rolled
  back to the previous release while the claim has unfinished repetitions
- **THEN** the previous release continues the claim with its recorded inputs, and its reports are
  the same as if the previous release had admitted it

#### Scenario: Freeze the same inputs for the same request

- **WHEN** the same eval request body is admitted under the same configuration and branch
  positions by this release and by the previous release
- **THEN** both claims record identical settings, revisions, and sources, in the same order, and
  the same request fingerprint

#### Scenario: Keep a continued request a continuation

- **WHEN** an issue whose claim the previous release admitted is re-read by this release with an
  unchanged eval block
- **THEN** its request fingerprint matches the claim's, and it is treated as the same request,
  not as a fresh request

#### Scenario: Reject the same unsupported key

- **WHEN** an eval block supplies a key the previous release rejected, such as
  `agent_validator_ref`
- **THEN** the request is rejected with the same unsupported-key message and no evaluation starts

#### Scenario: Report the same unresolvable revision

- **WHEN** an eval cannot be admitted because its Runner ref, Skills ref, Validator branch, or
  fixture ref cannot be resolved or accepted
- **THEN** the issue receives the same "waiting for revision readiness" reason that the previous
  release posted for that failure

#### Scenario: List honored revisions

- **WHEN** an operator or the deploy script runs `agent-factory honored-revisions` against this
  release
- **THEN** it prints `runner`, `skills`, `evals`, `validator`, and `fixture`, one per line in
  that order, and the deploy fixture guard treats the release as fixture-capable
