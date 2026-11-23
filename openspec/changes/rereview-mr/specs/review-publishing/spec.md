# Spec Delta

## MODIFIED Requirements

### Requirement: No duplicate publication
The system SHALL re-check the MR's comments for an existing valid marker immediately before publishing and SHALL NOT publish if one is found; in that case it SHALL delete its own claim comment. In a re-review round the existing earlier markers are expected: the system SHALL then NOT publish only if a marker-bearing comment of the system's user appeared after the report the round was based on.

#### Scenario: Another pass published first
- **WHEN** a valid marker by the system's GitLab user appears on the MR after the review started but before publishing
- **THEN** the system SHALL discard its report, SHALL NOT publish it, and SHALL delete its claim comment

#### Scenario: Re-review over earlier markers
- **WHEN** a re-review round finishes and the only markers of the system's user on the MR are those that existed when it started
- **THEN** the system SHALL publish its report

#### Scenario: Another round published during a re-review
- **WHEN** a re-review round finishes and a marker-bearing comment of the system's user newer than the report it was based on exists on the MR
- **THEN** the system SHALL discard its report, SHALL NOT publish it, and SHALL delete its claim comment
