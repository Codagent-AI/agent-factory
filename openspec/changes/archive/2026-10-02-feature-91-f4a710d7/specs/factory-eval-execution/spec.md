## ADDED Requirements

### Requirement: Run against the claim's frozen fixture revision

When a claim recorded a fixture revision, every invocation of the and-scene suite for that claim SHALL pass the full recorded SHA through the suite's `--fixture-ref` argument. This covers each repetition's initial launch, a fresh retry after an attempt that stopped before checkpoint creation, and a `--resume` recovery. It applies under both Docker and Fly execution. Each such invocation SHALL also pass the and-scene fixture repository `https://github.com/Codagent-AI/and-scene.git` through the suite's `--repo` argument. That is the repository admission certified the commit against, so the suite clones the fixture from it whatever repository default the pinned harness carries. The factory SHALL NOT pass the requested ref name, re-resolve the ref, or substitute the harness's own fixture pin. The suite clones and checks out the fixture itself, so the factory SHALL NOT create a fixture worktree, and the Fly image build and Machine transport SHALL be unchanged.

When a claim recorded no fixture revision, the invocation SHALL NOT include `--fixture-ref` or `--repo`, so the suite uses the fixture its pinned harness selects, exactly as before this change.

Before launching an attempt for a claim with a fixture revision, selected-suite readiness SHALL verify that the claim's pinned harness accepts both `--fixture-ref` and `--repo`. If it does not, the factory SHALL report an actionable readiness problem naming the harness commit and follow the readiness-hold behavior in `factory-claim-lifecycle`. No attempt SHALL launch and no execution attempt or recovery retry SHALL be consumed. Support SHALL be judged from the pinned harness's `run.sh` option dispatch, which must contain a `--fixture-ref` case and a `--repo` case. Readiness SHALL NOT start a dry run or contact Fly for this check.

If the suite fails to fetch or check out the frozen fixture commit, for example because the commit's only branch was deleted after admission, the attempt SHALL follow the existing failure and recovery policy for a suite failure. The factory SHALL NOT substitute another fixture.

The recorded evaluated environment SHALL keep these three things distinguishable:

- the requested fixture ref and the frozen fixture SHA, which are requested inputs;
- the fixture commit the suite reports it checked out, which is observed provenance;
- the harness's default fixture pin, for claims without a fixture revision.

The requested ref and the frozen SHA SHALL be read from the claim's frozen inputs (`settings.fixture_ref` and `revisions.fixture`). The observed commit is the suite's own evidence in the repetition's retained artifact directory: `result.json` `candidate_source.fixture_commit`, and `proof-metadata.json` `repo`, `fixture_ref`, and `fixture_commit`. The factory SHALL retain that evidence unmodified with the repetition's other suite-owned evidence. The factory SHALL NOT itself compare the observed commit with the frozen SHA: the suite checks out the exact SHA it was given, and its resume checks revalidate the recorded fixture.

#### Scenario: Launch a repetition with a pinned fixture

- **WHEN** a claim with fixture revision `F` launches a repetition under Docker or Fly execution
- **THEN** the suite invocation includes `--fixture-ref F` with the full SHA and `--repo https://github.com/Codagent-AI/and-scene.git`, alongside the saved role profiles, component worktree paths, artifact directory, environment paths, and validator setting

#### Scenario: Recover a repetition with a pinned fixture

- **WHEN** a claim with fixture revision `F` recovers an interrupted repetition, with or without `--resume`
- **THEN** the recovery invocation includes `--fixture-ref F` and the same `--repo` value, and the suite's resume checks compare saved evidence against `F`

#### Scenario: Launch a default repetition

- **WHEN** a claim without a fixture revision launches or recovers a repetition
- **THEN** the suite invocation contains no `--fixture-ref` or `--repo` argument and is otherwise unchanged from before this change

#### Scenario: Pinned harness lacks fixture selection

- **WHEN** a claim with a fixture revision is ready to launch and its pinned harness does not accept `--fixture-ref` or `--repo`
- **THEN** no attempt launches, the claim is held with a readiness problem naming the harness commit, and no execution attempt or recovery retry is consumed

#### Scenario: Pinned harness defaults to another repository

- **WHEN** a claim with fixture revision `F` launches with a pinned harness whose own repository default is not `https://github.com/Codagent-AI/and-scene.git`
- **THEN** the invocation passes `--repo https://github.com/Codagent-AI/and-scene.git`, so the suite clones the fixture from the repository admission certified `F` against

#### Scenario: Fixture commit disappears after admission

- **WHEN** a claim's frozen fixture commit is no longer fetchable from the and-scene origin when a later repetition runs
- **THEN** the suite's checkout failure is handled as a suite failure under the existing failure and recovery policy
- **AND** the factory does not run the repetition against a different fixture

#### Scenario: Inspect the fixture used by a repetition

- **WHEN** the user inspects the saved evaluation details of a repetition from a claim with a fixture revision
- **THEN** the requested fixture ref and the frozen fixture SHA are identifiable from the claim's frozen inputs
- **AND** the fixture commit the suite observed is identifiable from the repetition's retained `result.json` (`candidate_source.fixture_commit`) and `proof-metadata.json`, unmodified by the factory
