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


# -- golden reference: the pre-template renderer, kept verbatim ---------------
# The built-in template must reproduce it character for character (AGENTS.md
# section 3: this prompt is the validated baseline).

import itertools
from string import Template

import pytest
from jinja2 import UndefinedError

from review_agent.prompt import BUILTIN_TEMPLATE

_REFERENCE_TEMPLATE = Template(
    """\
Role: Senior Software Engineer.

Task:

Проведи код-ревью изменений в этом репозитории: сравни текущее состояние
(HEAD) с базовым коммитом $base_sha. Рабочая копия уже подготовлена и
находится здесь: $worktree_path.

Контекст изменения:
- Заголовок: $mr_title
- Описание: $mr_description
$repo_context_section
$skills_section
Проверь:
- корректность;
- возможные runtime bugs и регрессии;
- читаемость и поддерживаемость;
- тестируемость;
- безопасность;
- принцип единственной ответственности;
- контракты и интеграции;
- лучшие практики разработки для этого стека.

Отметь ошибки и рискованные места. Для каждого замечания укажи SEV:
Blocker / Major / Minor.

Предложи минимальное исправление. Пример кода добавляй, если он помогает
объяснить исправление.

Не ограничивайся несколькими наиболее очевидными замечаниями. Найди как
можно больше потенциальных проблемных мест во всём изменении и связанном
с ним коде.

Допустимы замечания с неполной уверенностью и потенциальные риски, если
для них есть техническое основание. Лучше явно отметить риск, который
требует дополнительной проверки, чем пропустить потенциальную проблему.

Не изменяй код и не выполняй команды сборки/тестов/линтера.

Output: все результаты на русском языке.
"""
)


def _reference_repo_context(repo_instructions_path, docs_path):
    lines = []
    if repo_instructions_path is not None:
        lines.append(
            f"- Инструкции проекта (обязательно прочитай перед ревью): {repo_instructions_path}"
        )
    if docs_path is not None:
        lines.append(f"- Документация проекта: {docs_path}")
    if not lines:
        return ""
    return "\n" + "\n".join(lines) + "\n"


def _reference_skills(skill_paths):
    if not skill_paths:
        return ""
    paths_block = "\n".join(f"- {path}" for path in skill_paths)
    return (
        "\nСправочник частых паттернов ошибок (используй как подсказку о том, "
        "на что обратить внимание, но НЕ ограничивайся только этими "
        "паттернами и не пропускай другие находки):\n"
        f"{paths_block}\n"
    )


def _reference_render(**kw):
    return _REFERENCE_TEMPLATE.substitute(
        worktree_path=kw["worktree_path"],
        base_sha=kw["base_sha"],
        mr_title=kw["mr_title"] or "(не указан)",
        mr_description=kw["mr_description"] or "(не указано)",
        repo_context_section=_reference_repo_context(kw["repo_instructions_path"], kw["docs_path"]),
        skills_section=_reference_skills(kw["skill_paths"]),
    )


_SKILL_SETS = [[], [Path("/skills/a.md")], [Path("/skills/a.md"), Path("/skills/b.md")]]


@pytest.mark.parametrize(
    "instructions, docs, skills, title, description",
    list(
        itertools.product(
            [None, Path("/wt/AGENTS.md")],
            [None, Path("/wt/docs")],
            range(len(_SKILL_SETS)),
            ["", "Add review form"],
            ["", "Line one\nline two"],
        )
    ),
)
def test_builtin_template_matches_reference(instructions, docs, skills, title, description):
    kw = dict(
        worktree_path=Path("/scratch/run-1/worktree"),
        base_sha="d6a9078821e4",
        mr_title=title,
        mr_description=description,
        repo_instructions_path=instructions,
        docs_path=docs,
        skill_paths=_SKILL_SETS[skills],
    )
    assert render_review_prompt(**kw) == _reference_render(**kw)
    assert render_review_prompt(template=BUILTIN_TEMPLATE, **kw) == _reference_render(**kw)


# -- custom templates ---------------------------------------------------------

_KW = dict(worktree_path=Path("/wt"), base_sha="sha1", mr_title="T", mr_description="D")


def test_custom_template_replaces_prompt(tmp_path):
    template = tmp_path / "own.md.j2"
    template.write_text("Review {{ base_sha }} in {{ worktree_path }}\n", encoding="utf-8")
    rendered = render_review_prompt(template=str(template), **_KW)
    assert rendered == f"Review sha1 in {Path('/wt')}\n"


def test_custom_template_extends_one_block(tmp_path):
    template = tmp_path / "backend.md.j2"
    template.write_text(
        '{% extends "builtin/default.md.j2" %}'
        "{% block checklist %}Проверь транзакции и N+1.{% endblock %}",
        encoding="utf-8",
    )
    builtin = render_review_prompt(**_KW)
    custom = render_review_prompt(template=str(template), **_KW)
    start = builtin.index("Проверь:")
    end = builtin.index("лучшие практики разработки для этого стека.") + len(
        "лучшие практики разработки для этого стека."
    )
    assert custom == builtin[:start] + "Проверь транзакции и N+1." + builtin[end:]


def test_custom_template_includes_sibling(tmp_path):
    (tmp_path / "common.md.j2").write_text("Common part for {{ base_sha }}.", encoding="utf-8")
    template = tmp_path / "main.md.j2"
    template.write_text('Head.\n{% include "common.md.j2" %}\n', encoding="utf-8")
    assert render_review_prompt(template=str(template), **_KW) == "Head.\nCommon part for sha1.\n"


def test_strict_render_fails_on_unknown_variable(tmp_path):
    template = tmp_path / "typo.md.j2"
    template.write_text("{{ base_shaa }}", encoding="utf-8")
    with pytest.raises(UndefinedError):
        render_review_prompt(template=str(template), **_KW)


def test_template_never_loaded_from_worktree(tmp_path):
    configured = tmp_path / "config-dir"
    configured.mkdir()
    (configured / "prompt.md.j2").write_text("configured", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "prompt.md.j2").write_text("from MR", encoding="utf-8")
    rendered = render_review_prompt(
        template=str(configured / "prompt.md.j2"),
        worktree_path=worktree,
        base_sha="s",
        mr_title="",
        mr_description="",
    )
    assert rendered == "configured"


# -- configurable candidates --------------------------------------------------


def test_discover_repo_instructions_custom_candidates(tmp_path):
    (tmp_path / "CONTRIBUTING.md").write_text("c", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("a", encoding="utf-8")
    found = discover_repo_instructions_path(tmp_path, ["CONTRIBUTING.md", "AGENTS.md"])
    assert found == tmp_path / "CONTRIBUTING.md"


def test_discover_with_empty_candidates(tmp_path):
    (tmp_path / "AGENTS.md").write_text("a", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    assert discover_repo_instructions_path(tmp_path, []) is None
    assert discover_docs_path(tmp_path, []) is None


def test_discover_docs_custom_candidates(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "wiki").mkdir()
    assert discover_docs_path(tmp_path, ["wiki", "docs"]) == tmp_path / "wiki"
    assert discover_docs_path(tmp_path, ["absent"]) is None
