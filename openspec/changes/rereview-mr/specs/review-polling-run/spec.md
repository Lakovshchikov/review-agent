# Spec Delta

## ADDED Requirements

### Requirement: Re-review mode flag
The polling command SHALL accept a flag that switches the pass to re-review mode. In that mode the pass SHALL be interactive: it SHALL list the re-review candidates, review only the one the user selects, and then exit. The flag SHALL combine with the dry-run, closed-merge-request and debug options and SHALL be refused together with the automatic-mode flag.

#### Scenario: User selects one MR for another round
- **WHEN** the pass runs in re-review mode with three reviewed MRs
- **THEN** the system SHALL print a numbered list with project, MR number, title, author, link and the change marks, ask for a number, and re-review only the chosen MR

#### Scenario: User declines
- **WHEN** the user quits at the selection prompt in re-review mode
- **THEN** the system SHALL review nothing, post nothing, and exit with status zero

#### Scenario: Re-review mode without a terminal
- **WHEN** the polling command runs in re-review mode and its standard input is not an interactive terminal
- **THEN** the system SHALL exit with a non-zero status and an error saying re-review is interactive-only, without reviewing anything

#### Scenario: Flag combination refused
- **WHEN** the re-review flag is combined with the automatic-mode flag
- **THEN** the exit status SHALL be the "nothing was started" status and nothing SHALL be queried or reviewed
