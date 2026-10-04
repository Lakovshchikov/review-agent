# Spec Delta

## Purpose

Учёт расхода агентского ревью. По каждому прогону собираются токены и
контекст прогона (MR, конфиг, размер изменения, находки) в журнал с
провайдер-нейтральной схемой. По журналу строится сводка с
API-эквивалентом стоимости и оценкой доли лимитов подписки. Это
обоснование стоимости ревью и сравнение моделей.

## ADDED Requirements

### Requirement: Every review run is recorded in the usage ledger
When usage accounting is enabled, the system SHALL append exactly one review record to the usage ledger for every review run it starts, from the polling command and from the manual review command alike, whether the run succeeded, failed, or was interrupted.

#### Scenario: Successful review
- **WHEN** a review run completes and its report is produced
- **THEN** the ledger SHALL contain one new review record for that run with outcome "succeeded"

#### Scenario: Harness fails
- **WHEN** the harness exits with an error during a review run
- **THEN** the ledger SHALL contain one new review record for that run with outcome "failed"
- **AND** the record SHALL include the tokens the harness session consumed, if they can be obtained

#### Scenario: Accounting disabled
- **WHEN** usage accounting is disabled in configuration and a review runs
- **THEN** the system SHALL NOT query the harness for usage and SHALL NOT write to the ledger

### Requirement: Usage collection never affects the review
A failure to obtain usage data or to write the ledger SHALL NOT change the review's outcome, its report, its publication, or the exit status of the command. When usage data cannot be obtained, the review record SHALL still be written, without token counts and with the reason.

#### Scenario: Harness session not found
- **WHEN** a review succeeds but its harness session cannot be identified
- **THEN** the report SHALL be published as usual
- **AND** the review record SHALL have no token counts and SHALL state why usage is missing

#### Scenario: Ledger not writable
- **WHEN** the ledger file cannot be written
- **THEN** the review SHALL proceed as usual and the failure SHALL be recorded as a warning in the pass log

### Requirement: Review record identifies the run and its configuration
Each review record SHALL contain the time, run identifier, invocation source (poll or manual), project path and MR iid when known, base and head commit, provider, model, reasoning effort, the knowledge skills in effect, the harness session identifier when known, outcome, and duration.

#### Scenario: Polling review record
- **WHEN** the polling command reviews MR 544 of project "b2c/front-shopping"
- **THEN** its review record SHALL contain source "poll", that project path, iid 544, the MR's base and head commits, and the provider, model and reasoning effort the project's effective configuration used

#### Scenario: Manual review record
- **WHEN** the manual review command reviews a commit range
- **THEN** its review record SHALL contain source "manual" and the base and head commits, and SHALL leave project and iid empty

### Requirement: Review record carries token usage by kind
When usage is obtained, the review record SHALL report the harness session's tokens separately as uncached input, cache read, cache write, output, and reasoning, as reported by the harness for the whole session, plus the number of model steps when the harness reports it.

#### Scenario: Token breakdown
- **WHEN** the harness reports a session with 218796 uncached input, 121856 cache read, 0 cache write, 3696 output and 2269 reasoning tokens
- **THEN** the review record SHALL contain exactly these five values in their separate fields

### Requirement: Review record carries the change size measured outside the agent
Each review record SHALL contain the size of the reviewed change between base and head: number of changed files, added lines, deleted lines, and number of commits. The system SHALL measure it itself and SHALL NOT include it in the review prompt.

#### Scenario: Size recorded
- **WHEN** a review covers a range with 3 commits changing 12 files (+340/-85 lines)
- **THEN** the review record SHALL contain 12 files, 340 added, 85 deleted, and 3 commits

#### Scenario: Prompt unchanged
- **WHEN** a review runs with usage accounting enabled
- **THEN** the prompt given to the harness SHALL be identical to the prompt produced with accounting disabled

### Requirement: Review record carries findings counted by severity
For a review that produced a report, the review record SHALL contain the number of findings per severity (Blocker, Major, Minor) counted from the report text. When no report was produced, the counts SHALL be absent rather than zero.

#### Scenario: Report with findings
- **WHEN** a report contains two findings marked Major and one marked Minor
- **THEN** the review record SHALL contain Blocker 0, Major 2, Minor 1

#### Scenario: Failed run
- **WHEN** a review run fails without a report
- **THEN** the review record SHALL contain no findings counts

### Requirement: Ledger holds numbers and identifiers only
The usage ledger SHALL NOT contain prompt text, report text, MR title or description, source code, or credentials.

#### Scenario: Record content
- **WHEN** a review record is written for an MR with a title and description
- **THEN** the record SHALL contain neither the title, the description, nor any part of the report

### Requirement: Ledger records use a versioned provider-neutral schema
Each ledger record SHALL be one self-contained line carrying a schema version and record kind. Token, model and provider fields SHALL use OpenTelemetry generative-AI attribute names, review-specific fields SHALL use a separate `review.` namespace, and no field SHALL be specific to one model provider.

#### Scenario: Different providers in one ledger
- **WHEN** one review runs with an OpenAI model and another with a local Ollama model
- **THEN** both review records SHALL have the same set of fields, differing only in values

#### Scenario: Schema version present
- **WHEN** any record is written to the ledger
- **THEN** it SHALL state its schema version and its kind (review or quota)

### Requirement: Manual subscription quota notes
The system SHALL provide a command that records the current used percentage of one or more named subscription limit windows (for example "5h" and "week") as a quota record in the usage ledger, with the time of the note and an optional provider label.

#### Scenario: Recording a note
- **WHEN** the user runs the quota note command with "5h=23" and "week=41"
- **THEN** the ledger SHALL contain one new quota record with window "5h" at 23 percent and window "week" at 41 percent and the current time

#### Scenario: Invalid value
- **WHEN** the user gives a percentage outside 0-100 or a malformed window entry
- **THEN** the command SHALL exit with an error and SHALL NOT write to the ledger

### Requirement: Usage summary command
The system SHALL provide a command that summarizes the usage ledger for a chosen period, grouped by review (default), MR, model, or day, showing review count, tokens by kind, change size, findings, API-equivalent cost and the estimated subscription share, as a table, CSV, or JSON.

#### Scenario: Weekly summary by model
- **WHEN** the user requests the summary for the last 7 days grouped by model
- **THEN** the output SHALL contain one row per provider, model and reasoning effort combination used in that period, with totals for that group

#### Scenario: Records without usage
- **WHEN** the period contains review records without token counts
- **THEN** the summary SHALL count those reviews and SHALL report how many lacked usage data instead of treating them as zero-cost

#### Scenario: Empty or missing ledger
- **WHEN** the ledger is empty or does not exist
- **THEN** the command SHALL report that there is no data and SHALL exit successfully

### Requirement: API-equivalent cost from reference prices
The summary SHALL compute API-equivalent cost from token counts at summary time, pricing uncached input, cache read, cache write, and output plus reasoning separately. Prices SHALL come from the configured price table, which overrides an optional local price catalog file. A model without a price SHALL show cost as unavailable.

#### Scenario: Priced model
- **WHEN** a review used a model whose prices are configured
- **THEN** its cost SHALL equal the sum of each token kind multiplied by its price, with reasoning tokens priced as output

#### Scenario: Price changed later
- **WHEN** the configured prices change after reviews were recorded
- **THEN** the next summary SHALL compute the cost of those reviews with the new prices

#### Scenario: Unpriced model
- **WHEN** a review used a model with no price in the table or the catalog
- **THEN** its cost SHALL be shown as unavailable and SHALL be excluded from cost totals, and the summary SHALL say how many reviews were excluded

#### Scenario: Model priced as another model
- **WHEN** the price table maps a model to another priced model as its stand-in
- **THEN** that model's reviews SHALL be priced with the stand-in's prices and the summary SHALL show which stand-in was used

### Requirement: Estimated subscription share from quota notes
The summary SHALL estimate each review's share of every noted limit window from its API-equivalent cost and a per-window coefficient derived from pairs of consecutive quota notes. Pairs where the percentage decreased SHALL be excluded. The summary SHALL show how many pairs formed the basis and SHALL label the share as an estimate.

#### Scenario: Calibrated window
- **WHEN** consecutive "week" notes rose from 40 to 44 percent while reviews costing 8 dollars API-equivalent in total were recorded between them, and this is the only valid pair
- **THEN** the coefficient SHALL be 0.5 percent per dollar, and a review costing 2 dollars SHALL be shown as about 1 percent of the week window, marked as an estimate based on 1 pair

#### Scenario: Window reset between notes
- **WHEN** a later note of a window shows a lower percentage than the previous note
- **THEN** that pair SHALL NOT contribute to the coefficient

#### Scenario: No calibration available
- **WHEN** no valid note pair exists for a window
- **THEN** the summary SHALL omit the share for that window and state that quota notes are needed
