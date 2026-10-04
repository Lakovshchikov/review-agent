# Spec Delta

## RENAMED Requirements

- FROM: `### Requirement: Review runs against a configured local clone`
- TO: `### Requirement: Review runs against the project's repository source`

## MODIFIED Requirements

### Requirement: Review runs against the project's repository source
For each configured project the system SHALL run reviews against the project's repository source: the configured local clone when the project sets one, otherwise the project's managed copy. Before each review the system SHALL bring the MR's target branch and the MR's head (including MRs from forks) up to date in that source, and SHALL NOT modify a local clone's working files, current branch, local branches, or index.

#### Scenario: Head commit not yet present locally
- **WHEN** a candidate's head commit is not present in the project's repository source
- **THEN** the system SHALL fetch it from GitLab before running the review

#### Scenario: Target branch moved since the source was last updated
- **WHEN** the MR's base commit was added to the target branch after the repository source was last updated
- **THEN** the system SHALL update the target branch from GitLab before the review, and the review SHALL run against the MR's base and head commits

#### Scenario: Local branches of a local clone are left alone
- **WHEN** a review updates the MR's target branch in a configured local clone that has a local branch of the same name
- **THEN** that local branch, the current branch, the index, and the working files SHALL remain unchanged

#### Scenario: MR head reference no longer available
- **WHEN** GitLab no longer provides the MR's head reference (for example, for an old closed MR) but the commits can still be obtained
- **THEN** the system SHALL still try to obtain the base and head commits and SHALL review the MR if it succeeds

#### Scenario: Local clone missing or invalid
- **WHEN** a project configures a local clone path that does not exist or is not a git repository
- **THEN** the system SHALL report that project as failed and SHALL continue with the other projects

#### Scenario: Project without a local clone
- **WHEN** a project does not configure a local clone
- **THEN** the configuration SHALL be accepted and the project's MRs SHALL be reviewed against its managed copy

#### Scenario: Remote name without a local clone
- **WHEN** a project sets a remote name but no local clone
- **THEN** the configuration SHALL be rejected with an error naming that project, before any GitLab call or review
