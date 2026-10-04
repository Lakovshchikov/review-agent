## MODIFIED Requirements

### Requirement: Project settings fall back to global configuration
For each configured project the system SHALL use the project's own value for reviewers, draft reviewing, model provider, knowledge-skill files, prompt template, instruction-file candidates and docs-directory candidates when the project sets it, and the global configured value otherwise; for the three prompt settings, when neither the project nor the global configuration sets a value, the built-in default SHALL apply. Each setting SHALL be resolved on its own: a project that sets only some of the prompt settings SHALL inherit the others. A set value SHALL replace the value below it as a whole (a list is replaced, not merged with it).

#### Scenario: Project without overrides
- **WHEN** a configured project sets none of the overridable settings
- **THEN** the system SHALL review that project's MRs with the global reviewers, draft policy, provider, knowledge-skill files and prompt settings, exactly as before this change

#### Scenario: Project overrides the provider
- **WHEN** a project sets its own model provider and another project does not
- **THEN** reviews of the first project's MRs SHALL use the project's provider and reasoning effort, and reviews of the second project's MRs SHALL use the global provider

#### Scenario: Project overrides skills with an empty list
- **WHEN** the global configuration attaches knowledge-skill files and a project sets an empty list of knowledge-skill files
- **THEN** reviews of that project's MRs SHALL run with no knowledge-skill files attached

#### Scenario: Project overrides reviewers
- **WHEN** a project sets its own reviewer list
- **THEN** discovery for that project SHALL use only the project's reviewer list, not the global one

#### Scenario: Project overrides only the prompt template
- **WHEN** the global configuration sets instruction-file candidates and a project sets only its own prompt template
- **THEN** reviews of that project's MRs SHALL use the project's template and the global instruction-file candidates

#### Scenario: Nothing set anywhere
- **WHEN** neither the global configuration nor a project sets any prompt setting
- **THEN** that project's reviews SHALL use the built-in template and the default instruction-file and docs-directory candidates

## ADDED Requirements

### Requirement: Configured file paths are made absolute when loaded
The system SHALL resolve relative paths of prompt templates and knowledge-skill files against the current working directory of the process when the configuration is loaded, and SHALL use the absolute paths from then on, including in the paths handed to the harness.

#### Scenario: Relative skill path reaches the agent
- **WHEN** the configuration lists a knowledge-skill file by a relative path and the process runs in the folder that contains it
- **THEN** the prompt given to the harness SHALL contain the absolute path of that file
