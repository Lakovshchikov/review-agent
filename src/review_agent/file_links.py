"""Turns file references in a review report into GitLab links.

Pure logic, no I/O (see design.md of the gitlab-file-links change). The
model refers to files the way it saw them: by the absolute path of the
run's throwaway worktree (`[...](E:/.../worktree/src/a.ts#L13)`) or as
inline code (`` `src/a.ts:13` ``). Before publishing, both become links
to the file in the reviewed commit, and no absolute worktree path is left
in the comment: it is meaningless (and private) for anyone reading the MR.

The prompt does not ask for any link format on purpose (AGENTS.md
section 3): rewriting afterwards is deterministic and cannot cost recall.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from urllib.parse import quote, unquote

# Opening/closing line of a fenced code block: content inside is code, not
# references - only the worktree prefix is removed there.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A markdown link (or image) or an inline code span, whichever comes first.
_TOKEN_RE = re.compile(
    r"(?P<link>(?P<bang>!?)\[(?P<text>[^\]\n]*)\]\((?P<target>[^)\n]+)\))"
    r"|`(?P<code>[^`\n]+)`"
)
# `path`, `path:13` or `path:13-20` - nothing else counts as a reference.
# Spaces are allowed: a path only becomes a link if it is exactly a path
# of the commit, so prose in backticks never matches anyway.
_LOCATION_RE = re.compile(r"^(?P<path>[^:`\n]+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")
# `#L13`, `#L13-20`, `#L13-L20`
_ANCHOR_RE = re.compile(r"^L(?P<start>\d+)(?:-L?(?P<end>\d+))?$")
# `http:`, `mailto:` - but not a drive letter (`E:`).
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")


def project_web_url(mr_web_url: str, hostname: str, project_path: str) -> str:
    """The project's web URL as GitLab itself gives it in the MR's `web_url`."""
    base, sep, _ = (mr_web_url or "").partition("/-/merge_requests/")
    if sep and base:
        return base.rstrip("/")
    return f"https://{hostname}/{project_path}"


def _prefix_re(worktree_path: PurePath | str | None) -> re.Pattern[str] | None:
    """Matches the worktree path however the model spelled it.

    Any separator, any case (the drive letter differs between tools), an
    optional `file://` scheme and leading slashes (`/E:/...`).
    """
    if worktree_path is None:
        return None
    parts = [p for p in re.split(r"[\\/]+", str(worktree_path)) if p]
    if not parts:
        return None
    body = r"[\\/]+".join(re.escape(p) for p in parts)
    return re.compile(
        r"(?:file:)?[\\/]*" + body + r"(?![\w-])(?!\.[\w-])(?P<sep>[\\/]+)?", re.IGNORECASE
    )


def _ancestors(paths: set[str]) -> set[str]:
    dirs: set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return dirs


class _Linker:
    def __init__(
        self,
        *,
        worktree_path: PurePath | str | None,
        project_url: str,
        head_sha: str,
        base_sha: str,
        head_paths: set[str] | None,
        base_paths: set[str] | None,
    ) -> None:
        self.prefix = _prefix_re(worktree_path)
        self.project_url = project_url.rstrip("/")
        self.head_sha = head_sha
        self.base_sha = base_sha
        self.known = head_paths is not None
        self.commits = [
            (head_sha, head_paths or set(), _ancestors(head_paths or set())),
            (base_sha, base_paths or set(), _ancestors(base_paths or set())),
        ]

    # -- local paths -------------------------------------------------------

    def strip(self, text: str) -> str:
        """Replace every worktree path by the repository-relative one."""
        if self.prefix is None:
            return text
        return self.prefix.sub(lambda m: "" if m.group("sep") else ".", text)

    def relative(self, raw: str) -> str | None:
        """`raw` minus the worktree prefix, `/`-separated; None if not inside it."""
        if self.prefix is None:
            return None
        match = self.prefix.match(raw)
        if match is None or not match.group("sep"):
            return None
        return raw[match.end():].replace("\\", "/")

    # -- GitLab URLs ---------------------------------------------------------

    def locate(self, path: str, *, trust_head: bool) -> tuple[str, bool] | None:
        """(commit, is_directory) the path exists in, head first.

        Without a file list, a path from the worktree is trusted to be in
        head (the worktree IS head); anything else cannot be checked.
        """
        if not self.known:
            return (self.head_sha, False) if trust_head else None
        for sha, files, dirs in self.commits:
            if path in dirs:
                return sha, True
            if path in files:
                return sha, False
        return None

    def url(self, path: str, sha: str, is_dir: bool, start: str | None, end: str | None) -> str:
        kind = "tree" if is_dir else "blob"
        url = f"{self.project_url}/-/{kind}/{sha}/{quote(path, safe='/')}"
        if is_dir or not start:
            return url
        if end and int(end) > int(start):
            return f"{url}#L{int(start)}-{int(end)}"
        return f"{url}#L{int(start)}"

    # -- tokens --------------------------------------------------------------

    def link(self, match: re.Match[str]) -> str:
        text = self.strip(match.group("text"))
        raw_target = match.group("target").strip()
        if match.group("bang"):
            return self.strip(match.group(0))
        target = unquote(raw_target.strip("<>"))
        path_part, _, fragment = target.partition("#")
        rel = self.relative(path_part)
        from_worktree = rel is not None
        if rel is None:
            if _SCHEME_RE.match(path_part) or path_part.startswith(("/", "\\")) or not self.known:
                # http(s), mailto, other absolute paths: not ours to judge.
                return f"[{text}]({self.strip(raw_target)})"
            rel = path_part.replace("\\", "/")
        rel = rel.removeprefix("./")

        anchor = _ANCHOR_RE.match(fragment)
        start, end = (anchor.group("start"), anchor.group("end")) if anchor else (None, None)
        if anchor is None:
            location = _LOCATION_RE.match(rel)
            if location and location.group("start"):
                rel, start, end = location.group("path"), location.group("start"), location.group("end")

        found = self.locate(rel, trust_head=from_worktree) if rel else None
        if found is None:
            # A relative link we cannot place stays as written; a worktree
            # link to nowhere loses only its (local, broken) target.
            return text if from_worktree else match.group(0)
        sha, is_dir = found
        return f"[{text}]({self.url(rel, sha, is_dir, start, end)})"

    def code(self, match: re.Match[str]) -> str:
        content = match.group("code")
        rel = self.relative(content)
        shown = content if rel is None else rel.removeprefix("./")
        candidate = shown.removeprefix("./")
        location = _LOCATION_RE.match(candidate)
        if location and self.known:
            path = location.group("path")
            found = self.locate(path, trust_head=False)
            if found is not None:
                sha, is_dir = found
                url = self.url(path, sha, is_dir, location.group("start"), location.group("end"))
                return f"[`{shown}`]({url})"
        return f"`{self.strip(shown)}`"

    def prose(self, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            return self.link(match) if match.group("link") else self.code(match)

        out = []
        last = 0
        for match in _TOKEN_RE.finditer(text):
            out.append(self.strip(text[last:match.start()]))
            out.append(replace(match))
            last = match.end()
        out.append(self.strip(text[last:]))
        return "".join(out)


def link_file_references(
    report: str,
    *,
    worktree_path: PurePath | str | None,
    project_url: str,
    head_sha: str,
    base_sha: str,
    head_paths: set[str] | None,
    base_paths: set[str] | None,
) -> str:
    """The report with file references turned into GitLab links.

    `head_paths`/`base_paths` are every file and directory of the two
    commits (None if they could not be listed): an inline-code path only
    becomes a link when it names one of them. Text other than the
    references themselves is left exactly as it was.
    """
    linker = _Linker(
        worktree_path=worktree_path,
        project_url=project_url,
        head_sha=head_sha,
        base_sha=base_sha,
        head_paths=head_paths,
        base_paths=base_paths,
    )
    out: list[str] = []
    prose: list[str] = []
    fence: str | None = None
    for line in report.splitlines(keepends=True):
        opening = _FENCE_RE.match(line)
        if fence is None:
            if opening:
                out.append(linker.prose("".join(prose)))
                prose = []
                fence = opening.group(1)
                out.append(linker.strip(line))
            else:
                prose.append(line)
            continue
        out.append(linker.strip(line))
        closing = line.strip()
        if opening and set(closing) == {fence[0]} and len(closing) >= len(fence):
            fence = None
    out.append(linker.prose("".join(prose)))
    return "".join(out)
