# Spec Delta

## Purpose

Provides a supported, repeatable way to make Windows Task Scheduler run the automatic polling pass on an interval, without the review agent itself containing any scheduling logic.

## ADDED Requirements

### Requirement: Registration script installs a periodic automatic pass
The project SHALL provide a PowerShell script that registers a Windows Task Scheduler task running the polling command in automatic mode on a configurable interval, for the current Windows user, with a configurable configuration file and with the project folder as the task's working directory.

#### Scenario: Install with defaults
- **WHEN** the user runs the registration script from the project folder without optional parameters
- **THEN** a task SHALL exist that runs the polling command in automatic mode with the project's default configuration file, starting in the project folder, repeating at the default interval

#### Scenario: Custom interval and configuration
- **WHEN** the user runs the script with an interval and a configuration file path
- **THEN** the registered task SHALL repeat at that interval and pass that configuration file to the polling command

#### Scenario: Re-running the script updates the task
- **WHEN** the script is run again for an already registered task name
- **THEN** the existing task SHALL be replaced with the new settings rather than a second task being created

### Requirement: Scheduled task never runs interactive or test-only modes
The registered task SHALL always pass the automatic-mode flag and SHALL NOT pass the include-closed flag. The script SHALL allow registering the task in dry-run mode.

#### Scenario: Dry-run registration
- **WHEN** the user registers the task with the dry-run option
- **THEN** the scheduled polling command SHALL run with both the automatic-mode and dry-run flags

### Requirement: Scheduled runs do not overlap
The registered task SHALL be configured so that Task Scheduler does not start a new instance while the previous one is still running.

#### Scenario: Pass longer than the interval
- **WHEN** a scheduled pass is still running when the next interval arrives
- **THEN** Task Scheduler SHALL NOT start a second instance

### Requirement: Task can be removed
The registration script SHALL be able to remove the task it registered.

#### Scenario: Uninstall
- **WHEN** the user runs the script with the remove option
- **THEN** the task SHALL no longer exist in Task Scheduler, and running it again SHALL succeed without error

### Requirement: Script fails clearly on a broken setup
The script SHALL refuse to register the task, with an error naming the problem, when the review-agent executable or the configuration file cannot be found.

#### Scenario: Executable not found
- **WHEN** the review-agent executable is not on PATH and no explicit path is given
- **THEN** the script SHALL exit with an error explaining how to pass the executable path, and SHALL NOT register a task
