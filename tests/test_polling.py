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


import itertools
from datetime import datetime, timedelta, timezone

from review_agent.harness import HarnessError
from review_agent.pipeline import ReviewResult
from review_agent.gitlab import NoteNotFound
from review_agent.publishing import build_claim_marker
from review_agent.repo_source import PreparedRepo

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_body(body_file, body):
    # Like the real adapter: the body goes through a file (in <work_dir>/tmp).
    assert "tmp" in body_file.parts
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(body, encoding="utf-8")


class FakeGitLab:
    """In-memory GitLab: notes get ids and created_at, so claims behave like the real thing."""

    def __init__(self, mrs, *, bot="ai-reviewer"):
        # mrs: {(project, iid): {"title", "author", "draft", "head", "base", "notes"}}
        self.mrs = mrs
        self.bot = bot
        self.ids = itertools.count(1000)
        self.calls = []  # ("post"|"update"|"delete", project, iid, note_id)
        self.preflight_error = None
        self.notes_hook = None  # called on every list_notes, may mutate notes
        self.after_post_hook = None  # called right after a note is posted
        self.delete_error = None
        self.reviewer_calls = []
        for mr in mrs.values():
            for note in mr["notes"]:
                note.setdefault("id", next(self.ids))
                note.setdefault("created_at", _iso(NOW - timedelta(days=1)))

    def __call__(self, hostname):
        self.hostname = hostname
        return self

    # -- reads
    def preflight(self):
        if self.preflight_error:
            raise GitLabError(self.preflight_error)
        return self.bot

    def list_review_candidates(self, project, reviewers, *, review_drafts, include_closed=False):
        self.reviewer_calls.append((project, list(reviewers), review_drafts))
        result = [
            MRCandidate(project, iid, mr["title"], mr.get("draft", False), mr.get("state", "opened"))
            for (p, iid), mr in sorted(self.mrs.items())
            if p == project and (include_closed or mr.get("state", "opened") == "opened")
        ]
        return [c for c in result if review_drafts or not c.draft]

    def list_notes(self, project, iid):
        if self.notes_hook:
            self.notes_hook(project, iid, self.mrs[(project, iid)]["notes"])
        return [dict(n) for n in self.mrs[(project, iid)]["notes"]]

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
        return DiffRefs(
            base_sha=mr.get("base", "b" * 40),
            head_sha=mr.get("head", f"{iid:040x}"),
            target_branch=mr.get("target", "main"),
        )

    # -- writes
    def _note(self, project, iid, note_id):
        for note in self.mrs[(project, iid)]["notes"]:
            if note["id"] == note_id:
                return note
        raise NoteNotFound(f"404 note {note_id}")

    def post_note(self, project, iid, body, *, body_file):
        _write_body(body_file, body)
        note_id = next(self.ids)
        self.calls.append(("post", project, iid, note_id))
        self.mrs[(project, iid)]["notes"].append(
            {"id": note_id, "body": body, "author": {"username": self.bot}, "created_at": _iso(NOW)}
        )
        if self.after_post_hook:
            self.after_post_hook(project, iid, note_id)
        return note_id

    def update_note(self, project, iid, note_id, body, *, body_file):
        _write_body(body_file, body)
        self.calls.append(("update", project, iid, note_id))
        self._note(project, iid, note_id)["body"] = body

    def delete_note(self, project, iid, note_id):
        self.calls.append(("delete", project, iid, note_id))
        if self.delete_error:
            raise GitLabError(self.delete_error)
        note = self._note(project, iid, note_id)
        self.mrs[(project, iid)]["notes"].remove(note)

    # -- assertions helpers
    def bot_notes(self, project, iid):
        return [n for n in self.mrs[(project, iid)]["notes"] if n["author"]["username"] == self.bot]

    @property
    def published(self):
        """(project, iid, body) of every bot note carrying a review marker."""
        return [
            (p, i, n["body"])
            for (p, i), mr in sorted(self.mrs.items())
            for n in mr["notes"]
            if n["author"]["username"] == self.bot and "<!-- ai-review: sha=" in n["body"]
        ]

    @property
    def writes(self):
        return [c for c in self.calls]


def _mr(title="MR", **extra):
    return {"title": title, "notes": [], **extra}


def _claim(head="c" * 40, age=timedelta(minutes=5), author="ai-reviewer"):
    started = NOW - age
    return {
        "body": "⏳ " + build_claim_marker(head, started),
        "author": {"username": author},
        "created_at": _iso(started),
    }


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
                "storage": {"work_dir": str(tmp_path / "work")},
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
    return {"tmp": tmp_path, "config": config_path, "work": tmp_path / "work"}


def _edit_config(setup, change):
    cfg = yaml.safe_load(setup["config"].read_text(encoding="utf-8"))
    change(cfg)
    setup["config"].write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


class Recorder:
    def __init__(self, report=GOOD_REPORT, fail_for=(), worktree_path=None):
        self.report = report
        self.worktree_path = worktree_path
        self.fail_for = set(fail_for)
        self.reviews = []
        self.prepares = []
        self.prepare_warnings = []
        self.prepare_error = None
        self.during_review = None  # called inside review(), e.g. to mutate GitLab

    def review(self, **kwargs):
        self.reviews.append(kwargs)
        if self.during_review:
            self.during_review(kwargs)
        if kwargs["debug_dir"] is not None:
            kwargs["debug_dir"].mkdir(parents=True, exist_ok=True)
            (kwargs["debug_dir"] / "prompt.md").write_text("prompt", encoding="utf-8")
        if kwargs["head_sha"] in self.fail_for:
            raise HarnessError("harness exploded", stderr="трасса упавшего харнесса")
        return ReviewResult(
            report=self.report, harness_stderr="trace", run_id="r1", worktree_path=self.worktree_path
        )

    def sources(self, config, work_dir, log):
        recorder = self

        class StubSources:
            def prepare(self, project, target_branch, iid):
                recorder.prepares.append((project.path, target_branch, iid))
                if recorder.prepare_error is not None:
                    raise recorder.prepare_error
                path = Path(project.local_repo) if project.local_repo else work_dir.repos / "copy.git"
                return PreparedRepo(path, list(recorder.prepare_warnings))

        return StubSources()


def _poll(setup, gitlab, recorder, *, answers=(), tty=True, output=None, **kwargs):
    answers = list(answers)
    out = output if output is not None else []

    def input_fn(prompt):
        out.append(prompt)
        if not answers:
            raise EOFError
        return answers.pop(0)

    kwargs.setdefault("orphan_cleanup_fn", lambda repo, tmp: None)
    kwargs.setdefault("tree_paths_fn", lambda repo, sha: set())
    return run_poll(
        config_path=setup["config"],
        client_factory=gitlab,
        review_fn=recorder.review,
        repo_sources_factory=recorder.sources,
        input_fn=input_fn,
        output_fn=out.append,
        stdin_isatty=lambda: tty,
        is_git_repo=lambda path: path.is_dir(),
        now_fn=lambda: NOW,
        **kwargs,
    )


def _pass_logs(setup):
    return sorted((setup["work"] / "logs").glob("poll-*.log"))


# -- automatic mode ----------------------------------------------------------


def test_all_mode_publishes_each_mr_once_with_marker(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr("one", head="a" * 40), ("b2c/other", 2): _mr("two", head="c" * 40)})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK

    assert [(p, i) for p, i, _ in gitlab.published] == [("b2c/front", 1), ("b2c/other", 2)]
    assert build_marker("a" * 40) in gitlab.published[0][2]
    assert GOOD_REPORT.strip() in gitlab.published[0][2]
    # Review used GitLab's diff_refs and MR metadata; the MR's branches were
    # brought up to date in the project's clone first.
    assert recorder.reviews[0]["head_sha"] == "a" * 40
    assert recorder.reviews[0]["mr_title"] == "one"
    assert recorder.prepares[0] == ("b2c/front", "main", 1)
    assert recorder.reviews[0]["repo_path"] == setup["tmp"] / "clone-a"


def test_already_reviewed_mr_is_skipped(setup):
    reviewed = _mr("old", notes=[{"body": build_marker("f" * 40), "author": {"username": "ai-reviewer"}}])
    gitlab = FakeGitLab({("b2c/front", 1): reviewed})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert recorder.reviews == []
    assert gitlab.calls == []


def test_second_all_pass_is_idempotent(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(), ("b2c/front", 2): _mr()})
    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_OK
    assert len(gitlab.published) == 2

    second = Recorder()
    assert _poll(setup, gitlab, second, review_all=True) == EXIT_OK
    assert len(gitlab.published) == 2
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
    assert [(p, i) for p, i, _ in gitlab.published] == [("b2c/front", 2)]


def test_interactive_invalid_input_asks_again(setup):
    gitlab = FakeGitLab(_five_mrs())
    recorder = Recorder()
    output = []

    assert _poll(setup, gitlab, recorder, answers=["abc", "9", "3"], output=output) == EXIT_OK

    assert sum("Неверный ввод" in line for line in output) == 2
    assert [(p, i) for p, i, _ in gitlab.published] == [("b2c/front", 3)]


@pytest.mark.parametrize("answers", [["q"], []])  # explicit quit, or EOF at the prompt
def test_interactive_quit_reviews_nothing(setup, answers):
    gitlab = FakeGitLab(_five_mrs())
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, answers=answers) == EXIT_OK
    assert recorder.reviews == []
    assert gitlab.published == []


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
    assert [(p, i) for p, i, _ in gitlab.published] == [("b2c/front", 2)]


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
    assert len(gitlab.published) == 1


def test_failed_fetch_reason_is_kept_when_review_then_fails(setup):
    gitlab = FakeGitLab({("b2c/front", 2): _mr(state="merged", head="2" * 40)})
    recorder = Recorder(fail_for={"2" * 40})
    recorder.prepare_warnings = ["fetch refs/merge-requests/2/head: couldn't find remote ref"]
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
    assert [(p, i) for p, i, _ in gitlab.published] == [("b2c/front", 2)]
    assert gitlab.mrs[("b2c/front", 1)]["notes"] == []
    assert any("[failed] b2c/front !1" in line and "harness exploded" in line for line in output)


def test_unusable_report_is_not_published(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    output = []

    assert _poll(setup, gitlab, Recorder(report="Understood."), review_all=True, output=output) == EXIT_FAILURES
    assert gitlab.published == []
    text = "\n".join(output)
    assert "[failed]" in text and "harness-stderr.log" in text


def test_missing_local_clone_fails_project_but_others_continue(setup):
    _edit_config(
        setup, lambda cfg: cfg["gitlab"]["projects"][0].update(local_repo=str(setup["tmp"] / "does-not-exist"))
    )
    gitlab = FakeGitLab({("b2c/front", 1): _mr(), ("b2c/other", 2): _mr()})
    output = []

    assert _poll(setup, gitlab, Recorder(), review_all=True, output=output) == EXIT_FAILURES
    assert [(p, i) for p, i, _ in gitlab.published] == [("b2c/other", 2)]
    assert any("[failed] b2c/front" in line and "does-not-exist" in line for line in output)


def test_marker_appearing_during_review_prevents_publication(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    recorder = Recorder()

    def someone_published(kwargs):
        gitlab.mrs[("b2c/front", 1)]["notes"].append(
            {"id": 1, "body": build_marker("9" * 40), "author": {"username": "ai-reviewer"}}
        )

    recorder.during_review = someone_published

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert [c[0] for c in gitlab.calls] == ["post", "delete"]  # claim, then claim removed
    assert [n["body"] for n in gitlab.bot_notes("b2c/front", 1)] == [build_marker("9" * 40)]


def test_gitlab_unreachable_runs_no_reviews(setup, capsys):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    gitlab.preflight_error = "not authenticated"
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_NOT_STARTED
    assert "not authenticated" in capsys.readouterr().err
    assert recorder.reviews == []


def test_missing_gitlab_section_is_config_error(setup, capsys):
    _edit_config(setup, lambda cfg: cfg.pop("gitlab"))

    assert _poll(setup, FakeGitLab({}), Recorder(), review_all=True) == EXIT_NOT_STARTED
    assert "gitlab" in capsys.readouterr().err


def test_dry_run_posts_nothing_and_saves_comment(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})
    output = []

    assert _poll(setup, gitlab, Recorder(), review_all=True, dry_run=True, output=output) == EXIT_OK
    assert gitlab.calls == []  # no claim, no note, nothing written to GitLab
    comments = list((setup["work"] / "dry-run").glob("*.comment.md"))
    assert len(comments) == 1
    assert build_marker("a" * 40) in comments[0].read_text(encoding="utf-8")
    assert any("[dry-run]" in line for line in output)


# -- file links --------------------------------------------------------------

LINK_WT = "E:/agent/.review-agent/tmp/r1/worktree"
LINK_REPORT = GOOD_REPORT + (
    f"\n### Major — x\n[`src/a.ts:13`]({LINK_WT}/src/a.ts#L13)\n\nСм. `src/b.ts:5-7`.\n"
)
LINK_BLOB = "https://gitlab.local/b2c/front/-/blob/" + "a" * 40


def _link_mr():
    return FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})


def test_published_comment_links_files_in_the_reviewed_commit(setup):
    gitlab = _link_mr()
    seen = []

    def tree_paths(repo, sha):
        seen.append((repo, sha))
        return {"src", "src/a.ts", "src/b.ts"}

    recorder = Recorder(report=LINK_REPORT, worktree_path=Path(LINK_WT))
    assert _poll(setup, gitlab, recorder, review_all=True, tree_paths_fn=tree_paths) == EXIT_OK

    body = gitlab.published[0][2]
    assert f"[`src/a.ts:13`]({LINK_BLOB}/src/a.ts#L13)" in body
    assert f"[`src/b.ts:5-7`]({LINK_BLOB}/src/b.ts#L5-7)" in body
    assert "review-agent/tmp" not in body
    # Both commits listed in the project's prepared repository.
    assert seen == [(setup["tmp"] / "clone-a", "a" * 40), (setup["tmp"] / "clone-a", "b" * 40)]


def test_dry_run_comment_has_the_same_links(setup):
    recorder = Recorder(report=LINK_REPORT, worktree_path=Path(LINK_WT))
    tree_paths = lambda repo, sha: {"src/a.ts", "src/b.ts"}  # noqa: E731
    assert _poll(
        setup, _link_mr(), recorder, review_all=True, dry_run=True, tree_paths_fn=tree_paths
    ) == EXIT_OK

    text = next((setup["work"] / "dry-run").glob("*.comment.md")).read_text(encoding="utf-8")
    assert f"({LINK_BLOB}/src/a.ts#L13)" in text
    assert f"({LINK_BLOB}/src/b.ts#L5-7)" in text
    assert "review-agent/tmp" not in text


def test_file_list_failure_still_publishes_with_worktree_links(setup):
    from review_agent.worktree import WorktreeError

    def broken(repo, sha):
        raise WorktreeError("git ls-tree exploded")

    gitlab = _link_mr()
    output = []
    recorder = Recorder(report=LINK_REPORT, worktree_path=Path(LINK_WT))
    assert _poll(
        setup, gitlab, recorder, review_all=True, tree_paths_fn=broken, output=output
    ) == EXIT_OK

    body = gitlab.published[0][2]
    assert f"[`src/a.ts:13`]({LINK_BLOB}/src/a.ts#L13)" in body
    assert "См. `src/b.ts:5-7`." in body  # unchecked inline code is left alone
    assert "review-agent/tmp" not in body
    log = _pass_logs(setup)[-1].read_text(encoding="utf-8")
    assert "b2c/front !1" in log and "git ls-tree exploded" in log


def test_link_rewrite_crash_publishes_the_report_as_is(setup, monkeypatch):
    import review_agent.polling as polling

    def boom(*args, **kwargs):
        raise ValueError("regex blew up")

    monkeypatch.setattr(polling, "link_file_references", boom)
    gitlab = _link_mr()
    assert _poll(setup, gitlab, Recorder(report=LINK_REPORT), review_all=True) == EXIT_OK

    assert LINK_REPORT.strip() in gitlab.published[0][2]
    assert "regex blew up" in _pass_logs(setup)[-1].read_text(encoding="utf-8")


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
    setup["work"].mkdir(parents=True)
    (setup["work"] / "poll.lock").write_text(str(os.getpid()), encoding="utf-8")  # a live PID
    recorder = Recorder()

    assert _poll(setup, FakeGitLab({("b2c/front", 1): _mr()}), recorder, review_all=True) == EXIT_NOT_STARTED
    err = capsys.readouterr().err
    assert "уже выполняется" in err and "poll.lock" in err
    assert recorder.reviews == []


def test_stdin_from_null_device_is_not_interactive():
    # Regression: on Windows isatty() is True for NUL, so `poll < NUL`
    # used to prompt instead of refusing.
    from review_agent.polling import stdin_is_interactive

    with open(os.devnull, encoding="utf-8") as null:
        assert not stdin_is_interactive(null)


def test_missing_or_fileless_stdin_is_not_interactive(monkeypatch):
    import io
    import sys

    from review_agent.polling import stdin_is_interactive

    monkeypatch.setattr(sys, "stdin", None)  # e.g. Task Scheduler without a console
    assert not stdin_is_interactive()
    assert not stdin_is_interactive(io.StringIO("1\n"))


def test_stdin_from_file_is_not_interactive(tmp_path):
    from review_agent.polling import stdin_is_interactive

    answers = tmp_path / "answers.txt"
    answers.write_text("1\n", encoding="utf-8")
    with open(answers, encoding="utf-8") as f:
        assert not stdin_is_interactive(f)


def test_real_pid_check_for_current_and_dead_process():
    from review_agent.polling import pid_alive

    assert pid_alive(os.getpid())
    assert not pid_alive(0)


# -- claim in GitLab (design.md decision 7) ----------------------------------


def _tmp_is_empty(setup):
    tmp = setup["work"] / "tmp"
    return not tmp.exists() or not any(tmp.iterdir())


def test_published_review_is_one_note_without_claim_marker(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})

    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_OK

    notes = gitlab.bot_notes("b2c/front", 1)
    assert len(notes) == 1
    assert build_marker("a" * 40) in notes[0]["body"]
    assert "ai-review-claim" not in notes[0]["body"]
    assert [c[0] for c in gitlab.calls] == ["post", "update"]  # claim, then claim -> report
    assert _tmp_is_empty(setup)


def test_claim_is_posted_before_the_harness_runs(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})
    recorder = Recorder()
    seen = {}
    recorder.during_review = lambda kwargs: seen.update(
        bodies=[n["body"] for n in gitlab.bot_notes("b2c/front", 1)]
    )

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert len(seen["bodies"]) == 1
    assert "ai-review-claim: sha=" + "a" * 40 in seen["bodies"][0]
    assert "выполняется" in seen["bodies"][0]


def test_reviewed_while_waiting_in_queue_is_skipped_without_claim(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="1" * 40), ("b2c/front", 2): _mr(head="2" * 40)})
    recorder = Recorder()

    def another_pass_reviews_mr2(kwargs):
        if kwargs["head_sha"] == "1" * 40:
            gitlab.mrs[("b2c/front", 2)]["notes"].append(
                {"id": 1, "body": build_marker("2" * 40), "author": {"username": "ai-reviewer"},
                 "created_at": _iso(NOW)}
            )

    recorder.during_review = another_pass_reviews_mr2
    output = []

    assert _poll(setup, gitlab, recorder, review_all=True, output=output) == EXIT_OK
    assert [k["head_sha"] for k in recorder.reviews] == ["1" * 40]
    assert not [c for c in gitlab.calls if c[2] == 2]  # nothing written on !2
    assert any("[skipped] b2c/front !2" in line and "уже отревьюен" in line for line in output)


def test_live_claim_of_another_pass_skips_mr(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(notes=[_claim(age=timedelta(minutes=10))])})
    recorder = Recorder()
    output = []

    assert _poll(setup, gitlab, recorder, review_all=True, output=output) == EXIT_OK
    assert recorder.reviews == []
    assert gitlab.calls == []
    assert any("ревью уже выполняется" in line for line in output)


def test_claim_appearing_after_listing_skips_at_review_time(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="1" * 40), ("b2c/front", 2): _mr(head="2" * 40)})
    recorder = Recorder()

    def another_pass_claims_mr2(kwargs):
        if kwargs["head_sha"] == "1" * 40:
            claim = _claim(head="2" * 40, age=timedelta(minutes=1))
            claim["id"] = 1
            gitlab.mrs[("b2c/front", 2)]["notes"].append(claim)

    recorder.during_review = another_pass_claims_mr2

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert len(recorder.reviews) == 1
    assert not [c for c in gitlab.calls if c[2] == 2]


def test_claim_by_another_user_is_ignored(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(notes=[_claim(author="ivanov")])})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert len(recorder.reviews) == 1
    assert len(gitlab.published) == 1


def test_simultaneous_claim_race_earlier_claim_wins(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    recorder = Recorder()

    def other_pass_claimed_first(project, iid, my_id):
        if not any(n["id"] == 1 for n in gitlab.mrs[(project, iid)]["notes"]):
            claim = _claim(head="1" * 40, age=timedelta(seconds=1))
            claim["id"] = 1  # lower id = posted earlier
            gitlab.mrs[(project, iid)]["notes"].append(claim)

    gitlab.after_post_hook = other_pass_claimed_first
    output = []

    assert _poll(setup, gitlab, recorder, review_all=True, output=output) == EXIT_OK
    assert recorder.reviews == []
    my_claim = gitlab.calls[0][3]
    assert gitlab.calls == [("post", "b2c/front", 1, my_claim), ("delete", "b2c/front", 1, my_claim)]
    assert [n["id"] for n in gitlab.bot_notes("b2c/front", 1)] == [1]  # only the winner's claim
    assert any("другой проход взял MR раньше" in line for line in output)


def test_stale_claim_is_removed_and_mr_reviewed(setup):
    stale = _claim(age=timedelta(hours=5))  # default TTL is 240 minutes
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40, notes=[stale])})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert len(recorder.reviews) == 1
    assert ("delete", "b2c/front", 1, stale["id"]) in gitlab.calls
    notes = gitlab.bot_notes("b2c/front", 1)
    assert len(notes) == 1 and build_marker("a" * 40) in notes[0]["body"]


def test_claim_ttl_comes_from_config(setup):
    _edit_config(setup, lambda cfg: cfg["gitlab"].update(claim_ttl_minutes=1))
    gitlab = FakeGitLab({("b2c/front", 1): _mr(notes=[_claim(age=timedelta(minutes=2))])})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert len(recorder.reviews) == 1


@pytest.mark.parametrize("report, fail", [(GOOD_REPORT, True), ("Understood.", False)])
def test_failed_or_unusable_review_removes_its_claim(setup, report, fail):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})
    recorder = Recorder(report=report, fail_for={"a" * 40} if fail else ())

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_FAILURES
    assert gitlab.bot_notes("b2c/front", 1) == []
    assert [c[0] for c in gitlab.calls] == ["post", "delete"]


def test_claim_deleted_by_someone_during_review_publishes_new_note(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})
    recorder = Recorder()
    recorder.during_review = lambda kwargs: gitlab.mrs[("b2c/front", 1)]["notes"].clear()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert [c[0] for c in gitlab.calls] == ["post", "update", "post"]
    assert len(gitlab.published) == 1


def test_failed_claim_deletion_is_reported_and_pass_continues(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="1" * 40), ("b2c/front", 2): _mr(head="2" * 40)})
    gitlab.delete_error = "HTTP 500"
    output = []

    code = _poll(setup, gitlab, Recorder(fail_for={"1" * 40}), review_all=True, output=output)

    assert code == EXIT_FAILURES
    line = next(l for l in output if "[failed] b2c/front !1" in l)
    assert "не удалось удалить claim" in line and "240 мин" in line
    assert [(p, i) for p, i, _ in gitlab.published] == [("b2c/front", 2)]


def test_dry_run_writes_nothing_even_with_stale_claim_and_skips_live_one(setup):
    gitlab = FakeGitLab(
        {
            ("b2c/front", 1): _mr(notes=[_claim(age=timedelta(hours=9))]),
            ("b2c/front", 2): _mr(notes=[_claim(age=timedelta(minutes=3))]),
        }
    )
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True, dry_run=True) == EXIT_OK
    assert gitlab.calls == []
    assert len(recorder.reviews) == 1  # !1 (stale claim) reviewed locally, !2 skipped


# -- per-project settings ----------------------------------------------------


def test_disabled_project_is_not_touched(setup):
    _edit_config(
        setup,
        lambda cfg: cfg["gitlab"]["projects"][0].update(
            enabled=False, local_repo=str(setup["tmp"] / "no-such-clone")
        ),
    )
    gitlab = FakeGitLab({("b2c/front", 1): _mr(), ("b2c/other", 2): _mr()})
    output = []

    assert _poll(setup, gitlab, Recorder(), review_all=True, output=output) == EXIT_OK
    assert [p for p, _, _ in gitlab.reviewer_calls] == ["b2c/other"]
    assert not any("[failed]" in line for line in output)
    assert any("Выключены в конфиге" in line and "b2c/front" in line for line in output)


def test_all_projects_disabled_means_nothing_to_review(setup):
    _edit_config(setup, lambda cfg: [p.update(enabled=False) for p in cfg["gitlab"]["projects"]])
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    output = []

    assert _poll(setup, gitlab, Recorder(), review_all=True, output=output) == EXIT_OK
    assert gitlab.reviewer_calls == []
    assert any("Нет MR" in line for line in output)


def test_project_settings_reach_discovery_engine_and_comment(setup):
    def change(cfg):
        cfg["skills"] = ["global.md"]
        cfg["gitlab"]["projects"][1].update(
            reviewers=["other-bot"],
            review_drafts=True,
            provider={"name": "anthropic", "model": "claude", "reasoning_effort": "high"},
            skills=[],
        )

    _edit_config(setup, change)
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="1" * 40), ("b2c/other", 2): _mr(head="2" * 40)})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK

    assert gitlab.reviewer_calls == [
        ("b2c/front", ["ai-reviewer"], False),
        ("b2c/other", ["other-bot"], True),
    ]
    by_head = {k["head_sha"]: k["config"] for k in recorder.reviews}
    assert by_head["1" * 40].provider.name == "openai" and by_head["1" * 40].skills == ["global.md"]
    assert by_head["2" * 40].provider.name == "anthropic" and by_head["2" * 40].skills == []
    bodies = {i: b for _, i, b in gitlab.published}
    assert "openai/gpt#medium" in bodies[1]
    assert "anthropic/claude#high" in bodies[2]


# -- pass log, leftovers, retention, debug -----------------------------------


def test_pass_writes_log_with_candidates_summary_and_duration(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr("Исправить корзину")})

    assert _poll(setup, gitlab, Recorder(), answers=["1"]) == EXIT_OK

    logs = _pass_logs(setup)
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "Исправить корзину" in text
    assert "Ответ на выбор MR: '1'" in text
    assert "Итог прохода" in text and "[published] b2c/front !1" in text
    assert "Проход завершён за" in text
    assert "cwd=" in text and "work_dir=" in text


def test_lock_refusal_is_logged(setup):
    setup["work"].mkdir(parents=True)
    (setup["work"] / "poll.lock").write_text(str(os.getpid()), encoding="utf-8")

    assert _poll(setup, FakeGitLab({}), Recorder(), review_all=True) == EXIT_NOT_STARTED
    assert "уже выполняется" in _pass_logs(setup)[0].read_text(encoding="utf-8")


def test_gitlab_unreachable_is_logged(setup):
    gitlab = FakeGitLab({})
    gitlab.preflight_error = "connection refused"

    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_NOT_STARTED
    assert "connection refused" in _pass_logs(setup)[0].read_text(encoding="utf-8")


def test_invalid_config_logged_in_its_readable_work_dir(setup):
    _edit_config(setup, lambda cfg: cfg["provider"].pop("reasoning_effort"))

    assert _poll(setup, FakeGitLab({}), Recorder(), review_all=True) == EXIT_NOT_STARTED
    assert "reasoning_effort" in _pass_logs(setup)[0].read_text(encoding="utf-8")


def test_unparsable_config_logged_in_default_work_dir(setup, tmp_path, monkeypatch):
    setup["config"].write_text("provider: [unclosed\n", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert _poll(setup, FakeGitLab({}), Recorder(), review_all=True) == EXIT_NOT_STARTED
    logs = list((cwd / ".review-agent" / "logs").glob("poll-*.log"))
    assert len(logs) == 1 and "Ошибка конфигурации" in logs[0].read_text(encoding="utf-8")


def test_failed_review_keeps_harness_stderr_in_logs(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40)})
    output = []

    assert _poll(setup, gitlab, Recorder(fail_for={"a" * 40}), review_all=True, output=output) == EXIT_FAILURES
    kept = list((setup["work"] / "logs").glob("*-b2c-front-1.harness-stderr.log"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == "трасса упавшего харнесса"
    assert any(str(kept[0]) in line for line in output)
    assert _tmp_is_empty(setup)


def test_successful_review_keeps_no_harness_log(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_OK
    assert not list((setup["work"] / "logs").glob("*.harness-stderr.log"))
    assert not (setup["work"] / "debug").exists()
    assert not (setup["work"] / "dry-run").exists()


def test_leftovers_and_orphan_worktrees_cleaned_under_lock(setup):
    leftover = setup["work"] / "tmp" / "1700000000-deadbeef"
    leftover.mkdir(parents=True)
    (leftover / "prompt.md").write_text("old", encoding="utf-8")
    cleaned = []

    code = _poll(
        setup,
        FakeGitLab({}),
        Recorder(),
        review_all=True,
        orphan_cleanup_fn=lambda repo, tmp: cleaned.append((repo, tmp)),
    )

    assert code == EXIT_OK
    assert not leftover.exists()
    assert [Path(r).name for r, _ in cleaned] == ["clone-a", "clone-b"]
    assert all(t == setup["work"] / "tmp" for _, t in cleaned)


def test_expired_artifacts_removed_even_when_gitlab_is_down(setup):
    old_log = setup["work"] / "logs" / "poll-20260101-000000-1.log"
    old_log.parent.mkdir(parents=True)
    old_log.write_text("old", encoding="utf-8")
    long_ago = NOW.timestamp() - 30 * 86400
    os.utime(old_log, (long_ago, long_ago))
    gitlab = FakeGitLab({})
    gitlab.preflight_error = "down"

    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_NOT_STARTED
    assert not old_log.exists()
    assert "Удалено устаревших артефактов (старше 7 дн.): 1" in _pass_logs(setup)[0].read_text(encoding="utf-8")


def test_nothing_deleted_when_lock_is_busy(setup):
    setup["work"].mkdir(parents=True)
    (setup["work"] / "poll.lock").write_text(str(os.getpid()), encoding="utf-8")
    leftover = setup["work"] / "tmp" / "run"
    leftover.mkdir(parents=True)

    assert _poll(setup, FakeGitLab({}), Recorder(), review_all=True) == EXIT_NOT_STARTED
    assert leftover.exists()


def test_cleanup_crash_does_not_change_exit_code(setup, monkeypatch):
    from review_agent import polling

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(polling, "cleanup_expired", boom)
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})

    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_OK
    assert len(gitlab.published) == 1
    assert "disk on fire" in _pass_logs(setup)[0].read_text(encoding="utf-8")


def test_debug_keeps_run_artifacts_and_bodies(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True, debug=True) == EXIT_OK

    debug_dir = recorder.reviews[0]["debug_dir"]
    assert debug_dir.parent == setup["work"] / "debug"
    assert (debug_dir / "prompt.md").exists()  # engine's files
    assert (debug_dir / "claim.json").exists() and (debug_dir / "report.json").exists()
    assert _tmp_is_empty(setup)

    # A later pass without --debug keeps them (they only expire by age).
    assert _poll(setup, gitlab, Recorder(), review_all=True) == EXIT_OK
    assert (debug_dir / "prompt.md").exists()


def test_no_console_streams(setup, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    gitlab = FakeGitLab({("b2c/front", 1): _mr("Кириллица")})

    code = run_poll(
        config_path=setup["config"],
        review_all=True,
        client_factory=gitlab,
        review_fn=Recorder().review,
        repo_sources_factory=Recorder().sources,
        orphan_cleanup_fn=lambda *a: None,
        is_git_repo=lambda path: path.is_dir(),
        now_fn=lambda: NOW,
    )

    assert code == EXIT_OK
    assert len(gitlab.published) == 1
    assert "[published] b2c/front !1" in _pass_logs(setup)[0].read_text(encoding="utf-8")


def test_orphan_worktree_of_killed_run_removed_on_real_clone(git_repo_with_base_and_head, tmp_path):
    import subprocess

    fixture = git_repo_with_base_and_head
    repo = fixture["repo"]
    work = tmp_path / "work"
    orphan = work / "tmp" / "1700000000-deadbeef" / "worktree"
    orphan.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(orphan), fixture["base_sha"]],
        check=True,
        capture_output=True,
    )
    (orphan.parent / "prompt.md").write_text("left behind", encoding="utf-8")
    branch_before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
    ).stdout
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "provider": {"name": "p", "model": "m", "reasoning_effort": None},
                "harness": {"command": ["stub"]},
                "report": {"output_path": "r-{run_id}.md"},
                "storage": {"work_dir": str(work)},
                "gitlab": {
                    "hostname": "h",
                    "reviewers": ["bot"],
                    "projects": [{"path": "a/b", "local_repo": str(repo)}],
                },
            }
        ),
        encoding="utf-8",
    )

    code = run_poll(
        config_path=config,
        review_all=True,
        client_factory=FakeGitLab({}),
        review_fn=Recorder().review,
        repo_sources_factory=Recorder().sources,
        output_fn=lambda line: None,
    )

    assert code == EXIT_OK
    assert not any((work / "tmp").iterdir())
    worktrees = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert str(orphan) not in worktrees
    assert worktrees.count("worktree ") == 1  # only the clone itself
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""
    assert subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
    ).stdout == branch_before


# -- managed repository copies ------------------------------------------------


def _without_local_repo(setup, index=0):
    def change(cfg):
        cfg["gitlab"]["projects"][index].pop("local_repo")

    _edit_config(setup, change)


def test_project_without_local_repo_reviewed_against_managed_copy(setup):
    _without_local_repo(setup)
    gitlab = FakeGitLab({("b2c/front", 1): _mr(target="develop")})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert recorder.prepares == [("b2c/front", "develop", 1)]
    assert recorder.reviews[0]["repo_path"] == setup["work"] / "repos" / "copy.git"
    assert len(gitlab.published) == 1


def test_project_without_candidates_is_not_cloned(setup):
    _without_local_repo(setup)
    recorder = Recorder()
    assert _poll(setup, FakeGitLab({("b2c/other", 2): _mr()}), recorder, review_all=True) == EXIT_OK
    assert [p for p, _, _ in recorder.prepares] == ["b2c/other"]


def test_unselected_project_is_not_cloned_interactively(setup):
    _without_local_repo(setup)
    gitlab = FakeGitLab({("b2c/front", 1): _mr("one"), ("b2c/other", 2): _mr("two")})
    recorder = Recorder()
    assert _poll(setup, gitlab, recorder, answers=["2"]) == EXIT_OK
    assert [p for p, _, _ in recorder.prepares] == ["b2c/other"]


def test_failure_to_obtain_copy_fails_mr_and_removes_claim(setup):
    from review_agent.repo_source import RepoSourceError

    _without_local_repo(setup)
    gitlab = FakeGitLab({("b2c/front", 1): _mr(), ("b2c/other", 2): _mr()})
    recorder = Recorder()
    recorder.prepare_error = RepoSourceError("не удалось склонировать: could not read Username")
    output = []

    assert _poll(setup, gitlab, recorder, review_all=True, output=output) == EXIT_FAILURES
    assert gitlab.mrs[("b2c/front", 1)]["notes"] == []  # claim removed
    assert recorder.reviews == []
    assert any("[failed] b2c/front !1" in line and "could not read Username" in line for line in output)
    assert _tmp_is_empty(setup)


def test_fetch_warning_does_not_fail_review(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    recorder = Recorder()
    recorder.prepare_warnings = ["fetch refs/merge-requests/1/head из 'origin' не удался: gone"]

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert len(gitlab.published) == 1
    assert "refs/merge-requests/1/head" in _pass_logs(setup)[0].read_text(encoding="utf-8")


def _managed_config(tmp_path, work, **storage):
    config = tmp_path / "managed-config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "provider": {"name": "p", "model": "m", "reasoning_effort": None},
                "harness": {"command": ["stub"]},
                "report": {"output_path": "r-{run_id}.md"},
                "storage": {"work_dir": str(work), **storage},
                "gitlab": {"hostname": "h", "reviewers": ["ai-reviewer"], "projects": [{"path": "group/project"}]},
            }
        ),
        encoding="utf-8",
    )
    return config


def _real_sources(fake_gitlab):
    from review_agent.repo_source import RepoSources

    def factory(config, work_dir, log):
        return RepoSources(
            work_dir.repos, "h", url_fn=lambda path: str(fake_gitlab.bare), credential_helper="!true", notify=log.info
        )

    return factory


def test_end_to_end_review_against_managed_copy(fake_gitlab, tmp_path):
    from conftest import has_commit

    from review_agent.repo_source import LAST_USED_MARKER, cache_name
    from review_agent.worktree import managed_worktree

    work = tmp_path / "work"
    config = _managed_config(tmp_path, work)
    seen = []

    def review(**kwargs):
        # What the engine does first: a worktree at head, base available.
        with managed_worktree(kwargs["repo_path"], kwargs["base_sha"], kwargs["head_sha"], work / "tmp") as wt:
            seen.append((wt.path / "f.txt").read_text(encoding="utf-8"))
        return ReviewResult(report=GOOD_REPORT, harness_stderr="", run_id="r")

    def run(iid, head, base):
        gitlab = FakeGitLab({("group/project", iid): _mr(head=head, base=base)})
        output = []
        code = run_poll(
            config_path=config, review_all=True, client_factory=gitlab, review_fn=review,
            repo_sources_factory=_real_sources(fake_gitlab), output_fn=output.append, now_fn=lambda: NOW,
        )  # fmt: skip
        return code, gitlab, output

    mr1 = fake_gitlab.push_mr(1, "mr one\n")
    code, gitlab, output = run(1, mr1, fake_gitlab.base_sha)
    assert code == EXIT_OK and len(gitlab.published) == 1
    assert any("Клонирование" in line for line in output)

    # Main moves after the clone; the next MR is based on the new main.
    new_base = fake_gitlab.commit_main("moved main\n")
    mr2 = fake_gitlab.push_mr(2, "mr two\n")
    code, gitlab, output = run(2, mr2, new_base)
    assert code == EXIT_OK and len(gitlab.published) == 1
    assert not any("Клонирование" in line for line in output)  # reused, not cloned again

    copy = work / "repos" / cache_name("group/project")
    assert seen == ["mr one\n", "mr two\n"]
    assert has_commit(copy, new_base) and (copy / LAST_USED_MARKER).is_file()
    assert not any((work / "tmp").iterdir())
    assert [p.name for p in (work / "repos").iterdir()] == [copy.name]


def _copy_with_orphan(fake_gitlab, work, age_days=0):
    import subprocess

    from review_agent.repo_source import LAST_USED_MARKER, RepoSources
    from review_agent.config import GitLabProjectConfig

    sources = RepoSources(work / "repos", "h", url_fn=lambda p: str(fake_gitlab.bare), credential_helper="!true")
    copy = sources.prepare(GitLabProjectConfig(path="group/project"), "main", 1).path
    orphan = work / "tmp" / "1700000000-deadbeef" / "worktree"
    orphan.parent.mkdir(parents=True)
    subprocess.run(["git", "-C", str(copy), "worktree", "add", "--detach", str(orphan), "main"], check=True, capture_output=True)
    stamp = NOW.timestamp() - age_days * 86400 if age_days else None
    if stamp is not None:
        os.utime(copy / LAST_USED_MARKER, (stamp, stamp))
    return copy, orphan


def _worktrees(repo):
    import subprocess

    return subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True).stdout


def test_orphan_worktree_in_managed_copy_removed_copy_kept(fake_gitlab, tmp_path):
    work = tmp_path / "work"
    copy, orphan = _copy_with_orphan(fake_gitlab, work)
    code = run_poll(
        config_path=_managed_config(tmp_path, work, retention_days=1), review_all=True,
        client_factory=FakeGitLab({}), review_fn=Recorder().review, output_fn=lambda line: None,
    )  # fmt: skip
    assert code == EXIT_OK
    assert copy.exists() and not orphan.exists()
    assert str(orphan) not in _worktrees(copy)


def test_unused_copy_expires_and_is_logged(fake_gitlab, tmp_path):
    work = tmp_path / "work"
    copy, _ = _copy_with_orphan(fake_gitlab, work, age_days=45)
    (work / "repos" / ".incoming-1-x").mkdir()
    output = []
    code = run_poll(
        config_path=_managed_config(tmp_path, work), review_all=True, client_factory=FakeGitLab({}),
        review_fn=Recorder().review, output_fn=output.append,
    )  # fmt: skip
    assert code == EXIT_OK
    assert not copy.exists() and not (work / "repos" / ".incoming-1-x").exists()
    text = "\n".join(output)
    assert "дольше 30 дн.: 1" in text and "недокачанные или повреждённые кэш-клоны: 1" in text


def test_copy_older_than_artifact_retention_but_recent_use_is_kept(fake_gitlab, tmp_path):
    work = tmp_path / "work"
    copy, _ = _copy_with_orphan(fake_gitlab, work, age_days=10)
    code = run_poll(
        config_path=_managed_config(tmp_path, work, retention_days=7), review_all=True,
        client_factory=FakeGitLab({}), review_fn=Recorder().review, output_fn=lambda line: None,
    )  # fmt: skip
    assert code == EXIT_OK and copy.exists()


def test_copies_untouched_when_lock_is_busy(fake_gitlab, tmp_path):
    work = tmp_path / "work"
    copy, orphan = _copy_with_orphan(fake_gitlab, work, age_days=45)
    (work / "poll.lock").write_text(str(os.getpid()), encoding="utf-8")
    code = run_poll(
        config_path=_managed_config(tmp_path, work), review_all=True, client_factory=FakeGitLab({}),
        review_fn=Recorder().review, output_fn=lambda line: None,
    )  # fmt: skip
    assert code == EXIT_NOT_STARTED
    assert copy.exists() and orphan.exists()


# -- usage accounting (review-usage-accounting) --------------------------------


def test_all_mode_passes_recorder_and_labels_without_quota_questions(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr("one", head="a" * 40), ("b2c/other", 2): _mr("two", head="c" * 40)})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK

    assert len(recorder.reviews) == 2
    first, second = recorder.reviews
    assert first["usage"] is second["usage"] is not None
    assert first["labels"].source == "poll"
    assert (first["labels"].project, first["labels"].mr_iid) == ("b2c/front", 1)
    assert (second["labels"].project, second["labels"].mr_iid) == ("b2c/other", 2)
    assert first["quota_prompt"] is None and second["quota_prompt"] is None
    assert first["usage"].ledger_path == setup["work"] / "usage" / "ledger.jsonl"


def test_interactive_single_mr_gets_quota_questions(setup):
    gitlab = FakeGitLab(_five_mrs())
    recorder = Recorder()
    output = []
    readings = {}
    recorder.during_review = lambda kwargs: readings.update(
        before=kwargs["quota_prompt"]("before"), after=kwargs["quota_prompt"]("after")
    )

    # pick MR 2, then answer REMAINING % for 5h/week before and after;
    # "abc" and "101" are asked again; the ledger gets percent used
    answers = ["2", "88", "abc", "60", "", "101", "58"]
    assert _poll(setup, gitlab, recorder, answers=answers, output=output) == EXIT_OK

    assert readings == {"before": {"5h": 12, "week": 40}, "after": {"week": 42}}
    assert any("ОСТАЛОСЬ" in line for line in output)
    assert sum("от 0 до 100" in line for line in output) == 2
    assert any("Замер квоты openai" in line for line in output)


def test_disabled_accounting_passes_nothing(setup):
    _edit_config(setup, lambda cfg: cfg.update(usage={"enabled": False}))
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    recorder = Recorder()

    assert _poll(setup, gitlab, recorder, review_all=True) == EXIT_OK
    assert "usage" not in recorder.reviews[0]
    assert not (setup["work"] / "usage").exists()


def test_drift_warning_reaches_console_once_per_pass(setup):
    gitlab = FakeGitLab({("b2c/front", 1): _mr(head="a" * 40), ("b2c/front", 2): _mr(head="b" * 40)})
    recorder = Recorder()
    recorder.during_review = lambda kwargs: kwargs["usage"].warn_once("версия OpenCode 9.9 не проверена")
    output = []

    assert _poll(setup, gitlab, recorder, review_all=True, output=output) == EXIT_OK

    assert sum("9.9" in line for line in output) == 1
    assert any(line.startswith("Предупреждение:") and "9.9" in line for line in output)
    assert "9.9" in _pass_logs(setup)[-1].read_text(encoding="utf-8")


def test_project_provider_is_used_for_quota_label(setup):
    def change(cfg):
        cfg["gitlab"]["projects"][0]["provider"] = {"name": "anthropic", "model": "x", "reasoning_effort": "high"}

    _edit_config(setup, change)
    gitlab = FakeGitLab({("b2c/front", 1): _mr()})
    recorder = Recorder()
    output = []
    recorder.during_review = lambda kwargs: kwargs["quota_prompt"]("before")

    assert _poll(setup, gitlab, recorder, answers=["1"], output=output) == EXIT_OK
    assert recorder.reviews[0]["config"].provider.name == "anthropic"
    assert any("Замер квоты anthropic" in line for line in output)
