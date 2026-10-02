"""Wires the engine together: one review of one commit range.

Repository + base/head commits in, report text out. No GitLab calls,
no scheduling, no file left behind: everything the run writes goes to
`<work_dir>/tmp/<run_id>/` and that whole folder is deleted when the run
ends, success or failure (see housekeeping.py for the layout). Callers
get the report and the harness stderr back in memory and decide what to
keep - the manual CLI writes the report to report.output_path, `poll`
publishes it and keeps the stderr of failed reviews in logs/.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from typing import Callable

from review_agent.config import Config, load_config
from review_agent.harness import HarnessError, invoke_harness
from review_agent.harness_config import write_opencode_agent_config, write_safety_note
from review_agent.housekeeping import WorkDir, copy_artifacts, remove_path
from review_agent.prompt import (
    discover_docs_path,
    discover_repo_instructions_path,
    render_review_prompt,
)
from review_agent.worktree import managed_worktree


@dataclasses.dataclass(frozen=True)
class ReviewResult:
    report: str
    harness_stderr: str
    run_id: str


def run_review(
    *,
    repo_path: Path,
    base_sha: str,
    head_sha: str,
    mr_title: str,
    mr_description: str,
    config_path: Path | None = None,
    config: Config | None = None,
    harness_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    debug_dir: Path | None = None,
) -> ReviewResult:
    """Run one full review and return the report and harness stderr.

    `config` overrides reading `config_path` - `poll` passes the
    project's effective config (its own provider/skills). With
    `debug_dir`, the run's files (prompt, safety note, report, harness
    stderr - not the worktree) are copied there before deletion.
    A failing harness raises HarnessError, which carries its stderr.
    """
    if config is None:
        if config_path is None:
            raise ValueError("run_review() needs either config or config_path")
        config = load_config(config_path)
    tmp_dir = WorkDir.from_config(config).tmp

    run_dir: Path | None = None
    try:
        with managed_worktree(repo_path, base_sha, head_sha, tmp_dir) as handle:
            run_dir = handle.path.parent

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
            prompt_file = run_dir / "prompt.md"
            prompt_file.write_text(prompt_text, encoding="utf-8")

            # Documentation only; the actual enforcement is opencode.json
            # below. See harness_config.py.
            write_safety_note(handle.path, run_dir, config.safety)

            # Written into the worktree root (not the run folder) because
            # that's where OpenCode auto-discovers project config from -
            # safe because the worktree is removed with the rest of the run.
            write_opencode_agent_config(
                handle.path, config.safety, agent_name=config.harness.agent_name
            )

            # Harnesses like OpenCode print their tool-call trace (what it
            # read, what it ran, permission denials) to stderr. It is the
            # only way to diagnose a run that "succeeds" with a thin
            # report, so it is kept in the run folder (for --debug) and
            # handed back to the caller (for failed-review logs).
            stderr_file = run_dir / "harness-stderr.log"
            try:
                result = invoke_harness(
                    config.harness,
                    config.provider,
                    prompt=prompt_text,
                    prompt_file=prompt_file,
                    worktree_path=handle.path,
                    runner=harness_runner,
                )
            except HarnessError as exc:
                stderr_file.write_text(exc.stderr, encoding="utf-8")
                raise
            stderr_file.write_text(result.stderr, encoding="utf-8")
            (run_dir / "report.md").write_text(result.stdout, encoding="utf-8")
            return ReviewResult(
                report=result.stdout, harness_stderr=result.stderr, run_id=handle.run_id
            )
    finally:
        # The worktree itself is already gone (managed_worktree); now the
        # rest of the run folder goes too, after a copy for --debug.
        if run_dir is not None:
            try:
                if debug_dir is not None:
                    copy_artifacts(run_dir, debug_dir)
                remove_path(run_dir)
            except OSError:
                # Never mask the run's own outcome; a folder that could
                # not be deleted is a leftover the next pass removes.
                pass
