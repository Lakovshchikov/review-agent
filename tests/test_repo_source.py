import os
import subprocess
import time

import pytest
from conftest import has_commit

from review_agent.config import GitLabProjectConfig
from review_agent.repo_source import (
    INCOMING_PREFIX,
    LAST_USED_MARKER,
    RepoSourceError,
    RepoSources,
    cache_name,
    expire_repos,
    glab_credential_helper,
    https_url,
    managed_copies,
)
from review_agent.worktree import managed_worktree

HELPER = "!'C:/Program Files/glab/glab.exe' auth git-credential"


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _sources(fake_gitlab, repos_dir):
    # Clones from the local fake "GitLab" instead of https://<host>/<path>.git.
    return RepoSources(
        repos_dir, "gitlab.local", url_fn=lambda path: str(fake_gitlab.bare), credential_helper=HELPER
    )


PROJECT = GitLabProjectConfig(path="group/project")


def test_names_and_urls():
    assert cache_name("a/b-c") != cache_name("a-b/c")
    assert cache_name("b2c/front-shopping").startswith("b2c-front-shopping-")
    assert cache_name("b2c/front-shopping").endswith(".git")
    assert https_url("git.example.local", "b2c/front-shopping") == "https://git.example.local/b2c/front-shopping.git"
    assert glab_credential_helper("C:\\Program Files\\glab\\glab.exe") == HELPER


def test_first_prepare_clones_a_bare_copy_without_working_files(fake_gitlab, tmp_path):
    repos = tmp_path / "repos"
    mr_sha = fake_gitlab.push_mr(7, "mr\n")

    prepared = _sources(fake_gitlab, repos).prepare(PROJECT, "main", 7)

    assert prepared.path == repos / cache_name("group/project")
    assert prepared.warnings == []
    assert _git(prepared.path, "rev-parse", "--is-bare-repository") == "true"
    assert not (prepared.path / "f.txt").exists()
    assert (prepared.path / LAST_USED_MARKER).is_file()
    assert has_commit(prepared.path, mr_sha)
    assert not any(p.name.startswith(INCOMING_PREFIX) for p in repos.iterdir())


def test_credential_helper_recorded_in_copy_and_no_token(fake_gitlab, tmp_path):
    prepared = _sources(fake_gitlab, tmp_path / "repos").prepare(PROJECT, "main", 1)
    helpers = subprocess.run(
        ["git", "-C", str(prepared.path), "config", "--local", "--get-all", "credential.helper"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    # Empty value first (drops GCM and other helpers configured elsewhere), then glab.
    assert helpers == ["", HELPER]
    config_text = (prepared.path / "config").read_text(encoding="utf-8")
    assert "password" not in config_text and "token" not in config_text


def test_clone_uses_helper_and_non_interactive_env(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 128, "", "fatal: could not read Username")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sources = RepoSources(tmp_path / "repos", "git.example.local", which=lambda name: "C:/glab/glab.exe")
    with pytest.raises(RepoSourceError, match="could not read Username"):
        sources.prepare(PROJECT, "main", 1)

    argv, kwargs = calls[0]
    assert "https://git.example.local/group/project.git" in argv
    assert argv[argv.index("clone") - 1] == "credential.helper=!'C:/glab/glab.exe' auth git-credential"
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert not any(p.name.startswith(INCOMING_PREFIX) for p in (tmp_path / "repos").iterdir())


def test_missing_glab_is_a_clear_error(tmp_path):
    sources = RepoSources(tmp_path / "repos", "h", which=lambda name: None)
    with pytest.raises(RepoSourceError, match="glab"):
        sources.prepare(PROJECT, "main", 1)


def test_base_added_to_target_after_clone_is_available(fake_gitlab, tmp_path):
    repos = tmp_path / "repos"
    _sources(fake_gitlab, repos).prepare(PROJECT, "main", 1)  # earlier pass: clone

    new_base = fake_gitlab.commit_main("moved main\n")
    mr_sha = fake_gitlab.push_mr(2, "mr two\n")
    prepared = _sources(fake_gitlab, repos).prepare(PROJECT, "main", 2)  # later pass: reuse

    assert has_commit(prepared.path, new_base) and has_commit(prepared.path, mr_sha)
    assert _git(prepared.path, "rev-parse", "refs/heads/main") == new_base
    with managed_worktree(prepared.path, new_base, mr_sha, tmp_path / "tmp") as handle:
        assert (handle.path / "f.txt").read_text(encoding="utf-8") == "mr two\n"


def test_second_prepare_in_a_pass_does_not_clone_again(fake_gitlab, tmp_path, monkeypatch):
    sources = _sources(fake_gitlab, tmp_path / "repos")
    sources.prepare(PROJECT, "main", 1)
    clones = []
    original = sources._clone
    monkeypatch.setattr(sources, "_clone", lambda *a: clones.append(a) or original(*a))
    sources.prepare(PROJECT, "main", 2)
    assert clones == []


def test_prepare_touches_marker(fake_gitlab, tmp_path):
    repos = tmp_path / "repos"
    path = _sources(fake_gitlab, repos).prepare(PROJECT, "main", 1).path
    old = time.time() - 40 * 86400
    os.utime(path / LAST_USED_MARKER, (old, old))
    _sources(fake_gitlab, repos).prepare(PROJECT, "main", 1)
    assert (path / LAST_USED_MARKER).stat().st_mtime > old + 86400


def test_interrupted_clone_is_not_used(fake_gitlab, tmp_path):
    repos = tmp_path / "repos"
    leftover = repos / f"{INCOMING_PREFIX}123-abc"
    subprocess.run(["git", "clone", "-q", "--bare", str(fake_gitlab.bare), str(leftover)], check=True)

    prepared = _sources(fake_gitlab, repos).prepare(PROJECT, "main", 1)
    assert prepared.path == repos / cache_name("group/project")


def test_damaged_copy_is_cloned_again(fake_gitlab, tmp_path):
    repos = tmp_path / "repos"
    damaged = repos / cache_name("group/project")
    damaged.mkdir(parents=True)
    (damaged / "junk").write_text("x", encoding="utf-8")

    prepared = _sources(fake_gitlab, repos).prepare(PROJECT, "main", 1)

    assert prepared.path == damaged
    assert not (damaged / "junk").exists()
    assert _git(damaged, "rev-parse", "--is-bare-repository") == "true"
    assert any("повреждён" in w for w in prepared.warnings)


def test_missing_mr_ref_is_a_warning_not_a_failure(fake_gitlab, tmp_path):
    prepared = _sources(fake_gitlab, tmp_path / "repos").prepare(PROJECT, "main", 99)
    assert len(prepared.warnings) == 1 and "refs/merge-requests/99/head" in prepared.warnings[0]


def test_local_clone_branches_and_files_untouched(fake_gitlab, tmp_path):
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(fake_gitlab.bare), str(clone)], check=True)
    local_main = _git(clone, "rev-parse", "refs/heads/main")
    new_base = fake_gitlab.commit_main("moved main\n")
    mr_sha = fake_gitlab.push_mr(3, "mr three\n")
    repos = tmp_path / "repos"

    prepared = _sources(fake_gitlab, repos).prepare(
        GitLabProjectConfig(path="group/project", local_repo=str(clone)), "main", 3
    )

    assert prepared.path == clone and prepared.warnings == []
    assert has_commit(clone, mr_sha)
    assert _git(clone, "rev-parse", "refs/remotes/origin/main") == new_base
    assert _git(clone, "rev-parse", "refs/heads/main") == local_main  # local branch unchanged
    assert _git(clone, "status", "--porcelain") == ""
    assert (clone / "f.txt").read_text(encoding="utf-8") == "base\n"
    assert not repos.exists()  # no managed copy for a project with a local clone


def _copy(fake_gitlab, repos, project_path, age_days=0):
    path = _sources(fake_gitlab, repos).prepare(GitLabProjectConfig(path=project_path), "main", 1).path
    stamp = time.time() - age_days * 86400
    os.utime(path / LAST_USED_MARKER, (stamp, stamp))
    return path


def test_expire_repos(fake_gitlab, tmp_path):
    repos = tmp_path / "repos"
    fresh = _copy(fake_gitlab, repos, "g/fresh", age_days=10)
    old = _copy(fake_gitlab, repos, "g/old", age_days=31)
    incoming = repos / f"{INCOMING_PREFIX}1-x"
    incoming.mkdir()
    no_marker = _copy(fake_gitlab, repos, "g/no-marker")
    (no_marker / LAST_USED_MARKER).unlink()

    assert expire_repos(repos, 30, warn=pytest.fail) == (2, 1)
    assert fresh.exists()
    assert not old.exists() and not incoming.exists() and not no_marker.exists()
    assert managed_copies(repos) == [fresh]


def test_expire_repos_disabled_keeps_old_copies_but_drops_incomplete(fake_gitlab, tmp_path):
    repos = tmp_path / "repos"
    old = _copy(fake_gitlab, repos, "g/old", age_days=365)
    (repos / f"{INCOMING_PREFIX}1-x").mkdir()
    assert expire_repos(repos, None, warn=pytest.fail) == (1, 0)
    assert old.exists()


def test_expire_repos_failure_is_a_warning(fake_gitlab, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    _copy(fake_gitlab, repos, "g/old", age_days=40)
    _copy(fake_gitlab, repos, "g/older", age_days=50)

    import review_agent.repo_source as repo_source

    def locked(path):
        if "older" in path.name:
            raise OSError("file is locked")
        return original(path)

    original = repo_source.remove_path
    monkeypatch.setattr(repo_source, "remove_path", locked)
    warnings = []
    assert expire_repos(repos, 30, warn=warnings.append) == (0, 1)
    assert len(warnings) == 1 and "locked" in warnings[0]
