# Spec Delta

## Purpose

Marks in GitLab itself that a merge request is being reviewed right now, so that polling passes running at the same time — on one machine or on several — never review the same merge request twice, without relying on any local state.

## ADDED Requirements

### Requirement: Claim before reviewing
Immediately before reviewing a merge request, outside dry-run mode, the system SHALL post a claim comment on it, authored by the GitLab user the system acts as, that states the review is in progress and contains a machine-readable claim marker identifying the head commit and the claim time, in a form not rendered as visible text.

#### Scenario: Claim posted before the harness starts
- **WHEN** an MR is selected for review in a non-dry-run pass and has no review-state marker and no live claim
- **THEN** the system SHALL post one claim comment on the MR before starting the review harness

### Requirement: Live claims block other passes
The system SHALL treat a claim comment by its own GitLab user that is younger than the configured claim lifetime as a live claim, and SHALL NOT review an MR that carries a live claim from another pass.

#### Scenario: Another pass is reviewing the MR
- **WHEN** an MR carries a live claim posted by another pass
- **THEN** the system SHALL skip that MR with a reason stating that its review is already in progress

#### Scenario: Claim by another user is ignored
- **WHEN** an MR carries a claim marker in a comment authored by a different GitLab user
- **THEN** the system SHALL ignore it

### Requirement: Simultaneous claims resolve to one winner
After posting its claim, the system SHALL re-read the MR's comments; if a live claim posted earlier than its own exists, it SHALL delete its own claim and skip the MR.

#### Scenario: Two passes claim at the same time
- **WHEN** two passes post claims on the same MR within a short interval
- **THEN** exactly one pass SHALL review the MR, and the other SHALL remove its claim and skip it

### Requirement: Stale claims are taken over
A claim older than the configured claim lifetime SHALL be treated as abandoned: the system SHALL delete it and MAY claim and review the MR. The claim lifetime SHALL default to 240 minutes and be configurable.

#### Scenario: Pass died while reviewing
- **WHEN** an MR carries only a claim older than the claim lifetime and no review-state marker
- **THEN** the next pass SHALL delete the stale claim and review the MR

### Requirement: Claim is resolved when the review ends
When the review succeeds, the claim comment SHALL become the published report (see review publishing). When the review fails, is unusable, or is abandoned for any reason, the system SHALL delete its claim comment.

#### Scenario: Review fails after claiming
- **WHEN** the harness fails for an MR the system has claimed
- **THEN** the system SHALL delete its claim, so the MR carries no comment from this pass and is a candidate again on the next pass

#### Scenario: Claim cannot be deleted
- **WHEN** deleting the claim fails
- **THEN** the system SHALL record that in the pass outcome, and the claim SHALL stop blocking the MR once it is older than the claim lifetime

### Requirement: No claims in dry-run mode
In dry-run mode the system SHALL NOT post, edit, or delete claims, but SHALL still skip MRs that carry a live claim or a review-state marker.

#### Scenario: Dry-run with a live claim from a real pass
- **WHEN** a dry-run pass finds an MR carrying a live claim
- **THEN** it SHALL skip that MR without writing anything to GitLab
