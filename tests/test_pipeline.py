import subprocess
from pathlib import Path

import pytest
import yaml

from review_agent.config import load_config
from review_agent.harness import HarnessError
from review_agent.pipeline import ReviewResult, run_review


def _config(tmp_path, **extra):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "provider": {
                    "name": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "reasoning_effort": "medium",
                },
                "harness": {
                    "command": ["stub-harness", "--agent", "{agent}", "--model", "{model}", "{prompt}"],
                    "agent_name": "reviewer",
                },
                "report": {"output_path": str(tmp_path / "reports" / "review-{run_id}.md")},
                "storage": {"work_dir": str(tmp_path / "work")},
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _review(fixture, runner, **kwargs):
    return run_review(
        repo_path=fixture["repo"],
        base_sha=fixture["base_sha"],
        head_sha=fixture["head_sha"],
        mr_title="Test MR",
        mr_description="Test description",
        harness_runner=runner,
        **kwargs,
    )


def _tmp_entries(tmp_path):
    tmp = tmp_path / "work" / "tmp"
    return sorted(tmp.iterdir()) if tmp.exists() else []


def test_run_review_end_to_end_with_stub_harness(git_repo_with_base_and_head, tmp_path):
    fixture = git_repo_with_base_and_head
    captured = {}

    def stub_runner(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        # The restricted agent config must exist in the worktree (cwd) by
        # the time the harness is invoked - that's what enforces read-only
        # for a real OpenCode run (see harness_config.py).
        captured["opencode_json"] = (Path(kwargs["cwd"]) / "opencode.json").exists()
        captured["run_dir_files"] = sorted(p.name for p in Path(kwargs["cwd"]).parent.iterdir())
        return subprocess.CompletedProcess(argv, 0, "# Review\n\nNo issues found.", "tool trace line")

    result = _review(fixture, stub_runner, config_path=_config(tmp_path))

    assert isinstance(result, ReviewResult)
    assert result.report == "# Review\n\nNo issues found."
    assert result.harness_stderr == "tool trace line"

    # The harness was actually invoked, inside the isolated worktree in <work_dir>/tmp.
    assert captured["argv"][0] == "stub-harness"
    assert Path(captured["cwd"]).parent.parent == (tmp_path / "work" / "tmp").resolve()
    assert "reviewer" in captured["argv"]
    assert "anthropic/claude-sonnet-4-5#medium" in captured["argv"]
    assert "Test MR" in captured["argv"][-1]
    assert captured["opencode_json"] is True
    assert {"prompt.md", "safety-note.md", "worktree"} <= set(captured["run_dir_files"])

    # Nothing of the run is left: not the worktree, not the run folder.
    assert _tmp_entries(tmp_path) == []
    # The primary repo was never touched, and no report was written anywhere.
    assert (fixture["repo"] / "file.txt").read_text(encoding="utf-8") == "v2\n"
    assert not (tmp_path / "reports").exists()


def test_failed_harness_leaves_nothing_and_carries_stderr(git_repo_with_base_and_head, tmp_path):
    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 3, "", "boom trace")

    with pytest.raises(HarnessError) as info:
        _review(git_repo_with_base_and_head, failing, config_path=_config(tmp_path))
    assert info.value.stderr == "boom trace"
    assert _tmp_entries(tmp_path) == []


def test_debug_dir_keeps_run_files_but_not_worktree(git_repo_with_base_and_head, tmp_path):
    debug_dir = tmp_path / "work" / "debug" / "run-1"

    def stub(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "# Отчёт", "трасса")

    _review(git_repo_with_base_and_head, stub, config_path=_config(tmp_path), debug_dir=debug_dir)

    assert sorted(p.name for p in debug_dir.iterdir()) == [
        "harness-stderr.log",
        "prompt.md",
        "report.md",
        "safety-note.md",
    ]
    assert (debug_dir / "report.md").read_text(encoding="utf-8") == "# Отчёт"
    assert (debug_dir / "harness-stderr.log").read_text(encoding="utf-8") == "трасса"
    assert _tmp_entries(tmp_path) == []


def test_debug_dir_kept_for_failed_run_too(git_repo_with_base_and_head, tmp_path):
    debug_dir = tmp_path / "work" / "debug" / "run-2"

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "почему упал")

    with pytest.raises(HarnessError):
        _review(git_repo_with_base_and_head, failing, config_path=_config(tmp_path), debug_dir=debug_dir)
    assert (debug_dir / "harness-stderr.log").read_text(encoding="utf-8") == "почему упал"


def test_passed_config_overrides_config_file(git_repo_with_base_and_head, tmp_path):
    import dataclasses

    skill = tmp_path / "frontend-patterns.md"
    skill.write_text("паттерны", encoding="utf-8")
    base = load_config(_config(tmp_path))
    config = dataclasses.replace(
        base,
        provider=dataclasses.replace(base.provider, name="openai", model="gpt-5", reasoning_effort="high"),
        skills=[str(skill)],
    )
    captured = {}

    def stub(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    _review(git_repo_with_base_and_head, stub, config=config)

    assert "openai/gpt-5#high" in captured["argv"]
    assert str(skill) in captured["argv"][-1]  # the prompt names the project's skill file
