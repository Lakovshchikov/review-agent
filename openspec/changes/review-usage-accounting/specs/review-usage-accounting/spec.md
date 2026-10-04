# Spec Delta

## Purpose

Учёт расхода агентского ревью. По каждому прогону собираются токены
(вместе с субагентами), время и контекст прогона (MR, конфиг, размер
изменения, находки) в журнал с провайдер-нейтральной схемой. По журналу
строится сводка с API-эквивалентом стоимости по актуальному справочнику
цен и долей лимитов подписки: это обоснование стоимости ревью и
сравнение моделей.

## ADDED Requirements

### Requirement: Every review run is recorded in the usage ledger
When usage accounting is enabled, the system SHALL append exactly one review record to the usage ledger for every review run it starts, in every mode (scheduled or automatic polling, interactive polling, manual review), whether the run succeeded, failed, or was interrupted.

#### Scenario: Successful review
- **WHEN** a review run completes and its report is produced
- **THEN** the ledger SHALL contain one new review record for that run with outcome "succeeded"

#### Scenario: Scheduled pass without a console
- **WHEN** an automatic polling pass started by the scheduler reviews two MRs
- **THEN** the ledger SHALL contain one review record for each of them, without any user interaction

#### Scenario: Harness fails
- **WHEN** the harness exits with an error during a review run
- **THEN** the ledger SHALL contain one new review record for that run with outcome "failed"
- **AND** the record SHALL include the tokens the harness sessions consumed, if they can be obtained

#### Scenario: Accounting disabled
- **WHEN** usage accounting is disabled in configuration and a review runs
- **THEN** the system SHALL NOT query the harness for usage, SHALL NOT ask about quotas, and SHALL NOT write to the ledger

### Requirement: Usage collection never affects the review
A failure to obtain usage data or to write the ledger SHALL NOT change the review's outcome, its report, its publication, or the exit status of the command. When usage data cannot be obtained, the review record SHALL still be written, without token counts and with the reason.

#### Scenario: Harness session not found
- **WHEN** a review succeeds but its harness session cannot be identified
- **THEN** the report SHALL be published as usual
- **AND** the review record SHALL have no token counts and SHALL state why usage is missing

#### Scenario: Ledger not writable
- **WHEN** the ledger file cannot be written
- **THEN** the review SHALL proceed as usual and the failure SHALL be reported as a warning

### Requirement: Review record identifies the run and its configuration
Each review record SHALL contain the time, run identifier, invocation source (poll or manual), project path and MR iid when known, base and head commit, provider, model, reasoning effort, the knowledge skills in effect, the harness version when known, the harness session identifiers when known, and outcome.

#### Scenario: Polling review record
- **WHEN** the polling command reviews MR 544 of project "b2c/front-shopping"
- **THEN** its review record SHALL contain source "poll", that project path, iid 544, the MR's base and head commits, and the provider, model and reasoning effort the project's effective configuration used

#### Scenario: Manual review record
- **WHEN** the manual review command reviews a commit range
- **THEN** its review record SHALL contain source "manual" and the base and head commits, and SHALL leave project and iid empty

### Requirement: Review record carries token usage by kind
When usage is obtained, the review record SHALL report the run's tokens separately as uncached input, cache read, cache write, output, and reasoning, totalled over all harness sessions of the run, plus the number of model steps when the harness reports it.

#### Scenario: Token breakdown
- **WHEN** the run has a single harness session with 218796 uncached input, 121856 cache read, 0 cache write, 3696 output and 2269 reasoning tokens
- **THEN** the review record SHALL contain exactly these five values in their separate fields

### Requirement: Subagent sessions are included
The system SHALL find every child session spawned during the run, recursively, and SHALL include their tokens in the run's totals. The review record SHALL also list each session of the run with its identifier, parent, agent, tokens by kind, and duration.

#### Scenario: Run with a subagent
- **WHEN** the main session used 100000 uncached input tokens and spawned one subagent session that used 40000
- **THEN** the review record's uncached input total SHALL be 140000
- **AND** the record SHALL list two sessions, the second with the first as its parent

#### Scenario: Nested subagents
- **WHEN** a subagent session itself spawned another session
- **THEN** that grandchild session SHALL also be found, listed, and included in the totals

#### Scenario: No subagents
- **WHEN** the run used only the main session
- **THEN** the record SHALL list exactly one session and its totals SHALL equal that session's tokens

### Requirement: Durations are recorded
Each review record SHALL contain the run's total duration measured by the system around the harness invocation and, for each listed harness session, its duration as reported by the harness.

#### Scenario: Durations present
- **WHEN** a review run's harness invocation took 5 minutes and its single session reports 4 minutes 50 seconds
- **THEN** the record SHALL contain a total duration of 5 minutes and a session duration of 4 minutes 50 seconds

#### Scenario: Usage unavailable
- **WHEN** usage data cannot be obtained for a run
- **THEN** the record SHALL still contain the run's total duration

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
Each ledger record SHALL be one self-contained line carrying a schema version. Token, model and provider fields SHALL use OpenTelemetry generative-AI attribute names, review-specific fields SHALL use a separate `review.` namespace, and no field SHALL be specific to one model provider.

#### Scenario: Different providers in one ledger
- **WHEN** one review runs with an OpenAI model and another with a local Ollama model
- **THEN** both review records SHALL have the same set of fields, differing only in values

#### Scenario: Schema version present
- **WHEN** any record is written to the ledger
- **THEN** it SHALL state its schema version

### Requirement: Interactive quota measurement around a single review
When exactly one MR or commit range is reviewed from an interactive console (polling without the automatic mode, or the manual review command), the system SHALL ask for the current used percentage of each configured limit window before the review starts and again after the harness finishes, and SHALL store both readings in that run's review record. An empty answer SHALL skip that reading.

#### Scenario: Measured review
- **WHEN** the user picks one MR interactively and answers 12 and 40 for windows "5h" and "week" before the review and 21 and 42 after it
- **THEN** the review record SHALL contain the before readings 12 and 40 and the after readings 21 and 42 for those windows

#### Scenario: Skipped measurement
- **WHEN** the user presses Enter without a value at a quota question
- **THEN** the review SHALL proceed and the record SHALL contain no reading for that window at that point

#### Scenario: Invalid answer
- **WHEN** the user enters a value that is not a number between 0 and 100
- **THEN** the system SHALL ask again for that window

#### Scenario: Automatic or scheduled mode
- **WHEN** a polling pass runs in automatic mode or without an interactive console
- **THEN** the system SHALL NOT ask any quota question

### Requirement: Usage summary command
The system SHALL provide a command that summarizes the usage ledger for a chosen period, grouped by review (default), MR, model, or day, showing review count, tokens by kind, durations, change size, findings, API-equivalent cost and subscription share, as a table, CSV, or JSON. It SHALL include every review record of the period, whether or not it has quota readings.

#### Scenario: Weekly summary by model
- **WHEN** the user requests the summary for the last 7 days grouped by model
- **THEN** the output SHALL contain one row per provider, model and reasoning effort combination used in that period, with totals for that group

#### Scenario: Records without usage
- **WHEN** the period contains review records without token counts
- **THEN** the summary SHALL count those reviews and SHALL report how many lacked usage data instead of treating them as zero-cost

#### Scenario: Empty or missing ledger
- **WHEN** the ledger is empty or does not exist
- **THEN** the command SHALL report that there is no data and SHALL exit successfully

### Requirement: API-equivalent cost from the current price catalog
The summary SHALL compute API-equivalent cost at summary time, pricing uncached input, cache read, cache write, and output plus reasoning separately. Prices SHALL come from the price catalog downloaded from its configured location on every summary run, with entries from the configured overrides, written in the catalog's own format, taking precedence. A model without a price SHALL show cost as unavailable.

#### Scenario: Priced model
- **WHEN** a review used a model present in the catalog
- **THEN** its cost SHALL equal the sum of each token kind multiplied by the catalog's per-token price for that kind, with reasoning tokens priced as output

#### Scenario: Catalog updated
- **WHEN** the catalog at its configured location changed since the previous summary
- **THEN** the next summary SHALL compute all costs, including those of earlier reviews, with the new prices

#### Scenario: Override present
- **WHEN** the configuration contains an override entry for a model that is also in the catalog
- **THEN** that model's cost SHALL use the override entry

#### Scenario: Catalog unavailable
- **WHEN** the catalog cannot be downloaded and a previously saved copy exists
- **THEN** the summary SHALL use the saved copy and SHALL warn that prices may be outdated, stating the copy's date

#### Scenario: No catalog at all
- **WHEN** the catalog cannot be downloaded and no saved copy exists
- **THEN** the summary SHALL warn, show costs only for models with overrides, and show the others as unavailable

#### Scenario: Unpriced model
- **WHEN** a review used a model with no price in the catalog or overrides
- **THEN** its cost SHALL be shown as unavailable and excluded from cost totals, and the summary SHALL say how many reviews were excluded

### Requirement: Subscription share per model group
For every limit window, the summary SHALL show each measured review's measured share (after minus before) and SHALL estimate the share of unmeasured reviews from a coefficient computed only from measured reviews of the same provider, model and reasoning effort. Measurements where the percentage decreased SHALL be excluded. Estimates SHALL be labeled as such with the number of measurements behind them.

#### Scenario: Measured review
- **WHEN** a review has "week" readings 40 before and 42 after
- **THEN** the summary SHALL show its "week" share as 2 percent, marked as measured

#### Scenario: Estimated review of the same group
- **WHEN** the only valid measurement of a model group is a review costing 8 dollars API-equivalent with a "week" share of 4 percent, and another review of that group costs 2 dollars
- **THEN** the summary SHALL show the other review's "week" share as about 1 percent, marked as an estimate based on 1 measurement

#### Scenario: Window reset during the review
- **WHEN** a review's after reading is lower than its before reading
- **THEN** that measurement SHALL NOT contribute to any coefficient and its share SHALL be shown as invalid

#### Scenario: Group without measurements
- **WHEN** no valid measurement exists for a review's model group
- **THEN** the summary SHALL show no share for that review and SHALL state that interactive measurements of that model are needed

### Requirement: Harness output drift is reported
When the harness's version is not among the versions usage collection was verified with, or its output does not have the expected shape (missing or mistyped fields, unparseable session list lines), the system SHALL emit an explicit warning visible on the console when there is one and always in the pass log. This SHALL NOT be an error, and SHALL NOT change the review or the exit status.

#### Scenario: Unverified harness version
- **WHEN** usage is collected with a harness version not listed as verified
- **THEN** a warning naming the found and verified versions SHALL be shown once per pass, on the console and in the pass log

#### Scenario: Export field missing
- **WHEN** the harness's session export lacks an expected token field
- **THEN** a warning naming the missing field SHALL be shown on the console and in the pass log, the review SHALL be published as usual, and the record SHALL state the format problem as the reason usage is incomplete

#### Scenario: Drift counted in the summary
- **WHEN** the summary period contains records with format problems
- **THEN** the summary SHALL state how many records were affected
