"""Renders the direct review prompt from a Jinja2 template.

Per AGENTS.md / design.md: the harness's persistent config (harness_config.py)
carries no methodology - it lives in the per-run prompt, and the prompt's
text lives in a template file, never in this module. This module only
supplies the run's values. The built-in template (prompts/default.md.j2)
reproduces, character for character, the baseline prompt validated in
prior research (direct review prompt + minimal prepared context -> best
recall); a project may point to its own template file, which can replace
the prompt or extend the built-in one block by block.

Templates come only from the orchestrator's configuration and the package:
the loader never looks into the reviewed worktree, whose content is
controlled by the MR author.
"""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    PrefixLoader,
    StrictUndefined,
    Undefined,
)

# Name of the built-in template, also how it is extended from a custom one:
# {% extends "builtin/default.md.j2" %}
BUILTIN_TEMPLATE = "builtin/default.md.j2"
_BUILTIN_FILE = "default.md.j2"

DEFAULT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")
DEFAULT_DOCS_DIRS = ("docs", "documentation")

# Every value a template gets; a template that leaves one out is legal but
# warned about (prompt_check.py).
TEMPLATE_VARIABLES = (
    "worktree_path",
    "base_sha",
    "mr_title",
    "mr_description",
    "repo_instructions_path",
    "docs_path",
    "skill_paths",
)


def is_builtin(template: str | None) -> bool:
    return template is None or template == BUILTIN_TEMPLATE


def make_environment(
    template: str | None, undefined: type[Undefined] = StrictUndefined
) -> tuple[Environment, str]:
    """The Jinja2 environment for `template` and the name to load it by.

    The built-in prefix comes first, so `builtin/...` always means the
    package's template even if a `builtin/` folder sits next to a custom one.
    A custom template is loaded from its own folder, which also makes
    `{% include "sibling.md.j2" %}` work.
    """
    loaders: list[Any] = [PrefixLoader({"builtin": PackageLoader("review_agent", "prompts")})]
    if is_builtin(template):
        name = BUILTIN_TEMPLATE
    else:
        path = Path(template)
        loaders.append(FileSystemLoader(str(path.parent)))
        name = path.name
    env = Environment(
        loader=ChoiceLoader(loaders),
        undefined=undefined,
        keep_trailing_newline=True,
        autoescape=False,  # markdown, not HTML
    )
    return env, name


def template_context(
    *,
    worktree_path: Path,
    base_sha: str,
    mr_title: str,
    mr_description: str,
    repo_instructions_path: Path | None = None,
    docs_path: Path | None = None,
    skill_paths: list[Path] | None = None,
) -> dict[str, Any]:
    return {
        "worktree_path": worktree_path,
        "base_sha": base_sha,
        "mr_title": mr_title,
        "mr_description": mr_description,
        "repo_instructions_path": repo_instructions_path,
        "docs_path": docs_path,
        "skill_paths": list(skill_paths or []),
    }


def render_review_prompt(
    *,
    worktree_path: Path,
    base_sha: str,
    mr_title: str,
    mr_description: str,
    repo_instructions_path: Path | None = None,
    docs_path: Path | None = None,
    skill_paths: list[Path] | None = None,
    template: str | None = None,
) -> str:
    """Render the review prompt for one run; `template` None = the built-in one.

    Strict: an unknown variable raises instead of becoming an empty string.
    """
    env, name = make_environment(template)
    return env.get_template(name).render(
        template_context(
            worktree_path=worktree_path,
            base_sha=base_sha,
            mr_title=mr_title,
            mr_description=mr_description,
            repo_instructions_path=repo_instructions_path,
            docs_path=docs_path,
            skill_paths=skill_paths,
        )
    )


def template_label(template: str | None) -> str:
    """How a template is named in the usage ledger and in messages."""
    return BUILTIN_TEMPLATE if is_builtin(template) else str(template)


def template_sha256(template: str | None) -> str | None:
    """SHA-256 of the template file's own content (not of templates it extends).

    None if the file cannot be read.
    """
    try:
        if is_builtin(template):
            data = resources.files("review_agent").joinpath("prompts", _BUILTIN_FILE).read_bytes()
        else:
            data = Path(template).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def discover_repo_instructions_path(
    worktree_path: Path, candidates: tuple[str, ...] | list[str] = DEFAULT_INSTRUCTION_FILES
) -> Path | None:
    """Find the target repository's own instructions file: the first candidate that exists.

    Returns the path only - callers must never read/inline its content
    themselves (see spec requirement "Repository instructions are pointed
    to, not inlined").
    """
    for candidate in candidates:
        path = worktree_path / candidate
        if path.is_file():
            return path
    return None


def discover_docs_path(
    worktree_path: Path, candidates: tuple[str, ...] | list[str] = DEFAULT_DOCS_DIRS
) -> Path | None:
    for candidate in candidates:
        path = worktree_path / candidate
        if path.is_dir():
            return path
    return None
