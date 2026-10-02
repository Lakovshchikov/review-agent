# Spec Delta

## Purpose

Provides the single, stateless, idempotent command that an external scheduler invokes to discover, review, and publish reviews for all due merge requests in one pass.

## ADDED Requirements

### Requirement: Single-pass polling command
The system SHALL expose a CLI command that performs exactly one pass of discovery, review, and publication across all configured projects and then exits, without starting a loop, daemon, or scheduler and without depending on which process invoked it.

#### Scenario: Pass completes and exits
- **WHEN** the polling command is invoked
- **THEN** the system SHALL process every review candidate found in this pass and then exit

#### Scenario: Repeated invocation is idempotent
- **WHEN** the polling command is invoked twice in a row with no MR changes in between and the first pass succeeded for every candidate
- **THEN** the second pass SHALL post no comments

### Requirement: Review runs against a configured local clone
For each configured project the system SHALL run reviews against a configured local clone, SHALL make the MR's head commit available in that clone before reviewing (including MRs from forks), and SHALL NOT modify the clone's working files, current branch, or index.

#### Scenario: Head commit not yet present locally
- **WHEN** a candidate's head commit is not present in the configured local clone
- **THEN** the system SHALL fetch it from GitLab before running the review

#### Scenario: Local clone missing or invalid
- **WHEN** the configured local clone path for a project does not exist or is not a git repository
- **THEN** the system SHALL report that project as failed and SHALL continue with the other projects

### Requirement: Per-MR failure isolation
A failure while processing one merge request SHALL NOT prevent the system from processing the remaining candidates in the same pass.

#### Scenario: One MR fails, others succeed
- **WHEN** the review of one candidate fails and other candidates remain
- **THEN** the system SHALL continue to review and publish the remaining candidates

### Requirement: Pass outcome is reported
At the end of a pass the system SHALL output a per-MR summary (reviewed and published, skipped with reason, or failed with reason) and SHALL exit with a non-zero status if any candidate or project failed, and zero otherwise.

#### Scenario: All candidates succeed or are skipped
- **WHEN** every candidate in the pass was either published or skipped
- **THEN** the command SHALL exit with status zero

#### Scenario: At least one failure
- **WHEN** any candidate or project failed during the pass
- **THEN** the command SHALL still finish the pass and SHALL exit with a non-zero status

#### Scenario: GitLab unreachable
- **WHEN** GitLab cannot be reached or the GitLab CLI is not authenticated at the start of the pass
- **THEN** the system SHALL exit with a non-zero status and an error that names the cause, without running any reviews
