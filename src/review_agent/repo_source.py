"""Where a project's review worktrees come from: the user's clone or our own copy.

A project with `local_repo` is reviewed against that clone, as before; one
without it against a managed copy that review-agent keeps itself:

    <work_dir>/repos/
      <slug>-<hash8>.git/          bare clone of https://<hostname>/<path>.git
        review-agent-last-used     touched by every review that uses it
      .incoming-<id>/              a clone in progress (only during a pass)

- Bare, full history, no tags: no working files (the review reads code
  from its own worktree in tmp/), no partial clone (the agent's `git
  blame`/`log -p` would fetch blobs over the network mid-review).
- Credentials come from the already-authenticated `glab`, used as the
  copy's git credential helper and recorded in the copy's own config, so
  every later fetch authenticates the same way. Only the helper command
  is stored, never a token; an empty `credential.helper=` first drops
  helpers configured elsewhere (Git Credential Manager would open a login
  window nobody sees from Task Scheduler). Verified live - see
  validation-notes.md of the managed-repo-cache change.
- A copy only gets its final name after the clone completed and its
  marker was written, so an interrupted clone is never mistaken for one.
- Unused copies expire by the marker's mtime (expire_repos): a folder's
  own mtime says nothing about use, `git fetch` writes deeper inside it.

Everything here runs under the work-dir lock (lock.py), so no two passes
ever clone, fetch, or delete in the same repos/ folder at once.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable

from review_agent.config import GitLabProjectConfig
from review_agent.housekeeping import Warn, remove_path
from review_agent.worktree import NETWORK_ENV, fetch_refspecs, run_git

LAST_USED_MARKER = "review-agent-last-used"
INCOMING_PREFIX = ".incoming-"


class RepoSourceError(RuntimeError):
    """A project's repository could not be made ready for a review."""


def slug(project_path: str) -> str:
    """"b2c/front-shopping" -> "b2c-front-shopping" (file-name safe)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", project_path).strip("-")


def cache_name(project_path: str) -> str:
    """Folder name of a project's managed copy.

    The hash keeps projects apart whose slugs collide ("a/b-c", "a-b/c").
    """
    digest = hashlib.sha1(project_path.encode("utf-8")).hexdigest()[:8]
    return f"{slug(project_path)}-{digest}.git"


def https_url(hostname: str, project_path: str) -> str:
    return f"https://{hostname}/{project_path}.git"


def glab_credential_helper(glab_path: str) -> str:
    """Git credential helper that asks `glab` for the host's credentials.

    Absolute path (Task Scheduler may run with a reduced PATH), forward
    slashes and quotes (git runs a `!` helper through sh, and the path may
    contain spaces: C:/Program Files/...).
    """
    return f"!'{glab_path.replace(chr(92), '/')}' auth git-credential"


def is_valid_copy(path: Path) -> bool:
    if not (path / LAST_USED_MARKER).is_file():
        return False
    result = run_git(path, "rev-parse", "--is-bare-repository")
    return result.returncode == 0 and result.stdout.strip() == "true"


@dataclasses.dataclass(frozen=True)
class PreparedRepo:
    """The repository to create a review worktree from, ready for one MR."""

    path: Path
    # Non-fatal problems (a missing MR ref, a re-cloned damaged copy); the
    # review still decides whether base/head are there.
    warnings: list[str]


class RepoSources:
    """Gets the repository of each reviewed MR ready; one instance per pass."""

    def __init__(
        self,
        repos_dir: Path,
        hostname: str,
        *,
        url_fn: Callable[[str], str] | None = None,
        credential_helper: str | None = None,
        which: Callable[[str], str | None] = shutil.which,
        notify: Callable[[str], object] | None = None,
    ):
        self._repos_dir = repos_dir
        self._notify = notify or (lambda message: None)
        self._url_fn = url_fn or (lambda path: https_url(hostname, path))
        self._credential_helper = credential_helper
        self._which = which
        # Copies already checked or cloned in this pass.
        self._ready: dict[str, Path] = {}

    def prepare(self, project: GitLabProjectConfig, target_branch: str, iid: int) -> PreparedRepo:
        """Bring the MR's target branch and head into the project's repository.

        The target branch holds the MR's base; the head comes through
        `refs/merge-requests/<iid>/head`, which GitLab keeps in the target
        project even for MRs from forks. In a local clone the target branch
        only updates its remote-tracking ref - the user's branches, index
        and files are never touched. Raises RepoSourceError only when a
        managed copy cannot be obtained; fetch problems are warnings.
        """
        mr_ref = f"refs/merge-requests/{iid}/head"
        if project.local_repo is not None:
            repo = Path(project.local_repo)
            remote = project.local_remote
            branch_ref = f"+refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}"
            return PreparedRepo(repo, fetch_refspecs(repo, remote, [branch_ref, mr_ref]))

        repo, warnings = self._ensure_copy(project.path)
        branch_ref = f"+refs/heads/{target_branch}:refs/heads/{target_branch}"
        warnings += fetch_refspecs(repo, "origin", [branch_ref, mr_ref])
        (repo / LAST_USED_MARKER).touch()
        return PreparedRepo(repo, warnings)

    def _ensure_copy(self, project_path: str) -> tuple[Path, list[str]]:
        if project_path in self._ready:
            return self._ready[project_path], []
        final = self._repos_dir / cache_name(project_path)
        warnings = []
        if final.exists() and not is_valid_copy(final):
            warnings.append(f"кэш-клон {final.name} повреждён — склонирован заново")
            try:
                remove_path(final)
            except OSError as exc:
                raise RepoSourceError(f"не удалось удалить повреждённый кэш-клон {final}: {exc}") from exc
        if not final.exists():
            self._clone(project_path, final)
        self._ready[project_path] = final
        return final, warnings

    def _clone(self, project_path: str, final: Path) -> None:
        helper = self._helper()
        url = self._url_fn(project_path)
        incoming = self._repos_dir / f"{INCOMING_PREFIX}{int(time.time())}-{uuid.uuid4().hex[:8]}"
        self._repos_dir.mkdir(parents=True, exist_ok=True)
        self._notify(f"Клонирование {url} в кэш {final} (первое ревью проекта) ...")
        result = run_git(
            None,
            # -c: credentials for the clone itself; --config: the same,
            # recorded in the copy for every later fetch.
            "-c", "credential.helper=",
            "-c", f"credential.helper={helper}",
            "clone", "--bare", "--no-tags", "--quiet",
            "--config", "credential.helper=",
            "--config", f"credential.helper={helper}",
            url, str(incoming),
            extra_env=NETWORK_ENV,
        )  # fmt: skip
        try:
            if result.returncode != 0:
                raise RepoSourceError(f"не удалось склонировать {url}: {result.stderr.strip()}")
            (incoming / LAST_USED_MARKER).touch()
            incoming.rename(final)
        finally:
            if incoming.exists():
                try:
                    remove_path(incoming)
                except OSError:
                    pass  # the next pass deletes `.incoming-*` (expire_repos)

    def _helper(self) -> str:
        if self._credential_helper is None:
            glab = self._which("glab")
            if glab is None:
                raise RepoSourceError("glab не найден в PATH — нужен для авторизации git при клонировании")
            self._credential_helper = glab_credential_helper(glab)
        return self._credential_helper


def managed_copies(repos_dir: Path) -> list[Path]:
    """Valid managed copies in repos/ (for orphaned-worktree cleanup)."""
    if not repos_dir.is_dir():
        return []
    return [p for p in sorted(repos_dir.glob("*.git")) if p.is_dir() and is_valid_copy(p)]


def expire_repos(
    repos_dir: Path,
    retention_days: int | None,
    *,
    warn: Warn,
    now: float | None = None,
) -> tuple[int, int]:
    """Delete leftovers and unused copies in repos/ - only under the work-dir lock.

    - `.incoming-*`: clones a killed pass never finished - always deleted;
    - a copy without the marker or not a bare repository - deleted (the
      next review that needs it clones it again);
    - a copy whose marker is older than `retention_days` - deleted, whether
      or not its project is still configured; None disables this.

    Returns (incomplete or damaged removed, expired removed). A failure to
    delete is reported through `warn` and does not stop the others.
    """
    if not repos_dir.is_dir():
        return 0, 0
    cutoff = None if retention_days is None else (time.time() if now is None else now) - retention_days * 86400
    broken = expired = 0
    for entry in sorted(repos_dir.iterdir()):
        try:
            if entry.name.startswith(INCOMING_PREFIX) or not is_valid_copy(entry):
                remove_path(entry)
                broken += 1
            elif cutoff is not None and (entry / LAST_USED_MARKER).stat().st_mtime < cutoff:
                remove_path(entry)
                expired += 1
        except OSError as exc:
            warn(f"Не удалось удалить {entry}: {exc}")
    return broken, expired
