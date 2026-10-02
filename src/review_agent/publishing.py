"""Pure logic for publishing a review as a GitLab MR comment.

No I/O here: building/parsing the review-state marker, deciding whether
an MR was already reviewed, formatting the comment body, and the report
sanity check. GitLab calls live in gitlab.py, orchestration in polling.py.

The review-state marker is the ONLY record that an MR was reviewed (no
local state - see AGENTS.md section 5): `<!-- ai-review: sha=<head_sha> -->`
inside a comment authored by the GitLab user `glab` is authenticated as.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_MARKER_RE = re.compile(r"<!-- ai-review: sha=([0-9a-f]{7,64}) -->")


def build_marker(head_sha: str) -> str:
    return f"<!-- ai-review: sha={head_sha} -->"


def find_marker_shas(text: str) -> list[str]:
    """All head SHAs recorded by review-state markers in a comment body."""
    return _MARKER_RE.findall(text or "")


def reviewed_shas(notes: Iterable[dict[str, Any]], bot_username: str) -> list[str]:
    """Head SHAs reviewed according to markers in notes authored by `bot_username`.

    Markers in anyone else's notes are ignored, so a user quoting or
    pasting a marker cannot switch the review off for an MR.
    """
    shas: list[str] = []
    for note in notes:
        author = (note.get("author") or {}).get("username")
        if author != bot_username:
            continue
        shas.extend(find_marker_shas(note.get("body", "")))
    return shas


def is_already_reviewed(notes: Iterable[dict[str, Any]], bot_username: str) -> bool:
    """Skip rule for this change: ANY of our markers means "reviewed", whatever its SHA.

    Re-reviewing a new head after the developer pushes more commits is a
    backlog feature; enabling it only means changing this predicate to
    compare against the current head SHA - the marker format and storage
    stay the same.
    """
    return bool(reviewed_shas(notes, bot_username))


def is_report_usable(report: str, min_chars: int) -> bool:
    """Guards against a "silently empty" report.

    Change 1's live validation hit a transport bug where the harness
    exited 0 with a one-line non-review answer - only a human reading
    the report caught it. An empty or very short report is never posted.
    """
    stripped = report.strip()
    return bool(stripped) and len(stripped) >= min_chars


def format_comment(*, report: str, head_sha: str, base_sha: str, model: str) -> str:
    """Comment body: header naming the reviewed commit, the report as-is, marker last."""
    header = (
        "### 🤖 AI code review\n\n"
        f"Автоматический отчёт по коммиту `{head_sha[:12]}` "
        f"(base `{base_sha[:12]}`), модель `{model}`.\n"
        "Находки могут быть неполными или ошибочными — это подсказка ревьюеру, "
        "не вердикт.\n\n"
        "---\n\n"
    )
    return f"{header}{report.strip()}\n\n{build_marker(head_sha)}\n"
