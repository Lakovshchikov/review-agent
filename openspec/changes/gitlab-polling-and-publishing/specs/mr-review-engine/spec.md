# Spec Delta

## MODIFIED Requirements

### Requirement: Single-invocation CLI entrypoint
The system SHALL expose one CLI command that accepts a target repository location and a base/head commit range (or equivalent identifiers) as input and, on completion, leaves only the markdown report as output, without starting any scheduler, daemon, or GitLab interaction. GitLab interaction SHALL be confined to the separate polling command; the review engine itself SHALL remain independent of GitLab.

#### Scenario: Running the entrypoint against a known repository and commit range
- **WHEN** the CLI entrypoint is invoked with a repository location and base/head commits
- **THEN** the system SHALL run the full review (checkout, harness invocation, report generation, cleanup) and exit, without performing any GitLab API calls or scheduling future runs

#### Scenario: Review engine invoked by the polling command
- **WHEN** the polling command runs a review for a discovered merge request
- **THEN** the review itself (checkout, harness invocation, report generation, cleanup) SHALL behave exactly as for a manual invocation with the same repository and commits, and all GitLab calls SHALL happen outside it
