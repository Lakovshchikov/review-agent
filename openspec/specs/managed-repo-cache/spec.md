# managed-repo-cache Specification

## Purpose

Lets the polling command review projects that have no local clone configured by obtaining, reusing, and eventually expiring its own copies of their repositories, so a project is identified by its GitLab path alone.

## Requirements

### Requirement: Projects without a local clone use a managed copy
For an enabled project that does not configure a local clone, the system SHALL review its merge requests against a copy of the repository that the system obtains and keeps itself under the working directory's repository area, without requiring any manual preparation on the machine beyond an authenticated GitLab CLI.

#### Scenario: First review of a project without a local clone
- **WHEN** a pass reviews an MR of a project that has no local clone configured and no managed copy exists yet
- **THEN** the system SHALL obtain a copy of the project's repository, review the MR against it, and keep the copy after the pass

#### Scenario: Later review reuses the copy
- **WHEN** a later pass reviews an MR of the same project while its managed copy still exists
- **THEN** the system SHALL reuse the existing copy and SHALL bring in only what the MR needs, without obtaining the whole repository again

#### Scenario: Project with a local clone
- **WHEN** a project configures a local clone
- **THEN** the system SHALL NOT create a managed copy for that project

### Requirement: Repository address is derived from the GitLab project
The system SHALL obtain a managed copy over HTTPS from the configured GitLab host, at the address formed from that host and the project's GitLab path. The configuration SHALL NOT need a separate repository address.

#### Scenario: Address of a managed copy
- **WHEN** the GitLab host is `git.example.local` and the project path is `b2c/front-shopping`
- **THEN** the managed copy SHALL be obtained from `https://git.example.local/b2c/front-shopping.git`

### Requirement: Managed copies authenticate through the GitLab CLI
Network access for a managed copy SHALL authenticate with the credentials of the already-authenticated GitLab CLI. The system SHALL NOT write any credential to its configuration, to the repository address, or to any file, and SHALL NOT wait for interactive credential input.

#### Scenario: Authenticated GitLab CLI
- **WHEN** the GitLab CLI is authenticated for the configured host and a managed copy is created or updated
- **THEN** the operation SHALL succeed without any credential in the configuration file

#### Scenario: Credentials unavailable
- **WHEN** the GitLab CLI cannot supply credentials for the host
- **THEN** obtaining or updating the managed copy SHALL fail promptly with an error naming the project, without prompting for input and without hanging the pass

#### Scenario: No credential on disk
- **WHEN** a managed copy has been created and updated
- **THEN** no access token SHALL appear in the copy's files or in the working directory

### Requirement: Managed copies hold no working files
A managed copy SHALL contain only repository history, without checked-out working files and without installed dependencies; reviews SHALL read the code from a separate per-run checkout, as for a configured local clone.

#### Scenario: Contents of the repository area
- **WHEN** a managed copy exists between passes
- **THEN** it SHALL contain no checked-out project files and no dependency folders

### Requirement: Interrupted creation never leaves a usable-looking copy
A managed copy SHALL become visible as the project's copy only after it was obtained completely. A partially obtained or unusable copy SHALL be discarded and obtained again on the next review that needs it.

#### Scenario: Pass killed while obtaining a copy
- **WHEN** a pass is terminated while obtaining a project's managed copy
- **THEN** the next review of that project SHALL NOT use the incomplete data and SHALL obtain the copy anew

#### Scenario: Damaged copy
- **WHEN** a project's managed copy exists but is not a usable repository
- **THEN** the system SHALL discard it, obtain a new copy, and continue the review

#### Scenario: Copy cannot be obtained
- **WHEN** obtaining a project's managed copy fails
- **THEN** the system SHALL report that MR as failed with the reason and SHALL continue with the remaining candidates

### Requirement: Copies are obtained only when needed
The system SHALL obtain a managed copy only when an MR of that project is actually about to be reviewed in the pass, and at most once per project per pass.

#### Scenario: Project without candidates
- **WHEN** a pass finds no review candidates for a project without a local clone
- **THEN** the system SHALL NOT obtain or update a copy of that project

#### Scenario: Candidate not selected interactively
- **WHEN** in interactive mode the user selects an MR of another project
- **THEN** the system SHALL NOT obtain a copy of the unselected project

### Requirement: Unused managed copies expire
At the start of each polling pass that holds the working-directory lock, the system SHALL delete managed copies that were not used by any review for longer than the configured repository retention period. The period SHALL default to 30 days, SHALL be configured separately from the retention of diagnostic artifacts, and SHALL be possible to disable.

#### Scenario: Copy unused beyond the period
- **WHEN** a pass starts and a managed copy was last used by a review more than the repository retention period ago
- **THEN** the system SHALL delete it and record the deletion in the pass log, whether or not its project is still configured

#### Scenario: Recently used copy is kept
- **WHEN** a managed copy was used by a review within the repository retention period
- **THEN** the system SHALL keep it, even if the copy's files were not otherwise modified during that time

#### Scenario: Expired copy needed again
- **WHEN** a copy was deleted by expiry and a later pass reviews an MR of that project
- **THEN** the system SHALL obtain the copy again as on first use

#### Scenario: Repository expiry disabled
- **WHEN** repository retention is disabled in configuration
- **THEN** the system SHALL NOT delete managed copies by age
