# Spec Delta

## MODIFIED Requirements

### Requirement: Report published as an MR comment
After a successful review run, the system SHALL publish the report as one comment on the reviewed merge request, authored by the GitLab user the system acts as, by replacing the text of its own claim comment for that review; if that claim comment no longer exists, it SHALL post the report as a new comment. Publishing SHALL be performed by the orchestrating process, never by the review harness.

#### Scenario: Successful review is published
- **WHEN** a review run for an MR completes and its report passes the sanity check
- **THEN** the MR SHALL carry exactly one comment from this review, containing the full report and no claim marker

#### Scenario: Claim comment was removed during the review
- **WHEN** the system's claim comment was deleted by someone while the review ran
- **THEN** the system SHALL post the report as a new comment

#### Scenario: Harness cannot write to GitLab
- **WHEN** the review harness runs for a discovered MR
- **THEN** it SHALL NOT be given GitLab credentials or any means to post comments, change the MR, or call the GitLab API

### Requirement: No duplicate publication
The system SHALL re-check the MR's comments for an existing valid marker immediately before publishing and SHALL NOT publish if one is found; in that case it SHALL delete its own claim comment.

#### Scenario: Another pass published first
- **WHEN** a valid marker by the system's GitLab user appears on the MR after the review started but before publishing
- **THEN** the system SHALL discard its report, SHALL NOT publish it, and SHALL delete its claim comment

### Requirement: Failed reviews leave no marker
The system SHALL NOT leave any comment or marker on an MR whose review run failed: it SHALL delete the claim comment it posted for that run, so the MR remains eligible on a later pass.

#### Scenario: Harness fails for an MR
- **WHEN** the review run for an MR fails (fetch error, harness error, or unusable report)
- **THEN** the MR SHALL carry no comment from this run and SHALL be a review candidate again on the next pass

### Requirement: Dry-run mode
The system SHALL support a dry-run mode in which discovery and reviews run normally but nothing is written to GitLab.

#### Scenario: Pass in dry-run mode
- **WHEN** a polling pass runs in dry-run mode
- **THEN** the system SHALL save the comment it would have posted in the working directory's dry-run area, SHALL report its location, and SHALL NOT create, edit, or delete any comment, claim, or marker in GitLab
