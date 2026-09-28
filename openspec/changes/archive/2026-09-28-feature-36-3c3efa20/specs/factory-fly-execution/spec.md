## MODIFIED Requirements

### Requirement: Build the sandbox image once per claim

Under `fly` execution, the factory SHALL build the sandbox image once per eval claim from the claim's pinned Agent Runner worktree, with the worktree root as build context, on Fly's remote builder. It SHALL push the image to the image repository derived from `[fly] image` under the tag `claim-` followed by the first 12 characters of the claim id. The build SHALL pass a build argument `FACTORY_CLI_REFRESH` whose value is the claim id, so the model CLI layer and every later layer are rebuilt for each claim while earlier layers may come from the builder's cache.

When the claim recorded an Agent Validator revision, the image SHALL be built from the worktree's `docker/dev/Dockerfile` followed by factory-owned build steps. Those steps SHALL build Agent Validator at exactly that revision, from the Validator source the claim recorded at admission (never from the operator checkout's current origin), and install it as the image's `agent-validator`, in place of the npm release the Runner Dockerfile installs. They SHALL leave the image's final user, working directory, and default command as the Runner Dockerfile set them. The factory SHALL NOT modify the Runner worktree's Dockerfile. The build SHALL fail when the installed `agent-validator` does not report that revision. When the claim recorded no Validator revision, the image SHALL be built from `docker/dev/Dockerfile` alone, as before this change.

The build SHALL run in the detached process that launches the claim's first attempt, before any Machine is created, and SHALL NOT run inside the controller cycle. The factory SHALL take the immutable digest the build itself reports. Only when a build succeeded but its output carries no digest MAY the factory resolve the claim's own tag, which this build just created and nothing else writes, once through a registry manifest GET; it SHALL NOT use a registry HEAD request for this and SHALL NOT resolve any other tag. The digest SHALL be recorded durably with the attempt, in a form the factory reads back as the claim's image digest, before the Machine is created, so that it survives a controller or supervisor restart and does not depend on the attempt's result being consumed. Every later attempt of the claim, including recovery attempts and relaunches after a pre-suite failure, SHALL launch its Machine from the digest of the claim's most recent successful build and SHALL NOT build again. A build failure, or a successful build for which neither the build output nor the single GET yields a digest, SHALL be a pre-suite failure of the attempt that ran it, with the builder's diagnostic preserved with the attempt. In that case no digest is recorded, the factory SHALL NOT fall back to a previously built or configured image, and the relaunch permitted by the pre-suite policy SHALL build again.

#### Scenario: Build at the claim's first launch

- **WHEN** the first repetition of an admitted eval claim launches under `fly` execution and no digest is recorded on the claim
- **THEN** the image is built from the claim's pinned Runner worktree with `FACTORY_CLI_REFRESH` set to the claim id
- **AND** it is pushed to the repository derived from `[fly] image` under the tag `claim-` followed by the first 12 characters of the claim id
- **AND** the digest the build reports is durably recorded as the claim's image digest before the Machine is created from that digest

#### Scenario: Install the claim's pinned Validator

- **WHEN** the image is built for a claim that recorded Validator revision `C`
- **THEN** `agent-validator --version` inside the claim's Machines reports `C`, not the latest npm release
- **AND** the Runner worktree's Dockerfile is unchanged

#### Scenario: Build a claim without a Validator revision

- **WHEN** the image is built for a claim admitted before Validator revisions were recorded
- **THEN** the image is built from the Runner Dockerfile alone and installs the npm release as before this change

#### Scenario: Validator does not match its revision

- **WHEN** the Validator installed during the build does not report the claim's recorded revision
- **THEN** the build fails, no digest is recorded, and the attempt ends as a pre-suite failure carrying the builder's diagnostic

#### Scenario: Reuse the claim's image

- **WHEN** a later repetition, a recovery attempt, or a relaunch after a pre-suite failure launches for a claim that already has a successful build's digest
- **THEN** no build runs and its Machine is created from that digest

#### Scenario: Keep the controller cycle responsive during a build

- **WHEN** a claim's image build is running
- **THEN** controller cycles continue to observe running attempts, reconcile Machines, and admit and supervise fix work without waiting for the build

#### Scenario: Fail a build

- **WHEN** the remote build fails
- **THEN** no Machine is created, no digest is recorded, no stale or configured image is used, and the attempt ends as a pre-suite failure carrying the builder's diagnostic
- **AND** the relaunch the pre-suite policy permits builds the image again

#### Scenario: Build without a printed digest

- **WHEN** the build succeeds but its output carries no digest
- **THEN** the factory resolves the new `claim-` tag once with a registry manifest GET and records that digest; if the GET yields no digest, the attempt ends as a pre-suite failure

#### Scenario: Recover the digest after a restart

- **WHEN** the controller restarts after a claim's build recorded its digest but before that attempt's result was consumed
- **THEN** the claim's later attempts use that recorded digest without building again

### Requirement: Record Machine provenance

Before model execution, the factory SHALL record the following for the attempt as observed execution provenance:

- the Machine identity;
- the immutable image digest observed on the launched Machine;
- the Machine's CPU kind, CPU count, memory, and region.

The observed digest SHALL be read from the digest Fly reports for the Machine's image, not from the image reference in the Machine's configuration. A configured mutable image tag SHALL NOT be recorded in place of the observed digest; when Fly reports no digest, provenance SHALL say the digest is unavailable. Provenance SHALL also record the digest the claim's build reported, and the versions reported inside the Machine by `claude --version`, `codex --version`, and `agent-validator --version`, or say that a version is unavailable.

#### Scenario: Inspect a completed Fly repetition

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the Machine identity, observed image digest, size, and region used for that attempt are identifiable
- **AND** the claim's built image digest and the Claude, Codex, and Agent Validator versions are identifiable

#### Scenario: Record the observed digest, not the tag

- **WHEN** a Machine's configuration names an image by tag and Fly reports the image's digest for the Machine
- **THEN** provenance records that digest and never the tag
