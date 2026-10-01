from pathlib import Path

from review_agent.config import SafetyConfig
from review_agent.harness_config import (
    build_opencode_agent_config,
    render_safety_note,
    write_opencode_agent_config,
    write_safety_note,
)

# Vocabulary that belongs to the REVIEW PROMPT (methodology), not the
# generated safety artifacts. If any of this leaks in, the engine
# violates "Review methodology lives in the per-run prompt, not system
# configuration".
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


def test_safety_note_contains_only_safety_content():
    safety = SafetyConfig(output_language="ru", denied_bash_patterns=["npm *", "rm *"])
    worktree_path = Path("/scratch/run-1/worktree")
    scratch_path = Path("/scratch/run-1")

    rendered = render_safety_note(worktree_path, scratch_path, safety)

    assert str(worktree_path) in rendered
    assert str(scratch_path) in rendered
    assert "npm *" in rendered
    assert "rm *" in rendered
    assert "ru" in rendered

    for marker in METHODOLOGY_MARKERS:
        assert marker not in rendered, f"safety note leaked review methodology: {marker!r}"


def test_write_safety_note_creates_file(tmp_path):
    scratch = tmp_path / "run-1"
    safety = SafetyConfig()

    note_path = write_safety_note(tmp_path / "run-1" / "worktree", scratch, safety)

    assert note_path.exists()
    assert note_path.parent == scratch


def test_opencode_agent_config_denies_listed_patterns_only():
    """No catch-all "*" entry: verified live that adding one makes bash
    entirely unavailable in real OpenCode, even for explicitly allowed
    patterns - see harness_config.py module docstring."""
    safety = SafetyConfig(denied_bash_patterns=["npm *", "rm *"])

    config = build_opencode_agent_config(safety, agent_name="reviewer")

    agent = config["agent"]["reviewer"]
    assert agent["permission"]["bash"]["npm *"] == "deny"
    assert agent["permission"]["bash"]["rm *"] == "deny"
    assert "*" not in agent["permission"]["bash"]
    assert agent["permission"]["edit"] == "deny"
    assert agent["permission"]["webfetch"] == "deny"
    assert agent["permission"]["websearch"] == "deny"
    assert agent["permission"]["read"] == "allow"


def test_write_opencode_agent_config_writes_into_worktree_root(tmp_path):
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    safety = SafetyConfig()

    config_path = write_opencode_agent_config(worktree_path, safety, agent_name="reviewer")

    assert config_path == worktree_path / "opencode.json"
    assert config_path.exists()
    import json

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "reviewer" in data["agent"]
