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
    # The report refers to files by this path; publishing rewrites it.
    assert result.worktree_path == Path(captured["cwd"])

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


# -- usage accounting ----------------------------------------------------------

from review_agent.usage_ledger import RunLabels, UsageRecorder, read_records  # noqa: E402
from review_agent.usage_source import RunUsage, SessionUsage, TokenCounts  # noqa: E402


class StubSource:
    def __init__(self, usage=None, error=None):
        self.usage = usage
        self.error = error
        self.seen = []

    def collect(self, worktree_path):
        self.seen.append((worktree_path, worktree_path.exists()))
        if self.error:
            raise self.error
        return self.usage or RunUsage(
            harness_name="opencode",
            harness_version="2.0.21",
            sessions=[
                SessionUsage(
                    id="ses_1",
                    parent=None,
                    agent="reviewer",
                    tokens=TokenCounts(input=100, cache_read=50, cache_write=0, output=7, reasoning=3),
                )
            ],
        )


def _recorder(tmp_path, source=None):
    warnings, notes = [], []
    recorder = UsageRecorder(
        tmp_path / "work" / "usage" / "ledger.jsonl",
        source or StubSource(),
        warn=warnings.append,
        note=notes.append,
    )
    return recorder, warnings, notes


def _ok(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, "# Review\n\n### [Major] Bug\n", "")


def test_successful_review_writes_one_record(git_repo_with_base_and_head, tmp_path):
    source = StubSource()
    recorder, warnings, _ = _recorder(tmp_path, source)
    _review(
        git_repo_with_base_and_head,
        _ok,
        config_path=_config(tmp_path),
        usage=recorder,
        labels=RunLabels(source="poll", project="b2c/front", mr_iid=7),
    )
    records, _ = read_records(recorder.ledger_path)
    assert len(records) == 1
    record = records[0]
    assert record["review.outcome"] == "succeeded"
    assert (record["review.project"], record["review.mr.iid"]) == ("b2c/front", 7)
    assert record["gen_ai.provider.name"] == "anthropic"
    assert record["gen_ai.usage.input_tokens"] == 150
    assert record["review.findings"]["major"] == 1
    assert record["review.change.commits"] == 1
    assert isinstance(record["review.duration_ms"], int)
    assert record["review.quota"] is None
    # collected while the worktree still existed
    assert source.seen[0][1] is True
    assert warnings == []


def test_failed_harness_still_writes_a_failed_record(git_repo_with_base_and_head, tmp_path):
    recorder, _, _ = _recorder(tmp_path)

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 3, "", "boom")

    with pytest.raises(HarnessError):
        _review(git_repo_with_base_and_head, failing, config_path=_config(tmp_path), usage=recorder)
    records, _ = read_records(recorder.ledger_path)
    assert [r["review.outcome"] for r in records] == ["failed"]
    assert records[0]["review.findings"] is None
    assert records[0]["gen_ai.usage.input_tokens"] == 150


def test_quota_answers_land_in_the_record(git_repo_with_base_and_head, tmp_path):
    recorder, _, _ = _recorder(tmp_path)
    asked = []

    def prompt(stage):
        asked.append(stage)
        return {"5h": 12, "week": 40} if stage == "before" else {"5h": 21, "week": 42}

    _review(git_repo_with_base_and_head, _ok, config_path=_config(tmp_path), usage=recorder, quota_prompt=prompt)
    assert asked == ["before", "after"]
    record = read_records(recorder.ledger_path)[0][0]
    assert record["review.quota"] == {
        "provider": "anthropic",
        "before": {"5h": 12, "week": 40},
        "after": {"5h": 21, "week": 42},
    }


def test_broken_source_does_not_change_the_review(git_repo_with_base_and_head, tmp_path):
    recorder, warnings, _ = _recorder(tmp_path, StubSource(error=RuntimeError("boom")))
    result = _review(git_repo_with_base_and_head, _ok, config_path=_config(tmp_path), usage=recorder)
    assert result.report.startswith("# Review")
    assert warnings and "boom" in warnings[0]
    assert _tmp_entries(tmp_path) == []


def test_unwritable_ledger_does_not_change_the_review(git_repo_with_base_and_head, tmp_path):
    recorder, warnings, _ = _recorder(tmp_path)
    recorder.ledger_path.parent.parent.mkdir(parents=True, exist_ok=True)
    recorder.ledger_path.parent.write_text("not a folder", encoding="utf-8")
    result = _review(git_repo_with_base_and_head, _ok, config_path=_config(tmp_path), usage=recorder)
    assert result.report.startswith("# Review")
    assert any("журнал" in w for w in warnings)


def test_format_problems_warn_once_per_process(git_repo_with_base_and_head, tmp_path):
    usage = RunUsage(harness_name="opencode", format_problems=["версия 9.9 не проверена"])
    recorder, warnings, _ = _recorder(tmp_path, StubSource(usage=usage))
    for _ in range(2):
        _review(git_repo_with_base_and_head, _ok, config_path=_config(tmp_path), usage=recorder)
    assert len(warnings) == 1 and "9.9" in warnings[0]
    assert recorder.suppressed == 1
    assert len(read_records(recorder.ledger_path)[0]) == 2


def test_prompt_is_identical_with_and_without_accounting(git_repo_with_base_and_head, tmp_path):
    prompts = []

    def capture(argv, **kwargs):
        prompts.append((Path(kwargs["cwd"]).parent / "prompt.md").read_text(encoding="utf-8"))
        return _ok(argv)

    config_path = _config(tmp_path)
    recorder, _, _ = _recorder(tmp_path)
    _review(git_repo_with_base_and_head, capture, config_path=config_path)
    _review(git_repo_with_base_and_head, capture, config_path=config_path, usage=recorder)
    first, second = (p.replace(str(tmp_path), "") for p in prompts)
    # only the per-run worktree path differs
    import re

    strip = lambda text: re.sub(r"tmp[\\/][^\\/\s]+[\\/]worktree", "tmp/RUN/worktree", text)  # noqa: E731
    assert strip(first) == strip(second)


def test_without_recorder_no_ledger_appears(git_repo_with_base_and_head, tmp_path):
    _review(git_repo_with_base_and_head, _ok, config_path=_config(tmp_path))
    assert not (tmp_path / "work" / "usage").exists()
