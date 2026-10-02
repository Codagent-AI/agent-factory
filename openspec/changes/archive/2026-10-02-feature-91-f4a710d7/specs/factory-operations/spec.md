## ADDED Requirements

### Requirement: Locate the and-scene fixture checkout

The local configuration SHALL accept an optional `[repositories] and_scene` path to the operator's and-scene checkout. When the key is unset, the checkout SHALL be the `and-scene` directory next to the configured `agent_runner` checkout. The public example configuration SHALL show the key without a personal path.

Only eval requests that supply `fixture_ref` use the checkout. A deployment SHALL NOT need one to load configuration, pass `doctor`, or admit evals that do not select a fixture.

`doctor` SHALL report the checkout in the eval group as informational:

- whether it exists and is a Git repository;
- whether its origin is the and-scene suite's fixture repository.

A missing checkout or a mismatched origin SHALL NOT fail `doctor` or hold admission. The report SHALL say that requests supplying `fixture_ref` will wait for revision readiness until the problem is fixed. `doctor` SHALL NOT fetch the checkout or print credentials embedded in its origin.

#### Scenario: Use the sibling default

- **WHEN** the local configuration sets `agent_runner = "/Users/paul/codagent/agent-runner"` and leaves `and_scene` unset
- **THEN** the factory resolves requested fixture refs through `/Users/paul/codagent/and-scene`

#### Scenario: Use an explicit path

- **WHEN** the local configuration sets `and_scene` to a path
- **THEN** the factory uses that path and ignores the sibling default

#### Scenario: Run without an and-scene checkout

- **WHEN** `and_scene` is unset and no sibling checkout exists
- **THEN** configuration loads, `doctor` reports the missing checkout as informational without failing, and evals that do not supply `fixture_ref` are admitted as before this change

#### Scenario: Diagnose a checkout with the wrong origin

- **WHEN** the and-scene checkout's origin is not the suite's fixture repository
- **THEN** `doctor` reports it as informational, names the checkout and the normalized origin without credentials, and states that fixture-selecting requests will wait until the origin is corrected

### Requirement: Refuse a deploy that would drop frozen fixture revisions

`scripts/deploy.sh` SHALL NOT make live a release that cannot honor claims' frozen fixture revisions while any eval claim that recorded a fixture revision is unfinished. A release cannot honor them when it would launch the claim's next attempt without passing the frozen fixture. An unfinished claim is one that can still launch or recover an attempt. Making a release live means pointing the LaunchAgent or `shared_config` at it, or moving `releases/current` to it.

The deploy SHALL check before it pauses the factory. On refusal at that point it SHALL stop with a failing exit status and deploy nothing, leave the factory's pause state unchanged, and name each affected claim by claim identity and issue. Two preparatory steps that precede every deploy are the only exceptions to "deploy nothing": the Agent Runner checkout fast-forward, and building the target release's immutable worktree. Neither changes what the service runs.

The deploy SHALL check again after pausing and before it changes the LaunchAgent, `shared_config`, or `releases/current`, so that a claim admitted after the first check is also covered. On refusal at that point it SHALL stop, name each affected claim, leave the live release unchanged, and leave the factory paused, as other post-pause deploy failures do.

The refusal message SHALL give the rollback procedure:

1. pause the factory;
2. let each named claim settle, or cancel it;
3. deploy the older release.

The message SHALL also state that the older release cannot accept `fixture_ref`. A pinned evaluation that is still needed requires staying on a fixture-capable release. A new request without the key evaluates only the agent-evals default fixture.

The release's own executable SHALL answer whether the target release honors fixture revisions, through a read-only `agent-factory honored-revisions` command. It prints the frozen revision keys the release acts on and needs no configuration. A target release that lacks the command, or does not list `fixture`, does not honor them.

The live release's executable SHALL list the unfinished fixture-pinned claims, through a read-only `agent-factory --config <local> pinned-claims --revision fixture` command. It prints each such eval claim's identity and issue, and nothing when there are none. Handling depends on what the live release supports:

- If the live release does not honor fixture revisions itself, it cannot have admitted such a claim. The deploy SHALL warn and proceed.
- If the live release honors fixture revisions but cannot list the claims, the deploy SHALL stop as for a refusal at that stage.

The deploy SHALL offer no option that bypasses the check. A deploy of a release that honors fixture revisions, or a deploy while no unfinished claim recorded a fixture revision, SHALL be unaffected.

#### Scenario: Roll back while a fixture-pinned claim is unfinished

- **WHEN** the operator deploys a release that cannot honor fixture revisions while an eval claim with a fixture revision has unstarted or recoverable repetitions
- **THEN** the deploy stops before pausing the factory, names that claim and its issue, gives the rollback procedure, exits with failure, and leaves the live release, its plist, `shared_config`, and `releases/current` unchanged

#### Scenario: Claim admitted during the deploy

- **WHEN** a fixture-pinned claim is admitted after the deploy's first check and before the factory is paused, and the target release cannot honor fixture revisions
- **THEN** the second check, after the pause, refuses the deploy, names the claim, and leaves the live release unchanged with the factory paused

#### Scenario: Roll back after fixture-pinned claims finish

- **WHEN** the operator deploys a release that cannot honor fixture revisions and every claim that recorded a fixture revision has settled or been cancelled
- **THEN** the deploy proceeds as before this change

#### Scenario: Live release predates fixture revisions

- **WHEN** the operator deploys, with a deploy script that has this check, a target release that cannot honor fixture revisions while the live release also predates them
- **THEN** the deploy warns that the live release cannot have admitted fixture-pinned claims and proceeds as before this change

#### Scenario: Live release cannot list its fixture-pinned claims

- **WHEN** the live release honors fixture revisions, the target release does not, and listing the live release's fixture-pinned claims fails
- **THEN** the deploy stops at that stage without making the target release live, and states that the claims could not be listed

#### Scenario: List fixture-pinned claims

- **WHEN** the operator runs `agent-factory --config <local> pinned-claims --revision fixture` while one eval claim with a fixture revision is waiting and another has settled
- **THEN** the command prints only the waiting claim's identity and issue and changes nothing

#### Scenario: Deploy a release that honors fixture revisions

- **WHEN** the operator deploys a release that honors fixture revisions while fixture-pinned claims are unfinished
- **THEN** the deploy proceeds as before this change, and those claims keep their frozen fixture revisions

### Requirement: Document request-selected fixture revisions

The operations documentation and `AGENTS.md` SHALL explain:

- the `fixture_ref` eval-request key and that omitting it keeps the agent-evals pin;
- that the factory resolves it at admission through the and-scene checkout and its sibling default, and freezes the commit for the claim;
- that the commit must be published on the and-scene origin, and that deleting its branch before the claim finishes fails the remaining repetitions at fixture checkout;
- how reports and the `Refs` field show a pinned fixture;
- that results from a non-default fixture are not comparable with results from the agent-evals pin;
- the deploy's refusal to roll back past fixture support, and the rollback procedure: settle or cancel every unfinished fixture-pinned claim before rolling back. The older release cannot accept `fixture_ref`, so a pinned evaluation that is still needed means staying on a fixture-capable release, while a new request without the key evaluates only the default fixture;
- that a rollback done by hand, or with a deploy script without this check, bypasses the refusal.

#### Scenario: Request an eval against a fixture branch

- **WHEN** an operator reads the documentation to evaluate an unmerged and-scene fixture change
- **THEN** it tells them to push the fixture commit to a branch on the and-scene origin, add `fixture_ref` to the eval block, and how to confirm in the frozen inputs and `Refs` field which fixture was used

#### Scenario: Roll back with fixture-pinned claims

- **WHEN** an operator reads the documentation before rolling back to an older release
- **THEN** it tells them how to find unfinished fixture-pinned claims, to let them settle or cancel them before rolling back, and that the deploy refuses the rollback otherwise
- **AND** it states that the older release cannot accept `fixture_ref`, so a pinned evaluation still needed requires staying on a fixture-capable release
