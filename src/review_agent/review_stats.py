"""Facts about a reviewed change that the usage record keeps (design.md decisions 2, 10).

Measured by the orchestrator itself, after the prompt was rendered -
the agent never sees these numbers (AGENTS.md section 4: no prepared
context such as change size or file groups).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from review_agent.worktree import run_git

SEVERITIES = ("blocker", "major", "minor")


@dataclasses.dataclass(frozen=True)
class ChangeSize:
    files: int
    lines_added: int
    lines_deleted: int
    commits: int


def measure_change(repo_path: Path, base_sha: str, head_sha: str) -> ChangeSize | None:
    """Files and +/- lines of base..head, plus its commit count; None if git fails.

    Binary files (`-\\t-` in --numstat) count as changed files but add no lines.
    """
    numstat = run_git(repo_path, "diff", "--numstat", base_sha, head_sha)
    count = run_git(repo_path, "rev-list", "--count", f"{base_sha}..{head_sha}")
    if numstat.returncode != 0 or count.returncode != 0:
        return None
    files = added = deleted = 0
    for line in numstat.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            deleted += int(parts[1])
    try:
        commits = int(count.stdout.strip())
    except ValueError:
        return None
    return ChangeSize(files=files, lines_added=added, lines_deleted=deleted, commits=commits)


_SEV = r"(blocker|major|minor)"
# A severity word used as a LABEL, not just mentioned in prose:
#   SEV: Major / SEV Major / SEV-Major
#   [Major]  (Major)  **Major**  __Major__
#   "Major:" / "Major —" at the start of a heading or list item
_LABEL_PATTERNS = [
    re.compile(rf"\bSEV\s*[:=\-]?\s*\**\s*{_SEV}\b", re.IGNORECASE),
    re.compile(rf"[\[(]\s*{_SEV}\s*[\])]", re.IGNORECASE),
    re.compile(rf"(\*\*|__)\s*{_SEV}\s*(\*\*|__)", re.IGNORECASE),
    re.compile(
        rf"^\s*(?:#{{1,6}}\s+|[-*+]\s+|\d+[.)]\s+)?(?:\d+[.)]\s*)?{_SEV}\s*[:—–-]",
        re.IGNORECASE,
    ),
]


def _label_in(line: str) -> str | None:
    best: tuple[int, str] | None = None
    for pattern in _LABEL_PATTERNS:
        match = pattern.search(line)
        if match is None:
            continue
        word = next(g for g in match.groups() if g and g.lower() in SEVERITIES).lower()
        if best is None or match.start() < best[0]:
            best = (match.start(), word)
    return best[1] if best else None


def count_findings(report: str | None) -> dict[str, int] | None:
    """Findings per severity, estimated from the report's labels; None without a report.

    One line counts as at most one finding (its first label). This is an
    estimate of a free-form markdown report - the published report stays
    the source of truth.
    """
    if report is None:
        return None
    counts = {severity: 0 for severity in SEVERITIES}
    for line in report.splitlines():
        label = _label_in(line)
        if label is not None:
            counts[label] += 1
    return counts
