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
#   "### 1. Title — Major"  severity closing a heading or list item
_LABEL_PATTERNS = [
    re.compile(rf"\bSEV\s*[:=\-]?\s*\**\s*{_SEV}\b", re.IGNORECASE),
    re.compile(rf"[\[(]\s*{_SEV}\s*[\])]", re.IGNORECASE),
    re.compile(rf"(\*\*|__)\s*{_SEV}\s*(\*\*|__)", re.IGNORECASE),
    re.compile(
        rf"^\s*(?:#{{1,6}}\s+|[-*+]\s+|\d+[.)]\s+)?(?:\d+[.)]\s*)?{_SEV}\s*[:—–-]",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^\s*(?:#{{1,6}}\s+|[-*+]\s+|\d+[.)]\s+).*[:—–-]\s*\**\s*{_SEV}\s*\**\s*$",
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


_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
# A heading that is ONLY a severity - a group of findings, not a finding:
#   ### Major    ## 🔴 Blocker    ### SEV: Minor    ### Major (3)
_SECTION_RE = re.compile(rf"^[\W_]*(?:SEV\s*[:=\-]?\s*)?{_SEV}[\W_]*(?:\(?\d+\)?)?[\W_]*$", re.IGNORECASE)
# An item that may be a finding: a list item or a heading.
_ITEM_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>\d+[.)]|[-*+])\s+\S")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _item_style(line: str) -> tuple[str, int] | None:
    """(kind, indent/level) of a list item or heading line, None for prose."""
    heading = _HEADING_RE.match(line)
    if heading:
        return "heading", len(heading.group(1))
    item = _ITEM_RE.match(line)
    if item:
        kind = "numbered" if item.group("marker")[0].isdigit() else "bullet"
        return kind, len(item.group("indent").expandtabs(4))
    return None


def count_findings(report: str | None) -> dict[str, int] | None:
    """Findings per severity, estimated from the report's labels; None without a report.

    Two report shapes are understood:
    - a label on the finding itself (`### Major — ...`, `SEV: Minor`,
      `[Major]`): one line counts as at most one finding (its first label);
    - a heading that is only a severity (`### Major`) followed by the
      findings as list items or sub-headings: each item of the first
      item's kind and depth counts as one finding of that severity, until
      the next heading of the same or higher level. Labels inside such a
      section count only on those item lines, so a repeated `SEV: Major`
      under an item is not a second finding.

    Fenced code blocks are skipped. This is an estimate of a free-form
    markdown report - the published report stays the source of truth.
    """
    if report is None:
        return None
    counts = {severity: 0 for severity in SEVERITIES}
    section: str | None = None  # severity of the current group heading
    section_level = 0
    style: tuple[str, int] | None = None  # how the group's findings are written
    fence: str | None = None
    for line in report.splitlines():
        opening = _FENCE_RE.match(line)
        if fence is not None:
            if opening and set(line.strip()) == {fence[0]} and len(line.strip()) >= len(fence):
                fence = None
            continue
        if opening:
            fence = opening.group(1)
            continue

        heading = _HEADING_RE.match(line)
        if heading and (section is None or len(heading.group(1)) <= section_level):
            group = _SECTION_RE.match(heading.group(2))
            if group:
                section, section_level, style = group.group(1).lower(), len(heading.group(1)), None
                continue
            section = None

        label = _label_in(line)
        if section is None:
            if label is not None:
                counts[label] += 1
            continue
        current = _item_style(line)
        if current is None:
            continue
        if style is None:
            style = current
        if current == style:
            counts[label or section] += 1
    return counts
