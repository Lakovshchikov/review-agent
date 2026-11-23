# Spec Delta

## Purpose

Позволяет вручную провести повторный раунд ревью уже отревьюенного MR: агент получает свой прошлый отчёт, реплики после него и сам изучает изменения кода с прошлого раунда, а результат публикуется новым комментарием.

## ADDED Requirements

### Requirement: Re-review is started only manually
The system SHALL perform a re-review round only when the user requests it explicitly in the interactive polling mode, and SHALL NOT perform one in automatic mode, from a scheduler, or on its own initiative.

#### Scenario: Ordinary pass never re-reviews
- **WHEN** a polling pass runs without the re-review flag, in interactive or automatic mode, and an MR already carries a review-state marker
- **THEN** the system SHALL NOT review that MR again

#### Scenario: Re-review flag with automatic mode
- **WHEN** the polling command is invoked with both the re-review flag and the automatic-mode flag
- **THEN** the system SHALL exit with a non-zero status and a message that names both flags, without querying GitLab or reviewing anything

### Requirement: Re-review candidates are already-reviewed merge requests
In re-review mode the system SHALL offer as candidates the merge requests that match the usual discovery filters (reviewer, draft policy, closed-state option) AND carry at least one review-state marker authored by the GitLab user the system acts as. It SHALL NOT offer merge requests without such a marker, and SHALL NOT offer one whose claim from another pass is live.

#### Scenario: Reviewed MR is offered
- **WHEN** the pass runs in re-review mode and a matching MR carries a marker from the system's user
- **THEN** the MR SHALL be in the candidate list

#### Scenario: Never-reviewed MR is not offered
- **WHEN** the pass runs in re-review mode and a matching MR has no marker from the system's user
- **THEN** the MR SHALL NOT be in the candidate list

#### Scenario: Marker of another user does not qualify
- **WHEN** an MR carries a marker authored by a user other than the system's user and no marker of the system's user
- **THEN** the MR SHALL NOT be in the re-review candidate list

#### Scenario: Review in progress elsewhere
- **WHEN** a matching reviewed MR carries a live claim from another pass
- **THEN** the system SHALL NOT review it and SHALL report that its review is already in progress

#### Scenario: Nothing to re-review
- **WHEN** no matching MR carries a marker
- **THEN** the system SHALL report that nothing can be re-reviewed and exit with status zero without prompting

### Requirement: Candidate list shows what changed since the last report
In re-review mode each listed candidate SHALL be marked with the reasons it may be worth another round, derived only from GitLab data: that its head commit differs from the commit of its latest review-state marker, that comments by users other than the system's user exist after the latest report, or that neither holds. A candidate with neither change SHALL still be selectable.

#### Scenario: New commits
- **WHEN** the MR's current head commit differs from the commit in the latest marker
- **THEN** the list entry SHALL be marked as having new commits

#### Scenario: Replies after the report
- **WHEN** a user other than the system's user commented on the MR after the latest report was posted
- **THEN** the list entry SHALL be marked as having replies

#### Scenario: Nothing changed
- **WHEN** the head commit equals the latest marker's commit and there are no comments by others after the latest report
- **THEN** the entry SHALL be marked as unchanged and the user SHALL still be able to select it

### Requirement: Re-review context is assembled from the MR's comments
For the selected MR the previous report SHALL be the latest comment of the system's user that carries a review-state marker. The system SHALL pass to the agent, inside the prompt text, the full text of that report, the commit recorded in its marker, and the comments of other users posted after it, each with author and time. It SHALL NOT pass them as a file path and SHALL NOT prepare a diff, a commit list or any other summary of the changes.

#### Scenario: Context in the prompt
- **WHEN** a re-review runs for an MR with a report, two replies by the developer and a new commit
- **THEN** the prompt given to the harness SHALL contain the report text, the replies with their authors and times, the previous reviewed commit and the current head commit, and SHALL contain no diff and no commit list

#### Scenario: Latest of several reports
- **WHEN** the MR carries reports from several earlier rounds
- **THEN** the previous report SHALL be the most recent one, and only comments after it SHALL be passed as replies

#### Scenario: Previous commit unavailable
- **WHEN** the commit recorded in the latest marker cannot be found or fetched in the repository (for example after a force-push)
- **THEN** the review SHALL still run, and the prompt SHALL state that the history since the previous report is unavailable instead of naming a commit range

### Requirement: Replies passed to a re-review are size-limited
The comments passed to a re-review SHALL be limited to a configured number of characters. When the limit is exceeded the system SHALL keep the most recent comments and SHALL state in the prompt that older ones were omitted.

#### Scenario: Replies over the limit
- **WHEN** the comments after the report exceed the character limit
- **THEN** the most recent comments that fit SHALL be passed and the prompt SHALL state that older ones were omitted

#### Scenario: Replies within the limit
- **WHEN** the comments after the report fit the limit
- **THEN** all of them SHALL be passed and the prompt SHALL NOT mention omissions

### Requirement: Re-review prompt is independent first, reconciliation second
The built-in prompt of a re-review round SHALL ask the agent to review the whole MR on its own first, and only then to reconcile its findings with the previous report. The report it asks for SHALL state, for each finding of the previous report, whether it is fixed, not fixed, or disputed by the developer with the agent's assessment of that objection, and SHALL list new findings separately. The same read-only restrictions as for a first review SHALL apply.

#### Scenario: Prompt order
- **WHEN** the built-in re-review prompt is rendered
- **THEN** the instruction to review the whole MR SHALL precede the previous report and the replies

#### Scenario: Read-only safety unchanged
- **WHEN** a re-review runs
- **THEN** the harness SHALL run under the same read-only restrictions and without GitLab access as for a first review

### Requirement: Re-review publishes a new comment
A re-review SHALL publish its report as a new comment carrying a review-state marker for the head commit that was reviewed, through the same claim step as a first review, and SHALL leave the earlier reports untouched. The comment header SHALL identify it as a repeated review.

#### Scenario: Earlier reports stay
- **WHEN** a re-review is published
- **THEN** the MR SHALL carry the earlier report comment unchanged and the new report comment

#### Scenario: Same head commit
- **WHEN** a re-review runs on an MR whose head commit equals the commit in the latest marker
- **THEN** the system SHALL review and publish normally, with the marker carrying the same commit

#### Scenario: Failed re-review
- **WHEN** the re-review run fails or its report is unusable
- **THEN** the system SHALL delete its claim comment, SHALL leave the earlier reports untouched, and SHALL report the MR as failed

#### Scenario: Another round published meanwhile
- **WHEN** another pass publishes a report on the MR after this re-review started and before it publishes
- **THEN** the system SHALL discard its report, SHALL NOT publish it, and SHALL delete its claim comment

### Requirement: Re-review dry-run
In dry-run mode a re-review SHALL run normally and save the comment it would have posted in the dry-run area, writing nothing to GitLab.

#### Scenario: Dry-run re-review
- **WHEN** the re-review flag and the dry-run flag are used together
- **THEN** the comment SHALL be saved locally with the same links as would be published, and no claim, comment or edit SHALL be made in GitLab
