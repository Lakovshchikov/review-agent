import subprocess
import sys


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

    assert cli.main(["poll", "--all", "--dry-run", "--config", "c.yaml", "--scratch-dir", "s"]) == 0
    assert captured["review_all"] is True
    assert captured["dry_run"] is True
    assert str(captured["config_path"]) == "c.yaml"

    assert cli.main(["poll"]) == 0
    assert captured["review_all"] is False and captured["dry_run"] is False
    assert captured["include_closed"] is False

    assert cli.main(["poll", "--include-closed"]) == 0
    assert captured["include_closed"] is True


def test_manual_form_still_parses_without_poll(monkeypatch, tmp_path):
    from review_agent import cli, pipeline

    captured = {}

    def fake_run_review(**kwargs):
        captured.update(kwargs)
        return tmp_path / "r.md"

    monkeypatch.setattr(pipeline, "run_review", fake_run_review)
    assert cli.main(["--repo", "r", "--base", "b", "--head", "h"]) == 0
    assert captured["head_sha"] == "h"


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
            return DiffRefs("b" * 40, "a" * 40)

        def post_note(self, *a, **k):
            raise AssertionError("dry-run must not post")

    def review(**kwargs):
        kwargs["report_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["report_path"].write_text("x" * 500, encoding="utf-8")
        return kwargs["report_path"]

    code = run_poll(
        config_path=config,
        scratch_dir=tmp_path / "scratch",
        review_all=True,
        dry_run=True,
        client_factory=Client,
        review_fn=review,
        fetch_fn=lambda *a: None,
        output_fn=lambda line: None,
        is_git_repo=lambda p: True,
    )
    assert code == 0
