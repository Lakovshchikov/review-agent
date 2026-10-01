import subprocess
from pathlib import Path

import pytest

from review_agent.config import HarnessConfig, ProviderConfig
from review_agent.harness import HarnessError, invoke_harness


class _StubRunner:
    """Records the argv it was called with and returns a canned result."""

    def __init__(self, stdout: str = "stub review output", returncode: int = 0):
        self.calls = []
        self._stdout = stdout
        self._returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args=argv, returncode=self._returncode, stdout=self._stdout, stderr=""
        )


def test_invoke_harness_builds_correct_argv_and_returns_output():
    harness = HarnessConfig(
        command=["opencode", "run", "--model", "{model}", "--agents-file", "{agents_file}", "--prompt-file", "{prompt_file}"]
    )
    provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="medium")
    stub = _StubRunner(stdout="# Review\nfound 3 issues")

    prompt_file = Path("/scratch/run-1/prompt.md")
    agents_file = Path("/scratch/run-1/harness-agents.md")
    worktree_path = Path("/scratch/run-1/worktree")

    result = invoke_harness(
        harness,
        provider,
        prompt_file=prompt_file,
        agents_file=agents_file,
        worktree_path=worktree_path,
        runner=stub,
    )

    assert result.stdout == "# Review\nfound 3 issues"
    assert len(stub.calls) == 1
    argv = stub.calls[0]["argv"]
    # Paths render platform-native (backslashes on Windows, forward slashes
    # elsewhere) - compare against str(Path(...)), not a hardcoded literal.
    assert argv == [
        "opencode",
        "run",
        "--model",
        "anthropic/claude-sonnet-4-5",
        "--agents-file",
        str(agents_file),
        "--prompt-file",
        str(prompt_file),
    ]
    assert stub.calls[0]["kwargs"]["cwd"] == str(worktree_path)
    assert stub.calls[0]["kwargs"]["env"]["REVIEW_AGENT_REASONING_EFFORT"] == "medium"


def test_invoke_harness_raises_on_nonzero_exit():
    harness = HarnessConfig(command=["opencode", "run", "{model}"])
    provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="medium")
    stub = _StubRunner(returncode=1)
    stub._stdout = ""

    with pytest.raises(HarnessError):
        invoke_harness(
            harness,
            provider,
            prompt_file=Path("/p"),
            agents_file=Path("/a"),
            worktree_path=Path("/w"),
            runner=stub,
        )


def test_provider_swap_is_configuration_only():
    """Switching providers must not require a different code path."""
    harness = HarnessConfig(command=["opencode", "run", "--model", "{model}"])
    stub = _StubRunner()

    claude_provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="medium")
    local_provider = ProviderConfig(name="ollama", model="qwen2.5-coder:32b", reasoning_effort="high")

    for provider in (claude_provider, local_provider):
        result = invoke_harness(
            harness,
            provider,
            prompt_file=Path("/p"),
            agents_file=Path("/a"),
            worktree_path=Path("/w"),
            runner=stub,
        )
        assert result.stdout == "stub review output"

    models_used = [call["argv"][3] for call in stub.calls]
    assert models_used == ["anthropic/claude-sonnet-4-5", "ollama/qwen2.5-coder:32b"]
