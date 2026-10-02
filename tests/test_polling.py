import os
from pathlib import Path

import pytest
import yaml

from review_agent.gitlab import DiffRefs, GitLabError, MRCandidate, MRMetadata
from review_agent.polling import (
    EXIT_FAILURES,
    EXIT_NOT_STARTED,
    EXIT_OK,
    PollLockBusy,
    poll_lock,
    run_poll,
)
from review_agent.publishing import build_marker

GOOD_REPORT = "# Находки\n\n" + "## Major\n- Гонка в reset() пагинации.\n" * 20


class FakeGitLab:
    """In-memory GitLab: remembers posted notes, so a second pass sees them."""

    def __init__(self, mrs, *, bot="ai-reviewer"):
        # mrs: {(project, iid): {"title", "author", "draft", "head", "base", "notes"}}
        self.mrs = mrs
        self.bot = bot
        self.posted = []
        self.preflight_error = None
        self.notes_hook = None  # called on every list_notes, may mutate notes

    def __call__(self, hostname):
        self.hostname = hostname
        return self

    def preflight(self):
        if self.preflight_error:
            raise GitLabError(self.preflight_error)
        return self.bot

    def list_review_candidates(self, project, reviewers, *, review_drafts, include_closed=False):
        result = [
            MRCandidate(project, iid, mr["title"], mr.get("draft", False), mr.get("state", "opened"))
            for (p, iid), mr in sorted(self.mrs.items())
            if p == project and (include_closed or mr.get("state", "opened") == "opened")
        ]
        return [c for c in result if review_drafts or not c.draft]

    def list_notes(self, project, iid):
        if self.notes_hook:
            self.notes_hook(project, iid, self.mrs[(project, iid)]["notes"])
        return list(self.mrs[(project, iid)]["notes"])

    def get_mr_metadata(self, project, iid):
        mr = self.mrs[(project, iid)]
        return MRMetadata(
            iid=iid,
            title=mr["title"],
            description="desc",
            author=mr.get("author", "ivanov"),
            web_url=f"https://gitlab.local/{project}/-/merge_requests/{iid}",
            draft=mr.get("draft", False),
            state=mr.get("state", "opened"),
        )

    def get_diff_refs(self, project, iid):
        mr = self.mrs[(project, iid)]
        return DiffRefs(base_sha=mr.get("base", "b" * 40), head_sha=mr.get("head", f"{iid:040x}"))

    def post_note(self, project, iid, body, *, body_file):
        self.posted.append((project, iid, body))
        self.mrs[(project, iid)]["notes"].append({"body": body, "author": {"username": self.bot}})


def _mr(title="MR", **extra):
    return {"title": title, "notes": [], **extra}


@pytest.fixture
def setup(tmp_path):
    repo_a = tmp_path / "clone-a"
    repo_b = tmp_path / "clone-b"
    repo_a.mkdir()
    repo_b.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "provider": {"name": "openai", "model": "gpt", "reasoning_effort": "medium"},
                "harness": {"command": ["stub"]},
                "report": {"output_path": str(tmp_path / "reports" / "review-{run_id}.md")},
                "gitlab": {
                    "hostname": "gitlab.local",
                    "reviewers": ["ai-reviewer"],
                    "projects": [
                        {"path": "b2c/front", "local_repo": str(repo_a)},
                        {"path": "b2c/other", "local_repo": str(repo_b)},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return {"tmp": tmp_path, "config": config_path, "scratch": tmp_path / "scratch"}


class Recorder:
    def __init__(self, report=GOOD_REPORT, fail_for=()):
        self.report = report
        self.fail_for = set(fail_for)
        self.reviews = []
        self.fetches = []

    def review(self, **kwargs):
        self.reviews.append(kwargs)
        if kwargs["head_sha"] in self.fail_for:
            raise RuntimeError("harness exploded")
        kwargs["report_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["report_path"].write_text(self.report, encoding="utf-8")
        kwargs["stderr_log_path"].write_text("trace", encoding="utf-8")
        return kwargs["report_path"]

    def fetch(self, repo, remote, refspec):
        self.fetches.append((repo, remote, refspec))


def _poll(setup, gitlab, recorder, *, answers=(), tty=True, output=None, **kwargs):
    answers = list(answers)
    out = output if output is not None else []

    def input_fn(prompt):
        out.append(prompt)
        if not answers:
            raise EOFError
        return answers.pop(0)

    return run_poll(
        config_path=setup["config"],
        scratch_dir=setup["scratch"],
        client_factory=gitlab,
        review_fn=recorder.review,
        fetch_fn=recorder.fetch,
        input_fn=input_fn,
        output_fn=out.append,
        stdin_isatty=lambda: tty,
        is_git_repo=lambda path: path.is_dir(),
        **kwargs,
    )


# -- automatic mode ----------------------------------------------------------


def test_all_mode_publishes_each_mr_once_with_marker(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr("one", head="a" * 40), ("b2c/other", 2): _mr("two", head="c" * 40)})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK

    assert [(p, i) for p, i, _ in gitlab.posted] == [("b2c/front", 1), ("b2c/other", 2)]
    assert build_marker("a" * 40) in gitlab.posted[0][2]
    assert GOOD_REPORT.strip() in gitlab.posted[0][2]
    # Review used GitLab's diff_refs and MR metadata; MR ref was fetched first.
    assert recorder.reviews[0]["head_sha"] == "a" * 40
    assert recorder.reviews[0]["mr_title"] == "one"
    assert recorder.fetches[0][1:] == ("origin", "refs/merge-requests/1/head")


def test_already_reviewed_mr_is_skipped(setup):
    reviewed = _mr("old", notes=[{"body": build_marker("f" * 40), "author": {"username": "ai-reviewer"}}])
    gitlab = FakeGitLab({("b2c/front", 1): reviewed})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert recorder.reviews == []
    assert gitlab.posted == []


def test_second_all_pass_is_idempotent(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(), ("b2c/front", 2): _mr()})
    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_OK
    assert len(gitlab.posted) == 2

    second = Recorder()
    assert _poll(setup, gitlab, second, review_all=True) == EXIT_OK
    assert len(gitlab.posted) == 2
    assert second.reviews == []


# -- interactive mode --------------------------------------------------------


def _five_mrs():
    return {("b2c/front", i): _mr(f"MR номер {i}") for i in range(1, 6)}


def test_interactive_lists_links_and_reviews_only_selected(setup):
    gitlab = FakeGitLab(_five_mrs())
    recorder = Recorder()
    output = []

    assert _poll(setup, gitlab, recorder, answers=["2"], output=output) == EXIT_OK

    text = "\n".join(output)
    for i in range(1, 6):
        assert f"https://gitlab.local/b2c/front/-/merge_requests/{i}" in text
        assert f"MR номер {i}" in text
    assert "автор: ivanov" in text
    assert len(recorder.reviews) == 1
    assert [(p, i) for p, i, _ in gitlab.posted] == [("b2c/front", 2)]


def test_interactive_invalid_input_asks_again(setup):
    gitlab = FakeGitLab(_five_mrs())
    recorder = Recorder()
    output = []

    assert _poll(setup, gitlab, recorder, answers=["abc", "9", "3"], output=output) == EXIT_OK

    assert sum("Неверный ввод" in line for line in output) == 2
    assert [(p, i) for p, i, _ in gitlab.posted] == [("b2c/front", 3)]


@pytest.mark.parametrize("answers", [["q"], []])  # explicit quit, or EOF at the prompt
def test_interactive_quit_reviews_nothing(setup, answers):
    gitlab = FakeGitLab(_five_mrs())
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, answers=answers) == EXIT_OK
    assert recorder.reviews == []
    assert gitlab.posted == []


def test_no_candidates_exits_zero_without_prompt(setup):
    gitlab = FakeGitLab({})
    output = []

    assert _poll(setup, gitlab, Recorder(), output=output) == EXIT_OK
    assert any("Нет MR" in line for line in output)
    assert not any("Выберите номер" in line for line in output)


def test_interactive_without_tty_refuses(setup, capsys):
    gitlab = FakeGitLab(_five_mrs())
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, tty=False) == EXIT_NOT_STARTED
    assert "--all" in capsys.readouterr().err
    assert recorder.reviews == []


def test_interactive_reviewed_mr_disappears_from_list(setup):
    gitlab = FakeGitLab(_five_mrs())
    assert _poll(setup, gitlab, Recorder(), answers=["1"]) == EXIT_OK

    output = []
    assert _poll(setup, gitlab, Recorder(), answers=["q"], output=output) == EXIT_OK
    text = "\n".join(output)
    assert "merge_requests/1\n" not in text + "\n"
    assert "Найдено MR, где ревьюер назначен: 4" in text


# -- closed / merged MRs -----------------------------------------------------


def _mixed_states():
    return {
        ("b2c/front", 1): _mr("открытый"),
        ("b2c/front", 2): _mr("смёрженный", state="merged"),
        ("b2c/front", 3): _mr("закрытый", state="closed"),
    }


def test_closed_and_merged_ignored_by_default(setup):
    output = []
    assert _poll(setup, FakeGitLab(_mixed_states()), Recorder(), answers=["q"], output=output) == EXIT_OK
    text = "\n".join(output)
    assert "Найдено MR, где ревьюер назначен: 1" in text
    assert "смёрженный" not in text and "закрытый" not in text


def test_include_closed_lists_them_with_state_and_reviews_selected(setup):
    gitlab = FakeGitLab(_mixed_states())
    output = []

    assert _poll(setup, gitlab, Recorder(), answers=["2"], output=output, include_closed=True) == EXIT_OK

    text = "\n".join(output)
    assert "Найдено MR, где ревьюер назначен: 3" in text
    assert "[merged] смёрженный" in text and "[closed] закрытый" in text
    assert "[opened]" not in text
    assert [(p, i) for p, i, _ in gitlab.posted] == [("b2c/front", 2)]


def test_failed_mr_ref_fetch_is_not_fatal(setup):
    # GitLab may have cleaned up refs/merge-requests/<iid>/head of an old
    # merged MR; the engine then finds the commits itself.
    gitlab = FakeGitLab({("b2c/front", 2): _mr(state="merged")})
    recorder = Recorder()

    def failing_fetch(repo, remote, refspec):
        raise RuntimeError("couldn't find remote ref")

    recorder.fetch = failing_fetch

    assert _poll(setup, gitlab, recorder, review_all=True, include_closed=True) == EXIT_OK
    assert len(recorder.reviews) == 1
    assert len(gitlab.posted) == 1


def test_failed_fetch_reason_is_kept_when_review_then_fails(setup):
    gitlab = FakeGitLab({("b2c/front", 2): _mr(state="merged", head="2" * 40)})
    recorder = Recorder(fail_for={"2" * 40})

    def failing_fetch(repo, remote, refspec):
        raise RuntimeError("couldn't find remote ref")

    recorder.fetch = failing_fetch
    output = []

    assert _poll(setup, gitlab, recorder, review_all=True, include_closed=True, output=output) == EXIT_FAILURES
    line = next(l for l in output if "[failed]" in l)
    assert "couldn't find remote ref" in line and "harness exploded" in line


# -- failures and isolation --------------------------------------------------


def test_one_failed_review_does_not_stop_others(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="1" * 40), ("b2c/front", 2): _mr(head="2" * 40)})
    recorder = Recorder(fail_for={"1" * 40})
    output = []

    assert _poll(setup, gitlab, recorder, review_all=True, output=output) == EXIT_FAILURES
    assert [(p, i) for p, i, _ in gitlab.posted] == [("b2c/front", 2)]
    assert gitlab.mrs[("b2c/front", 1)]["notes"] == []
    assert any("[failed] b2c/front !1" in line and "harness exploded" in line for line in output)


def test_unusable_report_is_not_published(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    output = []

    assert _poll(setup, gitlab, Recorder(report="Understood."), review_all=True, output=output) == EXIT_FAILURES
    assert gitlab.posted == []
    text = "\n".join(output)
    assert "[failed]" in text and "harness-stderr.log" in text


def test_missing_local_clone_fails_project_but_others_continue(setup):
    cfg = yaml.safe_load(setup["config"].read_text(encoding="utf-8"))
    cfg["gitlab"]["projects"][0]["local_repo"] = str(setup["tmp"] / "does-not-exist")
    setup["config"].write_text(yaml.safe_dump(cfg), encoding="utf-8")
    gitlab = FakeGitLab({("b2c/front", 1): _mr(), ("b2c/other", 2): _mr()})
    output = []

    assert _poll(setup, gitlab, Recorder(), review_all=True, output=output) == EXIT_FAILURES
    assert [(p, i) for p, i, _ in gitlab.posted] == [("b2c/other", 2)]
    assert any("[failed] b2c/front" in line and "does-not-exist" in line for line in output)


def test_marker_appearing_during_review_prevents_publication(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    calls = {"n": 0}

    def hook(project, iid, notes):
        calls["n"] += 1
        if calls["n"] == 2:  # the re-check right before publishing
            notes.append({"body": build_marker("9" * 40), "author": {"username": "ai-reviewer"}})

    gitlab.notes_hook = hook

    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_OK
    assert gitlab.posted == []


def test_gitlab_unreachable_runs_no_reviews(setup, capsys):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    gitlab.preflight_error = "not authenticated"
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_NOT_STARTED
    assert "not authenticated" in capsys.readouterr().err
    assert recorder.reviews == []


def test_missing_gitlab_section_is_config_error(setup, capsys):
    cfg = yaml.safe_load(setup["config"].read_text(encoding="utf-8"))
    del cfg["gitlab"]
    setup["config"].write_text(yaml.safe_dump(cfg), encoding="utf-8")

    assert _poll(setup, FakeGitLab({}), Recorder(), review_all=True) == EXIT_NOT_STARTED
    assert "gitlab" in capsys.readouterr().err


def test_dry_run_posts_nothing_and_saves_comment(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})
    output = []

    assert _poll(setup, gitlab, Recorder(), review_all=True, dry_run=True, output=output) == EXIT_OK
    assert gitlab.posted == []
    comments = list((setup["tmp"] / "reports").glob("*.comment.md"))
    assert len(comments) == 1
    assert build_marker("a" * 40) in comments[0].read_text(encoding="utf-8")
    assert any("[dry-run]" in line for line in output)


# -- one pass at a time ------------------------------------------------------


def test_lock_busy_when_owner_alive(tmp_path):
    with poll_lock(tmp_path, is_alive=lambda pid: True):
        with pytest.raises(PollLockBusy, match="poll.lock"):
            with poll_lock(tmp_path, is_alive=lambda pid: True):
                pass


def test_stale_lock_is_taken_over(tmp_path):
    (tmp_path / "poll.lock").write_text("999999", encoding="utf-8")
    with poll_lock(tmp_path, is_alive=lambda pid: False) as lock_path:
        assert lock_path.read_text(encoding="utf-8") == str(os.getpid())
    assert not (tmp_path / "poll.lock").exists()


def test_lock_released_on_exception(tmp_path):
    with pytest.raises(ValueError):
        with poll_lock(tmp_path):
            raise ValueError("boom")
    assert not (tmp_path / "poll.lock").exists()


def test_second_pass_refused_while_first_running(setup, capsys):
    setup["scratch"].mkdir(parents=True)
    (setup["scratch"] / "poll.lock").write_text(str(os.getpid()), encoding="utf-8")  # a live PID
    recorder = Recorder()

    assert _poll(setup, FakeGitLab({("b2c/front", 1): _mr()}), recorder, review_all=True) == EXIT_NOT_STARTED
    err = capsys.readouterr().err
    assert "уже выполняется" in err and "poll.lock" in err
    assert recorder.reviews == []


def test_real_pid_check_for_current_and_dead_process():
    from review_agent.polling import pid_alive

    assert pid_alive(os.getpid())
    assert not pid_alive(0)
