import os
import subprocess
import sys

import pytest

from review_agent import proc


def test_no_flags_off_windows(monkeypatch):
    monkeypatch.setattr(proc.os, "name", "posix")
    assert proc.no_window_flags() == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows console API")
def test_no_window_flag_only_without_console():
    # pythonw-like child: no console of its own -> CREATE_NO_WINDOW for its children.
    code = "from review_agent.proc import no_window_flags; print(no_window_flags())"
    detached = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        creationflags=subprocess.DETACHED_PROCESS,
    )
    assert int(detached.stdout) == subprocess.CREATE_NO_WINDOW
    # With a console (the normal terminal case) children keep sharing it.
    with_console = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    assert int(with_console.stdout) == 0


def test_gitlab_and_harness_pass_creationflags(monkeypatch, tmp_path):
    from review_agent.config import HarnessConfig, ProviderConfig
    from review_agent.gitlab import GitLabClient
    from review_agent.harness import invoke_harness

    monkeypatch.setattr("review_agent.gitlab.no_window_flags", lambda: 4242)
    monkeypatch.setattr("review_agent.harness.no_window_flags", lambda: 4242)
    seen = []

    def runner(argv, **kwargs):
        seen.append(kwargs.get("creationflags"))
        return subprocess.CompletedProcess(argv, 0, '{"username": "bot"}', "")

    GitLabClient("h", runner=runner, which=lambda name: name).current_user()
    invoke_harness(
        HarnessConfig(command=["stub"]),
        ProviderConfig(name="p", model="m"),
        prompt="x",
        prompt_file=tmp_path / "p.md",
        worktree_path=tmp_path,
        runner=runner,
    )
    assert seen == [4242, 4242]


def test_python_dash_m_propagates_exit_code(tmp_path):
    # Task Scheduler shows the process exit code; `pythonw -m review_agent`
    # used to always exit 0 because __main__ dropped main()'s return value.
    result = subprocess.run(
        [sys.executable, "-m", "review_agent", "poll", "--all", "--config", str(tmp_path / "missing.yaml")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )
    assert result.returncode == 2


def test_main_survives_missing_streams(monkeypatch, tmp_path):
    from review_agent import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    # A bad flag makes argparse print an error to stderr - must not crash on None.
    with pytest.raises(SystemExit) as info:
        cli.main(["poll", "--no-such-flag"])
    assert info.value.code == 2
