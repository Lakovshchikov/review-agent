# Spec Delta

## Purpose

Lets the review agent be launched from any console on Windows and macOS while its configuration and every file it writes stay in one home folder, the repository of the tool itself.

## ADDED Requirements

### Requirement: Configuration lookup order
When a command needs the configuration, the system SHALL use the file named by the `--config` option; if the option is absent, `config.yaml` in the folder named by the `REVIEW_AGENT_HOME` environment variable; if that variable is unset or empty, `config.yaml` in the current folder of the process.

#### Scenario: Explicit option wins
- **WHEN** `--config` names a file and `REVIEW_AGENT_HOME` is also set
- **THEN** the file named by `--config` SHALL be loaded

#### Scenario: Home variable
- **WHEN** `--config` is absent, `REVIEW_AGENT_HOME` names a folder with `config.yaml` and the process runs in an unrelated folder
- **THEN** the configuration SHALL be loaded from that folder's `config.yaml`

#### Scenario: Fallback to current folder
- **WHEN** `--config` is absent, `REVIEW_AGENT_HOME` is unset and the current folder has `config.yaml`
- **THEN** that file SHALL be loaded

#### Scenario: No configuration found
- **WHEN** none of the three sources yields an existing file
- **THEN** the command SHALL exit with a non-zero status and an error that names the path it tried and mentions `REVIEW_AGENT_HOME` and `--config`

### Requirement: Relative paths resolve from the configuration folder
Every relative path set in the configuration SHALL be resolved against the folder that contains the loaded configuration file, regardless of the current folder of the process.

#### Scenario: Work directory from another folder
- **WHEN** the configuration sets `storage.work_dir` to `./.review-agent` and a command runs from an unrelated folder
- **THEN** all run files SHALL be created under `.review-agent` next to the configuration file and nothing SHALL be created in the current folder

#### Scenario: Configuration error is logged in the same place
- **WHEN** the configuration cannot be loaded but its `storage.work_dir` can be read
- **THEN** the pass log SHALL be written under that directory resolved from the configuration folder

### Requirement: Report path is shown in the console
Every command that produces a review report file SHALL print the absolute path of that file to standard output when it finishes.

#### Scenario: Manual review
- **WHEN** the manual review command completes
- **THEN** its output SHALL contain the absolute path of the report file

#### Scenario: Dry-run polling pass
- **WHEN** a polling pass with `--dry-run` reviews a merge request
- **THEN** its output SHALL contain the absolute path of the saved comment file

### Requirement: Home setup script for Windows
The repository SHALL provide a PowerShell script that, without any path argument, installs the `review-agent` command into the user's PATH and stores the repository folder in the user-level environment variable `REVIEW_AGENT_HOME`, taking the folder from the script's own location.

#### Scenario: First run
- **WHEN** the script runs on a machine with `pipx` available
- **THEN** the command SHALL be installed in editable mode, `REVIEW_AGENT_HOME` SHALL be set at user level to the repository folder, and the script SHALL tell the user to open a new console

#### Scenario: Repeated run
- **WHEN** the script runs again, for example after the repository folder was moved
- **THEN** the variable SHALL be overwritten and the installation refreshed, with no duplicate entries

#### Scenario: Missing pipx
- **WHEN** `pipx` is not found
- **THEN** the script SHALL stop before changing anything and print how to install it

#### Scenario: Dry run and removal
- **WHEN** the script runs with the dry-run option, or with the removal option
- **THEN** dry-run SHALL only report the planned actions and change nothing, and removal SHALL delete the variable and uninstall the command

### Requirement: Home setup script for macOS
The repository SHALL provide a shell script for macOS with the same behavior and options as the Windows script, storing the variable as a marked `export` block in the user's `~/.zshrc`, with an option to name another startup file.

#### Scenario: Block is replaced, not duplicated
- **WHEN** the script runs twice
- **THEN** `~/.zshrc` SHALL contain exactly one marked block with the current repository folder

#### Scenario: Removal
- **WHEN** the script runs with the removal option
- **THEN** only the marked block SHALL be removed from the startup file and the command SHALL be uninstalled
