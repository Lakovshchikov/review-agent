# Spec Delta

## ADDED Requirements

### Requirement: Re-review settings follow the same fallback
The re-review prompt template and the limit on the characters of replies passed to a re-review SHALL be resolved per project like the other prompt settings: the project's own value when it sets one, otherwise the global value, otherwise the built-in default. Each is resolved on its own, and setting the re-review template to null SHALL select the built-in one.

#### Scenario: Project overrides only the re-review template
- **WHEN** the global configuration sets a re-review template and a project sets its own
- **THEN** re-reviews of that project's MRs SHALL use the project's template and other projects SHALL use the global one

#### Scenario: Nothing set anywhere
- **WHEN** neither the global configuration nor a project sets the re-review settings
- **THEN** re-reviews SHALL use the built-in re-review template and the default reply limit

#### Scenario: Invalid limit
- **WHEN** the reply limit is not a positive integer
- **THEN** loading the configuration SHALL fail with an error naming the key
