"""Generates the harness's minimal safety/runtime configuration.

Regenerated fresh on every run. Contains ONLY runtime and safety
instructions - no review methodology (that lives in the per-run prompt,
see prompt.py, per the spec requirement "Review methodology lives in the
per-run prompt, not system configuration"). Written to the run's scratch
directory, never into the reviewed repository's own worktree, so it never
overwrites or shadows the target repository's own AGENTS.md/CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path

from review_agent.config import SafetyConfig

_TEMPLATE = """\
# Runtime safety configuration (generated per run - do not edit by hand)

You are reviewing code in an isolated, read-only checkout at:
{worktree_path}

Scratch directory for any temporary files you need: {scratch_path}

## Execution restrictions

You MUST NOT execute, build, test, or lint any code from this checkout.
In particular, do not run: {forbidden_commands}.

You MAY use read-only inspection: git diff/show/log/blame/grep/ls-files,
ripgrep, and reading files.

You MUST NOT access the network beyond the configured model provider
endpoint. You have no general web access.

You MUST NOT write, modify, or delete files in this checkout, and you
MUST NOT publish anything to GitLab yourself - your output is a review
report that something else will handle.

## Output language

Write all findings in: {output_language}
"""


def render_harness_runtime_config(
    worktree_path: Path, scratch_path: Path, safety: SafetyConfig
) -> str:
    """Render the harness's persistent/system-level safety config.

    Callers must never add review criteria, severity scheme, or
    exploration workflow here - see spec requirement "Review methodology
    lives in the per-run prompt, not system configuration".
    """
    return _TEMPLATE.format(
        worktree_path=worktree_path,
        scratch_path=scratch_path,
        forbidden_commands=", ".join(safety.forbidden_commands),
        output_language=safety.output_language,
    )


def write_harness_runtime_config(
    worktree_path: Path, scratch_path: Path, safety: SafetyConfig
) -> Path:
    """Render and write the safety config into the run's scratch directory."""
    content = render_harness_runtime_config(worktree_path, scratch_path, safety)
    scratch_path.mkdir(parents=True, exist_ok=True)
    config_path = scratch_path / "harness-agents.md"
    config_path.write_text(content, encoding="utf-8")
    return config_path
