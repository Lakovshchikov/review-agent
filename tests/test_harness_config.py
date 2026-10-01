from pathlib import Path

from review_agent.config import SafetyConfig
from review_agent.harness_config import render_harness_runtime_config, write_harness_runtime_config

# Vocabulary that belongs to the REVIEW PROMPT (methodology), not the
# generated runtime/safety config. If any of this leaks into the runtime
# config, the engine violates "Review methodology lives in the per-run
# prompt, not system configuration".
METHODOLOGY_MARKERS = [
    "SEV",
    "Blocker",
    "Major",
    "Minor",
    "correctness",
    "readability",
    "testability",
    "SRP",
    "best practices",
]


def test_runtime_config_contains_only_safety_content():
    safety = SafetyConfig(output_language="ru", forbidden_commands=["npm", "yarn"])
    worktree_path = Path("/scratch/run-1/worktree")
    scratch_path = Path("/scratch/run-1")
    rendered = render_harness_runtime_config(worktree_path, scratch_path, safety)

    # Paths render platform-native (backslashes on Windows) - compare
    # against str(Path(...)), not a hardcoded POSIX literal.
    assert str(worktree_path) in rendered
    assert str(scratch_path) in rendered
    assert "npm" in rendered
    assert "yarn" in rendered
    assert "ru" in rendered
    assert "MUST NOT execute" in rendered
    assert "MUST NOT access the network" in rendered
    assert "MUST NOT publish anything to GitLab" in rendered

    for marker in METHODOLOGY_MARKERS:
        assert marker not in rendered, f"runtime config leaked review methodology: {marker!r}"


def test_write_harness_runtime_config_creates_file(tmp_path):
    scratch = tmp_path / "run-1"
    safety = SafetyConfig()

    config_path = write_harness_runtime_config(tmp_path / "run-1" / "worktree", scratch, safety)

    assert config_path.exists()
    assert config_path.parent == scratch
    assert "MUST NOT execute" in config_path.read_text(encoding="utf-8")
