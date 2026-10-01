"""Adapter that invokes the configured agentic harness as a subprocess.

Isolates the rest of the engine from the chosen harness's exact CLI
surface (see design.md decision 1): only this module needs to change if
the harness, or its flags, change. The model provider and reasoning
effort are plain configuration values substituted into the configured
command template - never hardcoded to a specific provider.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path
from typing import Callable

from review_agent.config import HarnessConfig, ProviderConfig


class HarnessError(RuntimeError):
    """Raised when the harness process fails."""


@dataclasses.dataclass(frozen=True)
class HarnessResult:
    stdout: str
    stderr: str


def _build_argv(
    command_template: list[str],
    *,
    model: str,
    prompt_file: Path,
    agents_file: Path,
    reasoning_effort: str,
    worktree_path: Path,
) -> list[str]:
    substitutions = {
        "{model}": model,
        "{prompt_file}": str(prompt_file),
        "{agents_file}": str(agents_file),
        "{reasoning_effort}": reasoning_effort,
        "{worktree_path}": str(worktree_path),
    }
    argv = []
    for part in command_template:
        for placeholder, value in substitutions.items():
            part = part.replace(placeholder, value)
        argv.append(part)
    return argv


def invoke_harness(
    harness: HarnessConfig,
    provider: ProviderConfig,
    *,
    prompt_file: Path,
    agents_file: Path,
    worktree_path: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> HarnessResult:
    """Build the harness command for this run's config and invoke it.

    `runner` defaults to `subprocess.run` and is injectable so tests can
    exercise this adapter against a stub command instead of a real
    harness binary (see design.md decision 1 and spec requirement
    "Configurable model provider and reasoning effort").
    """
    model = f"{provider.name}/{provider.model}"
    argv = _build_argv(
        harness.command,
        model=model,
        prompt_file=prompt_file,
        agents_file=agents_file,
        reasoning_effort=provider.reasoning_effort,
        worktree_path=worktree_path,
    )

    env = os.environ.copy()
    # Reasoning effort is also exposed via env var for harnesses that read
    # it from the environment rather than a CLI flag; the command template
    # can use {reasoning_effort} directly when the harness takes a flag.
    env["REVIEW_AGENT_REASONING_EFFORT"] = provider.reasoning_effort

    result = runner(argv, capture_output=True, text=True, cwd=str(worktree_path), env=env)
    if result.returncode != 0:
        raise HarnessError(
            f"Harness exited with code {result.returncode}: {result.stderr.strip()}"
        )
    return HarnessResult(stdout=result.stdout, stderr=result.stderr)
