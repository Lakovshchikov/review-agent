# review-polling-run Specification

## Purpose

Provides the single, stateless, idempotent command that an external scheduler invokes to discover, review, and publish reviews for all due merge requests in one pass.

## Requirements

### Requirement: Single-pass polling command
The system SHALL expose a CLI command that performs exactly one pass of discovery, review, and publication across all configured projects and then exits, without starting a loop, daemon, or scheduler and without depending on which process invoked it.

#### Scenario: Pass completes and exits
- **WHEN** the polling command is invoked
- **THEN** the system SHALL process the review candidates selected for this pass and then exit

#### Scenario: Repeated invocation is idempotent
- **WHEN** the polling command is invoked twice in a row in automatic mode with no MR changes in between and the first pass succeeded for every candidate
- **THEN** the second pass SHALL post no comments

### Requirement: Interactive candidate selection by default
Unless automatic mode is requested, the system SHALL list the review candidates found in this pass, each with its project, MR number, title, author, and web link, and SHALL review only the one candidate the user selects.

#### Scenario: User selects one MR
- **WHEN** the pass finds five candidates and runs without the automatic-mode flag
- **THEN** the system SHALL print a numbered list of the five MRs, each with a link to the MR in GitLab, SHALL ask the user to choose one, and SHALL review and publish only the chosen MR

#### Scenario: User declines to select
- **WHEN** the user chooses to quit at the selection prompt
- **THEN** the system SHALL review nothing, post nothing, and exit with status zero

#### Scenario: Invalid selection
- **WHEN** the user enters a value that is not one of the listed numbers or the quit option
- **THEN** the system SHALL report the invalid input and ask again without reviewing anything

#### Scenario: No candidates found
- **WHEN** the pass finds no review candidates
- **THEN** the system SHALL report that nothing is due for review and exit with status zero without prompting

#### Scenario: Interactive mode without a terminal
- **WHEN** the polling command runs without the automatic-mode flag and its standard input is not an interactive terminal
- **THEN** the system SHALL exit with a non-zero status and an error that names the automatic-mode flag, without reviewing anything

### Requirement: Automatic mode reviews all candidates
When invoked with the automatic-mode flag, the system SHALL review every candidate found in the pass, one after another, without prompting.

#### Scenario: Automatic mode with several candidates
- **WHEN** the polling command runs with the automatic-mode flag and the pass finds several candidates
- **THEN** the system SHALL review and publish each of them sequentially, never two at the same time

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
