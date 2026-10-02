# Spec Delta

## MODIFIED Requirements

### Requirement: One pass at a time
The system SHALL allow at most one polling pass or manual review to run at a time per configured working directory, so the same merge request is never reviewed by two overlapping passes from that directory and one run never removes another run's files.

#### Scenario: Second pass started while one is running
- **WHEN** the polling command is invoked while another pass using the same working directory is still running
- **THEN** the second invocation SHALL exit immediately with a non-zero status and a message that a pass is already running, without querying candidates or reviewing anything

#### Scenario: Manual review while a pass is running
- **WHEN** the manual review command is invoked while a polling pass using the same working directory is running
- **THEN** it SHALL exit immediately with a non-zero status and a message that a run is already in progress, without creating a worktree

#### Scenario: Previous pass was killed
- **WHEN** a previous pass terminated abnormally and left its lock behind, and the process that held it is no longer running
- **THEN** the next invocation SHALL treat the lock as stale, take it over, and run normally
