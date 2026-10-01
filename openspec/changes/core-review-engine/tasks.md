# Tasks

## 1. Project scaffolding

- [x] 1.1 Initialize the Python project structure for the CLI entrypoint (package layout, dependency manifest) and verify `python -m <entrypoint> --help` runs and prints usage
- [x] 1.2 Define a YAML configuration schema (model provider + credentials reference, reasoning effort, report output path, enabled knowledge-skill file paths, harness command) with a documented example config file, and verify a sample config file loads without error via a config-loading test

## 2. Worktree lifecycle

- [x] 2.1 Implement isolated worktree creation (fetch base/head commits as needed, `git worktree add` at head SHA with a unique run-id) and verify a test creates a worktree for known commits in a scratch test repository
- [x] 2.2 Implement guaranteed worktree cleanup on both success and failure paths and verify a test asserts the worktree is removed after a successful run and after a forced-failure run
- [x] 2.3 Implement orphaned-worktree cleanup at startup (detect and remove leftover worktrees from a prior run that did not exit cleanly) and verify a test simulates a leftover worktree and confirms it is removed before a new run starts

## 3. Harness runtime configuration generation

- [x] 3.1 Implement generation of the harness's minimal safety/runtime config (the `AGENTS.md`-equivalent: execution restrictions, checkout/scratch paths, output language, no review methodology) from an internal template, and verify a test asserts the generated file contains only safety/runtime content and no review criteria or severity scheme
- [ ] 3.2 Configure the harness's native read-only tool-permission restrictions (block execute/build/test/lint on MR code, no general web access beyond the model provider endpoint) and verify by documenting and manually confirming (recorded in docs, task 7.1) that an attempted execute/build/test/lint action is blocked during a run

## 4. Review prompt templating

- [x] 4.1 Implement the direct review prompt template (correctness/readability/testability/security/SRP/contracts/best practices, SEV Blocker/Major/Minor, low-confidence findings permitted) with variable substitution (worktree path, base/head SHA, MR title/description, path to target repo's `AGENTS.md`/`CLAUDE.md` and `docs/` if present) and verify a test renders the template with sample variables and asserts every placeholder is substituted
- [x] 4.2 Implement optional knowledge-skill attachment (configured list of skill file paths included in the rendered prompt as reference material, not a limiting checklist) and verify a test confirms skill paths appear in the rendered prompt only when configured, and are absent when no skills are configured

## 5. Harness adapter and invocation

- [x] 5.1 Implement the harness adapter (worktree path + rendered prompt + provider config -> harness subprocess invocation, returning the harness's output) with configurable model provider and an explicit reasoning-effort parameter, and verify a test invokes the adapter against a stub harness command and asserts the correct CLI arguments (provider, reasoning effort, prompt) are passed
- [x] 5.2 Verify provider swapping is configuration-only: run the adapter with two different provider configs (e.g. Claude and a local-model config) against the stub harness and confirm no code path differs, documenting the supported provider config keys (task 7.1)

## 6. Report output and CLI entrypoint

- [x] 6.1 Implement writing the harness's review output as a markdown file to the configured output path and verify a test confirms the written file matches the harness's returned output
- [x] 6.2 Implement the single CLI entrypoint wiring together config loading, worktree creation, runtime-config generation, prompt rendering, harness invocation, report writing, and cleanup (repo location + base/head in, report out; no GitLab calls, no scheduling), and verify by running it end-to-end against a local test repository with the stub harness, confirming a report file is produced and the worktree is gone afterward

## 7. Documentation

- [x] 7.1 Write a README covering: installing the chosen harness, the YAML config format and provider options, read-only permission setup, and running the entrypoint, and verify the documented command sequence runs as written on a clean checkout

## 8. Manual validation against the benchmark MR

- [ ] 8.1 Run the entrypoint with a real configured model provider against the benchmark MR from prior research (project `b2c/m`, MR `!544`, base/head SHAs as recorded in the project's prior investigation), and record the produced findings alongside the benchmark findings list (items A-M) in a short comparison note, verified by the note existing and listing overlap/gaps explicitly
- [ ] 8.2 Based on the comparison, record a go/no-go decision on proceeding to the next change (gitlab-polling-and-publishing) as-is, or adjusting the prompt/config first, verified by the decision being recorded in the same note from 8.1
