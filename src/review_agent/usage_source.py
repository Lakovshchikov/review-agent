"""Where a review run's token usage comes from: the harness (design.md decision 1).

The rest of usage accounting only sees the provider-neutral structures
below (`RunUsage`, `SessionUsage`, `TokenCounts`), so another harness
means another source here and nothing else. A source never raises: a
problem becomes `missing_reason` (no usage at all) or an entry in
`format_problems` (the harness answered in an unexpected shape - shown
to the user as a warning, see usage_ledger.UsageRecorder).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Protocol

from review_agent.proc import no_window_flags

if TYPE_CHECKING:
    from review_agent.config import Config

TOKEN_KINDS = ("input", "cache_read", "cache_write", "output", "reasoning")


@dataclasses.dataclass(frozen=True)
class TokenCounts:
    """Tokens by kind as the harness reports them; `input` is UNCACHED input.

    A field is None when the harness did not report it in a usable form.
    """

    input: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    output: int | None = None
    reasoning: int | None = None

    @property
    def total_input(self) -> int | None:
        """All input incl. cache - what OpenTelemetry calls input_tokens."""
        parts = (self.input, self.cache_read, self.cache_write)
        return None if any(p is None for p in parts) else sum(parts)  # type: ignore[arg-type]

    @staticmethod
    def sum(counts: list["TokenCounts"]) -> "TokenCounts":
        """Per-kind sum; a kind missing in any of `counts` is missing in the sum."""

        def total(kind: str) -> int | None:
            values = [getattr(c, kind) for c in counts]
            return None if not values or any(v is None for v in values) else sum(values)

        return TokenCounts(**{kind: total(kind) for kind in TOKEN_KINDS})


@dataclasses.dataclass(frozen=True)
class SessionUsage:
    id: str
    parent: str | None
    agent: str | None
    tokens: TokenCounts
    duration_ms: int | None = None
    steps: int | None = None


@dataclasses.dataclass
class RunUsage:
    """Everything the source learned about one review run's harness sessions."""

    harness_name: str
    harness_version: str | None = None
    # Root session first, then its descendants (breadth-first).
    sessions: list[SessionUsage] = dataclasses.field(default_factory=list)
    format_problems: list[str] = dataclasses.field(default_factory=list)
    # Why there is no usage at all (no session found, source disabled...).
    missing_reason: str | None = None

    @property
    def totals(self) -> TokenCounts | None:
        if not self.sessions:
            return None
        return TokenCounts.sum([s.tokens for s in self.sessions])

    @property
    def steps(self) -> int | None:
        values = [s.steps for s in self.sessions]
        return None if not values or any(v is None for v in values) else sum(values)  # type: ignore[arg-type]


class UsageSource(Protocol):
    def collect(self, worktree_path: Path) -> RunUsage:
        """Usage of the run whose worktree is `worktree_path`; never raises."""


class NoUsageSource:
    """`usage.source: none` - records are still written, without tokens."""

    def collect(self, worktree_path: Path) -> RunUsage:
        return RunUsage(harness_name="none", missing_reason="usage.source: none")


# -- OpenCode -------------------------------------------------------------------

# Versions whose `session list` / `session export` output this parser was
# verified against on a real install; any other version still works but
# triggers a visible warning (design.md decision 4).
VERIFIED_OPENCODE_VERSIONS = frozenset({"2.0.21"})

MAX_CANDIDATES = 5  # newest top-level sessions checked for the run's worktree
MAX_DEPTH = 5  # subagent nesting followed
MAX_SESSIONS = 50  # sessions per run, root included
COMMAND_TIMEOUT_SECONDS = 60

_SESSION_ID = re.compile(r"\bses_[A-Za-z0-9]+\b")
_VERSION = re.compile(r"(\d+\.\d+\.\d+)")

Runner = Callable[..., subprocess.CompletedProcess]


def _same_path(a: str, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def _get(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return _MISSING
        data = data[part]
    return data


_MISSING = object()


def _int_field(info: dict[str, Any], dotted: str, problems: list[str]) -> int | None:
    value = _get(info, dotted)
    if value is _MISSING:
        problems.append(f"info.{dotted}: нет поля")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"info.{dotted}: ожидалось целое, получено {type(value).__name__}")
        return None
    return value


def _str_field(info: dict[str, Any], dotted: str, problems: list[str]) -> str | None:
    value = _get(info, dotted)
    if value is _MISSING:
        problems.append(f"info.{dotted}: нет поля")
        return None
    if not isinstance(value, str):
        problems.append(f"info.{dotted}: ожидалась строка, получено {type(value).__name__}")
        return None
    return value


def _strings(data: Any) -> Iterator[str]:
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for value in data.values():
            yield from _strings(value)
    elif isinstance(data, list):
        for value in data:
            yield from _strings(value)


class OpenCodeUsageSource:
    """Usage from `opencode session list` + `opencode session export` (design.md decision 1).

    The run's root session is the one whose `info.location.directory` is
    the run's worktree. Subagent sessions are followed breadth-first: a
    session id referenced in a session's messages is exported and counted
    as its child when the child names it as `info.parentID` (or carries
    no parent field at all - the link is then the reference itself).
    Only numbers and identifiers are taken from an export; prompt and
    report text are discarded right after parsing.
    """

    def __init__(
        self,
        list_command: list[str],
        export_command: list[str],
        *,
        version_command: list[str] | None = None,
        runner: Runner = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.list_command = list_command
        self.export_command = export_command
        # The harness that runs the reviews - not whatever the list command
        # starts with (it may be a wrapper).
        self.version_command = version_command or ["opencode", "--version"]
        self._runner = runner
        self._which = which
        self._version: str | None | object = _MISSING  # cached per process

    # -- subprocess plumbing --------------------------------------------------

    def _run(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
        argv = list(argv)
        resolved = self._which(argv[0])
        if resolved:
            argv[0] = resolved
        return self._runner(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd),
            timeout=COMMAND_TIMEOUT_SECONDS,
            creationflags=no_window_flags(),
        )

    def _harness_version(self, cwd: Path, problems: list[str]) -> str | None:
        if self._version is _MISSING:
            version: str | None = None
            try:
                result = self._run(self.version_command, cwd)
                match = _VERSION.search(result.stdout or "")
                version = match.group(1) if result.returncode == 0 and match else None
            except (OSError, subprocess.SubprocessError):
                version = None
            self._version = version
        version = self._version  # type: ignore[assignment]
        if version is None:
            problems.append("не удалось определить версию OpenCode (`--version`)")
        elif version not in VERIFIED_OPENCODE_VERSIONS:
            verified = ", ".join(sorted(VERIFIED_OPENCODE_VERSIONS))
            problems.append(
                f"версия OpenCode {version} не проверена с учётом расхода (проверено: {verified}) "
                "— сверьте записи журнала с `opencode session export`"
            )
        return version  # type: ignore[return-value]

    def _list_ids(self, cwd: Path, problems: list[str]) -> list[str] | None:
        try:
            result = self._run(self.list_command, cwd)
        except (OSError, subprocess.SubprocessError) as exc:
            problems.append(f"`{' '.join(self.list_command)}` не выполнилась: {exc}")
            return None
        if result.returncode != 0:
            problems.append(
                f"`{' '.join(self.list_command)}` завершилась с кодом {result.returncode}: "
                f"{(result.stderr or '').strip()[:200]}"
            )
            return None
        ids = list(dict.fromkeys(_SESSION_ID.findall(result.stdout or "")))
        if not ids and (result.stdout or "").strip():
            problems.append("в выводе `session list` не найдено ни одного id вида ses_…")
        return ids[:MAX_CANDIDATES]

    def _export(self, session_id: str, cwd: Path, problems: list[str]) -> dict[str, Any] | None:
        argv = [part.replace("{session_id}", session_id) for part in self.export_command]
        try:
            result = self._run(argv, cwd)
        except (OSError, subprocess.SubprocessError) as exc:
            problems.append(f"экспорт {session_id} не выполнился: {exc}")
            return None
        if result.returncode != 0:
            problems.append(f"экспорт {session_id} завершился с кодом {result.returncode}")
            return None
        try:
            data = json.loads(result.stdout)
        except ValueError:
            problems.append(f"экспорт {session_id}: ответ не JSON")
            return None
        if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
            problems.append(f"экспорт {session_id}: нет объекта info")
            return None
        if not isinstance(data.get("messages"), list):
            problems.append(f"экспорт {session_id}: нет списка messages")
            data["messages"] = []
        return data

    # -- interpretation ---------------------------------------------------------

    @staticmethod
    def _session(data: dict[str, Any], parent: str | None, problems: list[str]) -> SessionUsage:
        info = data["info"]
        session_id = info.get("id") if isinstance(info.get("id"), str) else "?"
        local: list[str] = []
        tokens = TokenCounts(
            input=_int_field(info, "tokens.input", local),
            cache_read=_int_field(info, "tokens.cache.read", local),
            cache_write=_int_field(info, "tokens.cache.write", local),
            output=_int_field(info, "tokens.output", local),
            reasoning=_int_field(info, "tokens.reasoning", local),
        )
        _str_field(info, "model.id", local)
        _str_field(info, "model.providerID", local)
        created = _int_field(info, "time.created", local)
        ends = [
            v
            for v in (_get(info, "time.updated"), _get(info, "time.idle"))
            if isinstance(v, int) and not isinstance(v, bool)
        ]
        duration = max(ends) - created if created is not None and ends else None
        types = [m.get("type") for m in data["messages"] if isinstance(m, dict)]
        steps = types.count("assistant") if "assistant" in types else None
        agent = info.get("agent") if isinstance(info.get("agent"), str) else None
        problems.extend(f"{session_id}: {p}" for p in local)
        return SessionUsage(
            id=session_id,
            parent=parent,
            agent=agent,
            tokens=tokens,
            duration_ms=duration if duration is None or duration >= 0 else None,
            steps=steps,
        )

    def _children(self, data: dict[str, Any], cwd: Path, seen: set[str]) -> list[dict[str, Any]]:
        own = data["info"].get("id")
        children = []
        for candidate in dict.fromkeys(
            m for s in _strings(data["messages"]) for m in _SESSION_ID.findall(s)
        ):
            if candidate in seen or candidate == own:
                continue
            seen.add(candidate)
            # A referenced id that does not export is not a session of ours
            # (any "ses_..." text could be quoted in a message) - not a problem.
            child = self._export(candidate, cwd, [])
            if child is None:
                continue
            parent_field = child["info"].get("parentID", _MISSING)
            if parent_field is _MISSING or parent_field == own:
                children.append(child)
        return children

    def collect(self, worktree_path: Path) -> RunUsage:
        usage = RunUsage(harness_name="opencode")
        problems = usage.format_problems
        try:
            usage.harness_version = self._harness_version(worktree_path, problems)
            ids = self._list_ids(worktree_path, problems)
            if ids is None:
                usage.missing_reason = "список сессий не получен"
                return usage
            root = None
            # Problems with OTHER sessions of the project matter only if ours
            # is not found (a neighbour may be deleted between list and export).
            candidate_problems: list[str] = []
            for session_id in ids:
                data = self._export(session_id, worktree_path, candidate_problems)
                if data is None:
                    continue
                directory = _get(data["info"], "location.directory")
                if directory is _MISSING:
                    candidate_problems.append(f"{session_id}: info.location.directory: нет поля")
                    continue
                if isinstance(directory, str) and _same_path(directory, worktree_path):
                    root = data
                    break
            if root is None:
                problems.extend(candidate_problems)
                usage.missing_reason = "сессия прогона не найдена среди последних сессий проекта"
                return usage

            seen = {root["info"].get("id")}
            level = [(root, None)]
            depth = 0
            while level:
                next_level = []
                for data, parent in level:
                    if len(usage.sessions) >= MAX_SESSIONS:
                        problems.append(
                            f"сессий больше {MAX_SESSIONS} — учтены только первые {MAX_SESSIONS}"
                        )
                        return usage
                    session = self._session(data, parent, problems)
                    usage.sessions.append(session)
                    if depth < MAX_DEPTH:
                        next_level.extend(
                            (child, session.id) for child in self._children(data, worktree_path, seen)
                        )
                    elif self._children(data, worktree_path, seen):
                        problems.append(f"вложенность субагентов глубже {MAX_DEPTH} — глубже не учтено")
                level = next_level
                depth += 1
            return usage
        except Exception as exc:  # noqa: BLE001 - a source never raises
            problems.append(f"сбор расхода прерван: {exc}")
            if not usage.sessions:
                usage.missing_reason = "сбор расхода прерван ошибкой"
            return usage


def make_usage_source(config: "Config") -> UsageSource:
    usage_config = config.usage
    if usage_config.source == "none":
        return NoUsageSource()
    return OpenCodeUsageSource(
        usage_config.session_list_command,
        usage_config.session_export_command,
        version_command=[config.harness.command[0], "--version"],
    )
