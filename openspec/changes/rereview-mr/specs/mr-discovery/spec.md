# Spec Delta

## MODIFIED Requirements

### Requirement: Already-reviewed merge requests are skipped
The system SHALL skip a merge request that already carries a review-state marker in a comment authored by the GitLab user the system acts as, and SHALL determine this solely from the MR's comments in GitLab, without any local state. The system SHALL perform this check again immediately before starting the review of each candidate, not only when the candidate list is built. This skip applies to every pass except one run in re-review mode, where the presence of such a marker is what makes an MR a candidate (see capability mr-rereview).

#### Scenario: MR already reviewed at its current head
- **WHEN** the MR has a comment by the system's GitLab user containing the review-state marker for the MR's current head commit
- **THEN** the system SHALL NOT review it again

#### Scenario: MR reviewed at an earlier head commit
- **WHEN** the MR has a comment by the system's GitLab user containing a review-state marker for a head commit other than the current one and the pass is not in re-review mode
- **THEN** the system SHALL NOT review it again

#### Scenario: Marker posted by another user is not trusted
- **WHEN** the MR has a comment containing a review-state marker that was authored by a user other than the system's GitLab user
- **THEN** the system SHALL ignore that marker when deciding whether the MR was reviewed

#### Scenario: Local state lost
- **WHEN** the machine running the system loses all local files between passes (scratch dir, reports)
- **THEN** the next pass SHALL still skip every MR that already carries a valid marker

#### Scenario: Reviewed by another pass while waiting in the queue
- **WHEN** an automatic-mode pass lists several candidates and, while earlier candidates are being reviewed, another pass publishes a review of a later candidate
- **THEN** when this pass reaches that candidate it SHALL skip it without claiming or reviewing it

#### Scenario: Review in progress elsewhere
- **WHEN** a candidate carries a live claim from another pass at the moment this pass is about to review it
- **THEN** the system SHALL skip it with a reason stating that its review is already in progress
