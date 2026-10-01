# Design

## Context

See proposal.md - Why. Greenfield project: no existing code, no existing specs. Target platform for this and the first manual validation run is the user's local Windows machine; the design must not assume a Linux-only toolchain, since the same pipeline is intended to later run inside a private GitLab CI runner. Git is the only assumed pre-existing tool on the host; the agentic harness is an external dependency installed separately.

## Goals / Non-Goals

**Goals**
- One CLI entrypoint: repository + base/head commits in, markdown report out.
- Harness and model provider are swappable via configuration, not code.
- The harness's persistent configuration carries no review methodology; the per-run prompt does.
- Preparation before the harness runs is minimal (no prebuilt diff/brief).

**Non-Goals** (deferred to later changes)
- GitLab API integration, MR polling, reviewer filtering, comment publishing (Change 2).
- Scheduling, multi-repository configuration, logging/observability for unattended operation (Change 3).
- Auto-resolving AI comment threads, self-generating best-practices skills (backlog, post-v1).

## Decisions

### 1. Agentic harness: OpenCode as the default, behind a thin adapter

OpenCode (`opencode run --model <provider/model> "<prompt>"`) is the default harness: it is provider-agnostic in one config value (Anthropic/OpenAI-Codex/Ollama and other local models), runs headless/non-interactively, and is a widely-adopted open-source project rather than a bespoke integration.

Alternatives considered:
- **Claude Code CLI headless (`claude -p`)** - same class of agentic tool-access behavior, but tied to Claude-only providers; rejected as the default because the project requires swapping in Codex and local models without rewriting the integration.
- **Hand-rolled per-provider multiplexer** (dispatch to a different CLI per configured provider) - rejected: reinvents the provider-abstraction problem OpenCode already solves, adding custom code the project explicitly wants to avoid.

The harness invocation is isolated behind a small internal adapter (one function: given a worktree path, a rendered prompt, and provider config, invoke the harness and return its output) so a different harness can be substituted later without reworking worktree management, prompt rendering, or report handling.

### 2. Worktree lifecycle

`git worktree add <scratch>/<run-id> <head-sha>` after confirming `base-sha` and `head-sha` are present locally (fetched as needed). `<run-id>` is unique per invocation (timestamp + short SHA) to avoid collisions across concurrent or overlapping runs. Cleanup (`git worktree remove`, falling back to manual removal + `git worktree prune` if the clean path fails) runs in a guaranteed-cleanup step so it executes on both success and failure paths. On startup, the entrypoint also checks the scratch directory for orphaned worktrees from a prior run that did not exit cleanly (e.g., process killed) and removes them before starting a new run.

### 3. Harness persistent configuration is generated per run, not hand-maintained

The system generates a minimal runtime/safety configuration file for the harness (its `AGENTS.md`-equivalent) from a small internal template on every run, written into the worktree or scratch directory. It contains only safety/runtime instructions (execution restrictions, checkout/scratch paths, output language) - never review methodology. This keeps the "no methodology in system config" requirement enforced by construction rather than by convention.

### 4. Review prompt as a rendered template

A single, user-editable prompt template (the direct review prompt validated in prior research: correctness/readability/testability/security/SRP/contracts/best practices, SEV Blocker/Major/Minor, explicit permission for low-confidence findings) lives in the project and is rendered per run with the run's variables (worktree path, base/head SHA, MR title/description, path to the target repo's `AGENTS.md`/`CLAUDE.md` and `docs/` if present, paths to any enabled knowledge-skill files). The rendered prompt is passed to the harness as its task input.

### 5. Execution restriction via the harness's own permission configuration

Read-only enforcement (no running/building/testing/linting MR code, no general web access) is implemented through the chosen harness's native tool-permission configuration (OpenCode supports restricting which tools/commands an agent run may use) rather than an OS-level sandbox or container.

Alternative considered: OS-level sandboxing/containerization - rejected for v1 as unnecessary extra infrastructure; the harness's own permission surface is the lowest-friction, most "ready-made" way to enforce this, consistent with the project's preference for off-the-shelf mechanisms. Revisit if the harness's permission granularity proves insufficient (see Risks).

### 6. Knowledge-skill files are plain files referenced by path

No special skill-loading machinery in this change: a skill is a markdown file; enabled skills are listed in configuration and their paths are included in the rendered prompt as "optional reference material, not a checklist to limit yourself to." This reuses the harness's native file-reading tool rather than building a skill registry.

### 7. CLI entrypoint implementation language: Python

Chosen for cross-platform subprocess/file handling (works identically on the user's current Windows machine and a future Linux CI runner) without being tied to any specific harness runtime - the harness itself (e.g., OpenCode, a separate Node/Bun binary) is invoked as a subprocess regardless of the wrapper language.

Alternative considered: PowerShell - rejected as the primary implementation language because it does not generalize to the planned future Linux CI environment without a rewrite, even though `pwsh` itself is cross-platform; Python's ecosystem and this project's automation conventions favor it for the entrypoint and config handling.

## Risks / Trade-offs

- [Risk] The harness's tool-permission configuration may not cleanly separate "read" from "write/execute" the way this design assumes → Mitigation: validate this explicitly during the manual benchmark run in this change's tasks, before Change 2 builds automation on top of it; if insufficient, revisit with OS-level sandboxing in a follow-up change.
- [Risk] Worktree cleanup may not run if the process is killed rather than erroring normally → Mitigation: orphaned-worktree cleanup on startup (see Decision 2) makes the system self-healing across runs rather than requiring perfect cleanup every time.
- [Risk] Pinning to a specific harness couples the project to that harness's CLI surface, which may change → Mitigation: the thin adapter (Decision 1) isolates this; only the adapter needs to change if the harness's CLI changes or a different harness is substituted.

## Migration Plan

New code; nothing existing to migrate. First validation is a manual CLI invocation against the benchmark MR from prior research (project `b2c/m`, MR `!544`) to confirm the engine's recall matches expectations before Change 2 (GitLab polling/publishing) is proposed.

## Open Questions

- Exact harness CLI flags/version to pin, and the exact local config file format (YAML/TOML/JSON) - both are implementation details that do not change the spec's behavior contract or this design's approach; resolve while writing tasks/implementation.
