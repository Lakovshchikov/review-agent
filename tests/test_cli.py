import subprocess
import sys

import pytest


def test_help_runs_and_prints_usage():
    result = subprocess.run(
        [sys.executable, "-m", "review_agent", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "--repo" in result.stdout


def test_poll_help_runs():
    result = subprocess.run(
        [sys.executable, "-m", "review_agent", "poll", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "--all" in result.stdout and "--dry-run" in result.stdout


def test_poll_subcommand_passes_flags_to_orchestrator(monkeypatch):
    from review_agent import cli, polling

    captured = {}
    monkeypatch.setattr(polling, "run_poll", lambda **kwargs: captured.update(kwargs) or 0)

    assert cli.main(["poll", "--all", "--dry-run", "--debug", "--config", "c.yaml"]) == 0
    assert captured["review_all"] is True
    assert captured["dry_run"] is True
    assert captured["debug"] is True
    assert str(captured["config_path"]) == "c.yaml"

    assert cli.main(["poll"]) == 0
    assert captured["review_all"] is False and captured["dry_run"] is False
    assert captured["include_closed"] is False and captured["debug"] is False

    assert cli.main(["poll", "--include-closed"]) == 0
    assert captured["include_closed"] is True


def _manual_config(tmp_path):
    import yaml

    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "provider": {"name": "p", "model": "m", "reasoning_effort": None},
                "harness": {"command": ["stub-harness", "{prompt_file}"]},
                "report": {"output_path": str(tmp_path / "reports" / "review-{run_id}.md")},
                "storage": {"work_dir": str(tmp_path / "work")},
            }
        ),
        encoding="utf-8",
    )
    return config


def _stub_harness(monkeypatch):
    from review_agent import pipeline
    from review_agent.harness import HarnessResult

    monkeypatch.setattr(
        pipeline, "invoke_harness", lambda *a, **k: HarnessResult("# Ручной отчёт", "trace")
    )


def _manual_args(fixture, config, *extra):
    return [
        "--repo", str(fixture["repo"]),
        "--base", fixture["base_sha"],
        "--head", fixture["head_sha"],
        "--config", str(config),
        *extra,
    ]


@pytest.mark.parametrize("command", [["--repo", "r", "--base", "b", "--head", "h"], ["poll"]])
def test_scratch_dir_flag_is_gone(command):
    from review_agent import cli

    with pytest.raises(SystemExit):
        cli.main([*command, "--scratch-dir", "s"])


def test_manual_review_keeps_only_the_report(git_repo_with_base_and_head, tmp_path, monkeypatch, capsys):
    from review_agent import cli

    _stub_harness(monkeypatch)
    config = _manual_config(tmp_path)

    assert cli.main(_manual_args(git_repo_with_base_and_head, config)) == 0

    reports = list((tmp_path / "reports").glob("review-*.md"))
    assert len(reports) == 1 and reports[0].read_text(encoding="utf-8") == "# Ручной отчёт"
    assert str(reports[0]) in capsys.readouterr().out
    # Nothing else of the run remains in the working directory - and no pass
    # log; only the usage ledger, which is history, not a file of the run.
    remaining = [p for p in (tmp_path / "work").rglob("*") if p.is_file()]
    assert remaining == [tmp_path / "work" / "usage" / "ledger.jsonl"]
    assert not (tmp_path / "work" / "logs").exists()


def test_manual_review_debug_keeps_artifacts(git_repo_with_base_and_head, tmp_path, monkeypatch):
    from review_agent import cli

    _stub_harness(monkeypatch)
    config = _manual_config(tmp_path)

    assert cli.main(_manual_args(git_repo_with_base_and_head, config, "--debug")) == 0

    kept = list((tmp_path / "work" / "debug").iterdir())
    assert len(kept) == 1 and "-manual-" in kept[0].name
    assert {"prompt.md", "report.md", "safety-note.md", "harness-stderr.log"} <= {
        p.name for p in kept[0].iterdir()
    }
    assert not any((tmp_path / "work" / "tmp").iterdir())


def test_manual_review_refused_while_a_pass_runs(git_repo_with_base_and_head, tmp_path, monkeypatch, capsys):
    import os

    from review_agent import cli

    _stub_harness(monkeypatch)
    config = _manual_config(tmp_path)
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "poll.lock").write_text(str(os.getpid()), encoding="utf-8")

    assert cli.main(_manual_args(git_repo_with_base_and_head, config)) == 2
    assert "уже выполняется" in capsys.readouterr().err
    assert not (tmp_path / "work" / "tmp").exists()  # no worktree was created
    assert not (tmp_path / "reports").exists()


def test_poll_dry_run_never_posts(tmp_path):
    import yaml

    from review_agent.gitlab import MRCandidate, MRMetadata, DiffRefs
    from review_agent.polling import run_poll

    clone = tmp_path / "clone"
    clone.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "provider": {"name": "p", "model": "m", "reasoning_effort": None},
                "harness": {"command": ["stub"]},
                "report": {"output_path": str(tmp_path / "reports" / "r-{run_id}.md")},
                "storage": {"work_dir": str(tmp_path / "work")},
                "gitlab": {
                    "hostname": "h",
                    "reviewers": ["bot"],
                    "projects": [{"path": "a/b", "local_repo": str(clone)}],
                },
            }
        ),
        encoding="utf-8",
    )

    class Client:
        def __init__(self, hostname):
            pass

        def preflight(self):
            return "bot"

        def list_review_candidates(self, *a, **k):
            return [MRCandidate("a/b", 1, "t", False)]

        def list_notes(self, *a):
            return []

        def get_mr_metadata(self, project, iid):
            return MRMetadata(iid, "t", "d", "u", "https://h/a/b/-/merge_requests/1", False)

        def get_diff_refs(self, *a):
            return DiffRefs("b" * 40, "a" * 40, "main")

        def post_note(self, *a, **k):
            raise AssertionError("dry-run must not post")

        update_note = delete_note = post_note

    def review(**kwargs):
        from review_agent.pipeline import ReviewResult

        return ReviewResult(report="x" * 500, harness_stderr="", run_id="r")

    code = run_poll(
        config_path=config,
        orphan_cleanup_fn=lambda *a: None,
        review_all=True,
        dry_run=True,
        client_factory=Client,
        review_fn=review,
        repo_sources_factory=lambda config, work_dir, log: _NoFetchSources(),
        output_fn=lambda line: None,
        is_git_repo=lambda p: True,
    )
    assert code == 0


class _NoFetchSources:
    def prepare(self, project, target_branch, iid):
        from pathlib import Path

        from review_agent.repo_source import PreparedRepo

        return PreparedRepo(Path(project.local_repo), [])


# -- usage accounting ----------------------------------------------------------


def _ledger_lines(tmp_path):
    import json

    path = tmp_path / "work" / "usage" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_manual_review_writes_a_manual_record(git_repo_with_base_and_head, tmp_path, monkeypatch):
    from review_agent import cli, polling

    _stub_harness(monkeypatch)
    monkeypatch.setattr(polling, "stdin_is_interactive", lambda stream=None: False)
    assert cli.main(_manual_args(git_repo_with_base_and_head, _manual_config(tmp_path))) == 0
    (record,) = _ledger_lines(tmp_path)
    assert record["review.source"] == "manual"
    assert record["review.project"] is None and record["review.mr.iid"] is None
    assert record["review.head_sha"] == git_repo_with_base_and_head["head_sha"]
    assert record["review.quota"] is None


def test_manual_review_asks_quota_on_a_console(git_repo_with_base_and_head, tmp_path, monkeypatch):
    from review_agent import cli, polling

    _stub_harness(monkeypatch)
    monkeypatch.setattr(polling, "stdin_is_interactive", lambda stream=None: True)
    answers = iter(["90", "70", "85", "69"])  # remaining % -> used 10/30, 15/31
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main(_manual_args(git_repo_with_base_and_head, _manual_config(tmp_path))) == 0
    (record,) = _ledger_lines(tmp_path)
    assert record["review.quota"]["before"] == {"5h": 10, "week": 30}
    assert record["review.quota"]["after"] == {"5h": 15, "week": 31}


def test_manual_review_with_accounting_disabled(git_repo_with_base_and_head, tmp_path, monkeypatch):
    import yaml

    from review_agent import cli

    _stub_harness(monkeypatch)
    config = _manual_config(tmp_path)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["usage"] = {"enabled": False}
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("no quota questions"))
    assert cli.main(_manual_args(git_repo_with_base_and_head, config)) == 0
    assert _ledger_lines(tmp_path) == []


def _write_ledger(tmp_path, records):
    import json

    path = tmp_path / "work" / "usage" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def _rec(run_id, model="gpt-5.5", effort="medium", problems=(), time=None):
    from datetime import datetime

    return {
        "schema": "review-agent.usage/1",
        "time": time or datetime.now().astimezone().isoformat(timespec="seconds"),
        "review.run_id": run_id,
        "review.project": "b2c/front",
        "review.mr.iid": 1,
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": model,
        "review.reasoning_effort": effort,
        "review.outcome": "succeeded",
        "review.duration_ms": 60000,
        "gen_ai.usage.input_tokens": 1000,
        "gen_ai.usage.cache_read.input_tokens": 0,
        "gen_ai.usage.cache_creation.input_tokens": 0,
        "gen_ai.usage.output_tokens": 10,
        "review.usage.reasoning_tokens": 0,
        "review.usage.format_problems": list(problems),
        "review.quota": None,
    }


PRICES = b'{"gpt-5.5": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.00001}}'


def test_usage_by_model_csv(tmp_path, capsys):
    import csv
    import io

    from review_agent import cli

    _write_ledger(tmp_path, [_rec("a"), _rec("b"), _rec("c", model="gpt-5.6-luna", effort="low")])
    code = cli.usage_main(
        ["--config", str(_manual_config(tmp_path)), "--by", "model", "--format", "csv"],
        fetch=lambda url: PRICES,
    )
    assert code == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert {(r["model"], r["effort"], r["reviews"]) for r in rows} == {("gpt-5.5", "medium", "2"), ("gpt-5.6-luna", "low", "1")}
    priced = next(r for r in rows if r["model"] == "gpt-5.5")
    assert float(priced["cost_usd"]) == pytest.approx(2 * (1000 * 1e-6 + 10 * 1e-5))


def test_usage_table_header_counts_format_problems(tmp_path, capsys):
    from review_agent import cli

    _write_ledger(tmp_path, [_rec("a", problems=["info.tokens.cache.read: нет поля"]), _rec("b")])
    assert cli.usage_main(["--config", str(_manual_config(tmp_path))], fetch=lambda url: PRICES) == 0
    out = capsys.readouterr().out
    assert "с проблемами формата харнесса: 1" in out
    assert "Ревью: 2" in out


def test_usage_without_ledger_reports_no_data(tmp_path, capsys):
    from review_agent import cli

    assert cli.main(["usage", "--config", str(_manual_config(tmp_path))]) == 0
    assert "Нет данных" in capsys.readouterr().out


def test_usage_offline_uses_saved_catalog_with_warning(tmp_path, capsys):
    from review_agent import cli

    _write_ledger(tmp_path, [_rec("a")])
    (tmp_path / "work" / "usage" / "price-catalog.json").write_bytes(PRICES)

    def offline(url):
        raise OSError("no network")

    assert cli.usage_main(["--config", str(_manual_config(tmp_path))], fetch=offline) == 0
    out = capsys.readouterr().out
    assert "устаревшими" in out and "копия от" in out


def test_usage_bad_period_is_an_error(tmp_path, capsys):
    from review_agent import cli

    assert cli.usage_main(["--config", str(_manual_config(tmp_path)), "--since", "week"]) == 2


def test_usage_defaults_to_the_whole_ledger_and_compact_table(tmp_path, capsys):
    from review_agent import cli

    _write_ledger(tmp_path, [_rec("old", time="2020-01-01T10:00:00+03:00"), _rec("new")])
    assert cli.usage_main(["--config", str(_manual_config(tmp_path))], fetch=lambda url: PRICES) == 0
    out = capsys.readouterr().out
    assert "Ревью: 2" in out and "всё время" in out
    assert "tokens_k" in out and "fresh_input" not in out
    assert cli.usage_main(["--config", str(_manual_config(tmp_path)), "--wide"], fetch=lambda url: PRICES) == 0
    assert "fresh_input" in capsys.readouterr().out
