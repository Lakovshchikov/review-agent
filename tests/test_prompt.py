import re
from pathlib import Path

from review_agent.prompt import (
    discover_docs_path,
    discover_repo_instructions_path,
    render_review_prompt,
)


def test_render_review_prompt_substitutes_all_placeholders():
    worktree_path = Path("/scratch/run-1/worktree")
    repo_instructions_path = Path("/scratch/run-1/worktree/AGENTS.md")
    docs_path = Path("/scratch/run-1/worktree/docs")

    rendered = render_review_prompt(
        worktree_path=worktree_path,
        base_sha="abc123",
        mr_title="Add review form",
        mr_description="Implements the review creation flow",
        repo_instructions_path=repo_instructions_path,
        docs_path=docs_path,
    )

    # Paths render platform-native (backslashes on Windows) - compare
    # against str(Path(...)), not a hardcoded POSIX literal.
    assert str(worktree_path) in rendered
    assert "abc123" in rendered
    assert "Add review form" in rendered
    assert "Implements the review creation flow" in rendered
    assert str(repo_instructions_path) in rendered
    assert str(docs_path) in rendered
    # No unresolved $placeholder left behind.
    assert not re.search(r"\$\w+", rendered)


def test_render_review_prompt_handles_missing_repo_context():
    rendered = render_review_prompt(
        worktree_path=Path("/scratch/run-1/worktree"),
        base_sha="abc123",
        mr_title="",
        mr_description="",
        repo_instructions_path=None,
        docs_path=None,
    )

    assert not re.search(r"\$\w+", rendered)
    assert "(не указан)" in rendered


def test_skills_section_present_only_when_configured():
    without_skills = render_review_prompt(
        worktree_path=Path("/wt"),
        base_sha="sha",
        mr_title="t",
        mr_description="d",
    )
    assert "Справочник частых паттернов" not in without_skills

    skill_path = Path("/skills/frontend-common-mistakes.md")
    with_skills = render_review_prompt(
        worktree_path=Path("/wt"),
        base_sha="sha",
        mr_title="t",
        mr_description="d",
        skill_paths=[skill_path],
    )
    assert "Справочник частых паттернов" in with_skills
    assert str(skill_path) in with_skills
    assert "НЕ ограничивайся только этими" in with_skills


def test_discover_repo_instructions_path_priority(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("claude", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")

    assert discover_repo_instructions_path(tmp_path) == tmp_path / "AGENTS.md"


def test_discover_repo_instructions_path_absent(tmp_path):
    assert discover_repo_instructions_path(tmp_path) is None


def test_discover_docs_path(tmp_path):
    (tmp_path / "docs").mkdir()
    assert discover_docs_path(tmp_path) == tmp_path / "docs"


def test_discover_docs_path_absent(tmp_path):
    assert discover_docs_path(tmp_path) is None
