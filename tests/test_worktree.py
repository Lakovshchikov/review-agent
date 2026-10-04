import subprocess
from pathlib import Path

import pytest
from conftest import has_commit

from review_agent.worktree import (
    cleanup_orphaned_worktrees,
    create_worktree,
    fetch_refspecs,
    WorktreeError,
    list_registered_worktrees,
    list_tree_paths,
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


def test_create_worktree_with_relative_scratch_dir_resolves_from_cwd(
    git_repo_with_base_and_head, tmp_path, monkeypatch
):
    """Regression test: `git -C <repo> worktree add <path>` resolves a
    relative <path> against <repo>, not against this process's cwd. A
    relative scratch_dir must still land where Python expects it."""
    fixture = git_repo_with_base_and_head
    cwd = tmp_path / "somewhere_else"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    handle = create_worktree(fixture["repo"], fixture["head_sha"], Path("relative-scratch"))

    assert handle.path.exists()
    assert handle.path.is_absolute()
    assert handle.path == (cwd / "relative-scratch" / handle.run_id / "worktree").resolve()
    # Must NOT have been created relative to the repo instead.
    assert not (fixture["repo"] / "relative-scratch").exists()


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


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_fetch_refspecs_brings_target_branch_and_mr_head_in_one_call(fake_gitlab, tmp_path):
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(fake_gitlab.bare), str(clone)], check=True)
    new_base = fake_gitlab.commit_main("moved main\n")
    mr_sha = fake_gitlab.push_mr(1, "mr change\n")
    head_before = _git(clone, "rev-parse", "HEAD")

    warnings = fetch_refspecs(
        clone, "origin", ["+refs/heads/main:refs/remotes/origin/main", "refs/merge-requests/1/head"]
    )

    assert warnings == []
    assert has_commit(clone, mr_sha)
    assert _git(clone, "rev-parse", "refs/remotes/origin/main") == new_base
    # Local branch, index and working files of the clone are untouched.
    assert _git(clone, "rev-parse", "HEAD") == head_before
    assert _git(clone, "status", "--porcelain") == ""
    assert (clone / "f.txt").read_text(encoding="utf-8") == "base\n"


def test_fetch_refspecs_missing_mr_ref_still_updates_target_branch(fake_gitlab, tmp_path):
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(fake_gitlab.bare), str(clone)], check=True)
    new_base = fake_gitlab.commit_main("moved main\n")

    warnings = fetch_refspecs(
        clone, "origin", ["+refs/heads/main:refs/remotes/origin/main", "refs/merge-requests/9/head"]
    )

    assert len(warnings) == 1 and "refs/merge-requests/9/head" in warnings[0]
    assert _git(clone, "rev-parse", "refs/remotes/origin/main") == new_base


def test_fetch_refspecs_unknown_remote_returns_warnings(git_repo_with_base_and_head):
    warnings = fetch_refspecs(git_repo_with_base_and_head["repo"], "no-such-remote", ["refs/merge-requests/9/head"])
    assert len(warnings) == 1 and "no-such-remote" in warnings[0]


def test_network_git_calls_never_prompt(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    fetch_refspecs(Path("."), "origin", ["refs/heads/main"])
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GCM_INTERACTIVE"] == "never"


def test_worktree_from_bare_clone(fake_gitlab, tmp_path):
    bare = tmp_path / "cache.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(fake_gitlab.bare), str(bare)], check=True)
    head = fake_gitlab.commit_main("head\n")
    fetch_refspecs(bare, "origin", ["+refs/heads/main:refs/heads/main"])

    with managed_worktree(bare, fake_gitlab.base_sha, head, tmp_path / "tmp") as handle:
        assert (handle.path / "f.txt").read_text(encoding="utf-8") == "head\n"
        assert handle.path in list_registered_worktrees(bare)
        worktree_path = handle.path

    assert not worktree_path.exists()
    assert worktree_path not in list_registered_worktrees(bare)
    assert not any((bare / "worktrees").glob("*")) if (bare / "worktrees").exists() else True


def test_worktree_add_skips_lfs_smudge(monkeypatch, tmp_path):
    captured = {}

    def fake_run(argv, **kwargs):
        captured[tuple(argv[3:5])] = kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_worktree(Path("repo"), "a" * 40, tmp_path)
    assert captured[("worktree", "add")]["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"


def test_list_tree_paths_lists_files_and_directories(git_repo_with_base_and_head):
    fixture = git_repo_with_base_and_head
    repo = fixture["repo"]
    nested = repo / "src" / "Страница группы"
    nested.mkdir(parents=True)
    (nested / "my page.tsx").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "nested"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    paths = list_tree_paths(repo, sha)

    assert paths == {
        "file.txt",
        "src",
        "src/Страница группы",
        "src/Страница группы/my page.tsx",
    }
    assert list_tree_paths(repo, fixture["base_sha"]) == {"file.txt"}


def test_list_tree_paths_unknown_commit_raises(git_repo_with_base_and_head):
    with pytest.raises(WorktreeError):
        list_tree_paths(git_repo_with_base_and_head["repo"], "0" * 40)
