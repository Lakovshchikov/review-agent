"""Checks prompt templates before any review work (docs/prompt.md).

A template is rendered with sample values, not parsed: static analysis
(jinja2.meta) cannot see variables used only by an extended parent
template, nor tell what actually reaches the rendered text.

- Errors (the work must not start): the template file - or a template it
  includes/extends - is missing, a syntax error (Jinja2 stops at the
  first one, so a file gets at most one), an unknown variable or
  attribute. Unknown names are collected by an Undefined that records
  instead of raising, so ALL of them are reported in one go; real
  reviews keep rendering strictly (prompt.py).
- Warnings (the work may go on): a template variable whose sample value
  never reaches the rendered text, a configured skill file that does not
  exist.

Pure check logic plus text formatting; what to do with the result
(stop, ask, log) is the caller's business (polling.py, cli.py).
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from jinja2 import ChainableUndefined, TemplateNotFound, TemplateSyntaxError
from jinja2.utils import missing

from review_agent.config import Config, GitLabProjectConfig, PromptSettings, effective_settings
from review_agent.prompt import (
    TEMPLATE_VARIABLES,
    is_builtin,
    make_environment,
    template_context,
    template_label,
)

ERROR = "error"
WARNING = "warning"

EXIT_CLEAN = 0
EXIT_WARNINGS = 1
EXIT_ERRORS = 2

GLOBAL_LABEL = "глобальные настройки"

_MARK = "RACHECK"


@dataclasses.dataclass(frozen=True)
class PromptIssue:
    level: str  # ERROR | WARNING
    message: str


@dataclasses.dataclass
class TemplateReport:
    """One distinct (template, skills) combination and everyone who uses it."""

    template: str | None
    skills: tuple[str, ...]
    users: list[str]
    issues: list[PromptIssue]
    # The prompt rendered with readable sample values; None if it cannot render.
    preview: str | None = None

    @property
    def label(self) -> str:
        return template_label(self.template)


@dataclasses.dataclass
class CheckResult:
    reports: list[TemplateReport]

    @property
    def errors(self) -> list[PromptIssue]:
        return [i for r in self.reports for i in r.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[PromptIssue]:
        return [i for r in self.reports for i in r.issues if i.level == WARNING]

    @property
    def exit_code(self) -> int:
        if self.errors:
            return EXIT_ERRORS
        return EXIT_WARNINGS if self.warnings else EXIT_CLEAN

    def format(self, *, only_problems: bool = False) -> str:
        """Human-readable report; `only_problems` leaves out clean templates."""
        lines = ["Проверка шаблонов промпта:"]
        for report in self.reports:
            if only_problems and not report.issues:
                continue
            lines.append(f"  {report.label}  ({', '.join(report.users)})")
            if not report.issues:
                lines.append("    OK")
            for issue in report.issues:
                tag = "ERROR  " if issue.level == ERROR else "WARNING"
                lines.append(f"    {tag} {issue.message}")
        lines.append(f"Итого: ошибок {len(self.errors)}, предупреждений {len(self.warnings)}.")
        return "\n".join(lines)


# -- one template ---------------------------------------------------------------


def _token(name: str) -> str:
    return f"{name}__{_MARK}"


def _marked_context() -> dict[str, Any]:
    """Every variable gets a value carrying a unique token; optional ones are non-empty
    so that conditional sections render too. Paths keep the token in their
    name, so `{{ path.name }}` still counts as use."""
    base = Path("/") / _MARK
    return template_context(
        worktree_path=base / _token("worktree_path"),
        base_sha=_token("base_sha"),
        mr_title=_token("mr_title"),
        mr_description=_token("mr_description"),
        repo_instructions_path=base / _token("repo_instructions_path"),
        docs_path=base / _token("docs_path"),
        skill_paths=[base / _token("skill_paths")],
    )


def _sample_context(skills: Iterable[str], prompt: PromptSettings) -> dict[str, Any]:
    """Readable values for --show, as if the first candidate of each list were found."""
    worktree = Path("/review-agent/tmp/<run_id>/worktree")
    return template_context(
        worktree_path=worktree,
        base_sha="0123456789abcdef0123456789abcdef01234567",
        mr_title="Пример заголовка MR",
        mr_description="Пример описания MR",
        repo_instructions_path=worktree / prompt.instruction_files[0] if prompt.instruction_files else None,
        docs_path=worktree / prompt.docs_dirs[0] if prompt.docs_dirs else None,
        skill_paths=[Path(s) for s in skills],
    )


def _recording_undefined(found: list[str]) -> type[ChainableUndefined]:
    """An Undefined that notes every use of an unknown name instead of failing.

    Only USE counts (printing, testing truth, iterating...): `x is defined`
    and `x | default(...)` are legitimate and never recorded.
    """

    class Recording(ChainableUndefined):
        def _note(self) -> None:
            if self._undefined_obj is missing:
                name = f"неизвестная переменная: {self._undefined_name}"
            else:
                owner = type(self._undefined_obj).__name__
                name = f"неизвестный атрибут: {self._undefined_name} (у значения типа {owner})"
            if name not in found:
                found.append(name)

        def __str__(self) -> str:
            self._note()
            return ""

        def __iter__(self):  # type: ignore[override]
            self._note()
            return iter(())

        def __bool__(self) -> bool:
            self._note()
            return False

        def __len__(self) -> int:
            self._note()
            return 0

        def __call__(self, *args: Any, **kwargs: Any) -> "Recording":
            # `{{ unknown.upper() }}`: note it and keep rendering.
            self._note()
            return self

    return Recording


def check_template(
    template: str | None,
    skills: Iterable[str] = (),
    *,
    want_preview: bool = False,
    prompt: PromptSettings | None = None,
) -> tuple[list[PromptIssue], str | None]:
    """All problems of one template (+ its skill files), and optionally a preview.

    `prompt` only shapes the preview (its candidate lists); None = defaults.
    """
    issues: list[PromptIssue] = []
    skills = list(skills)
    if not is_builtin(template) and not os.path.isfile(template):
        issues.append(PromptIssue(ERROR, f"файл шаблона не найден: {template}"))
        return issues + _skill_issues(skills), None

    unknown: list[str] = []
    env, name = make_environment(template, undefined=_recording_undefined(unknown))
    rendered: str | None = None
    try:
        rendered = env.get_template(name).render(_marked_context())
    except TemplateSyntaxError as exc:
        where = f"{exc.filename or exc.name or name}, строка {exc.lineno}"
        issues.append(PromptIssue(ERROR, f"синтаксическая ошибка ({where}): {exc.message}"))
    except TemplateNotFound as exc:
        issues.append(PromptIssue(ERROR, f"не найден подключаемый шаблон: {exc.name}"))
    except Exception as exc:  # noqa: BLE001 - any render failure is the template's error
        issues.append(PromptIssue(ERROR, f"ошибка рендера: {type(exc).__name__}: {exc}"))
    issues.extend(PromptIssue(ERROR, message) for message in unknown)

    if rendered is not None:
        for variable in TEMPLATE_VARIABLES:
            if _token(variable) not in rendered:
                issues.append(
                    PromptIssue(
                        WARNING,
                        f"шаблон не использует переменную {variable} — агент не получит это значение",
                    )
                )
    issues.extend(_skill_issues(skills))

    preview = None
    if want_preview and not any(i.level == ERROR for i in issues):
        strict_env, strict_name = make_environment(template)
        preview = strict_env.get_template(strict_name).render(
            _sample_context(skills, prompt or PromptSettings())
        )
    return issues, preview


def _skill_issues(skills: list[str]) -> list[PromptIssue]:
    return [
        PromptIssue(WARNING, f"файл skill не найден: {skill}")
        for skill in skills
        if not os.path.isfile(skill)
    ]


# -- several users ----------------------------------------------------------------


def check_settings(
    targets: Iterable[tuple[str, PromptSettings, list[str]]], *, want_preview: bool = False
) -> CheckResult:
    """Check (user label, prompt settings, skills) triples; identical combinations once.

    Candidate lists are not checked: they are looked up in the reviewed
    repository, and an absent candidate is a normal case. They only shape
    the preview, so with `want_preview` they also tell combinations apart.
    """
    reports: dict[tuple[Any, ...], TemplateReport] = {}
    for user, prompt, skills in targets:
        key: tuple[Any, ...] = (prompt.template, tuple(skills))
        if want_preview:
            key += (tuple(prompt.instruction_files), tuple(prompt.docs_dirs))
        if key in reports:
            reports[key].users.append(user)
            continue
        issues, preview = check_template(
            prompt.template, skills, want_preview=want_preview, prompt=prompt
        )
        reports[key] = TemplateReport(
            template=prompt.template, skills=key[1], users=[user], issues=issues, preview=preview
        )
    return CheckResult(reports=list(reports.values()))


Target = tuple[str, PromptSettings, list[str]]


def global_target(config: Config) -> Target:
    return (GLOBAL_LABEL, config.prompt, config.skills)


def project_targets(config: Config, projects: Iterable[GitLabProjectConfig]) -> list[Target]:
    targets = []
    for project in projects:
        settings = effective_settings(config, project).config
        targets.append((project.path, settings.prompt, settings.skills))
    return targets


_YES = ("y", "yes", "д", "да", "н")  # "н" is "y" typed in the Russian layout


def may_proceed(
    result: CheckResult,
    *,
    ask: Callable[[str], str] | None,
    show_error: Callable[[str], object],
    show_warning: Callable[[str], object],
) -> bool:
    """The before-work gate shared by `poll` and the manual command.

    Errors: report everything, never proceed. Only warnings: report
    everything, then one question when `ask` is given (interactive), or
    just go on (automatic mode, no console).
    """
    if result.errors:
        show_error(
            result.format(only_problems=True)
            + "\nРабота не начата: исправьте ошибки в шаблонах промпта "
            "(проверка — review-agent prompt-check)."
        )
        return False
    if not result.warnings:
        return True
    show_warning(result.format(only_problems=True))
    if ask is None:
        return True
    try:
        answer = ask("Продолжить с этими предупреждениями? [y/N]: ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in _YES
