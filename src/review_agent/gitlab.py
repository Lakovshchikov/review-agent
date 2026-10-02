"""Thin adapter over the `glab` CLI - the only module that talks to GitLab.

Split of responsibilities (design.md decision 1):
- `glab mr view --output json` - MR metadata a human looks at (author,
  title, description, web link, draft flag);
- `glab api` - everything else: candidate search by reviewer, diff_refs
  (base/head SHA), notes, the current user, and publishing a note.

Same conventions as harness.py, both learned the hard way in Change 1:
- `glab` is resolved through PATH via shutil.which before invocation, so
  the same argv works on Windows and Linux without shell=True;
- every call uses encoding="utf-8" explicitly, so Cyrillic titles,
  descriptions and reports are not decoded as cp1252 on Windows;
- nothing long or multi-line goes into argv: a note body is written to
  a JSON file and sent with `--input` (a report as an argv token is the
  same class of bug that silently truncated the prompt in Change 1).

The runner and the PATH lookup are injectable, so tests exercise this
adapter (and the orchestrator above it) against a stub instead of a
real GitLab, which is VPN-only.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

Runner = Callable[..., subprocess.CompletedProcess]


class GitLabError(RuntimeError):
    """A `glab` call failed, or returned something that cannot be used."""


@dataclasses.dataclass(frozen=True)
class MRCandidate:
    project: str
    iid: int
    title: str
    draft: bool
    state: str = "opened"


@dataclasses.dataclass(frozen=True)
class MRMetadata:
    iid: int
    title: str
    description: str
    author: str
    web_url: str
    draft: bool
    # "opened" | "closed" | "merged" | "locked"
    state: str = "opened"


@dataclasses.dataclass(frozen=True)
class DiffRefs:
    base_sha: str
    head_sha: str


def encode_project(path: str) -> str:
    """"b2c/front-shopping" -> "b2c%2Ffront-shopping" (GitLab REST project id form)."""
    return quote(path, safe="")


def _parse_json_stream(text: str) -> Any:
    """Parse `glab api` output, including `--paginate` output.

    With --paginate each page is printed as its own JSON array, so the
    output may be several concatenated documents (`[...][...]`). Lists
    are flattened into one list; a single non-list document is returned
    as is.
    """
    decoder = json.JSONDecoder()
    documents = []
    index = 0
    text = text.strip()
    while index < len(text):
        document, end = decoder.raw_decode(text, index)
        documents.append(document)
        index = end
        while index < len(text) and text[index].isspace():
            index += 1
    if not documents:
        raise ValueError("empty output")
    if len(documents) == 1 and not isinstance(documents[0], list):
        return documents[0]
    flattened: list[Any] = []
    for document in documents:
        if isinstance(document, list):
            flattened.extend(document)
        else:
            flattened.append(document)
    return flattened


class GitLabClient:
    def __init__(
        self,
        hostname: str,
        *,
        runner: Runner = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        glab_command: str = "glab",
    ) -> None:
        self.hostname = hostname
        self._runner = runner
        self._which = which
        self._glab_command = glab_command

    # -- low level -----------------------------------------------------

    def _glab_executable(self) -> str:
        resolved = self._which(self._glab_command)
        if not resolved:
            raise GitLabError(
                f"'{self._glab_command}' was not found in PATH - install glab "
                "and authenticate it (glab auth login --hostname "
                f"{self.hostname})"
            )
        return resolved

    def _run(self, args: list[str]) -> str:
        argv = [self._glab_executable(), *args]
        env = os.environ.copy()
        # Lets `glab mr view -R <group/project>` (which has no --hostname
        # flag) resolve the project against the configured instance
        # rather than glab's default host.
        env["GITLAB_HOST"] = self.hostname
        try:
            result = self._runner(
                argv, capture_output=True, text=True, encoding="utf-8", env=env
            )
        except OSError as exc:
            raise GitLabError(f"Failed to run glab: {exc}") from exc
        if result.returncode != 0:
            raise GitLabError(
                f"glab {' '.join(args[:3])} ... exited with code {result.returncode}: "
                f"{(result.stderr or result.stdout or '').strip()}"
            )
        return result.stdout

    def _json(self, args: list[str]) -> Any:
        output = self._run(args)
        try:
            return _parse_json_stream(output)
        except ValueError as exc:
            raise GitLabError(
                f"glab {' '.join(args[:3])} ... returned non-JSON output: {output[:200]!r}"
            ) from exc

    def api(self, endpoint: str, *, paginate: bool = False) -> Any:
        args = ["api", "--hostname", self.hostname]
        if paginate:
            args.append("--paginate")
        args.append(endpoint)
        return self._json(args)

    # -- queries -------------------------------------------------------

    def preflight(self) -> str:
        """Check glab is installed and authenticated for the host; return the username."""
        try:
            user = self.api("user")
        except GitLabError as exc:
            raise GitLabError(
                f"GitLab at '{self.hostname}' is unreachable or glab is not "
                f"authenticated for it (glab auth login --hostname {self.hostname}): {exc}"
            ) from exc
        return self._username(user)

    def current_user(self) -> str:
        return self._username(self.api("user"))

    @staticmethod
    def _username(user: Any) -> str:
        if not isinstance(user, dict) or not user.get("username"):
            raise GitLabError("glab api user did not return a username")
        return user["username"]

    def list_review_candidates(
        self,
        project: str,
        reviewers: list[str],
        *,
        review_drafts: bool,
        include_closed: bool = False,
    ) -> list[MRCandidate]:
        """Open MRs in `project` where any of `reviewers` is assigned as reviewer.

        One query per reviewer, merged by iid: an MR with two of our
        reviewers is one candidate. `include_closed` widens the search to
        closed and merged MRs too (state=all) - for testing on MRs where a
        bot comment bothers nobody.
        """
        state = "all" if include_closed else "opened"
        by_iid: dict[int, MRCandidate] = {}
        for reviewer in reviewers:
            endpoint = (
                f"projects/{encode_project(project)}/merge_requests"
                f"?state={state}&reviewer_username={quote(reviewer, safe='')}&per_page=100"
            )
            for mr in self.api(endpoint, paginate=True):
                iid = int(mr["iid"])
                draft = bool(mr.get("draft", mr.get("work_in_progress", False)))
                by_iid[iid] = MRCandidate(
                    project=project,
                    iid=iid,
                    title=mr.get("title", ""),
                    draft=draft,
                    state=mr.get("state", "opened"),
                )
        candidates = sorted(by_iid.values(), key=lambda c: c.iid)
        if not review_drafts:
            candidates = [c for c in candidates if not c.draft]
        return candidates

    def get_mr_metadata(self, project: str, iid: int) -> MRMetadata:
        data = self._json(["mr", "view", str(iid), "-R", project, "--output", "json"])
        if not isinstance(data, dict):
            raise GitLabError(f"glab mr view {project}!{iid} returned unexpected JSON")
        try:
            return MRMetadata(
                iid=int(data["iid"]),
                title=data["title"],
                description=data.get("description") or "",
                author=(data.get("author") or {})["username"],
                web_url=data["web_url"],
                draft=bool(data.get("draft", data.get("work_in_progress", False))),
                state=data.get("state") or "opened",
            )
        except (KeyError, TypeError) as exc:
            raise GitLabError(
                f"glab mr view {project}!{iid} JSON is missing field {exc}"
            ) from exc

    def get_diff_refs(self, project: str, iid: int) -> DiffRefs:
        mr = self.api(f"projects/{encode_project(project)}/merge_requests/{iid}")
        refs = (mr or {}).get("diff_refs") or {}
        if not refs.get("base_sha") or not refs.get("head_sha"):
            raise GitLabError(f"{project}!{iid} has no diff_refs (base/head SHA) yet")
        return DiffRefs(base_sha=refs["base_sha"], head_sha=refs["head_sha"])

    def list_notes(self, project: str, iid: int) -> list[dict[str, Any]]:
        notes = self.api(
            f"projects/{encode_project(project)}/merge_requests/{iid}/notes?per_page=100",
            paginate=True,
        )
        return notes if isinstance(notes, list) else [notes]

    # -- writes --------------------------------------------------------

    def post_note(self, project: str, iid: int, body: str, *, body_file: Path) -> None:
        """Publish `body` as a note. The body travels in a file, never in argv."""
        body_file.parent.mkdir(parents=True, exist_ok=True)
        body_file.write_text(json.dumps({"body": body}, ensure_ascii=False), encoding="utf-8")
        self._run(
            [
                "api",
                "--hostname",
                self.hostname,
                "--method",
                "POST",
                "-H",
                "Content-Type: application/json",
                "--input",
                str(body_file),
                f"projects/{encode_project(project)}/merge_requests/{iid}/notes",
            ]
        )
