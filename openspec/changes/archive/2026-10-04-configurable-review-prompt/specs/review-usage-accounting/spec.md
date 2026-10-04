## MODIFIED Requirements

### Requirement: Review record identifies the run and its configuration
Each review record SHALL contain the time, run identifier, invocation source (poll or manual), project path and MR iid when known, base and head commit, provider, model, reasoning effort, the knowledge skills in effect, the prompt template in effect (its configured path, or the built-in template's name) and the SHA-256 of that template file's content, the harness version when known, the harness session identifiers when known, and outcome.

#### Scenario: Polling review record
- **WHEN** the polling command reviews MR 544 of project "b2c/front-shopping"
- **THEN** its review record SHALL contain source "poll", that project path, iid 544, the MR's base and head commits, and the provider, model and reasoning effort the project's effective configuration used

#### Scenario: Manual review record
- **WHEN** the manual review command reviews a commit range
- **THEN** its review record SHALL contain source "manual" and the base and head commits, and SHALL leave project and iid empty

#### Scenario: Prompt template recorded
- **WHEN** one project is reviewed with the built-in template and another with a custom template file
- **THEN** the first record SHALL name the built-in template and the second SHALL name the custom file's path, each with the SHA-256 of its template file
