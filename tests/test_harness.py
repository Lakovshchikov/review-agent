import subprocess
from pathlib import Path

import pytest

from review_agent.config import HarnessConfig, ProviderConfig
from review_agent.harness import HarnessError, build_model_string, invoke_harness


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


def test_build_model_string_includes_reasoning_effort_as_variant():
    provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="high")
    assert build_model_string(provider) == "anthropic/claude-sonnet-4-5#high"


def test_build_model_string_omits_suffix_when_reasoning_effort_is_none():
    """A model without defined variants errors ("Variant unavailable") if
    any #suffix is appended - verified live against a local Ollama model."""
    provider = ProviderConfig(name="ollama", model="qwen3-14b-40k:latest", reasoning_effort=None)
    assert build_model_string(provider) == "ollama/qwen3-14b-40k:latest"


def test_invoke_harness_builds_correct_argv_and_returns_output():
    # A name that won't resolve on PATH, so this test exercises
    # placeholder substitution only - PATH resolution has its own test.
    harness = HarnessConfig(
        command=["not-a-real-harness-binary", "run", "--agent", "{agent}", "--model", "{model}", "{prompt}"]
    )
    provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="medium")
    stub = _StubRunner(stdout="# Review\nfound 3 issues")

    prompt_file = Path("/scratch/run-1/prompt.md")
    worktree_path = Path("/scratch/run-1/worktree")

    result = invoke_harness(
        harness,
        provider,
        prompt="Review this merge request.",
        prompt_file=prompt_file,
        worktree_path=worktree_path,
        runner=stub,
    )

    assert result.stdout == "# Review\nfound 3 issues"
    assert len(stub.calls) == 1
    argv = stub.calls[0]["argv"]
    assert argv == [
        "not-a-real-harness-binary",
        "run",
        "--agent",
        "reviewer",
        "--model",
        "anthropic/claude-sonnet-4-5#medium",
        "Review this merge request.",
    ]
    assert stub.calls[0]["kwargs"]["cwd"] == str(worktree_path)


def test_invoke_harness_resolves_command_via_path(monkeypatch):
    """Regression test: a bare command name must resolve through PATH
    (shutil.which) before being handed to subprocess, since on Windows
    many npm-installed CLIs are .CMD/.BAT shims that subprocess cannot
    locate from a bare name without shell=True."""
    harness = HarnessConfig(command=["opencode", "{prompt}"])
    provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="medium")
    stub = _StubRunner()

    monkeypatch.setattr(
        "review_agent.harness.shutil.which",
        lambda name: r"C:\nvm4w\nodejs\opencode.CMD" if name == "opencode" else None,
    )

    invoke_harness(
        harness,
        provider,
        prompt="review",
        prompt_file=Path("/p"),
        worktree_path=Path("/w"),
        runner=stub,
    )

    assert stub.calls[0]["argv"][0] == r"C:\nvm4w\nodejs\opencode.CMD"


def test_invoke_harness_raises_on_nonzero_exit():
    harness = HarnessConfig(command=["opencode", "run", "{model}", "{prompt}"])
    provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="medium")
    stub = _StubRunner(returncode=1)
    stub._stdout = ""

    with pytest.raises(HarnessError):
        invoke_harness(
            harness,
            provider,
            prompt="review",
            prompt_file=Path("/p"),
            worktree_path=Path("/w"),
            runner=stub,
        )


def test_provider_swap_is_configuration_only():
    """Switching providers must not require a different code path."""
    harness = HarnessConfig(command=["opencode", "run", "--model", "{model}", "{prompt}"])
    stub = _StubRunner()

    claude_provider = ProviderConfig(name="anthropic", model="claude-sonnet-4-5", reasoning_effort="medium")
    local_provider = ProviderConfig(name="ollama", model="qwen2.5-coder:32b", reasoning_effort="high")

    for provider in (claude_provider, local_provider):
        result = invoke_harness(
            harness,
            provider,
            prompt="review",
            prompt_file=Path("/p"),
            worktree_path=Path("/w"),
            runner=stub,
        )
        assert result.stdout == "stub review output"

    models_used = [call["argv"][3] for call in stub.calls]
    assert models_used == ["anthropic/claude-sonnet-4-5#medium", "ollama/qwen2.5-coder:32b#high"]


def test_invoke_harness_survives_non_utf8_output(tmp_path):
    """Regression: a byte that is not valid UTF-8 (0x87 - cp866 Cyrillic,
    seen live in opencode output on Windows) must not kill the reader
    thread; the run completes and the rest of the output is intact."""
    import sys

    # ASCII-only script (raw strings): non-ASCII argv itself gets mangled
    # on Windows. b"\xd0\xbe\xd0\xba" is UTF-8 "ок".
    script = (
        r"import sys;"
        r"sys.stdout.buffer.write(b'\xd0\xbe\xd0\xba \x87 end');"
        r"sys.stderr.buffer.write(b'err \x87')"
    )
    result = invoke_harness(
        HarnessConfig(command=[sys.executable, "-c", script]),
        ProviderConfig(name="p", model="m", reasoning_effort=None),
        prompt="review",
        prompt_file=tmp_path / "prompt.md",
        worktree_path=tmp_path,
    )
    assert result.stdout == "ок \ufffd end"
    assert result.stderr == "err \ufffd"
