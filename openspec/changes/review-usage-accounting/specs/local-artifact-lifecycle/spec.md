# Spec Delta

## ADDED Requirements

### Requirement: Usage accounting area is kept across passes
The usage ledger and the saved copy of the price catalog SHALL be located in the working directory's usage area. They are neither transient run files nor retained artifacts: transient-file deletion, leftover cleanup, and age-based expiry SHALL NOT delete or truncate them.

#### Scenario: Ledger survives cleanup
- **WHEN** a polling pass starts and runs its cleanup with a retention period shorter than the age of the oldest ledger record
- **THEN** the ledger and all its records SHALL remain unchanged

#### Scenario: Saved price catalog survives cleanup
- **WHEN** a polling pass runs its cleanup and the saved price catalog copy is older than the retention period
- **THEN** the saved copy SHALL remain

#### Scenario: Ledger location
- **WHEN** usage accounting is enabled and a review runs
- **THEN** the ledger SHALL be written under the configured working directory, in its usage area

## MODIFIED Requirements

### Requirement: Transient run files are deleted after each review
Unless debug mode is on, the system SHALL delete all transient files of a review run (worktree, prompt, safety note, report, publication request body, harness output) as soon as that merge request has been processed, whether the review succeeded, failed, or was skipped. Managed repository copies and the usage accounting area are not transient files of a run.

#### Scenario: Successful published review
- **WHEN** a polling pass reviews and publishes an MR without debug mode
- **THEN** after the pass no file belonging to that review run SHALL remain in the working directory's transient area

#### Scenario: Failed review
- **WHEN** a review run fails without debug mode
- **THEN** its transient files SHALL be deleted, and only its harness error output SHALL be kept, as a log file under the working directory's log area

#### Scenario: Manual review keeps only the report
- **WHEN** the manual review command completes without debug mode
- **THEN** the report SHALL exist at the configured report path and no other file of that run SHALL remain in the working directory, except the run's record appended to the usage ledger when usage accounting is enabled

#### Scenario: Managed copy survives the review
- **WHEN** a review against a managed copy finishes, successfully or not
- **THEN** the review's checkout SHALL be removed and the managed copy SHALL remain in the repository area
