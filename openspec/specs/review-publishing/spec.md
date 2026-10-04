# review-publishing Specification

## Purpose

Publishes a completed review report to its merge request as a single comment that also records, in GitLab itself, that the review for that head commit is done.

## Requirements

### Requirement: Report published as an MR comment
After a successful review run, the system SHALL publish the report as one comment on the reviewed merge request, authored by the GitLab user the system acts as, by replacing the text of its own claim comment for that review; if that claim comment no longer exists, it SHALL post the report as a new comment. Publishing SHALL be performed by the orchestrating process, never by the review harness.

#### Scenario: Successful review is published
- **WHEN** a review run for an MR completes and its report passes the sanity check
- **THEN** the MR SHALL carry exactly one comment from this review, containing the full report and no claim marker

#### Scenario: Claim comment was removed during the review
- **WHEN** the system's claim comment was deleted by someone while the review ran
- **THEN** the system SHALL post the report as a new comment

#### Scenario: Harness cannot write to GitLab
- **WHEN** the review harness runs for a discovered MR
- **THEN** it SHALL NOT be given GitLab credentials or any means to post comments, change the MR, or call the GitLab API

### Requirement: Published comment carries the review-state marker
Every comment the system publishes SHALL contain a machine-readable marker that identifies the head commit that was reviewed, in a form not rendered as visible text by GitLab.

#### Scenario: Marker matches the reviewed head
- **WHEN** the system publishes a report for a review run against head commit H
- **THEN** the comment SHALL contain the marker `<!-- ai-review: sha=H -->`

#### Scenario: Head moved during the review
- **WHEN** the MR's head commit changes while the review is running
- **THEN** the published comment's marker SHALL still identify the head commit that was actually reviewed, and the comment SHALL state which commit was reviewed

### Requirement: No duplicate publication
The system SHALL re-check the MR's comments for an existing valid marker immediately before publishing and SHALL NOT publish if one is found; in that case it SHALL delete its own claim comment.

#### Scenario: Another pass published first
- **WHEN** a valid marker by the system's GitLab user appears on the MR after the review started but before publishing
- **THEN** the system SHALL discard its report, SHALL NOT publish it, and SHALL delete its claim comment

### Requirement: Unusable reports are not published
The system SHALL NOT publish a report that is empty or below a configured minimum length, and SHALL record the failure for that MR so it is retried on a later pass.

#### Scenario: Harness returns an empty report
- **WHEN** the harness exits successfully but its report is empty or shorter than the configured minimum
- **THEN** the system SHALL NOT post a comment, SHALL NOT leave a marker, and SHALL report that MR as failed for this pass

### Requirement: Failed reviews leave no marker
The system SHALL NOT leave any comment or marker on an MR whose review run failed: it SHALL delete the claim comment it posted for that run, so the MR remains eligible on a later pass.

#### Scenario: Harness fails for an MR
- **WHEN** the review run for an MR fails (fetch error, harness error, or unusable report)
- **THEN** the MR SHALL carry no comment from this run and SHALL be a review candidate again on the next pass

### Requirement: Dry-run mode
The system SHALL support a dry-run mode in which discovery and reviews run normally but nothing is written to GitLab.

#### Scenario: Pass in dry-run mode
- **WHEN** a polling pass runs in dry-run mode
- **THEN** the system SHALL save the comment it would have posted in the working directory's dry-run area, SHALL report its location, and SHALL NOT create, edit, or delete any comment, claim, or marker in GitLab

### Requirement: File references link to the reviewed commit in GitLab
Before publishing a report (and before saving it in dry-run mode), the system SHALL rewrite file references in the comment into links to the file in the GitLab web UI, pinned to the reviewed head commit, in the form `<project web URL>/-/blob/<head_sha>/<repository-relative path>`. When the reference names a line, the link SHALL point to that line (`#L<n>`); when it names a line range, the link SHALL point to that range (`#L<start>-<end>`). The system SHALL recognise both a markdown link whose target is a path inside the review run's worktree and a path written as inline code, alone or followed by `:<line>` or `:<start>-<end>`. A path in inline code SHALL become a link only if that path exists in the reviewed head commit, or (for a file removed by the MR) in the base commit; in the latter case the link SHALL be pinned to the base commit. The rewrite SHALL NOT change the review's findings, their order, or any text other than the references themselves, and SHALL NOT alter content inside fenced code blocks except as stated in the requirement on local paths.

#### Scenario: Link to a worktree path with a line number
- **WHEN** the report contains `` [`src/a.ts:13`](<worktree>/src/a.ts#L13) ``, where `<worktree>` is the run's worktree path, for an MR whose project web URL is `https://git.example/grp/proj` and whose reviewed head is H
- **THEN** the published comment SHALL contain `` [`src/a.ts:13`](https://git.example/grp/proj/-/blob/H/src/a.ts#L13) ``

#### Scenario: Line range
- **WHEN** a reference names lines 565 to 573 of a file, either as `path:565-573` in inline code or as a worktree link ending in `#L565-L573` or `#L565-573`
- **THEN** the resulting link SHALL end in `#L565-573`

#### Scenario: Inline-code path of an existing file
- **WHEN** the report contains `` `src/pages/Wishlist.tsx:66` `` outside a link and `src/pages/Wishlist.tsx` exists in head commit H
- **THEN** the published comment SHALL show the same text as a link to `<project web URL>/-/blob/H/src/pages/Wishlist.tsx#L66`

#### Scenario: Reference without a line
- **WHEN** a recognised reference names a file but no line
- **THEN** the link SHALL point to the whole file, without a line anchor

#### Scenario: Inline code that is not a known path
- **WHEN** the report contains inline code such as `` `isFallback` `` or `` `src/missing.ts:4` `` that names no file of the head or base commit
- **THEN** that inline code SHALL be left unchanged

#### Scenario: File deleted by the MR
- **WHEN** a referenced path does not exist in head commit H but exists in base commit B
- **THEN** the link SHALL point to `<project web URL>/-/blob/B/<path>`

#### Scenario: Dry-run comment
- **WHEN** a polling pass runs in dry-run mode
- **THEN** the comment saved in the dry-run area SHALL contain the same rewritten links that would have been published

### Requirement: Published comments carry no local paths of the review machine
The published comment (and its dry-run copy) SHALL NOT contain the absolute path of the review run's worktree. A worktree path that cannot be turned into a GitLab link, including one inside a fenced code block or a link to a path that exists in neither commit, SHALL be replaced by the path relative to the repository root.

#### Scenario: Worktree path inside a code block
- **WHEN** the report contains `<worktree>/src/a.ts` inside a fenced code block
- **THEN** the published comment SHALL contain `src/a.ts` in its place and SHALL NOT contain `<worktree>`

#### Scenario: Path written with different separators or case
- **WHEN** the report refers to the worktree with backslashes, forward slashes, or a different drive-letter case than the system uses internally
- **THEN** the reference SHALL still be recognised and rewritten

### Requirement: Link rewriting never blocks publication
If the system cannot determine the files of the reviewed commit or the project's web URL, it SHALL still publish the report, rewriting whatever it can without that information (at minimum replacing local worktree paths with repository-relative paths), and SHALL record a warning in the pass log.

#### Scenario: Commit file list unavailable
- **WHEN** listing the files of the reviewed head commit fails
- **THEN** the report SHALL be published, worktree links SHALL still be rewritten into GitLab links, inline-code paths SHALL be left unchanged, and the pass log SHALL contain a warning for that MR
