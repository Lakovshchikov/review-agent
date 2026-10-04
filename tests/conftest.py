import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


@pytest.fixture
def git_repo_with_base_and_head(tmp_path):
    """A local git repo with two commits: base (file v1) and head (file v2)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "file.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    (repo / "file.txt").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "head")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    return {"repo": repo, "base_sha": base_sha, "head_sha": head_sha}


class FakeGitLab:
    """A bare repo standing in for a GitLab project: `main` plus MR refs.

    Commits are made in a private work clone and pushed, so a test can move
    `main` or publish `refs/merge-requests/<iid>/head` after a clone exists.
    """

    def __init__(self, root: Path):
        self.work = root / "upstream-work"
        self.bare = root / "gitlab" / "group" / "project.git"
        self.work.mkdir(parents=True)
        _git(self.work, "init", "-q", "-b", "main")
        _git(self.work, "config", "user.email", "t@example.com")
        _git(self.work, "config", "user.name", "T")
        (self.work / "f.txt").write_text("base\n", encoding="utf-8")
        _git(self.work, "add", "f.txt")
        _git(self.work, "commit", "-q", "-m", "base")
        self.base_sha = _git(self.work, "rev-parse", "HEAD").stdout.strip()
        self.bare.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(self.work), str(self.bare)], check=True)

    def commit_main(self, content: str) -> str:
        _git(self.work, "checkout", "-q", "main")
        (self.work / "f.txt").write_text(content, encoding="utf-8")
        _git(self.work, "add", "f.txt")
        _git(self.work, "commit", "-q", "-m", content.strip())
        _git(self.work, "push", "-q", str(self.bare), "main:refs/heads/main")
        return _git(self.work, "rev-parse", "HEAD").stdout.strip()

    def push_mr(self, iid: int, content: str, base: str = "main") -> str:
        """Commit `content` on top of `base` and publish it only as the MR ref."""
        _git(self.work, "checkout", "-q", "--detach", base)
        (self.work / "f.txt").write_text(content, encoding="utf-8")
        _git(self.work, "commit", "-q", "-am", content.strip())
        sha = _git(self.work, "rev-parse", "HEAD").stdout.strip()
        _git(self.work, "push", "-q", str(self.bare), f"HEAD:refs/merge-requests/{iid}/head")
        _git(self.work, "checkout", "-q", "main")
        return sha


@pytest.fixture
def fake_gitlab(tmp_path):
    return FakeGitLab(tmp_path)


def has_commit(repo: Path, sha: str) -> bool:
    return (
        subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode
        == 0
    )
