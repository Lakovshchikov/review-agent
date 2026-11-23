"""`review-agent poll`: one pass of discover -> select -> claim -> review -> publish.

One invocation = one pass, then exit. No loop, no daemon, no local
review state: whether an MR was reviewed - or is being reviewed right
now by another pass - is decided only by markers in its GitLab comments
(publishing.py). Whoever calls this on a schedule (Task Scheduler now,
cron/CI later) is not this module's concern.

Modes:
- interactive (default, for the testing period): list the MRs found,
  each with a link, and review only the one the user picks;
- automatic (`--all`): review every MR found, one after another;
- further round (`--again`, interactive only): the MRs that already carry
  a report of ours are the candidates, the user picks one, the agent gets
  its previous report and the replies after it, and the result is a NEW
  comment (the earlier reports stay).

Protection against reviewing one MR twice, in layers:
- `<work_dir>/poll.lock`: one run at a time per working directory;
- right before each review, a fresh look at the MR's notes (the
  candidate list may be minutes old by then), then a claim comment in
  GitLab that blocks other passes on any machine (design.md decision 7);
- one more look for a finished review right before publishing.

Every pass writes its own log (passlog.py) and leaves nothing in
`<work_dir>/tmp/` behind (housekeeping.py). A project without `local_repo`
is reviewed against a managed copy in `<work_dir>/repos/` (repo_source.py),
cloned on first need and kept across passes until unused for
`storage.repo_retention_days`.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from review_agent import __version__
from review_agent.config import (
    Config,
    ConfigError,
    GitLabProjectConfig,
    ProjectSettings,
    best_effort_work_dir,
    effective_settings,
    load_config,
)
from review_agent.file_links import link_file_references, project_web_url
from review_agent.gitlab import GitLabClient, GitLabError, MRCandidate, MRMetadata, NoteNotFound
from review_agent.harness import HarnessError, build_model_string
from review_agent.housekeeping import (
    WorkDir,
    cleanup_expired,
    clear_tmp,
    copy_artifacts,
    remove_path,
    stamp,
)
from review_agent.lock import LOCK_FILE_NAME, PollLockBusy, pid_alive, poll_lock  # noqa: F401
from review_agent.passlog import PassLog, pass_log
from review_agent.prompt_check import check_settings, may_proceed, project_targets
from review_agent.proc import no_window_flags
from review_agent.pipeline import RereviewInput, ReviewResult, run_review
from review_agent.usage_ledger import QuotaPrompt, RunLabels, UsageRecorder, make_recorder
from review_agent.worktree import list_tree_paths
from review_agent.quota_prompt import make_quota_prompt
from review_agent.repo_source import RepoSources, expire_repos, managed_copies, slug
from review_agent.publishing import (
    Report,
    Reply,
    format_claim_comment,
    format_comment,
    is_already_reviewed,
    is_report_usable,
    latest_report,
    live_claims,
    newer_report_exists,
    replies_after,
    round_number,
    stale_claims,
)

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_NOT_STARTED = 2


# -- interactive terminal detection ------------------------------------------


def stdin_is_interactive(stream: Any = None) -> bool:
    """True only if `stream` (default: sys.stdin) is a real interactive console.

    `isatty()` alone is not enough on Windows - verified live: with stdin
    redirected from NUL (`review-agent poll < NUL`), isatty() returns True
    because NUL is a character device, so the interactive prompt ran, hit
    EOF and exited 0 instead of refusing with a hint about --all. On
    Windows the fd must therefore also be a console handle
    (GetConsoleMode succeeds only for those). Under Task Scheduler stdin
    may be missing entirely (sys.stdin is None) - also "not interactive".
    """
    stream = sys.stdin if stream is None else stream
    if stream is None:
        return False
    try:
        fd = stream.fileno()
        if not os.isatty(fd):
            return False
    except (AttributeError, OSError, ValueError):
        return False
    if os.name != "nt":
        return True

    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(fd)
    except OSError:
        return False
    mode = ctypes.c_ulong()
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    return bool(kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))


# -- outcomes ----------------------------------------------------------------


@dataclasses.dataclass
class Outcome:
    project: str
    iid: int | None  # None for a project-level failure
    status: str  # "published" | "dry-run" | "skipped" | "failed"
    reason: str = ""
    web_url: str = ""
    # Kept files worth pointing at: a failed review's harness stderr, a
    # dry-run comment, debug artifacts.
    kept_paths: list[Path] = dataclasses.field(default_factory=list)

    def describe(self) -> str:
        target = self.project if self.iid is None else f"{self.project} !{self.iid}"
        parts = [f"[{self.status}] {target}"]
        if self.reason:
            parts.append(f"— {self.reason}")
        line = " ".join(parts)
        details = [str(d) for d in (self.web_url, *self.kept_paths) if d]
        return line + "".join(f"\n    {d}" for d in details)


MARK_NEW_COMMITS = "новые коммиты"
MARK_REPLIES = "есть ответы"
MARK_UNCHANGED = "без изменений"


@dataclasses.dataclass
class _Pending:
    project: GitLabProjectConfig
    settings: ProjectSettings
    candidate: MRCandidate
    metadata: MRMetadata
    # `--again` only: what changed since our latest report, for the list.
    marks: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _PassContext:
    client: GitLabClient
    bot_username: str
    config: Config
    work_dir: WorkDir
    claim_ttl: timedelta
    dry_run: bool
    debug: bool
    log: PassLog
    review_fn: Callable[..., ReviewResult]
    repo_sources: RepoSources
    now_fn: Callable[[], datetime]
    # Usage accounting (usage_ledger.py); None when disabled.
    usage: UsageRecorder | None = None
    # Interactive single-MR pass only: builds the quota questions for a
    # provider name (quota_prompt.py); None in --all mode.
    quota_factory: Callable[[str], QuotaPrompt] | None = None
    # Files and directories of a commit, for linking the report's file
    # references (worktree.list_tree_paths; stubbed in tests).
    tree_paths_fn: Callable[[Path, str], set[str]] = list_tree_paths
    # A further review round (`--again`): MRs WITH a report of ours are the
    # candidates, and the review gets that report and the replies after it.
    again: bool = False


# -- the pass ----------------------------------------------------------------


def _is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=no_window_flags(),
    )
    return result.returncode == 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _select(
    pending: list[_Pending],
    *,
    input_fn: Callable[[str], str],
    log: PassLog,
    again: bool = False,
) -> list[_Pending]:
    if again:
        log.info(f"\nНайдено уже отревьюенных MR для повторного ревью: {len(pending)}\n")
    else:
        log.info(f"\nНайдено MR, где ревьюер назначен: {len(pending)}\n")
    for number, item in enumerate(pending, start=1):
        meta = item.metadata
        state = "" if meta.state == "opened" else f"[{meta.state}] "
        marks = "".join(f"[{mark}] " for mark in item.marks)
        log.info(
            f"  {number}) {item.project.path} !{meta.iid}  {marks}{state}{meta.title}  (автор: {meta.author})"
        )
        log.info(f"     {meta.web_url}")
    log.info("")
    while True:
        try:
            answer = input_fn("Выберите номер MR для ревью (q — выход): ").strip()
        except EOFError:
            answer = "q"
        log.file_only(f"Ответ на выбор MR: {answer!r}")
        if answer.lower() in ("q", "й", "quit", "exit"):
            log.info("Выбор отменён — ничего не ревьюим.")
            return []
        if answer.isdigit() and 1 <= int(answer) <= len(pending):
            return [pending[int(answer) - 1]]
        log.info(f"Неверный ввод: {answer!r}. Введите число от 1 до {len(pending)} или q.")


def _in_progress_reason(notes: list[dict[str, Any]], ctx: _PassContext) -> str:
    """Why an MR must not be taken now ("" if it may be): reviewed or claimed.

    A further round (`--again`) turns the first test around: it needs a
    report of ours to build on, and only a live claim stops it.
    """
    if ctx.again:
        if latest_report(notes, ctx.bot_username) is None:
            return "ещё не отревьюен: повторное ревью возможно только после первого"
    elif is_already_reviewed(notes, ctx.bot_username):
        return "уже отревьюен (есть маркер)"
    claims = live_claims(notes, ctx.bot_username, now=ctx.now_fn(), ttl=ctx.claim_ttl)
    if claims:
        started = claims[0].started_at
        when = started.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if started else "?"
        return f"ревью уже выполняется другим проходом (claim от {when})"
    return ""


def _again_marks(
    notes: list[dict[str, Any]], report: Report, head_sha: str, ctx: _PassContext
) -> list[str]:
    """Why a reviewed MR may deserve another round, from GitLab data only."""
    marks: list[str] = []
    if head_sha != report.head_sha:
        marks.append(MARK_NEW_COMMITS)
    if replies_after(notes, ctx.bot_username, report):
        marks.append(MARK_REPLIES)
    return marks or [MARK_UNCHANGED]


def _review_one(item: _Pending, ctx: _PassContext) -> Outcome:
    """Claim, review and publish one MR; see design.md decision 7 for the steps."""
    project = item.project
    meta = item.metadata
    client = ctx.client
    outcome = Outcome(project=project.path, iid=meta.iid, status="failed", web_url=meta.web_url)
    moment = stamp()
    name = f"{moment}-{slug(project.path)}-{meta.iid}"
    # Per-MR transient folder for publication request bodies; deleted (or
    # copied to debug/) in `finally` like the engine's own run folder.
    mr_tmp = ctx.work_dir.tmp / f"mr-{name}"
    debug_dir = ctx.work_dir.debug / name if ctx.debug else None
    my_claim: int | None = None
    fetch_warning = ""
    try:
        refs = client.get_diff_refs(project.path, meta.iid)

        # 1. Fresh look: the candidate list is as old as the pass, and
        #    another pass may have reviewed or claimed this MR meanwhile.
        notes = client.list_notes(project.path, meta.iid)
        reason = _in_progress_reason(notes, ctx)
        if reason:
            outcome.status = "skipped"
            outcome.reason = reason
            return outcome

        # A further round builds on our latest report and what was said
        # after it - read now, before our own claim lands among the notes.
        base_report: Report | None = None
        replies: list[Reply] = []
        round_no = 1
        if ctx.again:
            base_report = latest_report(notes, ctx.bot_username)
            assert base_report is not None  # _in_progress_reason guarantees it
            replies = replies_after(notes, ctx.bot_username, base_report)
            round_no = round_number(notes, ctx.bot_username)

        if not ctx.dry_run:
            # 2. Claims of a pass that died are removed, not left forever.
            for stale in stale_claims(
                notes, ctx.bot_username, now=ctx.now_fn(), ttl=ctx.claim_ttl
            ):
                try:
                    client.delete_note(project.path, meta.iid, stale.note_id)
                    ctx.log.info(f"Снят протухший claim #{stale.note_id} на {project.path} !{meta.iid}")
                except GitLabError as exc:
                    ctx.log.warning(
                        f"Не удалось снять протухший claim #{stale.note_id} на "
                        f"{project.path} !{meta.iid}: {exc}"
                    )
            # 3. Our own claim.
            my_claim = client.post_note(
                project.path,
                meta.iid,
                format_claim_comment(head_sha=refs.head_sha, started_at=ctx.now_fn()),
                body_file=mr_tmp / "claim.json",
            )
            # 4. Two passes may have claimed at once: the earliest claim
            #    (lowest note id - GitLab's ordering, not machine clocks) wins.
            notes = client.list_notes(project.path, meta.iid)
            earlier = [
                c
                for c in live_claims(notes, ctx.bot_username, now=ctx.now_fn(), ttl=ctx.claim_ttl)
                if c.note_id < my_claim
            ]
            if earlier:
                outcome.status = "skipped"
                outcome.reason = f"другой проход взял MR раньше (claim #{earlier[0].note_id})"
                return outcome  # our claim is deleted in `finally`

        # 4.5. The project's repository (local clone, or our managed copy -
        #      cloned now if this is its first use) with the MR's target
        #      branch and head brought up to date. Only failing to obtain
        #      a managed copy is fatal for the MR; fetch problems are not:
        #      GitLab may clean up MR refs of old closed/merged MRs, and
        #      the engine still looks for both commits and tries `git fetch
        #      <sha>` itself (worktree.ensure_commits_available).
        prepared = ctx.repo_sources.prepare(project, refs.target_branch, meta.iid)
        for warning in prepared.warnings:
            ctx.log.file_only(f"{project.path} !{meta.iid}: {warning}")
        if prepared.warnings:
            fetch_warning = "; ".join(prepared.warnings) + "; "

        # 5. The review itself, with the project's own provider/skills.
        try:
            usage_kwargs: dict[str, Any] = {}
            if ctx.usage is not None:
                provider = item.settings.config.provider.name
                usage_kwargs = dict(
                    usage=ctx.usage,
                    labels=RunLabels(
                        source="poll-again" if ctx.again else "poll",
                        project=project.path,
                        mr_iid=meta.iid,
                    ),
                    quota_prompt=ctx.quota_factory(provider) if ctx.quota_factory else None,
                )
            if base_report is not None:
                usage_kwargs["rereview"] = RereviewInput(
                    previous_report=base_report.body,
                    previous_sha=base_report.head_sha,
                    replies=replies,
                )
            result = ctx.review_fn(
                repo_path=prepared.path,
                base_sha=refs.base_sha,
                head_sha=refs.head_sha,
                mr_title=meta.title,
                mr_description=meta.description,
                config=item.settings.config,
                debug_dir=debug_dir,
                **usage_kwargs,
            )
        except HarnessError as exc:
            _keep_harness_log(exc.stderr, name, outcome, ctx)
            raise

        if not is_report_usable(result.report, ctx.config.gitlab.min_report_chars):
            _keep_harness_log(result.harness_stderr, name, outcome, ctx)
            outcome.reason = (
                f"отчёт пустой или короче {ctx.config.gitlab.min_report_chars} символов — "
                "не опубликован, см. лог харнесса"
            )
            return outcome

        body = format_comment(
            report=_link_files(result, refs, meta, project, prepared.path, ctx),
            head_sha=refs.head_sha,
            base_sha=refs.base_sha,
            model=build_model_string(item.settings.config.provider),
            round=round_no,
            previous_sha=base_report.head_sha if base_report is not None else None,
        )

        # 6. Someone may have published while we were reviewing: any marker
        #    for a first review, a report newer than ours for a further round.
        latest_notes = client.list_notes(project.path, meta.iid)
        if base_report is not None:
            raced = newer_report_exists(latest_notes, ctx.bot_username, base_report.note_id)
            raced_reason = "отчёт другого раунда появился во время прогона — отчёт не опубликован"
        else:
            raced = is_already_reviewed(latest_notes, ctx.bot_username)
            raced_reason = "маркер ревью появился во время прогона — отчёт не опубликован"
        if raced:
            outcome.status = "skipped"
            outcome.reason = raced_reason
            return outcome

        if ctx.dry_run:
            ctx.work_dir.dry_run.mkdir(parents=True, exist_ok=True)
            comment_path = ctx.work_dir.dry_run / f"{name}.comment.md"
            comment_path.write_text(body, encoding="utf-8")
            outcome.kept_paths.append(comment_path)
            outcome.status = "dry-run"
            outcome.reason = "комментарий НЕ опубликован, сохранён локально"
            return outcome

        try:
            client.update_note(
                project.path, meta.iid, my_claim, body, body_file=mr_tmp / "report.json"
            )
        except NoteNotFound:
            # Someone deleted our claim during the review: publish anew.
            client.post_note(project.path, meta.iid, body, body_file=mr_tmp / "report.json")
        my_claim = None  # the claim has become the report
        outcome.status = "published"
        outcome.reason = (
            f"повторное ревью (раунд {round_no}): отревьюен коммит {refs.head_sha[:12]}"
            if ctx.again
            else f"отревьюен коммит {refs.head_sha[:12]}"
        )
        return outcome
    except Exception as exc:  # noqa: BLE001 - per-MR isolation is the point
        outcome.status = "failed"
        outcome.reason = f"{fetch_warning}{type(exc).__name__}: {exc}"
        return outcome
    finally:
        # 7. A claim that did not become a report never stays behind.
        if my_claim is not None:
            try:
                client.delete_note(project.path, meta.iid, my_claim)
            except NoteNotFound:
                pass
            except Exception as exc:  # noqa: BLE001
                outcome.reason += (
                    f"; не удалось удалить claim #{my_claim} ({exc}) — он перестанет "
                    f"блокировать MR через {int(ctx.claim_ttl.total_seconds() // 60)} мин"
                )
        _finish_mr_tmp(mr_tmp, debug_dir, outcome, ctx)


def _link_files(
    result: ReviewResult,
    refs: Any,
    meta: MRMetadata,
    project: GitLabProjectConfig,
    repo_path: Path,
    ctx: _PassContext,
) -> str:
    """The report with file references turned into GitLab links (file_links.py).

    Links are a convenience: whatever fails here, the report is still
    published - with what could be rewritten, or as it was.
    """
    where = f"{project.path} !{meta.iid}"
    paths: dict[str, set[str] | None] = {}
    for name, sha in (("head", refs.head_sha), ("base", refs.base_sha)):
        try:
            paths[name] = ctx.tree_paths_fn(repo_path, sha)
        except Exception as exc:  # noqa: BLE001
            ctx.log.warning(
                f"{where}: не удалось получить файлы коммита {sha[:12]} для ссылок "
                f"в отчёте ({exc}); пути в inline-коде останутся без ссылок"
            )
            paths[name] = None
    # Without head's list nothing can be checked; without base's only
    # files deleted by the MR go unlinked.
    head_paths = paths["head"]
    base_paths = None if head_paths is None else (paths["base"] or set())
    try:
        return link_file_references(
            result.report,
            worktree_path=result.worktree_path,
            project_url=project_web_url(meta.web_url, ctx.config.gitlab.hostname, project.path),
            head_sha=refs.head_sha,
            base_sha=refs.base_sha,
            head_paths=head_paths,
            base_paths=base_paths,
        )
    except Exception as exc:  # noqa: BLE001
        ctx.log.warning(f"{where}: не удалось переписать ссылки в отчёте ({exc}); опубликован как есть")
        return result.report


def _keep_harness_log(stderr: str, name: str, outcome: Outcome, ctx: _PassContext) -> None:
    """A failed review's harness stderr is the one transient file worth keeping."""
    try:
        ctx.work_dir.logs.mkdir(parents=True, exist_ok=True)
        path = ctx.work_dir.logs / f"{name}.harness-stderr.log"
        path.write_text(stderr or "", encoding="utf-8")
        outcome.kept_paths.append(path)
    except OSError as exc:
        ctx.log.warning(f"Не удалось сохранить лог харнесса: {exc}")


def _finish_mr_tmp(
    mr_tmp: Path, debug_dir: Path | None, outcome: Outcome, ctx: _PassContext
) -> None:
    try:
        if debug_dir is not None:
            copy_artifacts(mr_tmp, debug_dir)
            if debug_dir.exists():
                outcome.kept_paths.append(debug_dir)
        if mr_tmp.exists():
            remove_path(mr_tmp)
    except OSError as exc:
        ctx.log.warning(f"Не удалось удалить временную папку {mr_tmp}: {exc}")


def run_poll(
    *,
    config_path: Path,
    review_all: bool = False,
    again: bool = False,
    dry_run: bool = False,
    include_closed: bool = False,
    debug: bool = False,
    client_factory: Callable[[str], GitLabClient] = GitLabClient,
    review_fn: Callable[..., ReviewResult] = run_review,
    repo_sources_factory: Callable[[Config, WorkDir, PassLog], RepoSources] | None = None,
    orphan_cleanup_fn: Callable[[Path, Path], None] | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], object] = print,
    error_fn: Callable[[str], object] | None = None,
    stdin_isatty: Callable[[], bool] = lambda: stdin_is_interactive(),
    is_alive: Callable[[int], bool] = pid_alive,
    is_git_repo: Callable[[Path], bool] = _is_git_repo,
    now_fn: Callable[[], datetime] = _utc_now,
    tree_paths_fn: Callable[[Path, str], set[str]] = list_tree_paths,
) -> int:
    """Run one polling pass and return the process exit code."""
    if repo_sources_factory is None:
        repo_sources_factory = _default_repo_sources
    if orphan_cleanup_fn is None:
        from review_agent.worktree import cleanup_orphaned_worktrees

        orphan_cleanup_fn = cleanup_orphaned_worktrees
    log_kwargs: dict[str, Any] = {"output_fn": output_fn}
    if error_fn is not None:
        log_kwargs["error_fn"] = error_fn

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        with pass_log(WorkDir(best_effort_work_dir(config_path)).logs, **log_kwargs) as log:
            log.error(f"Ошибка конфигурации: {exc}")
        return EXIT_NOT_STARTED

    work_dir = WorkDir.from_config(config)
    started = time.monotonic()
    with pass_log(work_dir.logs, **log_kwargs) as log:
        flags = [
            name
            for name, on in (
                ("--all", review_all),
                ("--again", again),
                ("--dry-run", dry_run),
                ("--include-closed", include_closed),
                ("--debug", debug),
            )
            if on
        ]
        log.file_only(
            f"review-agent {__version__} poll {' '.join(flags)}; cwd={Path.cwd()}; "
            f"config={config_path}; work_dir={work_dir.root.resolve()}"
        )
        try:
            if config.gitlab is None:
                log.error(f"В конфиге '{config_path}' нет секции 'gitlab' — она обязательна для poll.")
                return EXIT_NOT_STARTED

            if again and review_all:
                log.error(
                    "--again и --all несовместимы: повторное ревью запускается только "
                    "вручную и только по одному MR. Уберите --all."
                )
                return EXIT_NOT_STARTED

            if not review_all and not stdin_isatty():
                if again:
                    log.error(
                        "Повторное ревью (--again) только интерактивное: нужен терминал, "
                        "а stdin не интерактивный. В расписании и CI оно не запускается."
                    )
                else:
                    log.error(
                        "Интерактивный режим требует терминала, а stdin не интерактивный. "
                        "Для запуска без вопросов (планировщик, CI) используйте --all."
                    )
                return EXIT_NOT_STARTED

            # Prompt templates of every project this pass may review, before
            # the lock, GitLab and any claim: a refusal leaves nothing behind.
            enabled = [p for p in config.gitlab.projects if p.enabled]
            check = check_settings(project_targets(config, enabled), rereview=True)
            if not may_proceed(
                check,
                ask=None if review_all else input_fn,
                show_error=log.error,
                # Each line of the report is already tagged WARNING.
                show_warning=lambda message: log.warning(message, prefix=False),
            ):
                if not check.errors:
                    log.info("Проход отменён: предупреждения шаблонов промпта не приняты.")
                return EXIT_NOT_STARTED

            try:
                with poll_lock(work_dir.root, is_alive=is_alive):
                    _housekeeping(config, work_dir, log, orphan_cleanup_fn, is_git_repo)
                    ctx_kwargs = dict(
                        config=config,
                        work_dir=work_dir,
                        claim_ttl=timedelta(minutes=config.gitlab.claim_ttl_minutes),
                        dry_run=dry_run,
                        debug=debug,
                        log=log,
                        review_fn=review_fn,
                        repo_sources=repo_sources_factory(config, work_dir, log),
                        now_fn=now_fn,
                        tree_paths_fn=tree_paths_fn,
                        again=again,
                        usage=make_recorder(config, warn=log.warning, note=log.file_only),
                        quota_factory=(
                            None
                            if review_all or not config.usage.enabled
                            else lambda provider: make_quota_prompt(
                                config.usage.quota_windows,
                                provider,
                                input_fn=input_fn,
                                output_fn=output_fn,
                            )
                        ),
                    )
                    return _run_locked(
                        ctx_kwargs=ctx_kwargs,
                        review_all=review_all,
                        include_closed=include_closed,
                        client_factory=client_factory,
                        input_fn=input_fn,
                        is_git_repo=is_git_repo,
                    )
            except PollLockBusy as exc:
                log.error(str(exc))
                return EXIT_NOT_STARTED
        finally:
            log.file_only(f"Проход завершён за {time.monotonic() - started:.1f} с")


def _default_repo_sources(config: Config, work_dir: WorkDir, log: PassLog) -> RepoSources:
    return RepoSources(work_dir.repos, config.gitlab.hostname, notify=log.info)


def _housekeeping(
    config: Config,
    work_dir: WorkDir,
    log: PassLog,
    orphan_cleanup_fn: Callable[[Path, Path], None],
    is_git_repo: Callable[[Path], bool],
) -> None:
    """Under the lock, before anything else: leftovers of killed runs, then expired files.

    Never fails the pass - every problem becomes a warning in the log.
    """
    try:
        # Worktrees of killed runs are registered in the clone they came
        # from: the local clones of enabled projects and every managed
        # copy (also of projects disabled or removed from the config).
        clones = [
            Path(p.local_repo)
            for p in config.gitlab.projects
            if p.enabled and p.local_repo is not None and is_git_repo(Path(p.local_repo))
        ]
        for clone in [*clones, *managed_copies(work_dir.repos)]:
            try:
                orphan_cleanup_fn(clone, work_dir.tmp)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"Не удалось убрать осиротевшие worktree в {clone}: {exc}")
        leftovers = clear_tmp(work_dir, log.warning)
        if leftovers:
            log.info(f"Удалены остатки прерванных прогонов: {leftovers}")
        broken, expired_repos = expire_repos(
            work_dir.repos, config.storage.repo_retention_days, warn=log.warning
        )
        if broken:
            log.info(f"Удалены недокачанные или повреждённые кэш-клоны: {broken}")
        if expired_repos:
            log.info(
                f"Удалены кэш-клоны, не использовавшиеся дольше "
                f"{config.storage.repo_retention_days} дн.: {expired_repos}"
            )
        expired = cleanup_expired(work_dir, config.storage.retention_days, warn=log.warning)
        if config.storage.retention_days is not None:
            log.file_only(
                f"Удалено устаревших артефактов (старше {config.storage.retention_days} дн.): {expired}"
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Очистка рабочей папки не удалась: {exc}")


def _run_locked(
    *,
    ctx_kwargs: dict[str, Any],
    review_all: bool,
    include_closed: bool,
    client_factory: Callable[[str], GitLabClient],
    input_fn: Callable[[str], str],
    is_git_repo: Callable[[Path], bool],
) -> int:
    config: Config = ctx_kwargs["config"]
    log: PassLog = ctx_kwargs["log"]
    gitlab_config = config.gitlab
    client = client_factory(gitlab_config.hostname)
    try:
        bot_username = client.preflight()
    except GitLabError as exc:
        log.error(f"GitLab недоступен: {exc}")
        return EXIT_NOT_STARTED
    ctx = _PassContext(client=client, bot_username=bot_username, **ctx_kwargs)

    outcomes: list[Outcome] = []
    pending: list[_Pending] = []

    disabled = [p.path for p in gitlab_config.projects if not p.enabled]
    if disabled:
        log.info(f"Выключены в конфиге (не опрашиваются): {', '.join(disabled)}")

    for project in gitlab_config.projects:
        if not project.enabled:
            continue
        settings = effective_settings(config, project)
        if project.local_repo is not None and not is_git_repo(Path(project.local_repo)):
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
                project.path,
                settings.reviewers,
                review_drafts=settings.review_drafts,
                include_closed=include_closed,
            )
        except GitLabError as exc:
            outcomes.append(Outcome(project=project.path, iid=None, status="failed", reason=str(exc)))
            continue
        for candidate in candidates:
            try:
                notes = client.list_notes(project.path, candidate.iid)
                marks: list[str] = []
                if ctx.again:
                    report = latest_report(notes, bot_username)
                    if report is None:
                        # Never reviewed: nothing to build a further round on.
                        log.file_only(
                            f"{project.path} !{candidate.iid}: нет отчёта бота — "
                            "в повторном ревью не предлагается"
                        )
                        continue
                    marks = _again_marks(
                        notes, report, client.get_diff_refs(project.path, candidate.iid).head_sha, ctx
                    )
                reason = _in_progress_reason(notes, ctx)
                if reason:
                    outcomes.append(Outcome(project.path, candidate.iid, "skipped", reason))
                    continue
                metadata = client.get_mr_metadata(project.path, candidate.iid)
            except GitLabError as exc:
                outcomes.append(Outcome(project.path, candidate.iid, "failed", str(exc)))
                continue
            pending.append(
                _Pending(
                    project=project,
                    settings=settings,
                    candidate=candidate,
                    metadata=metadata,
                    marks=marks,
                )
            )

    if not pending:
        if ctx.again:
            log.info("Нет MR, которые можно отревьюить повторно (ни на одном нет отчёта бота).")
        else:
            log.info("Нет MR, ожидающих ревью.")
        selected: list[_Pending] = []
    elif review_all:
        selected = pending
    else:
        selected = _select(pending, input_fn=input_fn, log=log, again=ctx.again)

    for item in selected:  # strictly one after another - no parallelism
        log.info(f"Ревью {item.project.path} !{item.metadata.iid}: {item.metadata.web_url} ...")
        outcome = _review_one(item, ctx)
        log.file_only(outcome.describe())
        outcomes.append(outcome)

    if outcomes:
        log.info("\nИтог прохода:")
        for outcome in outcomes:
            log.info(outcome.describe())

    return EXIT_FAILURES if any(o.status == "failed" for o in outcomes) else EXIT_OK
