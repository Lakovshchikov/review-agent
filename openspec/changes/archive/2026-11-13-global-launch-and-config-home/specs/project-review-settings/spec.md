# Spec Delta

## MODIFIED Requirements

### Requirement: Configured file paths are made absolute when loaded
The system SHALL resolve relative paths set in the configuration (prompt templates, knowledge-skill files, working directory, manual report path, local clones of projects) against the folder that contains the loaded configuration file when the configuration is loaded, and SHALL use the absolute paths from then on, including in the paths handed to the harness.

#### Scenario: Relative skill path reaches the agent
- **WHEN** the configuration lists a knowledge-skill file by a relative path and the process runs in a different folder than the one containing the configuration
- **THEN** the prompt given to the harness SHALL contain the absolute path of that file, resolved from the configuration's folder

#### Scenario: Configuration in the current folder
- **WHEN** the configuration file lies in the current folder and lists a relative path
- **THEN** the resolved path SHALL be the same as it was when paths were resolved from the current folder
