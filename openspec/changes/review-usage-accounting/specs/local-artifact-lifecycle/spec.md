# Spec Delta

## ADDED Requirements

### Requirement: Usage ledger is kept across passes
The usage ledger SHALL be located in the working directory's usage area. It is neither a transient run file nor a retained artifact: transient-file deletion, leftover cleanup, and age-based expiry SHALL NOT delete or truncate it.

#### Scenario: Ledger survives cleanup
- **WHEN** a polling pass starts and runs its cleanup with a retention period shorter than the age of the oldest ledger record
- **THEN** the ledger and all its records SHALL remain unchanged

#### Scenario: Ledger location
- **WHEN** usage accounting is enabled and a review runs
- **THEN** the ledger SHALL be written under the configured working directory, in its usage area
