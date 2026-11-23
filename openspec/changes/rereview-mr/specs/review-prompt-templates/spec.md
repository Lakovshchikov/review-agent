# Spec Delta

## ADDED Requirements

### Requirement: Re-review prompt has its own template
The system SHALL render the prompt of a re-review round from a template that is separate from the first-review template: a built-in one by default, or a custom file set by the configuration key for the re-review template. The first-review prompt SHALL be unchanged by this. A custom re-review template SHALL be able to extend the built-in re-review template and SHALL come only from the orchestrator's configuration, never from the reviewed repository.

#### Scenario: Built-in re-review template
- **WHEN** a re-review runs and no re-review template is configured
- **THEN** the prompt SHALL be rendered from the built-in re-review template

#### Scenario: First review unaffected
- **WHEN** a first review runs while a re-review template is configured
- **THEN** the prompt SHALL be rendered from the first-review template exactly as without that setting

#### Scenario: Custom re-review template
- **WHEN** the configuration sets a path to a re-review template file
- **THEN** a re-review SHALL use that file

### Requirement: Re-review template variables
In addition to the variables of the first-review template, the system SHALL make available to a re-review template: the text of the previous report, the previous reviewed commit (or nothing when unavailable), the head commit, the list of comments by other users after the previous report with their author and time, and whether older comments were omitted.

#### Scenario: Variables rendered
- **WHEN** the built-in re-review template is rendered for a round with a previous commit and two replies
- **THEN** the prompt SHALL contain the previous report, both replies with author and time, and both commits

#### Scenario: Previous commit missing
- **WHEN** the previous commit is unavailable
- **THEN** the built-in template SHALL state that the history is unavailable and SHALL NOT mention a commit range

### Requirement: Re-review template is checked like the first-review template
The pre-work template check and the prompt check command SHALL also check the re-review template of every project and of the global settings (existence, syntax, unknown variables, unused variables), with the same error and warning rules and exit codes as for the first-review template, and the preview SHALL be able to show the re-review prompt.

#### Scenario: Error in re-review template
- **WHEN** the configured re-review template file does not exist
- **THEN** the check SHALL report an error and no polling pass SHALL start

#### Scenario: Preview of the re-review prompt
- **WHEN** the user asks the prompt check command to show the re-review preview
- **THEN** the output SHALL contain the re-review prompt rendered with readable sample values
