## MODIFIED Requirements

### Requirement: Review methodology lives in the per-run prompt, not system configuration
The system SHALL keep the agentic harness's persistent/system-level configuration limited to runtime and safety instructions, and SHALL deliver the review methodology (what to check, severity labels, output format) exclusively through a per-run review prompt. The prompt SHALL be rendered from the template in effect for the run (see capability `review-prompt-templates`); the built-in template SHALL carry the baseline methodology.

#### Scenario: System configuration contains no review methodology
- **WHEN** the system generates the harness's persistent/system-level configuration for a run
- **THEN** that configuration SHALL contain only safety/runtime instructions (execution restrictions, checkout/scratch paths, output language) and SHALL NOT contain review criteria, severity scheme, or exploration workflow

#### Scenario: Review prompt carries the review methodology
- **WHEN** the system builds the per-run prompt for a review with the built-in template
- **THEN** that prompt SHALL state what to check (correctness, readability, testability, security, SRP, contracts, best practices), the severity scheme (Blocker/Major/Minor), and that low-confidence findings with technical grounding are acceptable

#### Scenario: Custom template carries a custom methodology
- **WHEN** the system builds the per-run prompt with a custom template
- **THEN** the methodology SHALL be whatever that template states, and the harness's persistent configuration SHALL still contain no methodology

### Requirement: Minimal prepared context
The system SHALL hand the harness only the minimum context needed to start (worktree path, base and head commit identifiers, scratch path, merge request title and description, and a pointer to the target repository's own instructions file and docs directory if present), and SHALL NOT precompute a diff, changed-file grouping, risk-zone classification, or other prepared analysis before invoking the harness. The instructions file SHALL be the first existing file from the configured list of instruction-file candidates, and the docs directory SHALL be the first existing directory from the configured list of docs-directory candidates, both relative to the repository root. By default these lists SHALL be `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` and `docs`, `documentation`.

#### Scenario: No prebuilt diff or risk map is passed to the harness
- **WHEN** the system prepares a review run
- **THEN** it SHALL NOT generate a diff file, changed-file list, risk-zone grouping, or dependency-impact summary as part of the run's prepared input

#### Scenario: Repository instructions are pointed to, not inlined
- **WHEN** the target repository contains an AGENTS.md, CLAUDE.md, or docs directory
- **THEN** the system SHALL include their paths in the run's input and SHALL NOT read or inline their contents itself

#### Scenario: Configured instruction-file candidates
- **WHEN** a project's instruction-file candidates are `CONTRIBUTING.md`, `AGENTS.md` and its repository contains both files
- **THEN** the run's input SHALL point to `CONTRIBUTING.md`

#### Scenario: No candidate found
- **WHEN** none of the configured candidates exists in the repository
- **THEN** the review SHALL proceed without a pointer to an instructions file and without an error
