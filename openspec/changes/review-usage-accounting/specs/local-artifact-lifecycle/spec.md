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
