# local-artifact-lifecycle Specification

## Purpose

Keeps everything the review agent writes to the local machine in one configured working directory, deletes transient files as soon as a run no longer needs them, and retains only diagnostic artifacts for a bounded, configurable time, so an unattended scheduled agent leaves a predictable and verifiable footprint.

## Requirements

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

### Requirement: Every polling pass writes a log file
Each invocation of the polling command SHALL write its own log file in the working directory's log area, containing timestamped copies of everything the pass reports to the console plus the pass start, end, and duration. The log file SHALL be written in UTF-8.

#### Scenario: Normal pass
- **WHEN** a polling pass discovers, reviews, and publishes MRs
- **THEN** a new log file SHALL exist after the pass containing the candidates found, each review's outcome, the final summary, and the pass duration

#### Scenario: Pass that does not start
- **WHEN** a polling pass stops before reviewing because another pass holds the lock or GitLab is unreachable
- **THEN** its log file SHALL record the reason it stopped

#### Scenario: Invalid configuration
- **WHEN** the configuration file is missing, is not valid YAML, or fails validation
- **THEN** the system SHALL report the error on standard error, exit with a non-zero status, and record the error in a log file in the configured working directory if that setting can still be read, otherwise in the default working directory

#### Scenario: Non-ASCII text
- **WHEN** MR titles or messages contain Cyrillic text
- **THEN** the log file SHALL contain that text without corruption

### Requirement: Pass runs without a console
A polling pass SHALL complete normally, including writing its log file, when no console output stream is available.

#### Scenario: Started by a scheduler without a console
- **WHEN** the polling command runs in automatic mode and its standard output and standard error are unavailable
- **THEN** the pass SHALL review and publish as usual and SHALL record its output in the log file

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

### Requirement: Debug mode keeps run artifacts
When invoked with the debug flag, the system SHALL keep all files of each review run (except the removed worktree checkout itself) in the working directory's debug area instead of deleting them, and SHALL delete them later only by the retention period.

#### Scenario: Debug polling pass
- **WHEN** a polling pass runs with the debug flag and reviews an MR
- **THEN** that run's prompt, safety note, report, publication body, and harness output SHALL remain in the debug area after the pass

#### Scenario: Next pass without debug
- **WHEN** a non-debug pass starts after a debug pass, within the retention period
- **THEN** it SHALL NOT delete the debug artifacts

### Requirement: Cleanup stays inside the working directory
Cleanup SHALL NOT delete anything outside the configured working directory, and SHALL NOT delete the working directory's lock while a pass holds it.

#### Scenario: Unrelated file next to the working directory
- **WHEN** an old file exists in the parent of the working directory
- **THEN** cleanup SHALL leave it in place

### Requirement: Cleanup failure does not fail the pass
A failure to delete any file SHALL be recorded in the pass log as a warning and SHALL NOT stop the pass or by itself change its exit status.

#### Scenario: File locked by another process
- **WHEN** an expired log file cannot be deleted because another process has it open
- **THEN** the pass SHALL log a warning naming the file and SHALL continue with discovery and reviews
