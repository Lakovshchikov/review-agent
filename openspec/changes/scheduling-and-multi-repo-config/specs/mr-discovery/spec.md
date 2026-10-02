# Spec Delta

## MODIFIED Requirements

### Requirement: Reviewer-filtered discovery
The system SHALL consider for review only open merge requests, in the configured and enabled GitLab projects, on which at least one of the reviewer users effective for that project (the project's own reviewer list, or the global one if the project sets none) is assigned as a reviewer.

#### Scenario: MR with a configured reviewer is discovered
- **WHEN** an enabled configured project has an open MR whose reviewers include a reviewer user effective for that project
- **THEN** the system SHALL include that MR as a review candidate

#### Scenario: MR without a configured reviewer is ignored
- **WHEN** a configured project has an open MR whose reviewers include none of the reviewer users effective for that project (including MRs where such a user is only the author or assignee)
- **THEN** the system SHALL NOT review that MR

#### Scenario: Closed or merged MR is ignored
- **WHEN** an MR with a configured reviewer is closed or merged and the include-closed flag is not set
- **THEN** the system SHALL NOT review that MR

#### Scenario: MR matched by several configured reviewers is reviewed once
- **WHEN** an open MR lists two or more reviewer users effective for its project as reviewers
- **THEN** the system SHALL treat it as a single review candidate and review it at most once per pass

#### Scenario: Reviewer of another project only
- **WHEN** project A sets its own reviewer list and an open MR in project A has as reviewer only a user listed for project B or in the global list
- **THEN** the system SHALL NOT review that MR

#### Scenario: Disabled project
- **WHEN** a configured project is disabled
- **THEN** the system SHALL NOT query or review any of its MRs

### Requirement: Draft merge requests are skipped by default
The system SHALL skip merge requests marked as draft unless draft reviewing is enabled for the MR's project, either by the project's own setting or, if the project sets none, by the global setting.

#### Scenario: Draft MR with default configuration
- **WHEN** an open MR with a configured reviewer is marked as draft and draft reviewing is not enabled for its project
- **THEN** the system SHALL NOT review that MR in this pass

#### Scenario: Draft MR with draft reviewing enabled
- **WHEN** draft reviewing is enabled for the MR's project and an open MR with a configured reviewer is marked as draft
- **THEN** the system SHALL treat it as a review candidate

#### Scenario: Project disables drafts despite global setting
- **WHEN** draft reviewing is enabled globally but a project explicitly disables it
- **THEN** the system SHALL NOT review draft MRs of that project
