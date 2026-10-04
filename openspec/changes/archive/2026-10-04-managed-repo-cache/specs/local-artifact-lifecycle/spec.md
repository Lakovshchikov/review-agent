# Spec Delta

## MODIFIED Requirements

### Requirement: Single configured working directory
The system SHALL write every file it creates during a review or polling pass (worktrees, prompts, reports, request bodies, locks, logs, dry-run output, debug artifacts, managed repository copies) under one working directory set in configuration, except the manual review command's final report, which goes to the configured report path. When the setting is absent, a documented default directory SHALL be used.

#### Scenario: Configured working directory
- **WHEN** the configuration sets the working directory to a path and a polling pass runs
- **THEN** every file the pass creates SHALL be located under that path

#### Scenario: No working directory configured
- **WHEN** the configuration does not set the working directory
- **THEN** the system SHALL use the documented default directory and SHALL NOT fail

#### Scenario: Managed copies are kept apart from per-run checkouts
- **WHEN** a pass reviews an MR of a project without a local clone
- **THEN** the managed copy SHALL be located in the working directory's repository area and the review's checkout SHALL be located in the transient area, not inside the repository area

### Requirement: Transient run files are deleted after each review
Unless debug mode is on, the system SHALL delete all transient files of a review run (worktree, prompt, safety note, report, publication request body, harness output) as soon as that merge request has been processed, whether the review succeeded, failed, or was skipped. Managed repository copies are not transient files of a run.

#### Scenario: Successful published review
- **WHEN** a polling pass reviews and publishes an MR without debug mode
- **THEN** after the pass no file belonging to that review run SHALL remain in the working directory's transient area

#### Scenario: Failed review
- **WHEN** a review run fails without debug mode
- **THEN** its transient files SHALL be deleted, and only its harness error output SHALL be kept, as a log file under the working directory's log area

#### Scenario: Manual review keeps only the report
- **WHEN** the manual review command completes without debug mode
- **THEN** the report SHALL exist at the configured report path and no other file of that run SHALL remain in the working directory

#### Scenario: Managed copy survives the review
- **WHEN** a review against a managed copy finishes, successfully or not
- **THEN** the review's checkout SHALL be removed and the managed copy SHALL remain in the repository area

### Requirement: Leftovers of interrupted runs are deleted at the next pass
At the start of each polling pass that holds the working-directory lock, the system SHALL delete everything left in the working directory's transient area by earlier runs that did not finish cleanly, including stale review worktrees registered in the configured local clones and in the managed repository copies, and incompletely obtained managed copies.

#### Scenario: Previous pass was killed mid-review
- **WHEN** a pass was terminated during a review and left a worktree and run files behind
- **THEN** the next pass SHALL remove them before discovering candidates

#### Scenario: Previous pass was killed during a review against a managed copy
- **WHEN** a pass was terminated during a review against a managed copy
- **THEN** the next pass SHALL remove the leftover checkout and its registration in the managed copy, and SHALL keep the managed copy itself

#### Scenario: Previous pass was killed while obtaining a copy
- **WHEN** a pass was terminated while obtaining a managed copy
- **THEN** the next pass SHALL delete the incomplete data before discovering candidates

### Requirement: Retained artifacts expire
At the start of each polling pass that holds the working-directory lock, the system SHALL delete retained artifacts (pass logs, failed-review harness logs, dry-run output, debug artifacts) older than the configured retention period. The retention period SHALL default to 7 days and SHALL be possible to disable. Managed repository copies are not retained artifacts and SHALL expire only by their own repository retention period.

#### Scenario: Artifacts older than the retention period
- **WHEN** a pass starts and retained artifacts were last modified more than the retention period ago
- **THEN** the system SHALL delete them and record in the pass log how many were removed

#### Scenario: Recent artifacts are kept
- **WHEN** retained artifacts were last modified within the retention period
- **THEN** the system SHALL NOT delete them

#### Scenario: Retention disabled
- **WHEN** retention is disabled in configuration
- **THEN** the system SHALL NOT delete retained artifacts by age

#### Scenario: Pass did not get the lock
- **WHEN** a polling pass exits because another pass holds the lock
- **THEN** it SHALL NOT delete anything

#### Scenario: Managed copy older than the artifact retention period
- **WHEN** a managed copy was last used longer ago than the artifact retention period but within the repository retention period
- **THEN** the system SHALL NOT delete it
