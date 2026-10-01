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
   It defines a restricted custom agent whose `bash` permission DENIES a
   list of dangerous command patterns.

   WHY A DENYLIST, NOT AN ALLOWLIST (the original design): verified live
   against a real OpenCode v2.0.21 install with a real cloud provider
   (openai/gpt-5.6-terra) that an allowlist-with-catch-all-deny does not
   work as the schema implies - adding a `"*": "deny"` entry to the
   `bash` pattern map makes the bash tool entirely UNAVAILABLE, even for
   patterns explicitly marked "allow". Without a catch-all, an unmatched
   command is implicitly ALLOWED by default, so there is currently no
   working way to express "deny everything except these patterns" to
   OpenCode's bash permission. A denylist of specific dangerous patterns
   (weaker in principle - a sufficiently different invocation can evade
   a specific pattern - but what the tool actually supports) was
   verified live instead: `git log`/`git status` etc. (not in the
   denylist) executed and returned real output; `npm test` (explicitly
   denied) was blocked ("Permission denied: shell") with no side effect.

   Separately: invoking the harness WITHOUT `--standalone` appeared to
   use a long-lived background service that can return "Agent not
   found" for an agent defined in an opencode.json it had not yet
   indexed (our worktree is a brand-new directory every run) - the
   harness command in config.example.yaml includes `--standalone` for
   exactly this reason, verified live to avoid the issue.

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

- Shell commands matching these patterns are denied: {denied_bash_patterns}
  (everything else is allowed by default - see harness_config.py module
  docstring for why this is a denylist, not an allowlist)
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
        denied_bash_patterns=", ".join(safety.denied_bash_patterns),
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

    `bash` is a denylist pattern map with NO catch-all "*" entry - adding
    one makes bash entirely unavailable in this OpenCode version (see
    module docstring). Unmatched commands (including the read-only git
    inspection the engine relies on) are allowed by default.
    """
    bash_permissions = {pattern: "deny" for pattern in safety.denied_bash_patterns}
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
