"""Pure logic for publishing a review as a GitLab MR comment.

No I/O here: building/parsing the review-state marker, deciding whether
an MR was already reviewed, formatting the comment body, and the report
sanity check. GitLab calls live in gitlab.py, orchestration in polling.py.

The review-state marker is the ONLY record that an MR was reviewed (no
local state - see AGENTS.md section 5): `<!-- ai-review: sha=<head_sha> -->`
inside a comment authored by the GitLab user `glab` is authenticated as.

The claim marker `<!-- ai-review-claim: sha=<head> started=<UTC> -->` is
the only record that an MR is being reviewed RIGHT NOW (design.md
decision 7): a pass posts a claim comment before running the harness and
later turns that same comment into the report (or deletes it). A claim
younger than the configured TTL blocks every other pass, on any machine.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta, timezone
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


_CLAIM_RE = re.compile(r"<!-- ai-review-claim: sha=([0-9a-f]{7,64}) started=(\S+) -->")


@dataclasses.dataclass(frozen=True)
class Claim:
    note_id: int
    head_sha: str
    # When the claim was made: GitLab's own note `created_at` (one clock
    # for all machines), falling back to the marker's `started`. None if
    # neither parses - such a claim is treated as stale, so a broken
    # note can never block an MR forever.
    started_at: datetime | None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def build_claim_marker(head_sha: str, started_at: datetime) -> str:
    started = started_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"<!-- ai-review-claim: sha={head_sha} started={started} -->"


def format_claim_comment(*, head_sha: str, started_at: datetime) -> str:
    started = started_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"⏳ AI-ревью коммита `{head_sha[:12]}` выполняется (начато {started}). "
        "Этот комментарий будет заменён отчётом.\n\n"
        f"{build_claim_marker(head_sha, started_at)}\n"
    )


def find_claims(notes: Iterable[dict[str, Any]], bot_username: str) -> list[Claim]:
    """Claims in notes authored by `bot_username`; anyone else's are ignored."""
    claims: list[Claim] = []
    for note in notes:
        if (note.get("author") or {}).get("username") != bot_username:
            continue
        match = _CLAIM_RE.search(note.get("body", "") or "")
        if not match or note.get("id") is None:
            continue
        started_at = _parse_time(note.get("created_at")) or _parse_time(match.group(2))
        claims.append(Claim(note_id=int(note["id"]), head_sha=match.group(1), started_at=started_at))
    return claims


def _is_live(claim: Claim, now: datetime, ttl: timedelta) -> bool:
    return claim.started_at is not None and now - claim.started_at < ttl


def live_claims(
    notes: Iterable[dict[str, Any]], bot_username: str, *, now: datetime, ttl: timedelta
) -> list[Claim]:
    """Claims younger than `ttl`, oldest first (lowest note id = claimed first)."""
    found = [c for c in find_claims(notes, bot_username) if _is_live(c, now, ttl)]
    return sorted(found, key=lambda c: c.note_id)


def stale_claims(
    notes: Iterable[dict[str, Any]], bot_username: str, *, now: datetime, ttl: timedelta
) -> list[Claim]:
    """Claims left by a pass that died: older than `ttl` (or with no usable time)."""
    return [c for c in find_claims(notes, bot_username) if not _is_live(c, now, ttl)]


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
