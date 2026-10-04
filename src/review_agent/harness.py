"""Adapter that invokes the configured agentic harness as a subprocess.

Isolates the rest of the engine from the chosen harness's exact CLI
surface (see design.md decision 1): only this module needs to change if
the harness, or its flags, change. The model provider and reasoning
effort are plain configuration values substituted into the configured
command template - never hardcoded to a specific provider.

Verified against a real OpenCode install (v2.0.21): there is no
"--agents-file" flag. The model string format is "provider/model#variant",
where the variant controls reasoning effort (e.g.
"anthropic/claude-sonnet-4-20250514#high", "openai/gpt-5#high") - so
reasoning effort is folded into {model} here, not passed as a separate
flag or env var. Safety enforcement is a separate generated
opencode.json + "--agent {agent}" (see harness_config.py), not a flag on
this command.

The prompt MUST be passed via "--file {prompt_file}" in the configured
command template, not as a positional "{prompt}" text argument - verified
live to be a real bug, not a style preference: on Windows, "opencode"
resolves through PATH to an "opencode.CMD" npm shim, and Windows can only
execute a .CMD file by routing it through cmd.exe. cmd.exe parses its
command line by line, so a multi-line positional argument gets silently
truncated at the first newline - the model only ever saw the prompt's
first line ("Role: Senior Software Engineer.") and answered that alone,
with zero tool calls, while exiting 0 (no error surfaced). "--file"
attaches the full content as a message attachment instead of a raw CLI
token, bypassing cmd.exe's line-based parsing entirely - confirmed live
that the model then receives and acts on the complete multi-paragraph
prompt. This module still substitutes a "{prompt}" placeholder too, for
any future harness whose CLI genuinely takes the message inline safely.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from review_agent.config import HarnessConfig, ProviderConfig
from review_agent.proc import no_window_flags


class HarnessError(RuntimeError):
    """Raised when the harness process fails.

    Carries the harness's stderr (its tool-call trace) separately, so the
    caller can keep it as a log for a failed review.
    """

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclasses.dataclass(frozen=True)
class HarnessResult:
    stdout: str
    stderr: str


def build_model_string(provider: ProviderConfig) -> str:
    """"provider/model#reasoning_effort" - OpenCode's native variant syntax.

    Omits the "#variant" suffix entirely when reasoning_effort is None -
    verified live that appending any suffix to a model without defined
    variants (e.g. a local Ollama model) fails with "Variant unavailable".
    """
    base = f"{provider.name}/{provider.model}"
    if provider.reasoning_effort is None:
        return base
    return f"{base}#{provider.reasoning_effort}"


def _build_argv(
    command_template: list[str],
    *,
    model: str,
    prompt: str,
    prompt_file: Path,
    agent: str,
    worktree_path: Path,
) -> list[str]:
    substitutions = {
        "{model}": model,
        "{prompt}": prompt,
        "{prompt_file}": str(prompt_file),
        "{agent}": agent,
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
    prompt: str,
    prompt_file: Path,
    worktree_path: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> HarnessResult:
    """Build the harness command for this run's config and invoke it.

    `runner` defaults to `subprocess.run` and is injectable so tests can
    exercise this adapter against a stub command instead of a real
    harness binary (see design.md decision 1 and spec requirement
    "Configurable model provider and reasoning effort").
    """
    model = build_model_string(provider)
    argv = _build_argv(
        harness.command,
        model=model,
        prompt=prompt,
        prompt_file=prompt_file,
        agent=harness.agent_name,
        worktree_path=worktree_path,
    )
    # On Windows, a bare command name (e.g. "opencode") resolves via PATH
    # search for many npm-installed CLIs to a .CMD/.BAT shim, not a .EXE.
    # subprocess with a bare name and shell=False does not apply PATHEXT
    # resolution the way cmd.exe does, and fails with WinError 2 even
    # though the command genuinely exists on PATH. Resolving via
    # shutil.which first (which does apply PATHEXT) and substituting the
    # full path makes the same command list work on Windows, Linux, and
    # macOS without a shell=True fallback.
    resolved = shutil.which(argv[0])
    if resolved:
        argv[0] = resolved

    env = os.environ.copy()
    # Explicit encoding="utf-8": without it, `text=True` decodes using
    # locale.getpreferredencoding() (cp1252 on Windows), garbling OpenCode's
    # UTF-8 output (em-dashes, curly quotes) into mojibake - verified live.
    # errors="replace": the output is not guaranteed to be pure UTF-8 - a
    # Windows tool may print in the OEM code page (verified live: byte 0x87,
    # cp866 Cyrillic, in opencode output). Strict decoding kills
    # subprocess's reader thread and the run hangs or fails; a replaced
    # character costs nothing.
    result = runner(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(worktree_path),
        env=env,
        # No console of our own (pythonw under Task Scheduler) -> keep
        # opencode from popping up a window; see proc.py.
        creationflags=no_window_flags(),
    )
    if result.returncode != 0:
        raise HarnessError(
            f"Harness exited with code {result.returncode}: {result.stderr.strip()}",
            stderr=result.stderr,
        )
    return HarnessResult(stdout=result.stdout, stderr=result.stderr)
