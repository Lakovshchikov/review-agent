# Spec Delta

## Purpose

Determines the effective review settings for each configured GitLab project — whether it is polled, who counts as its reviewer, its draft policy, the model provider and the knowledge-skill files — falling back to the global configuration for anything the project does not set.

## ADDED Requirements

### Requirement: Project settings fall back to global configuration
For each configured project the system SHALL use the project's own value for reviewers, draft reviewing, model provider, and knowledge-skill files when the project sets it, and the global configured value otherwise. A project-level value SHALL replace the global value as a whole, not be merged with it.

#### Scenario: Project without overrides
- **WHEN** a configured project sets none of the overridable settings
- **THEN** the system SHALL review that project's MRs with the global reviewers, draft policy, provider, and knowledge-skill files, exactly as before this change

#### Scenario: Project overrides the provider
- **WHEN** a project sets its own model provider and another project does not
- **THEN** reviews of the first project's MRs SHALL use the project's provider and reasoning effort, and reviews of the second project's MRs SHALL use the global provider

#### Scenario: Project overrides skills with an empty list
- **WHEN** the global configuration attaches knowledge-skill files and a project sets an empty list of knowledge-skill files
- **THEN** reviews of that project's MRs SHALL run with no knowledge-skill files attached

#### Scenario: Project overrides reviewers
- **WHEN** a project sets its own reviewer list
- **THEN** discovery for that project SHALL use only the project's reviewer list, not the global one

### Requirement: Published comment names the model actually used
The header of a published review comment SHALL name the model provider and reasoning effort that were effective for that MR's project.

#### Scenario: Project-specific provider in the comment header
- **WHEN** an MR of a project with its own provider is reviewed and published
- **THEN** the comment header SHALL name the project's provider and model, not the global one

### Requirement: Projects can be disabled
The system SHALL allow a configured project to be disabled through configuration. A disabled project SHALL NOT be polled, reviewed, or reported as failed, and SHALL NOT require its local clone to exist.

#### Scenario: Disabled project is skipped silently
- **WHEN** a project is marked as disabled and its local clone path does not exist
- **THEN** the pass SHALL neither query GitLab for that project nor report it as failed, and SHALL process the other projects normally

#### Scenario: All projects disabled
- **WHEN** every configured project is disabled
- **THEN** the pass SHALL report that nothing is due for review and exit with status zero

### Requirement: Effective settings are validated before any work
The system SHALL validate project-level settings with the same rules as their global counterparts and SHALL reject, before any GitLab call or review, a configuration in which an enabled project ends up with no reviewers.

#### Scenario: No reviewers anywhere for an enabled project
- **WHEN** the global reviewer list is absent and an enabled project does not set its own reviewers
- **THEN** the polling command SHALL exit with a non-zero status and a configuration error naming that project, without querying GitLab

#### Scenario: Global reviewers omitted but every project has its own
- **WHEN** the global reviewer list is absent and every enabled project sets its own non-empty reviewer list
- **THEN** the configuration SHALL be accepted

#### Scenario: Project provider without explicit reasoning effort
- **WHEN** a project sets its own provider but omits the reasoning effort field
- **THEN** the configuration SHALL be rejected with an error naming that project, as it would be for the global provider
