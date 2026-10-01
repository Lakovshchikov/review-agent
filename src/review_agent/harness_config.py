"""Generates the harness's safety enforcement artifacts.

Two things are generated per run, both regenerated fresh and containing
NO review methodology (that lives in the per-run prompt, see prompt.py,
per the spec requirement "Review methodology lives in the per-run
prompt, not system configuration"):

1. A prose safety note (for logging/documentation) - what the run is
   restricted to and why.
2. `opencode.json`, written to the WORKTREE ROOT (OpenCode auto-discovers
   project config from the current directory - there is no
   "--agents-file"-style override flag, verified against a real install).
   It defines a restricted custom agent whose `bash` permission is an
   allowlist of read-only inspection patterns with everything else
   denied by default.

   VERIFIED LIVE (local Ollama model via this restricted agent): asking
   it to run a forbidden command (`npm test`) was refused - the model
   reported no bash tool was available, and no side effect occurred.
   NOT YET CONCLUSIVELY VERIFIED: that an explicitly allowed pattern
   (`git log*` etc.) actually executes. The local models available for
   this check appear to route tool calls through an OpenCode "Code Mode"
   (a JS-sandboxed `execute` tool) that may not expose real git/bash
   access at all, independent of this permission config - undocumented
   in OpenCode's public docs as of this writing, and not reproducible
   with a cloud provider (Claude/Codex) in this environment. Before
   relying on this for an unattended run, re-verify the allow side with
   the actual provider you configure (see tasks.md task 3.2/8.1).

Writing `opencode.json` into the worktree (not the scratch dir) is safe
specifically because the whole worktree is a throwaway copy removed after
the run (see worktree.py) - it never touches the reviewed repository's
real history, and a different filename than `AGENTS.md`/`CLAUDE.md` means
it never shadows the target repository's own instructions file either.
"""

from __future__ import annotations

import json
from pathlib import Path

from review_agent.config import SafetyConfig

_SAFETY_NOTE_TEMPLATE = """\
# Safety note for this run (generated - do not edit by hand)

Worktree: {worktree_path}
Scratch directory: {scratch_path}

Read-only enforcement for this run is NOT this file - it is the
restricted agent defined in opencode.json at the worktree root (see
harness_config.py). This note is a human-readable record of what that
config enforces:

- Shell commands are denied by default; only read-only inspection is
  allowed: {allowed_bash_patterns}
- File edits, web fetch, and web search are denied.
- Write all findings in: {output_language}
"""


def render_safety_note(worktree_path: Path, scratch_path: Path, safety: SafetyConfig) -> str:
    """Render the human-readable safety note (logging/documentation only).

    Callers must never add review criteria, severity scheme, or
    exploration workflow here - see spec requirement "Review methodology
    lives in the per-run prompt, not system configuration".
    """
    return _SAFETY_NOTE_TEMPLATE.format(
        worktree_path=worktree_path,
        scratch_path=scratch_path,
        allowed_bash_patterns=", ".join(safety.allowed_bash_patterns),
        output_language=safety.output_language,
    )


def write_safety_note(worktree_path: Path, scratch_path: Path, safety: SafetyConfig) -> Path:
    """Render and write the safety note into the run's scratch directory."""
    content = render_safety_note(worktree_path, scratch_path, safety)
    scratch_path.mkdir(parents=True, exist_ok=True)
    note_path = scratch_path / "safety-note.md"
    note_path.write_text(content, encoding="utf-8")
    return note_path


def build_opencode_agent_config(safety: SafetyConfig, agent_name: str = "reviewer") -> dict:
    """Build the opencode.json content for a restricted review agent.

    `bash` is a pattern map (verified against OpenCode's real config
    schema): each configured pattern is allowed, "*" is denied, so any
    command that doesn't match an explicit read-only pattern is blocked
    by default - not a denylist of known-bad command names, which a
    slightly different invocation trivially bypasses.
    """
    bash_permissions = {pattern: "allow" for pattern in safety.allowed_bash_patterns}
    bash_permissions["*"] = "deny"
    return {
        "agent": {
            agent_name: {
                "description": "Read-only code reviewer (generated per run)",
                "mode": "primary",
                "permission": {
                    "bash": bash_permissions,
                    "edit": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "read": "allow",
                    "grep": "allow",
                    "glob": "allow",
                    "list": "allow",
                },
            }
        }
    }


def write_opencode_agent_config(
    worktree_path: Path, safety: SafetyConfig, agent_name: str = "reviewer"
) -> Path:
    """Write opencode.json into the worktree root for this run.

    The worktree is a throwaway copy (see worktree.py) - this file is
    removed along with it and never reaches the target repository's real
    history or shadows its own AGENTS.md/CLAUDE.md.
    """
    config = build_opencode_agent_config(safety, agent_name=agent_name)
    config_path = worktree_path / "opencode.json"
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return config_path
