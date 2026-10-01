from pathlib import Path

from review_agent.worktree import (
    cleanup_orphaned_worktrees,
    create_worktree,
    list_registered_worktrees,
    managed_worktree,
)


def test_create_worktree_checks_out_head_commit(git_repo_with_base_and_head, tmp_path):
    fixture = git_repo_with_base_and_head
    scratch = tmp_path / "scratch"

    handle = create_worktree(fixture["repo"], fixture["head_sha"], scratch)

    assert handle.path.exists()
    assert (handle.path / "file.txt").read_text(encoding="utf-8") == "v2\n"


def test_successful_run_cleans_up_worktree(git_repo_with_base_and_head, tmp_path):
    fixture = git_repo_with_base_and_head
    scratch = tmp_path / "scratch"

    with managed_worktree(fixture["repo"], fixture["base_sha"], fixture["head_sha"], scratch) as handle:
        worktree_path = handle.path
        assert worktree_path.exists()

    assert not worktree_path.exists()
    assert worktree_path not in list_registered_worktrees(fixture["repo"])


def test_failed_run_still_cleans_up_worktree(git_repo_with_base_and_head, tmp_path):
    fixture = git_repo_with_base_and_head
    scratch = tmp_path / "scratch"
    worktree_path: Path | None = None

    class SimulatedFailure(Exception):
        pass

    try:
        with managed_worktree(
            fixture["repo"], fixture["base_sha"], fixture["head_sha"], scratch
        ) as handle:
            worktree_path = handle.path
            raise SimulatedFailure("harness blew up")
    except SimulatedFailure:
        pass

    assert worktree_path is not None
    assert not worktree_path.exists()


def test_review_never_touches_primary_working_copy(git_repo_with_base_and_head, tmp_path):
    fixture = git_repo_with_base_and_head
    repo = fixture["repo"]
    scratch = tmp_path / "scratch"

    branch_before = (repo / ".git" / "HEAD").read_text(encoding="utf-8")
    content_before = (repo / "file.txt").read_text(encoding="utf-8")

    with managed_worktree(repo, fixture["base_sha"], fixture["head_sha"], scratch) as handle:
        (handle.path / "file.txt").write_text("modified in worktree only\n", encoding="utf-8")

    branch_after = (repo / ".git" / "HEAD").read_text(encoding="utf-8")
    content_after = (repo / "file.txt").read_text(encoding="utf-8")

    assert branch_before == branch_after
    assert content_before == content_after


def test_orphaned_worktree_removed_before_new_run_starts(git_repo_with_base_and_head, tmp_path):
    fixture = git_repo_with_base_and_head
    repo = fixture["repo"]
    scratch = tmp_path / "scratch"

    # Simulate a prior run that crashed and never cleaned up: create a
    # worktree directly, without going through the managed context manager.
    leftover = create_worktree(repo, fixture["head_sha"], scratch, run_id="crashed-run")
    assert leftover.path.exists()
    assert leftover.path in list_registered_worktrees(repo)

    # A fresh call (as happens at the start of every new run) should sweep
    # it away before doing anything else.
    cleanup_orphaned_worktrees(repo, scratch)

    assert not leftover.path.exists()
    assert leftover.path not in list_registered_worktrees(repo)
