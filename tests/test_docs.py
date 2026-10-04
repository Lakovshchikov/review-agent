"""The documentation keeps up with the CLI (see AGENTS.md section 11).

README.md must mention every flag of every command and every parameter of
scripts/register-task.ps1; relative links from README.md, AGENTS.md and
docs/*.md must point at files that exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from review_agent.cli import build_arg_parser, build_poll_arg_parser, build_usage_arg_parser

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
REGISTER_SCRIPT = ROOT / "scripts" / "register-task.ps1"


def _long_options(parser) -> list[str]:
    return [
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    ]


def _script_parameters() -> list[str]:
    text = REGISTER_SCRIPT.read_text(encoding="utf-8-sig")
    block = re.search(r"^param\((.*?)^\)", text, re.MULTILINE | re.DOTALL)
    assert block, "param(...) block not found in register-task.ps1"
    return re.findall(r"\]\s*\$(\w+)", block.group(1))


def _readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "parser_factory", [build_arg_parser, build_poll_arg_parser, build_usage_arg_parser]
)
def test_readme_mentions_every_cli_flag(parser_factory):
    readme = _readme()
    parser = parser_factory()
    missing = [opt for opt in _long_options(parser) if f"`{opt}" not in readme]
    assert not missing, f"README.md does not mention {parser.prog} flags: {missing}"


def test_readme_mentions_every_register_task_parameter():
    readme = _readme()
    parameters = _script_parameters()
    assert "IntervalMinutes" in parameters  # the regex really parsed the block
    missing = [name for name in parameters if f"`-{name}`" not in readme]
    assert not missing, f"README.md does not mention register-task.ps1 parameters: {missing}"


_LINK = re.compile(r"\]\(([^)\s]+)\)")
# Code blocks and inline code hold examples of links, not links.
_CODE = re.compile(r"```.*?```|``.*?``|`[^`\n]*`", re.DOTALL)


def _documents() -> list[Path]:
    return [README, ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md"))]


@pytest.mark.parametrize("document", _documents(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_point_at_existing_files(document):
    broken = []
    for target in _LINK.findall(_CODE.sub("", document.read_text(encoding="utf-8"))):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue  # external URL or an anchor within the same file
        path = target.split("#", 1)[0]
        if not (document.parent / path).exists():
            broken.append(target)
    assert not broken, f"{document.name}: broken relative links {broken}"
