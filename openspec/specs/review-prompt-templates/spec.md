# review-prompt-templates Specification

## Purpose

Lets the operator define the review prompt per project as a template file referenced from configuration, with a built-in default that reproduces the validated baseline prompt, and checks templates before any review so that a broken or incomplete prompt is caught up front.

## Requirements

### Requirement: Review prompt is rendered from a template file
The system SHALL render each run's review prompt from a template. When no template is configured, the system SHALL use a built-in template whose rendered output is identical, character for character, to the review prompt the system produced before templates were introduced, for the same inputs. The configuration SHALL reference a custom template only by file path and SHALL NOT contain prompt text.

#### Scenario: Built-in template reproduces the baseline prompt
- **WHEN** a review runs with no template configured, for any combination of present or absent repository instructions file, docs directory, knowledge-skill files, MR title and MR description
- **THEN** the prompt given to the harness SHALL be identical to the prompt produced before templates were introduced for the same inputs

#### Scenario: Custom template replaces the prompt
- **WHEN** a project's effective settings reference a template file
- **THEN** the prompt for that project's reviews SHALL be rendered from that file

### Requirement: Template variables
The system SHALL make the following values available to a template: the worktree path, the base commit, the MR title, the MR description, the path of the target repository's instructions file (or nothing when not found), the path of its docs directory (or nothing when not found), and the list of knowledge-skill file paths. Wording, conditions and layout of every section of the prompt, including the sections built from these values and the text shown for an empty title or description, SHALL be defined by the template and not by the system.

#### Scenario: Optional section absent
- **WHEN** the target repository has no instructions file and no docs directory and the built-in template is used
- **THEN** the prompt SHALL contain no repository-context section

#### Scenario: Template decides section wording
- **WHEN** a custom template renders the knowledge-skill paths under its own heading
- **THEN** the prompt SHALL contain that heading and the skill paths, and no skill wording from the built-in template

### Requirement: Custom template may extend the built-in template
A custom template SHALL be able to extend the built-in template and replace only selected named blocks of it, keeping the rest of the built-in text. A custom template SHALL also be able to include other template files from its own directory.

#### Scenario: Only the checklist block is replaced
- **WHEN** a custom template extends the built-in template and overrides only the block with the list of what to check
- **THEN** the rendered prompt SHALL equal the built-in prompt except for that block

### Requirement: Templates come only from the orchestrator configuration
The system SHALL load templates only from paths in its own configuration and from the built-in templates, and SHALL NOT load a template or template fragment from the repository under review.

#### Scenario: Reviewed repository contains a template-like file
- **WHEN** the reviewed repository contains a file with the same name as the configured template
- **THEN** the system SHALL render the prompt from the configured file, not from the repository's file

### Requirement: Template check reports errors and warnings
The system SHALL check a template by rendering it with sample values and SHALL classify problems as:
- errors: the template file does not exist or cannot be read, the template has a syntax error, or the template refers to a variable or attribute that does not exist;
- warnings: the template does not use one of the template variables, or a configured knowledge-skill file does not exist.

The check SHALL report every problem it finds in all checked templates in one result, not stop at the first one, except that a syntax error in a file SHALL be reported alone for that file because the file cannot be analysed further. A template used by several projects SHALL be checked once and reported with all projects that use it. During actual reviews the system SHALL render templates strictly, never substituting an empty value for an unknown variable.

#### Scenario: Several unknown variables
- **WHEN** a template refers to two variables that do not exist and does not use the worktree path
- **THEN** the check SHALL report two errors naming both variables and one warning naming the worktree path, in one result

#### Scenario: Variable used only in a conditional section
- **WHEN** a template uses the skill paths only inside a section shown when skills are configured
- **THEN** the check SHALL NOT report the skill paths as unused

#### Scenario: Variable used only in the extended template
- **WHEN** a custom template extends the built-in template and does not override the blocks that use the base commit
- **THEN** the check SHALL NOT report the base commit as unused

### Requirement: Templates are checked before any review work
Before any review work begins (for the polling command: before acquiring the pass lock, contacting GitLab or claiming an MR; for the manual command: before preparing a worktree), the system SHALL check the effective template of every project it may review (every enabled project for the polling command, the global settings for the manual command) and SHALL print all errors and warnings together.
- If there is any error, the system SHALL NOT start the work and SHALL exit with status 2.
- If there are only warnings and the command runs interactively with a console, the system SHALL ask once whether to continue; a negative answer SHALL end the command with status 2 without doing any work.
- If there are only warnings and the command runs without questions (the polling command's automatic mode, or no interactive console), the system SHALL write the warnings to its output and pass log and continue without asking.

#### Scenario: Error stops the pass
- **WHEN** an enabled project's template refers to an unknown variable and the polling command is started
- **THEN** the command SHALL print the error, SHALL NOT contact GitLab, and SHALL exit with status 2

#### Scenario: Warnings in interactive mode
- **WHEN** two projects' templates each have one unused variable and the polling command runs interactively
- **THEN** the system SHALL print both warnings and ask a single question whether to continue

#### Scenario: Warnings in automatic mode
- **WHEN** a template has an unused variable and the polling command runs in automatic mode
- **THEN** the warning SHALL be written to the pass log and the pass SHALL continue without a question

#### Scenario: Disabled project with a broken template
- **WHEN** a disabled project references a template file that does not exist
- **THEN** the polling command SHALL NOT report it and SHALL proceed with the enabled projects

### Requirement: Prompt check command
The system SHALL provide a command that checks prompt templates without running a review or contacting GitLab:
- by default it SHALL check the global settings and the effective template of every configured project, enabled or not;
- with a project path it SHALL check only that project's effective settings;
- with a template file path it SHALL check that file on its own, without a configuration's projects;
- on request it SHALL print the prompt rendered with sample values.

It SHALL exit with status 0 when no problems are found, 1 when only warnings are found, and 2 when any error is found or the configuration cannot be loaded.

#### Scenario: Clean configuration
- **WHEN** the command is run against a configuration whose templates all use every variable correctly
- **THEN** it SHALL report no problems and exit with status 0

#### Scenario: Checking a new template file
- **WHEN** the command is run with a template file that extends the built-in template and overrides one block correctly
- **THEN** it SHALL report no problems and exit with status 0

#### Scenario: Unknown project
- **WHEN** the command is run with a project path that is not in the configuration
- **THEN** it SHALL report that the project is not configured and exit with status 2

#### Scenario: Preview
- **WHEN** the command is run with the preview option
- **THEN** it SHALL print the rendered prompt for each checked template in addition to the check result
