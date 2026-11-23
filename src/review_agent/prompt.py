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

import dataclasses
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

# The template of a further review round (`poll --again`); it extends the
# first-review one. Same rules: a custom file, or this name for "built-in".
BUILTIN_REREVIEW_TEMPLATE = "builtin/rereview.md.j2"
_BUILTIN_REREVIEW_FILE = "rereview.md.j2"
DEFAULT_REREVIEW_MAX_REPLY_CHARS = 20000

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


# Extra values of a further round's template, on top of TEMPLATE_VARIABLES.
REREVIEW_VARIABLES = (
    "previous_report",
    "previous_sha",
    "head_sha",
    "replies",
    "replies_truncated",
)


def is_builtin(template: str | None, *, rereview: bool = False) -> bool:
    builtin = BUILTIN_REREVIEW_TEMPLATE if rereview else BUILTIN_TEMPLATE
    return template is None or template == builtin


def make_environment(
    template: str | None, undefined: type[Undefined] = StrictUndefined, *, rereview: bool = False
) -> tuple[Environment, str]:
    """The Jinja2 environment for `template` and the name to load it by.

    The built-in prefix comes first, so `builtin/...` always means the
    package's template even if a `builtin/` folder sits next to a custom one.
    A custom template is loaded from its own folder, which also makes
    `{% include "sibling.md.j2" %}` work.
    """
    loaders: list[Any] = [PrefixLoader({"builtin": PackageLoader("review_agent", "prompts")})]
    if is_builtin(template, rereview=rereview):
        name = BUILTIN_REREVIEW_TEMPLATE if rereview else BUILTIN_TEMPLATE
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


@dataclasses.dataclass(frozen=True)
class PromptReply:
    """One comment of someone else after the previous report, as a template sees it."""

    author: str
    created_at: str
    body: str


@dataclasses.dataclass(frozen=True)
class RereviewContext:
    """What a further review round adds to the prompt (design.md decision 3).

    All of it goes into the prompt text, never behind a file path: the
    agent cannot read outside its worktree and has no GitLab access.
    `previous_sha` is None when that commit is not available (force-push).
    """

    previous_report: str
    previous_sha: str | None
    head_sha: str
    replies: list[PromptReply] = dataclasses.field(default_factory=list)
    replies_truncated: bool = False


def limit_replies(replies: list[Any], max_chars: int) -> tuple[list[PromptReply], bool]:
    """The most recent replies that fit `max_chars`, oldest first, and whether any were left out.

    A reply counts by its author, time and text. If even the newest one
    alone does not fit, it is kept with its text cut to the limit - the
    latest word of the developer is never dropped entirely.
    """
    kept: list[PromptReply] = []
    used = 0
    for reply in reversed(replies):
        size = len(reply.author) + len(reply.created_at) + len(reply.body)
        if used + size > max_chars:
            break
        kept.append(PromptReply(reply.author, reply.created_at, reply.body))
        used += size
    cut = False
    if not kept and replies:
        newest = replies[-1]
        room = max(max_chars - len(newest.author) - len(newest.created_at), 0)
        kept.append(PromptReply(newest.author, newest.created_at, newest.body[:room] + "…"))
        cut = True
    kept.reverse()
    return kept, cut or len(kept) < len(replies)


def template_context(
    *,
    worktree_path: Path,
    base_sha: str,
    mr_title: str,
    mr_description: str,
    repo_instructions_path: Path | None = None,
    docs_path: Path | None = None,
    skill_paths: list[Path] | None = None,
    rereview: RereviewContext | None = None,
) -> dict[str, Any]:
    context = {
        "worktree_path": worktree_path,
        "base_sha": base_sha,
        "mr_title": mr_title,
        "mr_description": mr_description,
        "repo_instructions_path": repo_instructions_path,
        "docs_path": docs_path,
        "skill_paths": list(skill_paths or []),
    }
    if rereview is not None:
        context.update(
            previous_report=rereview.previous_report,
            previous_sha=rereview.previous_sha,
            head_sha=rereview.head_sha,
            replies=list(rereview.replies),
            replies_truncated=rereview.replies_truncated,
        )
    return context


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


def render_rereview_prompt(
    *,
    worktree_path: Path,
    base_sha: str,
    mr_title: str,
    mr_description: str,
    rereview: RereviewContext,
    repo_instructions_path: Path | None = None,
    docs_path: Path | None = None,
    skill_paths: list[Path] | None = None,
    template: str | None = None,
) -> str:
    """The prompt of a further review round; `template` None = the built-in re-review one."""
    env, name = make_environment(template, rereview=True)
    return env.get_template(name).render(
        template_context(
            worktree_path=worktree_path,
            base_sha=base_sha,
            mr_title=mr_title,
            mr_description=mr_description,
            repo_instructions_path=repo_instructions_path,
            docs_path=docs_path,
            skill_paths=skill_paths,
            rereview=rereview,
        )
    )


def template_label(template: str | None, *, rereview: bool = False) -> str:
    """How a template is named in the usage ledger and in messages."""
    if is_builtin(template, rereview=rereview):
        return BUILTIN_REREVIEW_TEMPLATE if rereview else BUILTIN_TEMPLATE
    return str(template)


def template_sha256(template: str | None, *, rereview: bool = False) -> str | None:
    """SHA-256 of the template file's own content (not of templates it extends).

    None if the file cannot be read.
    """
    try:
        if is_builtin(template, rereview=rereview):
            file = _BUILTIN_REREVIEW_FILE if rereview else _BUILTIN_FILE
            data = resources.files("review_agent").joinpath("prompts", file).read_bytes()
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
