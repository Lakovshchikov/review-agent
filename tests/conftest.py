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
