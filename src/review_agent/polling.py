"""`review-agent poll`: one pass of discover -> select -> review -> publish.

One invocation = one pass, then exit. No loop, no daemon, no local
review state: whether an MR was reviewed is decided only by the marker
in its GitLab comments (publishing.py). Whoever calls this on a schedule
(Task Scheduler now, cron/CI later) is not this module's concern.

Modes (design.md decision 6a):
- interactive (default, for the testing period): list the MRs found,
  each with a link, and review only the one the user picks;
- automatic (`--all`): review every MR found, one after another.

Everything runs strictly sequentially, and a lock file guarantees at
most one pass at a time per scratch directory (decision 10), so two
overlapping passes never review the same MR.
"""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from review_agent.config import Config, ConfigError, GitLabProjectConfig, load_config
from review_agent.gitlab import GitLabClient, GitLabError, MRCandidate, MRMetadata
from review_agent.harness import build_model_string
from review_agent.pipeline import run_review
from review_agent.publishing import format_comment, is_already_reviewed, is_report_usable

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_NOT_STARTED = 2

LOCK_FILE_NAME = "poll.lock"


# -- one pass at a time ------------------------------------------------------


class PollLockBusy(RuntimeError):
    """Another pass holds the lock for this scratch directory."""


def pid_alive(pid: int) -> bool:
    """Cross-platform "is this process still running" without new dependencies."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


@contextmanager
def poll_lock(
    scratch_dir: Path, *, is_alive: Callable[[int], bool] = pid_alive
) -> Iterator[Path]:
    """Hold `<scratch_dir>/poll.lock` for the duration of a pass.

    Created with exclusive-create ("x"), which is atomic on both Windows
    and Linux. A lock left behind by a killed pass (its PID no longer
    running) is taken over; a live one makes this raise PollLockBusy.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    lock_path = scratch_dir / LOCK_FILE_NAME
    for attempt in range(2):
        try:
            with open(lock_path, "x", encoding="utf-8") as lock_file:
                lock_file.write(str(os.getpid()))
            break
        except FileExistsError:
            try:
                owner = int(lock_path.read_text(encoding="utf-8").strip() or "0")
            except (OSError, ValueError):
                owner = 0
            if attempt == 0 and not is_alive(owner):
                lock_path.unlink(missing_ok=True)  # stale: previous pass was killed
                continue
            raise PollLockBusy(
                f"Другой проход review-agent poll уже выполняется (PID {owner}). "
                f"Если это не так, удалите lock-файл вручную: {lock_path}"
            ) from None
    try:
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)


# -- outcomes ----------------------------------------------------------------


@dataclasses.dataclass
class Outcome:
    project: str
    iid: int | None  # None for a project-level failure
    status: str  # "published" | "dry-run" | "skipped" | "failed"
    reason: str = ""
    web_url: str = ""
    report_path: Path | None = None
    stderr_log_path: Path | None = None

    def describe(self) -> str:
        target = self.project if self.iid is None else f"{self.project} !{self.iid}"
        parts = [f"[{self.status}] {target}"]
        if self.reason:
            parts.append(f"— {self.reason}")
        line = " ".join(parts)
        details = [d for d in (self.web_url, self.report_path, self.stderr_log_path) if d]
        return line + "".join(f"\n    {d}" for d in details)


@dataclasses.dataclass
class _Pending:
    project: GitLabProjectConfig
    candidate: MRCandidate
    metadata: MRMetadata


# -- the pass ----------------------------------------------------------------


def _is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode == 0


def _slug(project_path: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", project_path).strip("-")


def _select(
    pending: list[_Pending],
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> list[_Pending]:
    output_fn(f"\nНайдено MR, где ревьюер назначен: {len(pending)}\n")
    for number, item in enumerate(pending, start=1):
        meta = item.metadata
        output_fn(f"  {number}) {item.project.path} !{meta.iid}  {meta.title}  (автор: {meta.author})")
        output_fn(f"     {meta.web_url}")
    output_fn("")
    while True:
        try:
            answer = input_fn("Выберите номер MR для ревью (q — выход): ").strip()
        except EOFError:
            answer = "q"
        if answer.lower() in ("q", "й", "quit", "exit"):
            output_fn("Выбор отменён — ничего не ревьюим.")
            return []
        if answer.isdigit() and 1 <= int(answer) <= len(pending):
            return [pending[int(answer) - 1]]
        output_fn(f"Неверный ввод: {answer!r}. Введите число от 1 до {len(pending)} или q.")


def _review_one(
    item: _Pending,
    *,
    client: GitLabClient,
    bot_username: str,
    config: Config,
    config_path: Path,
    scratch_dir: Path,
    dry_run: bool,
    review_fn: Callable[..., Path],
    fetch_fn: Callable[[Path, str, str], None],
) -> Outcome:
    project = item.project
    meta = item.metadata
    outcome = Outcome(project=project.path, iid=meta.iid, status="failed", web_url=meta.web_url)
    try:
        refs = client.get_diff_refs(project.path, meta.iid)
        local_repo = Path(project.local_repo)
        fetch_fn(local_repo, project.remote, f"refs/merge-requests/{meta.iid}/head")

        reports_dir = Path(config.report.output_path).parent
        stem = f"{_slug(project.path)}-{meta.iid}-{refs.head_sha[:12]}"
        report_path = reports_dir / f"{stem}.md"
        outcome.report_path = report_path
        outcome.stderr_log_path = reports_dir / f"{stem}.harness-stderr.log"

        review_fn(
            repo_path=local_repo,
            base_sha=refs.base_sha,
            head_sha=refs.head_sha,
            mr_title=meta.title,
            mr_description=meta.description,
            config_path=config_path,
            scratch_dir=scratch_dir,
            report_path=report_path,
            stderr_log_path=outcome.stderr_log_path,
        )
        report = report_path.read_text(encoding="utf-8")
        if not is_report_usable(report, config.gitlab.min_report_chars):
            outcome.reason = (
                f"отчёт пустой или короче {config.gitlab.min_report_chars} символов — "
                "не опубликован, проверьте harness-stderr.log"
            )
            return outcome

        body = format_comment(
            report=report,
            head_sha=refs.head_sha,
            base_sha=refs.base_sha,
            model=build_model_string(config.provider),
        )

        # Second line of defence against double publication (the lock is
        # the first): someone may have published while we were reviewing.
        if is_already_reviewed(client.list_notes(project.path, meta.iid), bot_username):
            outcome.status = "skipped"
            outcome.reason = "маркер ревью появился во время прогона — отчёт не опубликован"
            return outcome

        if dry_run:
            comment_path = reports_dir / f"{stem}.comment.md"
            comment_path.write_text(body, encoding="utf-8")
            outcome.status = "dry-run"
            outcome.reason = f"комментарий НЕ опубликован, сохранён в {comment_path}"
            return outcome

        client.post_note(
            project.path, meta.iid, body, body_file=scratch_dir / "notes" / f"{stem}.json"
        )
        outcome.status = "published"
        outcome.reason = f"отревьюен коммит {refs.head_sha[:12]}"
        return outcome
    except Exception as exc:  # noqa: BLE001 - per-MR isolation is the point
        outcome.status = "failed"
        outcome.reason = f"{type(exc).__name__}: {exc}"
        return outcome


def run_poll(
    *,
    config_path: Path,
    scratch_dir: Path,
    review_all: bool = False,
    dry_run: bool = False,
    client_factory: Callable[[str], GitLabClient] = GitLabClient,
    review_fn: Callable[..., Path] = run_review,
    fetch_fn: Callable[[Path, str, str], None] | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    stdin_isatty: Callable[[], bool] = lambda: sys.stdin.isatty(),
    is_alive: Callable[[int], bool] = pid_alive,
    is_git_repo: Callable[[Path], bool] = _is_git_repo,
) -> int:
    """Run one polling pass and return the process exit code."""
    if fetch_fn is None:
        from review_agent.worktree import fetch_ref

        fetch_fn = fetch_ref

    def error(message: str) -> None:
        print(message, file=sys.stderr)

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        error(f"Ошибка конфигурации: {exc}")
        return EXIT_NOT_STARTED
    if config.gitlab is None:
        error(f"В конфиге '{config_path}' нет секции 'gitlab' — она обязательна для poll.")
        return EXIT_NOT_STARTED

    if not review_all and not stdin_isatty():
        error(
            "Интерактивный режим требует терминала, а stdin не интерактивный. "
            "Для запуска без вопросов (планировщик, CI) используйте --all."
        )
        return EXIT_NOT_STARTED

    try:
        with poll_lock(scratch_dir, is_alive=is_alive):
            return _run_locked(
                config=config,
                config_path=config_path,
                scratch_dir=scratch_dir,
                review_all=review_all,
                dry_run=dry_run,
                client_factory=client_factory,
                review_fn=review_fn,
                fetch_fn=fetch_fn,
                input_fn=input_fn,
                output_fn=output_fn,
                is_git_repo=is_git_repo,
            )
    except PollLockBusy as exc:
        error(str(exc))
        return EXIT_NOT_STARTED


def _run_locked(
    *,
    config: Config,
    config_path: Path,
    scratch_dir: Path,
    review_all: bool,
    dry_run: bool,
    client_factory: Callable[[str], GitLabClient],
    review_fn: Callable[..., Path],
    fetch_fn: Callable[[Path, str, str], None],
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    is_git_repo: Callable[[Path], bool],
) -> int:
    gitlab_config = config.gitlab
    client = client_factory(gitlab_config.hostname)
    try:
        bot_username = client.preflight()
    except GitLabError as exc:
        print(f"GitLab недоступен: {exc}", file=sys.stderr)
        return EXIT_NOT_STARTED

    outcomes: list[Outcome] = []
    pending: list[_Pending] = []

    for project in gitlab_config.projects:
        if not is_git_repo(Path(project.local_repo)):
            outcomes.append(
                Outcome(
                    project=project.path,
                    iid=None,
                    status="failed",
                    reason=f"локальный клон '{project.local_repo}' не найден или не git-репозиторий",
                )
            )
            continue
        try:
            candidates = client.list_review_candidates(
                project.path, gitlab_config.reviewers, review_drafts=gitlab_config.review_drafts
            )
        except GitLabError as exc:
            outcomes.append(Outcome(project=project.path, iid=None, status="failed", reason=str(exc)))
            continue
        for candidate in candidates:
            try:
                if is_already_reviewed(client.list_notes(project.path, candidate.iid), bot_username):
                    outcomes.append(
                        Outcome(project.path, candidate.iid, "skipped", "уже отревьюен (есть маркер)")
                    )
                    continue
                metadata = client.get_mr_metadata(project.path, candidate.iid)
            except GitLabError as exc:
                outcomes.append(Outcome(project.path, candidate.iid, "failed", str(exc)))
                continue
            pending.append(_Pending(project=project, candidate=candidate, metadata=metadata))

    if not pending:
        output_fn("Нет MR, ожидающих ревью.")
        selected: list[_Pending] = []
    elif review_all:
        selected = pending
    else:
        selected = _select(pending, input_fn=input_fn, output_fn=output_fn)

    for item in selected:  # strictly one after another - no parallelism
        output_fn(f"Ревью {item.project.path} !{item.metadata.iid}: {item.metadata.web_url} ...")
        outcomes.append(
            _review_one(
                item,
                client=client,
                bot_username=bot_username,
                config=config,
                config_path=config_path,
                scratch_dir=scratch_dir,
                dry_run=dry_run,
                review_fn=review_fn,
                fetch_fn=fetch_fn,
            )
        )

    if outcomes:
        output_fn("\nИтог прохода:")
        for outcome in outcomes:
            output_fn(outcome.describe())

    return EXIT_FAILURES if any(o.status == "failed" for o in outcomes) else EXIT_OK
