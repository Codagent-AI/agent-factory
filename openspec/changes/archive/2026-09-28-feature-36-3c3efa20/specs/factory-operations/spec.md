## ADDED Requirements

### Requirement: Locate the Agent Validator checkout

The local configuration SHALL accept an optional `[repositories] agent_validator` path to the operator's Agent Validator checkout. When the key is unset, the checkout SHALL be the `agent-validator` directory next to the configured `agent_runner` checkout. The public example configuration SHALL show the key without a personal path. A deployment whose kinds use only Docker execution SHALL NOT need a Validator checkout.

#### Scenario: Use the sibling default

- **WHEN** the local configuration sets `agent_runner = "/Users/paul/codagent/agent-runner"` and leaves `agent_validator` unset
- **THEN** the factory and the deploy use `/Users/paul/codagent/agent-validator` as the Agent Validator checkout

#### Scenario: Use an explicit path

- **WHEN** the local configuration sets `agent_validator` to a path
- **THEN** the factory and the deploy use that path and ignore the sibling default

#### Scenario: Run Docker-only without a checkout

- **WHEN** the eval and fix kinds use Docker execution, the feature kind is not configured, `agent_validator` is unset, and no sibling checkout exists
- **THEN** configuration loads, doctor reports no Validator checkout problem, and evals and fixes are admitted as before this change

### Requirement: Build the host Agent Validator from main on deploy

Unless the operator passes `--no-validator`, `scripts/deploy.sh` SHALL bring the Agent Validator checkout up to `origin/main` and build it, so that the `agent-validator` host fixes and features run is built from Agent Validator `main`. Before pausing the factory, the deploy SHALL fetch the checkout and check it, without changing its working tree. The deploy SHALL skip the whole Validator step with a warning, and continue, when the checkout is not on `main`, has uncommitted changes, or has commits not on `origin/main`. It SHALL never push the checkout. When `agent_validator` is unset and no checkout exists at the default path, the deploy SHALL skip the Validator step with a warning and continue. When `agent_validator` is set and the path is not a git checkout, the deploy SHALL stop before pausing the factory and deploy nothing. When the Validator step will run and `bun`, which the Validator's build needs, is not on the deploy's PATH, the deploy SHALL stop before pausing the factory, name `bun`, and deploy nothing; `--no-validator` deploys without it.

After pausing the factory, the deploy SHALL fast-forward the checkout to `origin/main` and build it with the Validator's local build, which records the built commit in `agent-validator --version`. The deploy SHALL skip both the fast-forward and the build with a warning, and continue, while any fix or feature attempt runs on the host. Whether an attempt runs on the host SHALL be decided by the backend recorded for that attempt, not by the current execution configuration; an attempt whose backend is not yet recorded SHALL count as a host attempt. A running eval, under any execution mode, SHALL NOT prevent the build. A failed build SHALL stop the deploy with the factory paused, as a failed Agent Runner build does. After a build, the deploy SHALL check that `agent-validator` on the PATH in the installed LaunchAgent definition resolves to the executable this build produced, and warn, naming both paths, when it does not. The deploy SHALL NOT create, replace, or remove the operator's `agent-validator` link. `--no-runner` SHALL continue to affect only the Agent Runner step. The existing rule that removes old releases only while every slot is free SHALL be unchanged.

#### Scenario: Deploy with the checkout on main

- **WHEN** the operator deploys, the Validator checkout is on `main` with no local changes, and `origin/main` has new commits
- **THEN** the checkout is fetched but unchanged before the pause, and is fast-forwarded to `origin/main` and built after the factory pauses
- **AND** `agent-validator --version` reports the new commit, and nothing is pushed

#### Scenario: Skip an unsafe checkout

- **WHEN** the Validator checkout is on another branch, has uncommitted changes, or has commits not on `origin/main`
- **THEN** the deploy warns that it is skipping the Agent Validator update and build, leaves the checkout and its build unchanged, and completes the rest of the deploy

#### Scenario: Defer the build while a host fix runs

- **WHEN** the operator deploys while a fix attempt runs on the host
- **THEN** the Validator fast-forward and build are both skipped with a warning, the checkout and its build are unchanged, the running attempt keeps using the Validator it started with, and the deploy completes

#### Scenario: Defer the build after the fix mode changes

- **WHEN** a fix attempt launched on the host is still running, and `[fix] execution` has since been changed to `docker`
- **THEN** the deploy still treats it as a host attempt and skips the Validator fast-forward and build

#### Scenario: Build while only an eval runs

- **WHEN** the operator deploys while a Fly eval runs and no fix or feature attempt runs on the host
- **THEN** the Validator is built

#### Scenario: Deploy without a default checkout

- **WHEN** `agent_validator` is unset and no `agent-validator` directory exists next to the Agent Runner checkout
- **THEN** the deploy warns that it is skipping the Agent Validator step and completes the rest of the deploy

#### Scenario: Deploy with a missing configured checkout

- **WHEN** `agent_validator` is set to a path that is not a git checkout
- **THEN** the deploy stops before pausing the factory, names the path, and deploys nothing

#### Scenario: Deploy without the build tool

- **WHEN** the Validator step will run and `bun` is not on the deploy's PATH
- **THEN** the deploy stops before pausing the factory, names `bun` and `--no-validator`, and deploys nothing

#### Scenario: Fail the Validator build

- **WHEN** the Validator build fails
- **THEN** the deploy stops with the factory paused and names the checkout

#### Scenario: Warn about a service PATH that uses another Validator

- **WHEN** the build succeeds but `agent-validator` on the LaunchAgent PATH resolves to an npm-installed copy
- **THEN** the deploy warns, naming the resolved executable and the built one, and does not change either

#### Scenario: Skip the Validator on request

- **WHEN** the operator deploys with `--no-validator`
- **THEN** the Validator checkout and its build are untouched, and the Agent Runner step runs as before

### Requirement: Report host attempts in status

`agent-factory status` SHALL include a line `host attempts: <n>`, where `<n>` counts the unfinished attempts of every kind whose recorded backend is `host`, plus unfinished attempts whose backend is not yet recorded. The line SHALL NOT change the existing slot lines.

#### Scenario: Count a host fix after a mode change

- **WHEN** a host fix attempt is running, a Fly eval is running, and `[fix] execution` has been changed to `docker`
- **THEN** status shows `host attempts: 1` and the existing eval and fix slot lines

#### Scenario: No host attempts

- **WHEN** no unfinished attempt runs on the host
- **THEN** status shows `host attempts: 0`

### Requirement: Diagnose the Agent Validator build

Doctor SHALL report the Agent Validator checkout path, the commit that the `agent-validator` on the service PATH reports, and whether that commit is behind the checkout's `origin/main`. A build that is behind SHALL be informational and SHALL NOT fail doctor. When the fix kind is configured for host execution, or the feature kind is configured, the fix-host and feature-host groups SHALL fail when the Validator checkout is missing, or when `agent-validator` on the service PATH, and on the PATH in the installed LaunchAgent definition when it is at its documented location, does not resolve to the checkout's built executable. The failure SHALL name the resolved executable, the expected one, and the action to take. When the eval kind is configured for Fly execution, the eval-fly group SHALL fail when the Validator checkout is missing or is not a git repository. Doctor SHALL NOT build, fetch into, or relink the Validator.

#### Scenario: Report a current build

- **WHEN** host fixes are configured and `agent-validator` on the service PATH is the checkout's build at the checkout's `origin/main`
- **THEN** doctor passes the Validator check and shows the checkout path and the reported commit

#### Scenario: Report a build that is behind

- **WHEN** a deploy skipped the Validator build and the build on PATH reports a commit behind the checkout's `origin/main`
- **THEN** doctor reports the reported commit and that it is behind, as informational, and does not fail

#### Scenario: Diagnose an npm Validator on the host

- **WHEN** host fixes or features are configured and `agent-validator` on the service PATH resolves to an npm-installed copy
- **THEN** doctor fails the host group, naming the resolved executable, the checkout's expected executable, and the action to link it

#### Scenario: Diagnose a missing checkout for Fly evals

- **WHEN** the eval kind is configured for Fly execution and the Validator checkout does not exist
- **THEN** doctor fails the eval-fly group, naming the expected path

### Requirement: Document the Agent Validator build

The installation and operations documentation SHALL explain:

- the Agent Validator checkout and its sibling default;
- how to link `agent-validator` on the service PATH to the checkout's build once;
- that deploys fast-forward and build the checkout under the same skip rules as the Agent Runner checkout, and skip the build while a host fix or feature runs;
- `--no-validator`;
- that Fly eval images install the Validator at the claim's recorded revision;
- that Docker execution still uses the published npm release;
- that changing the Validator can change eval results, and that existing results stay tied to the Validator revision they recorded.

#### Scenario: Set up the host Validator

- **WHEN** an operator follows the documentation to run fixes or features on the host
- **THEN** it tells them where the Validator checkout goes, how to build it and link it on the service PATH, and which doctor check confirms it

## MODIFIED Requirements

### Requirement: Apply shared deployment changes through explicit updates

Shared deployment configuration SHALL be versioned with the factory and contain source repositories, routing rules, Project/field mappings, eval defaults, fix defaults, and the branch names for the `agent-evals` harness, Agent Runner, Agent Skills, Agent Validator (the eval default `agent_validator_ref`), and fix target repositories (each defaulting to `main`). Configuration SHALL NOT pin any of these repositories to a commit; commits are resolved per claim at admission and recorded on the claim. Machine-specific paths, schedule, execution limits, and credential-file locations SHALL be configured separately in local TOML. Secret values SHALL remain outside the versioned deployment configuration.

The installed factory SHALL use the shared configuration from its explicitly installed version and SHALL NOT automatically fetch configuration changes from main. Reusable routing workflows SHALL use shared configuration from their explicitly pinned factory revision. Deployment instructions SHALL cover updating the local factory and the caller workflows' routing revision together. Updating configuration SHALL NOT mutate frozen inputs or the execution configuration of an already-running attempt.

#### Scenario: Edit shared configuration in GitHub

- **WHEN** shared deployment configuration changes on main but the local factory has not been explicitly updated
- **THEN** the installed factory continues using its installed configuration
- **AND** ordinary queue polling and operational controls continue without waiting for a software update

#### Scenario: Deploy a shared configuration change

- **WHEN** the operator explicitly updates the local installation and routing workflow pins to the intended factory revision
- **THEN** subsequent routing and new claims use that revision's shared deployment configuration
- **AND** existing claims retain frozen inputs and already-running attempts retain their execution configuration

#### Scenario: Migrate a pinned harness configuration

- **WHEN** the installed configuration still contains a harness commit pin
- **THEN** the factory reports the obsolete setting at startup and in doctor instead of silently ignoring it

#### Scenario: Leave the Validator branch unset

- **WHEN** the shared configuration does not set `agent_validator_ref`
- **THEN** Fly eval claims resolve Agent Validator `main` at admission
