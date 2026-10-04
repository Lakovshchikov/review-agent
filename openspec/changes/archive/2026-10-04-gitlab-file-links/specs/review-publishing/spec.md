# Spec Delta

## ADDED Requirements

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
