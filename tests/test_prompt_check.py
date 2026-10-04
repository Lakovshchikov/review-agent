from pathlib import Path

from review_agent.config import PromptSettings
from review_agent.prompt import BUILTIN_TEMPLATE, TEMPLATE_VARIABLES
from review_agent.prompt_check import (
    ERROR,
    EXIT_CLEAN,
    EXIT_ERRORS,
    EXIT_WARNINGS,
    WARNING,
    check_settings,
    check_template,
)

# Uses every variable; the reference point for "clean" custom templates.
_FULL = (
    "{{ worktree_path }} {{ base_sha }} {{ mr_title }} {{ mr_description }}\n"
    "{% if repo_instructions_path %}{{ repo_instructions_path }}{% endif %}\n"
    "{% if docs_path %}{{ docs_path }}{% endif %}\n"
    "{% for p in skill_paths %}{{ p }}{% endfor %}\n"
)


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _messages(issues, level):
    return [i.message for i in issues if i.level == level]


def test_builtin_template_is_clean():
    issues, _ = check_template(None)
    assert issues == []
    issues, _ = check_template(BUILTIN_TEMPLATE)
    assert issues == []


def test_full_custom_template_is_clean(tmp_path):
    issues, _ = check_template(_write(tmp_path, "t.md.j2", _FULL))
    assert issues == []


def test_all_unknown_variables_and_unused_ones_in_one_result(tmp_path):
    text = _FULL.replace("{{ worktree_path }}", "{{ base_shaa }} {{ mr_titel }} {{ base_shaa }}")
    issues, _ = check_template(_write(tmp_path, "t.md.j2", text))
    assert _messages(issues, ERROR) == [
        "неизвестная переменная: base_shaa",
        "неизвестная переменная: mr_titel",
    ]
    assert len(_messages(issues, WARNING)) == 1
    assert "worktree_path" in _messages(issues, WARNING)[0]


def test_unknown_names_in_conditions_loops_and_calls(tmp_path):
    text = _FULL + "{% if flag %}x{% endif %}{% for i in items %}{% endfor %}{{ name.upper() }}{{ base_sha.nope }}"
    errors = _messages(check_template(_write(tmp_path, "t.md.j2", text))[0], ERROR)
    assert errors[:3] == [
        "неизвестная переменная: flag",
        "неизвестная переменная: items",
        "неизвестная переменная: name",
    ]
    assert errors[3].startswith("неизвестный атрибут: nope")


def test_defined_test_and_default_filter_are_not_errors(tmp_path):
    text = _FULL + "{% if extra is defined %}{{ extra }}{% endif %}{{ other | default('') }}"
    assert check_template(_write(tmp_path, "t.md.j2", text))[0] == []


def test_variable_only_in_conditional_section_counts_as_used(tmp_path):
    # _FULL prints skill_paths only inside a loop and docs_path inside an if.
    issues, _ = check_template(_write(tmp_path, "t.md.j2", _FULL))
    assert not _messages(issues, WARNING)


def test_variable_used_only_in_a_condition_is_unused(tmp_path):
    text = _FULL.replace("{% if docs_path %}{{ docs_path }}{% endif %}", "{% if docs_path %}читай docs{% endif %}")
    warnings = _messages(check_template(_write(tmp_path, "t.md.j2", text))[0], WARNING)
    assert len(warnings) == 1 and "docs_path" in warnings[0]


def test_path_attribute_counts_as_use(tmp_path):
    text = _FULL.replace("{{ worktree_path }}", "{{ worktree_path.name }}")
    assert check_template(_write(tmp_path, "t.md.j2", text))[0] == []


def test_variable_used_only_in_extended_parent_is_used(tmp_path):
    path = _write(
        tmp_path,
        "child.md.j2",
        '{% extends "builtin/default.md.j2" %}{% block checklist %}Своё.{% endblock %}',
    )
    assert check_template(path)[0] == []


def test_block_override_dropping_a_variable_warns(tmp_path):
    path = _write(
        tmp_path,
        "child.md.j2",
        '{% extends "builtin/default.md.j2" %}{% block task %}Ревью.{% endblock %}',
    )
    warnings = _messages(check_template(path)[0], WARNING)
    assert [w.split()[4] for w in warnings] == ["worktree_path", "base_sha"]


def test_every_variable_missing_gives_one_warning_each(tmp_path):
    warnings = _messages(check_template(_write(tmp_path, "t.md.j2", "Пусто."))[0], WARNING)
    assert len(warnings) == len(TEMPLATE_VARIABLES)


def test_syntax_error_is_the_only_error_for_the_file(tmp_path):
    issues, _ = check_template(_write(tmp_path, "t.md.j2", "{% if x %}open\n{{ a }} {{ b }}"))
    assert len(issues) == 1 and issues[0].level == ERROR
    assert issues[0].message.startswith("синтаксическая ошибка")


def test_missing_template_file(tmp_path):
    issues, _ = check_template(str(tmp_path / "absent.md.j2"))
    assert _messages(issues, ERROR) == [f"файл шаблона не найден: {tmp_path / 'absent.md.j2'}"]


def test_missing_include(tmp_path):
    issues, _ = check_template(_write(tmp_path, "t.md.j2", '{% include "absent.md.j2" %}'))
    assert _messages(issues, ERROR) == ["не найден подключаемый шаблон: absent.md.j2"]


def test_missing_skill_file_is_a_warning(tmp_path):
    present = _write(tmp_path, "present.md", "skill")
    absent = str(tmp_path / "absent.md")
    issues, _ = check_template(None, [present, absent])
    assert issues == [i for i in issues if i.level == WARNING]
    assert _messages(issues, WARNING) == [f"файл skill не найден: {absent}"]


def test_same_settings_checked_once_for_several_users(tmp_path):
    broken = _write(tmp_path, "broken.md.j2", "{{ nope }}")
    result = check_settings(
        [
            ("глобальные настройки", PromptSettings(), []),
            ("a/b", PromptSettings(template=broken), []),
            ("c/d", PromptSettings(template=broken, instruction_files=["X.md"]), []),
            ("e/f", PromptSettings(), []),
        ]
    )
    assert [r.users for r in result.reports] == [["глобальные настройки", "e/f"], ["a/b", "c/d"]]
    assert len(result.errors) == 1
    assert result.exit_code == EXIT_ERRORS
    text = result.format()
    assert "broken.md.j2" in text and "a/b, c/d" in text and "OK" in text
    assert "    ERROR   неизвестная переменная: nope" in text


def test_exit_codes(tmp_path):
    assert check_settings([("g", PromptSettings(), [])]).exit_code == EXIT_CLEAN
    unused = _write(tmp_path, "u.md.j2", "{{ base_sha }}")
    assert check_settings([("g", PromptSettings(template=unused), [])]).exit_code == EXIT_WARNINGS


def test_format_only_problems(tmp_path):
    unused = _write(tmp_path, "u.md.j2", _FULL.replace("{{ mr_title }}", ""))
    result = check_settings([("g", PromptSettings(), []), ("a/b", PromptSettings(template=unused), [])])
    text = result.format(only_problems=True)
    assert "builtin" not in text and "u.md.j2" in text and "mr_title" in text


def test_preview_renders_readable_values(tmp_path):
    skill = _write(tmp_path, "skill.md", "s")
    result = check_settings([("g", PromptSettings(), [skill])], want_preview=True)
    preview = result.reports[0].preview
    assert preview.startswith("Role: Senior Software Engineer.")
    assert "Пример заголовка MR" in preview and skill in preview
    assert "RACHECK" not in preview


def test_no_preview_when_template_has_errors(tmp_path):
    broken = _write(tmp_path, "b.md.j2", "{{ nope }}")
    result = check_settings([("g", PromptSettings(template=broken), [])], want_preview=True)
    assert result.reports[0].preview is None


def test_preview_follows_the_candidate_lists():
    prompt = PromptSettings(instruction_files=["README.md"], docs_dirs=[])
    result = check_settings([("g", prompt, []), ("h", PromptSettings(), [])], want_preview=True)
    first, second = (r.preview for r in result.reports)
    assert "README.md" in first and "Документация проекта" not in first
    assert "AGENTS.md" in second and "Документация проекта" in second
