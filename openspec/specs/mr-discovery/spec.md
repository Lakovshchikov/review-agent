# mr-discovery Specification

## Purpose

Determines which open GitLab merge requests in the configured projects are due for an automated review, and resolves the base/head commits each review must run against.

## Requirements

### Requirement: Reviewer-filtered discovery
The system SHALL consider for review only open merge requests, in the configured and enabled GitLab projects, on which at least one of the reviewer users effective for that project (the project's own reviewer list, or the global one if the project sets none) is assigned as a reviewer.

#### Scenario: MR with a configured reviewer is discovered
- **WHEN** an enabled configured project has an open MR whose reviewers include a reviewer user effective for that project
- **THEN** the system SHALL include that MR as a review candidate

#### Scenario: MR without a configured reviewer is ignored
- **WHEN** a configured project has an open MR whose reviewers include none of the reviewer users effective for that project (including MRs where such a user is only the author or assignee)
- **THEN** the system SHALL NOT review that MR

#### Scenario: Closed or merged MR is ignored
- **WHEN** an MR with a configured reviewer is closed or merged and the include-closed flag is not set
- **THEN** the system SHALL NOT review that MR

#### Scenario: MR matched by several configured reviewers is reviewed once
- **WHEN** an open MR lists two or more reviewer users effective for its project as reviewers
- **THEN** the system SHALL treat it as a single review candidate and review it at most once per pass

#### Scenario: Reviewer of another project only
- **WHEN** project A sets its own reviewer list and an open MR in project A has as reviewer only a user listed for project B or in the global list
- **THEN** the system SHALL NOT review that MR

#### Scenario: Disabled project
- **WHEN** a configured project is disabled
- **THEN** the system SHALL NOT query or review any of its MRs

### Requirement: Closed and merged merge requests on request
When the polling command is invoked with the include-closed flag, the system SHALL also consider closed and merged merge requests with a configured reviewer as review candidates, and SHALL show each candidate's state when listing them. All other discovery rules (reviewer filter, drafts, already-reviewed markers) SHALL apply unchanged.

#### Scenario: Merged MR found with the flag
- **WHEN** the include-closed flag is set and a merged MR has a configured reviewer and no review-state marker
- **THEN** the system SHALL treat it as a review candidate and SHALL label it as merged in the interactive list

#### Scenario: Merged MR already reviewed
- **WHEN** the include-closed flag is set and a merged MR already carries a valid review-state marker
- **THEN** the system SHALL NOT review it again

#### Scenario: MR ref no longer available
- **WHEN** GitLab no longer serves the MR's head ref for an old closed or merged MR, but both review commits are available to the local clone
- **THEN** the system SHALL still run the review against those commits

### Requirement: Draft merge requests are skipped by default
The system SHALL skip merge requests marked as draft unless draft reviewing is enabled for the MR's project, either by the project's own setting or, if the project sets none, by the global setting.

#### Scenario: Draft MR with default configuration
- **WHEN** an open MR with a configured reviewer is marked as draft and draft reviewing is not enabled for its project
- **THEN** the system SHALL NOT review that MR in this pass

#### Scenario: Draft MR with draft reviewing enabled
- **WHEN** draft reviewing is enabled for the MR's project and an open MR with a configured reviewer is marked as draft
- **THEN** the system SHALL treat it as a review candidate

#### Scenario: Project disables drafts despite global setting
- **WHEN** draft reviewing is enabled globally but a project explicitly disables it
- **THEN** the system SHALL NOT review draft MRs of that project

### Requirement: Already-reviewed merge requests are skipped
The system SHALL skip a merge request that already carries a review-state marker in a comment authored by the GitLab user the system acts as, and SHALL determine this solely from the MR's comments in GitLab, without any local state. The system SHALL perform this check again immediately before starting the review of each candidate, not only when the candidate list is built.

#### Scenario: MR already reviewed at its current head
- **WHEN** the MR has a comment by the system's GitLab user containing the review-state marker for the MR's current head commit
- **THEN** the system SHALL NOT review it again

#### Scenario: MR reviewed at an earlier head commit
- **WHEN** the MR has a comment by the system's GitLab user containing a review-state marker for a head commit other than the current one
- **THEN** the system SHALL NOT review it again in this change's behavior (re-review on new commits is out of scope)

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

### Requirement: Review commit range comes from GitLab
For each review candidate, the system SHALL use the base and head commit identifiers that GitLab reports for the merge request's current diff, not values supplied by hand.

#### Scenario: Base and head resolved for a candidate
- **WHEN** a review candidate is selected
- **THEN** the review SHALL run against the base and head commits GitLab reports for that MR's current diff
