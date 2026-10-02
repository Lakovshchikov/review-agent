# Spec Delta

## Purpose

Leaves a durable, bounded trace of every polling pass on disk, so unattended scheduled runs can be diagnosed afterwards, and removes local artifacts of old runs so the machine does not fill up over time.

## ADDED Requirements

### Requirement: Every polling pass writes a log file
Each invocation of the polling command SHALL write its own log file, in a configurable log directory, containing timestamped copies of everything the pass reports to the console, plus the pass start, end, and duration. The log file SHALL be written in UTF-8.

#### Scenario: Normal pass
- **WHEN** a polling pass discovers, reviews, and publishes MRs
- **THEN** a new log file SHALL exist after the pass containing the candidates found, each review's outcome, the final summary, and the pass duration

#### Scenario: Pass that does not start
- **WHEN** a polling pass stops before reviewing because another pass is running or GitLab is unreachable
- **THEN** its log file SHALL record the reason it stopped

#### Scenario: Non-ASCII text
- **WHEN** MR titles or messages contain Cyrillic text
- **THEN** the log file SHALL contain that text without corruption

#### Scenario: Invalid configuration
- **WHEN** the configuration file is missing, is not valid YAML, or fails validation
- **THEN** the system SHALL report the error on standard error, exit with a non-zero status, and record the error in a log file in the configured log directory if that setting can still be read, otherwise in the default log directory

### Requirement: Pass runs without a console
A polling pass SHALL complete normally, including writing its log file, when no console output stream is available.

#### Scenario: Started by a scheduler without a console
- **WHEN** the polling command runs in automatic mode and its standard output and standard error are unavailable
- **THEN** the pass SHALL review and publish as usual and SHALL record its output in the log file

### Requirement: Old local artifacts are removed by age
At the start of each polling pass that holds the pass lock, the system SHALL delete local artifacts older than the configured retention period: pass log files, per-run scratch folders, saved publication request bodies, and review reports with their companion files in the report directory. The retention period SHALL default to 14 days and SHALL be possible to disable.

#### Scenario: Artifacts older than the retention period
- **WHEN** a polling pass starts and the log directory, scratch directory, and report directory contain artifacts last modified more than the retention period ago
- **THEN** the system SHALL delete them and record in the pass log how many were removed

#### Scenario: Recent artifacts are kept
- **WHEN** artifacts were last modified within the retention period
- **THEN** the system SHALL NOT delete them

#### Scenario: Retention disabled
- **WHEN** retention is disabled in configuration
- **THEN** the system SHALL delete nothing

#### Scenario: Pass did not get the lock
- **WHEN** a polling pass exits because another pass holds the lock
- **THEN** it SHALL NOT delete anything

### Requirement: Cleanup never touches live or foreign files
Cleanup SHALL only remove files and folders whose names match the artifacts the system itself creates, SHALL NOT remove a per-run scratch folder that still contains a review worktree, and SHALL NOT remove anything outside the configured log, scratch, and report directories.

#### Scenario: Unrelated file in the report directory
- **WHEN** the report directory contains an old file that the system did not create
- **THEN** cleanup SHALL leave it in place

#### Scenario: Old run folder with a leftover worktree
- **WHEN** an old per-run scratch folder still contains a worktree directory
- **THEN** cleanup SHALL leave that folder in place

### Requirement: Cleanup failure does not fail the pass
A failure to delete any artifact SHALL be recorded in the pass log as a warning and SHALL NOT stop the pass or by itself change its exit status.

#### Scenario: File locked by another process
- **WHEN** an expired log file cannot be deleted because another process has it open
- **THEN** the pass SHALL log a warning naming the file and SHALL continue with discovery and reviews

### Requirement: Manual review is unaffected
The manual single-review command SHALL NOT write a pass log file and SHALL NOT perform retention cleanup.

#### Scenario: Manual review run
- **WHEN** the manual review command is run with a repository and base/head commits
- **THEN** no pass log file SHALL be created and no artifacts SHALL be deleted
