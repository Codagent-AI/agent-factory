## ADDED Requirements

### Requirement: Do not flag the archive and phase checkpoints as unreviewed commits

The Runner verifies and runs acceptance before the factory archives the change, so the archive commit and the workflow's phase checkpoints follow the accepted head on every feature pull request. The description SHALL list them with the later commits, and the "Commits after acceptance" item SHALL name only the other commits made after acceptance. A commit is the archive or a checkpoint only when its subject begins with `[archive]`, `[factory-feature]`, or `[commit-plan]` and it changes files under `openspec/` alone. This applies to the description built by the feature workflow and to the one a review round restores.

#### Scenario: Only the archive and checkpoints follow acceptance

- **WHEN** the commits after the accepted head are the archive of the change and the phase checkpoints
- **THEN** the description lists them with the later commits and has no "Commits after acceptance" item

#### Scenario: A CI repair follows the archive

- **WHEN** finalization pushes a CI repair after the archive commit
- **THEN** the "Commits after acceptance" item names the repair and not the archive or the checkpoints

## REMOVED Requirements

### Requirement: Report the task-compliance result
**Reason**: The feature workflow no longer gates on task-compliance, so no result exists to report.
**Migration**: Repositories that enable task-compliance in their Validator configuration see it in the validator's own output.
