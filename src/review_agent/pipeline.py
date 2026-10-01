"""Wires the engine together: the single CLI entrypoint's implementation.

Repository + base/head commits in, markdown report out. No GitLab calls,
no scheduling - those are later changes (see AGENTS.md roadmap).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from review_agent.config import load_config
from review_agent.harness import invoke_harness
from review_agent.harness_config import write_harness_runtime_config
from review_agent.prompt import (
    discover_docs_path,
    discover_repo_instructions_path,
    render_review_prompt,
)
from review_agent.report import write_report
from review_agent.worktree import managed_worktree


def run_review(
    *,
    repo_path: Path,
    base_sha: str,
    head_sha: str,
    mr_title: str,
    mr_description: str,
    config_path: Path,
    scratch_dir: Path,
    harness_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Run one full review and return the path to the written report."""
    config = load_config(config_path)

    with managed_worktree(repo_path, base_sha, head_sha, scratch_dir) as handle:
        run_scratch_path = handle.path.parent

        repo_instructions_path = discover_repo_instructions_path(handle.path)
        docs_path = discover_docs_path(handle.path)
        skill_paths = [Path(p) for p in config.skills]

        prompt_text = render_review_prompt(
            worktree_path=handle.path,
            base_sha=base_sha,
            mr_title=mr_title,
            mr_description=mr_description,
            repo_instructions_path=repo_instructions_path,
            docs_path=docs_path,
            skill_paths=skill_paths,
        )
        prompt_file = run_scratch_path / "prompt.md"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        agents_file = write_harness_runtime_config(handle.path, run_scratch_path, config.safety)

        result = invoke_harness(
            config.harness,
            config.provider,
            prompt_file=prompt_file,
            agents_file=agents_file,
            worktree_path=handle.path,
            runner=harness_runner,
        )

        output_path = Path(config.report.output_path.format(run_id=handle.run_id))
        return write_report(result.stdout, output_path)
