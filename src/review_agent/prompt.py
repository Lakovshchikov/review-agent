"""Renders the direct review prompt - the only place review methodology lives.

Per AGENTS.md / design.md: the harness's persistent config (harness_config.py)
carries no methodology. This module renders the baseline prompt validated in
prior research (direct review prompt + minimal prepared context -> best
recall), substituting this run's variables. Knowledge-skill content, if any
is configured, is attached here as optional reference material, never as a
limiting checklist.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

_TEMPLATE = Template(
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


def _render_repo_context_section(
    repo_instructions_path: Path | None, docs_path: Path | None
) -> str:
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


def _render_skills_section(skill_paths: list[Path]) -> str:
    if not skill_paths:
        return ""
    paths_block = "\n".join(f"- {path}" for path in skill_paths)
    return (
        "\nСправочник частых паттернов ошибок (используй как подсказку о том, "
        "на что обратить внимание, но НЕ ограничивайся только этими "
        "паттернами и не пропускай другие находки):\n"
        f"{paths_block}\n"
    )


def render_review_prompt(
    *,
    worktree_path: Path,
    base_sha: str,
    mr_title: str,
    mr_description: str,
    repo_instructions_path: Path | None = None,
    docs_path: Path | None = None,
    skill_paths: list[Path] | None = None,
) -> str:
    """Render the direct review prompt for one run."""
    return _TEMPLATE.substitute(
        worktree_path=worktree_path,
        base_sha=base_sha,
        mr_title=mr_title or "(не указан)",
        mr_description=mr_description or "(не указано)",
        repo_context_section=_render_repo_context_section(repo_instructions_path, docs_path),
        skills_section=_render_skills_section(skill_paths or []),
    )


_INSTRUCTION_FILE_CANDIDATES = ["AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md"]
_DOCS_DIR_CANDIDATES = ["docs", "documentation"]


def discover_repo_instructions_path(worktree_path: Path) -> Path | None:
    """Find the target repository's own instructions file, by priority.

    Returns the path only - callers must never read/inline its content
    themselves (see spec requirement "Repository instructions are pointed
    to, not inlined").
    """
    for candidate in _INSTRUCTION_FILE_CANDIDATES:
        path = worktree_path / candidate
        if path.is_file():
            return path
    return None


def discover_docs_path(worktree_path: Path) -> Path | None:
    for candidate in _DOCS_DIR_CANDIDATES:
        path = worktree_path / candidate
        if path.is_dir():
            return path
    return None
