"""Isolated git worktree lifecycle for a single review run.

Every review runs against a throwaway `git worktree`, never against the
user's primary working copy. Cleanup is guaranteed on both success and
failure, and a startup pass removes worktrees left behind by a run that
did not exit cleanly (e.g. the process was killed).
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


@dataclasses.dataclass(frozen=True)
class WorktreeHandle:
    path: Path
    run_id: str


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
    )


def _commit_exists(repo_path: Path, sha: str) -> bool:
    return _run_git(repo_path, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def ensure_commits_available(repo_path: Path, *shas: str) -> None:
    """Fetch any of the given commits that are not already present locally."""
    for sha in shas:
        if _commit_exists(repo_path, sha):
            continue
        result = _run_git(repo_path, "fetch", "origin", sha)
        if result.returncode != 0:
            raise WorktreeError(
                f"Commit '{sha}' is not available locally and could not be "
                f"fetched: {result.stderr.strip()}"
            )


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
    result = _run_git(repo_path, "worktree", "add", "--detach", str(worktree_path), head_sha)
    if result.returncode != 0:
        raise WorktreeError(
            f"Failed to create worktree at '{worktree_path}': {result.stderr.strip()}"
        )
    return WorktreeHandle(path=worktree_path, run_id=run_id)


def remove_worktree(repo_path: Path, handle: WorktreeHandle) -> None:
    result = _run_git(repo_path, "worktree", "remove", "--force", str(handle.path))
    if result.returncode != 0:
        shutil.rmtree(handle.path, ignore_errors=True)
        _run_git(repo_path, "worktree", "prune")
    try:
        handle.path.parent.rmdir()
    except OSError:
        pass


def list_registered_worktrees(repo_path: Path) -> list[Path]:
    """Return paths of worktrees git currently knows about for this repo."""
    result = _run_git(repo_path, "worktree", "list", "--porcelain")
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
            result = _run_git(repo_path, "worktree", "remove", "--force", str(worktree_path))
            if result.returncode != 0:
                shutil.rmtree(worktree_path, ignore_errors=True)
    _run_git(repo_path, "worktree", "prune")


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
