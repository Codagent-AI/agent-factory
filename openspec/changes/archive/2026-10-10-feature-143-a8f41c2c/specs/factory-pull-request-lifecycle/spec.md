## MODIFIED Requirements

### Requirement: Re-admit a review round through the claim's kind slot

An eligible review round SHALL be admitted through the execution slot, window, and limits of the claim's own work kind: a fix claim's round through the fix slot and fix window, a feature claim's round through the feature slot and feature window, and a task claim's round through the task slot and task window. A review round SHALL be a new start in the lane of the card's current Priority (see `factory-claim-lifecycle`). It SHALL be admitted only when all of these hold: the factory is not paused; that lane is free; no attempt of the claim's kind occupies a higher lane; that window is open; memory headroom is available; and no applicable quota or readiness hold is active. Within a kind, the factory SHALL consider review rounds, blocked-claim resumes, and new Ready work in Priority order. A review round SHALL be considered before new Ready work of the same kind and the same Priority in the same cycle. An attempt reserved earlier in the same cycle SHALL count as occupying its lane. A review round SHALL NOT compete with work of another kind. Admission SHALL reconcile side effects, verify the recorded PR is still open and read its head commit, cut fresh clones with the target checked out on the PR branch at that head, reserve a run with reason `review` and the claim's recorded Runner and Skills commits, set the claim lifecycle to active, and comment on the issue that a review round started, naming the comments it will address. When the recorded PR is no longer open at admission, the factory SHALL record why and SHALL NOT launch.

#### Scenario: Review round beats a new bug

- **WHEN** the fix lanes are free, a settled Medium fix claim has an eligible review comment, and another Medium bug sits in Ready
- **THEN** the review round is admitted first and the new bug waits for the Medium fix lane

#### Scenario: Feature review round uses the feature slot

- **WHEN** a settled feature claim has an eligible review comment, its feature lane and every higher feature lane are free, and a fix attempt holds a fix lane
- **THEN** the feature review round is admitted through its feature lane under the feature limits
- **AND** it is admitted before any new Ready feature of the same Priority in the same cycle

#### Scenario: PR closed before admission

- **WHEN** the reviewer closed the PR before the poll admits the round
- **THEN** no attempt starts, the checkpoint is not advanced, and the reason is recorded on the claim

#### Scenario: Slot busy

- **WHEN** another attempt of the same kind holds the review round's lane, or a higher lane of that kind
- **THEN** the review round waits and is admitted on a later poll while its comments remain eligible

#### Scenario: Task review round uses the task slot

- **WHEN** a settled task claim has an eligible review comment, its task lane and every higher task lane are free, and a fix attempt holds a fix lane
- **THEN** the task review round is admitted through its task lane under the task limits
- **AND** it is admitted before any new Ready task of the same Priority in the same cycle

#### Scenario: A higher Ready bug beats a lower review round

- **WHEN** the fix lanes are free, a settled Low fix claim has an eligible review comment, and an eligible High bug sits in Ready in the same cycle
- **THEN** the High bug is admitted, and the Low review round waits until no fix attempt occupies a higher lane

#### Scenario: A higher review round starts beside a lower fix

- **WHEN** a Low fix attempt is running and a settled High fix claim has an eligible review comment
- **THEN** the High review round is admitted into the High fix lane, and the Low attempt continues
