## ADDED Requirements

### Requirement: Delete a claim's image once it can no longer be used

The factory SHALL delete from the Fly registry the image it built for an eval claim, once no active or resumable work can use that image.

**When an image may be deleted.** A claim's image SHALL become eligible for deletion only when all of the following hold:

- the claim is settled, cancelled, or superseded;
- no run of the claim is non-terminal or of unverified ownership;
- no Fly Machine is recorded for the claim;
- the claim has no active stop for a quota hold.

Eligibility SHALL NOT wait for the card to reach Done or for any retention period. It SHALL be judged on each successful poll for every terminal claim in the store, including claims whose card is no longer on the board and claims that predate this requirement.

**What is deleted.** The factory SHALL delete only image digests it durably recorded as built for that claim.

- Before deleting a digest, it SHALL resolve the claim's `claim-` tag with a registry manifest GET. It SHALL delete the digest by digest only when the tag still resolves to that digest.
- A tag that no longer exists SHALL count as already deleted.
- A tag that resolves to a different digest SHALL NOT be deleted. It SHALL be recorded as a cleanup failure for the operator and SHALL NOT be retried automatically.
- The factory SHALL NOT delete the configured `[fly] image`, any tag it did not record for a claim, or any image found only by listing the registry.
- A claim with no recorded digest SHALL need no image deletion.

**How outcomes are handled.** The factory SHALL persist the outcome of each deletion with the claim.

- A successful deletion, or a registry reply that the manifest is unknown, SHALL complete the claim's image deletion.
- A network error, an authentication failure, a timeout, or a server error SHALL be recorded and retried on later polls.
- A registry reply that deletion is unsupported SHALL be recorded once as a persistent failure and SHALL NOT be retried on every poll.
- Controller restarts and repeated attempts SHALL tolerate an image that was already deleted.

**Isolation.** Image deletion SHALL run apart from Machine disposal and stale-Machine reconciliation. A registry failure SHALL NOT do any of the following:

- delay, fail, or change Machine disposal, reconciliation, or attempt settlement;
- block other claims' cleanup;
- block admission.

#### Scenario: Delete a settled claim's image

- **WHEN** an eval claim built its image, settled, and every one of its Machines has been destroyed
- **THEN** the next successful poll resolves its `claim-` tag, confirms it points to the recorded digest, and deletes that digest from the registry
- **AND** the deletion is recorded as complete for the claim

#### Scenario: Keep an image a claim may still use

- **WHEN** a claim is active, waiting, or blocked, or has a Machine stopped for a quota hold
- **THEN** its image is not deleted

#### Scenario: Keep an image while a Machine record remains

- **WHEN** a claim is terminal but a Machine is still recorded for it because disposal has not been confirmed
- **THEN** its image is not deleted until the Machine record is gone

#### Scenario: Delete a cancelled or superseded claim's image promptly

- **WHEN** an eval claim is cancelled or superseded and its execution has stopped with no Machine remaining
- **THEN** its image is deleted on the next successful poll, without waiting for Done or any retention period

#### Scenario: Find the tag already gone

- **WHEN** the claim's `claim-` tag no longer exists in the registry
- **THEN** the claim's image deletion is recorded as complete without error

#### Scenario: Find the tag pointing elsewhere

- **WHEN** the claim's `claim-` tag resolves to a digest other than the one the claim recorded
- **THEN** nothing is deleted, the mismatch is recorded as a cleanup failure shown in status, and it is not retried automatically

#### Scenario: Retry a registry failure

- **WHEN** the registry is unreachable, rejects authentication, or returns a server error during deletion
- **THEN** the failure is recorded, status lists it, and a later poll retries the deletion
- **AND** Machine disposal, reconciliation, admission, and other claims proceed unaffected

#### Scenario: Registry refuses deletion

- **WHEN** the registry replies that manifest deletion is unsupported
- **THEN** the factory records a persistent failure for the claim once, status lists it, and it is not retried on every poll

#### Scenario: Clean up images that predate this rule

- **WHEN** the factory first runs with this requirement and older terminal claims have recorded image digests
- **THEN** each such claim's image is deleted under the same conditions on a successful poll

#### Scenario: Leave unrecorded tags alone

- **WHEN** the registry holds the configured base tag, deployment tags, or `claim-` tags that no stored claim recorded
- **THEN** the factory does not delete them
