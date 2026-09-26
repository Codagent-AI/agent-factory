## REMOVED Requirements

### Requirement: Detect eligible review comments on a settled fix claim

**Reason**: Review-comment detection applies to every pull-request claim, fix or feature.
**Migration**: Replaced by "Detect eligible review comments on a settled pull-request claim" in `factory-pull-request-lifecycle`; fix behavior is unchanged.

### Requirement: Re-admit a review round through the fix slot

**Reason**: A review round is admitted through the slot of its claim's own work kind.
**Migration**: Replaced by "Re-admit a review round through the claim's kind slot" in `factory-pull-request-lifecycle`; a fix claim's round still uses the fix slot and window.
