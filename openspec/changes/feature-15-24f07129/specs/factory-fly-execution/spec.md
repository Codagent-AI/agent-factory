## ADDED Requirements

### Requirement: Remove a finished claim's registry image

When an eval claim has recorded an image digest from its per-claim build, the factory SHALL
delete that image from the sandbox registry once the claim no longer needs it. The claim no
longer needs its image when all of these hold:

- its lifecycle is `settled`, `cancelled`, or `superseded`;
- none of its runs is non-terminal or of unverified ownership;
- no Machine recorded for any of its runs remains undestroyed or unverified.

Active, waiting, and blocked claims SHALL keep their image, including a claim whose Machine
is stopped for a quota hold. Deletion SHALL NOT depend on the claim's card status and SHALL
cover claims whose card is no longer on the Project.

The factory SHALL delete only the manifest recorded as that claim's digest, in the
repository recorded for the claim's build, and SHALL never delete by tag. It SHALL resolve
tags and digests with registry GET requests, not HEAD requests.

The factory SHALL delete a recorded digest only while it holds proof of ownership: on that
poll, the claim's own `claim-` tag resolves to exactly that digest, and no other tag in the
repository resolves to it. In every other case, the factory SHALL NOT delete anything for that digest,
with these outcomes:

- **The image is gone.** If neither the claim's tag nor the recorded digest resolves, the
  image SHALL be treated as already deleted.
- **Ownership cannot be proven.** In the following cases the factory SHALL record the skip
  and its reason as a cleanup failure that `status` shows:
  - another tag resolves to the digest;
  - the claim's tag resolves to a different digest;
  - the claim's tag is absent while the recorded digest still resolves.
- **A delete returns not-found.** The image SHALL count as already deleted only after GET
  requests confirm that both the claim's tag and the digest are absent.

The factory SHALL never delete `base`, `deployment-` tags, another claim's `claim-` tag,
any tag it did not create, or a claim's image while the claim still needs it.

The factory SHALL record each deletion's outcome with the claim, so a completed deletion is
not repeated after a controller restart. Any other registry error SHALL be recorded as a
cleanup failure of the claim and retried on later polls. That includes an authorization
failure, a network failure, a server error, or a response refusing deletion as unsupported.
The failure SHALL stay visible in `status` until a deletion succeeds. It SHALL NOT be
recorded as success. Registry image deletion SHALL run separately from Machine disposal and
stale-Machine reconciliation. Its failures SHALL NOT be recorded as Machine cleanup
failures. They SHALL NOT delay or fail a Machine's destruction, reconciliation, result
consumption, admission, the claim's terminal release or evidence pruning, or any other
claim's cleanup. The factory SHALL NOT promise that the registry reclaims storage for a
deleted image.

#### Scenario: Delete a settled claim's image

- **WHEN** a settled Fly eval claim has no non-terminal run, every Machine it used has been destroyed, and its `claim-` tag resolves to its recorded digest and no other tag does
- **THEN** the next poll deletes that manifest by digest from the sandbox registry, after which neither the claim's tag nor the digest resolves, and records the deletion with the claim

#### Scenario: Keep the image of a claim waiting on quota

- **WHEN** an eval claim is waiting for a usage reset with its Machine stopped
- **THEN** its registry image is not deleted

#### Scenario: Delete a superseded claim's image

- **WHEN** an eval claim is superseded by a fresh claim that builds its own image
- **THEN** the superseded claim's image is deleted once none of its runs or Machines remain, and the fresh claim's image is untouched

#### Scenario: Refuse to delete a shared digest

- **WHEN** another tag in the repository resolves to a finished claim's recorded digest
- **THEN** nothing is deleted and status shows the skipped image with the other tag as the reason

#### Scenario: Survive a registry outage

- **WHEN** the registry answers the deletion with a server error while a Machine of another claim is due for destruction
- **THEN** that Machine is still destroyed on schedule, the failed deletion is recorded with the claim and shown by status, and a later poll retries it

#### Scenario: Registry refuses deletion

- **WHEN** the registry answers that manifest deletion is unsupported
- **THEN** the claim keeps a visible cleanup failure naming the registry's answer, and the deletion is not recorded as done

#### Scenario: Find the image already gone

- **WHEN** a finished claim's tag no longer exists and its recorded digest no longer resolves
- **THEN** the deletion is recorded as done without error and no DELETE is sent

#### Scenario: Refuse an untagged live digest

- **WHEN** a finished claim's tag no longer exists but its recorded digest still resolves
- **THEN** nothing is deleted and status shows the skipped image with the reason that ownership cannot be proven

#### Scenario: Keep an older build whose tag moved on

- **WHEN** a finished claim recorded two distinct digests and its tag resolves to the newer one, which no other tag uses
- **THEN** the newer digest is deleted, and the older digest is left in place and shown by status as skipped

#### Scenario: Restart after a deletion

- **WHEN** the controller restarts after a claim's image deletion was recorded
- **THEN** no further registry request is made for that image

#### Scenario: Claim without a recorded digest

- **WHEN** an eval claim's every build failed so no digest was recorded
- **THEN** no registry request is made for it and no failure is recorded
