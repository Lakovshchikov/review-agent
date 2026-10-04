"""Isolated git worktree lifecycle for a single review run.

Every review runs against a throwaway `git worktree`, never against the
user's primary working copy. Cleanup is guaranteed on both success and
failure, and a startup pass removes worktrees left behind by a run that
did not exit cleanly (e.g. the process was killed).
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from review_agent.proc import no_window_flags


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


@dataclasses.dataclass(frozen=True)
class WorktreeHandle:
    path: Path
    run_id: str


# For every git command that may go to the network: fail at once instead
# of waiting for a password - in a terminal prompt, or in a Git Credential
# Manager window that nobody sees when the pass runs from Task Scheduler.
NETWORK_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}

# For `worktree add`: LFS assets are of no use to the review, and the
# smudge filter would download them (with credentials) mid-checkout.
CHECKOUT_ENV = {"GIT_LFS_SKIP_SMUDGE": "1"}


def run_git(
    repo_path: Path | None, *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """`git [-C repo_path] <args>`, output captured as text, no console window."""
    prefix = ["git"] if repo_path is None else ["git", "-C", str(repo_path)]
    return subprocess.run(
        [*prefix, *args],
        capture_output=True,
        text=True,
        env={**os.environ, **extra_env} if extra_env else None,
        creationflags=no_window_flags(),
    )



def _commit_exists(repo_path: Path, sha: str) -> bool:
    return run_git(repo_path, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def ensure_commits_available(repo_path: Path, *shas: str) -> None:
    """Fetch any of the given commits that are not already present locally."""
    for sha in shas:
        if _commit_exists(repo_path, sha):
            continue
        result = run_git(repo_path, "fetch", "origin", sha, extra_env=NETWORK_ENV)
        if result.returncode != 0:
            raise WorktreeError(
                f"Commit '{sha}' is not available locally and could not be "
                f"fetched: {result.stderr.strip()}"
            )


def fetch_refspecs(repo_path: Path, remote: str, refspecs: list[str]) -> list[str]:
    """`git fetch <remote> <refspec>...` in one call; returns warnings, never raises.

    Used to bring a merge request's branches up to date before a review
    (see repo_source.py). Which refs get written is decided by the
    refspecs the caller hands in; a refspec without a destination only
    touches FETCH_HEAD and the object store.

    A fetch with several refspecs fails AS A WHOLE when one remote ref is
    missing (verified live: GitLab may clean up the MR ref of an old MR) -
    nothing else is updated then either. So on failure each refspec is
    retried alone and every one that still fails becomes a warning: the
    caller decides whether the commits it needs are there anyway
    (ensure_commits_available).
    """
    args = ["fetch", "--quiet", "--no-tags", remote]
    if run_git(repo_path, *args, *refspecs, extra_env=NETWORK_ENV).returncode == 0:
        return []
    warnings = []
    for refspec in refspecs:
        result = run_git(repo_path, *args, refspec, extra_env=NETWORK_ENV)
        if result.returncode != 0:
            warnings.append(f"fetch {refspec} из '{remote}' не удался: {result.stderr.strip()}")
    return warnings


def list_tree_paths(repo_path: Path, sha: str) -> set[str]:
    """Every file and directory path of commit `sha`, repository-relative.

    Read-only and local (works in a bare clone too). `-z` with quotepath
    off and an explicit UTF-8 decode keep non-ASCII paths intact - plain
    `text=True` decodes with the Windows ANSI codepage (Change 1's bug).
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "-c", "core.quotepath=off",
         "ls-tree", "-r", "-t", "-z", "--name-only", sha],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        creationflags=no_window_flags(),
    )
    if result.returncode != 0:
        raise WorktreeError(f"git ls-tree {sha} failed: {result.stderr.strip()}")
    return {path for path in result.stdout.split("\0") if path}


def _new_run_id() -> str:
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def create_worktree(
    repo_path: Path, head_sha: str, scratch_dir: Path, run_id: str | None = None
) -> WorktreeHandle:
    run_id = run_id or _new_run_id()
    # Resolve to absolute: `git -C <repo_path> worktree add <path> ...`
    # resolves a relative <path> against repo_path, not against this
    # process's cwd - a relative scratch_dir would silently create the
    # worktree somewhere other than where Python then looks for it.
    worktree_path = (scratch_dir / run_id / "worktree").resolve()
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(
        repo_path, "worktree", "add", "--detach", str(worktree_path), head_sha, extra_env=CHECKOUT_ENV
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"Failed to create worktree at '{worktree_path}': {result.stderr.strip()}"
        )
    return WorktreeHandle(path=worktree_path, run_id=run_id)


def remove_worktree(repo_path: Path, handle: WorktreeHandle) -> None:
    result = run_git(repo_path, "worktree", "remove", "--force", str(handle.path))
    if result.returncode != 0:
        shutil.rmtree(handle.path, ignore_errors=True)
        run_git(repo_path, "worktree", "prune")
    try:
        handle.path.parent.rmdir()
    except OSError:
        pass


def list_registered_worktrees(repo_path: Path) -> list[Path]:
    """Return paths of worktrees git currently knows about for this repo."""
    result = run_git(repo_path, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        raise WorktreeError(f"Failed to list worktrees: {result.stderr.strip()}")
    paths = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[len("worktree ") :]))
    return paths


def cleanup_orphaned_worktrees(repo_path: Path, scratch_dir: Path) -> None:
    """Remove worktrees left behind by a run that did not exit cleanly."""
    scratch_dir = scratch_dir.resolve()
    if not scratch_dir.exists():
        return
    repo_resolved = repo_path.resolve()
    for worktree_path in list_registered_worktrees(repo_path):
        try:
            resolved = worktree_path.resolve()
        except OSError:
            continue
        if resolved == repo_resolved:
            continue  # the main working copy itself - never touch it
        if scratch_dir in resolved.parents:
            result = run_git(repo_path, "worktree", "remove", "--force", str(worktree_path))
            if result.returncode != 0:
                shutil.rmtree(worktree_path, ignore_errors=True)
    run_git(repo_path, "worktree", "prune")


@contextmanager
def managed_worktree(
    repo_path: Path, base_sha: str, head_sha: str, scratch_dir: Path
) -> Iterator[WorktreeHandle]:
    """Create an isolated worktree and guarantee its removal, success or failure."""
    cleanup_orphaned_worktrees(repo_path, scratch_dir)
    ensure_commits_available(repo_path, base_sha, head_sha)
    handle = create_worktree(repo_path, head_sha, scratch_dir)
    try:
        yield handle
    finally:
        remove_worktree(repo_path, handle)
