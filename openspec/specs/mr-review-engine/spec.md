# mr-review-engine Specification

## Purpose

Produces a high-recall markdown code-review report for a merge request by running a configurable agentic model harness against an isolated local checkout, without relying on a prescriptive review pipeline.

## Requirements

### Requirement: Isolated merge request checkout
The system SHALL prepare an isolated git worktree for the configured repository, checked out at the review's head commit, before invoking the review engine, and SHALL remove that worktree after the run completes, whether the run succeeds or fails.

#### Scenario: Successful review cleans up the worktree
- **WHEN** a review run for a given repository and base/head commits completes successfully
- **THEN** the system SHALL have removed the isolated worktree and SHALL NOT leave it on disk

#### Scenario: Failed review still cleans up the worktree
- **WHEN** a review run fails (harness error, invalid commits, or any other failure)
- **THEN** the system SHALL still remove the isolated worktree before exiting

#### Scenario: Review never touches the user's primary working copy
- **WHEN** a review run executes for a repository the user also has checked out locally
- **THEN** the system SHALL NOT modify files, branches, or the index of the user's primary working copy

### Requirement: Read-only execution safety
During a review run, the system SHALL restrict the agentic harness to read-only operations on the repository, SHALL NOT allow it to execute, build, test, or lint code contained in the merge request, and SHALL NOT grant it general web/network access beyond the configured model provider endpoint.

#### Scenario: Harness attempts to run a script from the MR
- **WHEN** the harness's tool use would execute a script, package manager command, or build/test/lint command originating from the reviewed repository
- **THEN** the system SHALL block that action

#### Scenario: Harness has no general internet access
- **WHEN** the harness runs during a review
- **THEN** its outbound network access SHALL be limited to the configured model provider endpoint, and it SHALL NOT be able to reach arbitrary external sites

### Requirement: Review methodology lives in the per-run prompt, not system configuration
The system SHALL keep the agentic harness's persistent/system-level configuration limited to runtime and safety instructions, and SHALL deliver the review methodology (what to check, severity labels, output format) exclusively through a per-run review prompt.

#### Scenario: System configuration contains no review methodology
- **WHEN** the system generates the harness's persistent/system-level configuration for a run
- **THEN** that configuration SHALL contain only safety/runtime instructions (execution restrictions, checkout/scratch paths, output language) and SHALL NOT contain review criteria, severity scheme, or exploration workflow

#### Scenario: Review prompt carries the review methodology
- **WHEN** the system builds the per-run prompt for a review
- **THEN** that prompt SHALL state what to check (correctness, readability, testability, security, SRP, contracts, best practices), the severity scheme (Blocker/Major/Minor), and that low-confidence findings with technical grounding are acceptable

### Requirement: Minimal prepared context
The system SHALL hand the harness only the minimum context needed to start (worktree path, base and head commit identifiers, scratch path, merge request title and description, and a pointer to the target repository's own instructions file and docs directory if present), and SHALL NOT precompute a diff, changed-file grouping, risk-zone classification, or other prepared analysis before invoking the harness.

#### Scenario: No prebuilt diff or risk map is passed to the harness
- **WHEN** the system prepares a review run
- **THEN** it SHALL NOT generate a diff file, changed-file list, risk-zone grouping, or dependency-impact summary as part of the run's prepared input

#### Scenario: Repository instructions are pointed to, not inlined
- **WHEN** the target repository contains an AGENTS.md, CLAUDE.md, or docs directory
- **THEN** the system SHALL include their paths in the run's input and SHALL NOT read or inline their contents itself

### Requirement: Configurable model provider and reasoning effort
The system SHALL allow the model provider used by the harness (including Claude, Codex, and locally hosted models) to be set through configuration without code changes, and SHALL allow the reasoning/thinking effort to be set explicitly per provider rather than left at an implicit default.

#### Scenario: Switching providers requires only configuration
- **WHEN** the user changes the configured model provider from one supported provider to another
- **THEN** the system SHALL use the newly configured provider on the next run without requiring any code change

#### Scenario: Reasoning effort is explicit
- **WHEN** the system invokes the harness
- **THEN** it SHALL pass an explicitly configured reasoning/thinking effort value for the active provider rather than rely on the harness's undocumented default

### Requirement: Optional knowledge-skill hints
The system SHALL support attaching zero or more knowledge-skill files (reference material describing common issue patterns) to a review run, toggleable per run, and SHALL present any attached skill content to the harness as optional reference material rather than as a mandatory review workflow.

#### Scenario: Review runs with no skills attached
- **WHEN** a review run is configured with no knowledge-skill files
- **THEN** the system SHALL proceed using only the direct review prompt, with no reduction in the harness's exploration freedom

#### Scenario: Review runs with a skill attached
- **WHEN** a review run is configured with one or more knowledge-skill files
- **THEN** the system SHALL make their content available to the harness as supplementary reference and SHALL NOT instruct the harness to limit findings to the patterns they describe

### Requirement: Markdown review report output
The system SHALL produce the review's findings as a single markdown file written to a configured local output path after the harness run completes.

#### Scenario: Successful run produces a report file
- **WHEN** a review run completes successfully
- **THEN** the system SHALL write a markdown file containing the harness's review findings to the configured output path

### Requirement: Single-invocation CLI entrypoint
The system SHALL expose one CLI command that accepts a target repository location and a base/head commit range (or equivalent identifiers) as input and, on completion, leaves only the markdown report as output, without starting any scheduler, daemon, or GitLab interaction. GitLab interaction SHALL be confined to the separate polling command; the review engine itself SHALL remain independent of GitLab.

#### Scenario: Running the entrypoint against a known repository and commit range
- **WHEN** the CLI entrypoint is invoked with a repository location and base/head commits
- **THEN** the system SHALL run the full review (checkout, harness invocation, report generation, cleanup) and exit, without performing any GitLab API calls or scheduling future runs

#### Scenario: Review engine invoked by the polling command
- **WHEN** the polling command runs a review for a discovered merge request
- **THEN** the review itself (checkout, harness invocation, report generation, cleanup) SHALL behave exactly as for a manual invocation with the same repository and commits, and all GitLab calls SHALL happen outside it
