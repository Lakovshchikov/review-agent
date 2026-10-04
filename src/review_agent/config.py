"""Loading and validating the review engine's YAML configuration.

The config is the only place a model provider, its reasoning effort, the
harness command, the report output path, and attached knowledge-skill files
are set - nothing here is hardcoded in the rest of the engine, so switching
providers or harnesses never requires a code change.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import yaml

from review_agent.prompt import BUILTIN_TEMPLATE, DEFAULT_DOCS_DIRS, DEFAULT_INSTRUCTION_FILES


class ConfigError(ValueError):
    """Raised when the configuration file is missing required fields or is malformed."""


@dataclasses.dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    # Explicit per spec requirement "Configurable model provider and
    # reasoning effort" - but verified against a real OpenCode install:
    # the "#variant" model suffix this maps to only exists for models
    # that define variants (e.g. Anthropic/OpenAI cloud models). A local
    # model (Ollama etc.) without variants errors with "Variant
    # unavailable" if any suffix is appended - set this to null/omit it
    # for such providers to explicitly document "not applicable", rather
    # than silently defaulting.
    reasoning_effort: str | None = None
    api_key_env: str | None = None


@dataclasses.dataclass(frozen=True)
class HarnessConfig:
    # argv template; placeholders substituted by the harness adapter:
    # {model} (provider/model#reasoning_effort), {prompt} (inline text),
    # {prompt_file} (path, for harnesses that prefer a file), {agent}
    # (name of the generated restricted agent, see harness_config.py)
    command: list[str]
    agent_name: str = "reviewer"


@dataclasses.dataclass(frozen=True)
class ReportConfig:
    # output path template; placeholders: {run_id}
    output_path: str


@dataclasses.dataclass(frozen=True)
class SafetyConfig:
    """Read-only enforcement config.

    An allowlist-with-catch-all-deny was the original design (deny-by-
    default is safer in principle than a denylist, which a slightly
    different invocation can bypass) - but verified live against a real
    OpenCode v2.0.21 install with a real cloud provider: adding a `"*":
    "deny"` entry to the `bash` permission pattern map makes the bash
    tool entirely UNAVAILABLE, even for explicitly allowed patterns, not
    just filtered. Without any catch-all, an unmatched command is
    implicitly ALLOWED by default - so a true allowlist is not currently
    achievable through this mechanism.

    `denied_bash_patterns` is therefore a DENYLIST (weaker in principle -
    a sufficiently different invocation can evade a specific pattern -
    but this is what OpenCode's real behavior supports): verified live
    that an explicitly denied pattern (`npm *`) is genuinely blocked
    ("Permission denied: shell", no side effect), while unlisted
    read-only git commands still execute normally. Patterns are matched
    against the full command line (see harness_config.py).
    """

    output_language: str = "ru"
    denied_bash_patterns: list[str] = dataclasses.field(
        default_factory=lambda: [
            "npm *",
            "npx *",
            "yarn *",
            "pnpm *",
            "bun *",
            "*test*",
            "*build*",
            "*lint*",
            "tsc*",
            "node *",
            "python *",
            "python3 *",
            "ruby *",
            "rm *",
            "curl *",
            "wget *",
            "sh *",
            "bash *",
            "powershell *",
            "cmd *",
        ]
    )


@dataclasses.dataclass(frozen=True)
class PromptSettings:
    """What the review prompt is built from (prompt.py, docs/prompt.md).

    `template` is an absolute path, or None for the built-in template.
    The candidate lists are relative to the reviewed repository's root;
    the first existing entry is pointed to in the prompt.
    """

    template: str | None = None
    instruction_files: list[str] = dataclasses.field(
        default_factory=lambda: list(DEFAULT_INSTRUCTION_FILES)
    )
    docs_dirs: list[str] = dataclasses.field(default_factory=lambda: list(DEFAULT_DOCS_DIRS))


@dataclasses.dataclass(frozen=True)
class PromptOverrides:
    """A `prompt` section as written: None = "not set, inherit", per key.

    `template` is BUILTIN_TEMPLATE when the section explicitly sets
    `template: null` (back to the built-in one), unlike an absent key.
    """

    template: str | None = None
    instruction_files: list[str] | None = None
    docs_dirs: list[str] | None = None


DEFAULT_WORK_DIR = "./.review-agent"
DEFAULT_RETENTION_DAYS = 7
DEFAULT_REPO_RETENTION_DAYS = 30
DEFAULT_CLAIM_TTL_MINUTES = 240


@dataclasses.dataclass(frozen=True)
class StorageConfig:
    """Where review-agent writes anything at all, and for how long it keeps it.

    Everything (worktrees, prompts, reports, request bodies, the lock,
    pass logs, dry-run output, debug artifacts) lives under `work_dir` -
    see housekeeping.WorkDir for the layout. Only the manual command's
    final report goes elsewhere (report.output_path).
    """

    work_dir: str = DEFAULT_WORK_DIR
    # Age limit for retained artifacts (logs/, dry-run/, debug/); None
    # disables age-based deletion. Transient files are deleted right
    # after each review regardless.
    retention_days: int | None = DEFAULT_RETENTION_DAYS
    # Age limit for managed repository copies (<work_dir>/repos/, see
    # repo_source.py), counted from their last use by a review; None
    # disables age-based deletion.
    repo_retention_days: int | None = DEFAULT_REPO_RETENTION_DAYS


DEFAULT_PRICE_CATALOG = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
# Per-token price fields of a LiteLLM catalog entry - the only ones the
# usage summary reads (see usage_prices.py). Overrides use the same names
# so an entry can be copied from the catalog as is.
PRICE_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
)


@dataclasses.dataclass(frozen=True)
class UsageConfig:
    """Usage accounting: what each review run cost (see usage_ledger.py).

    Collecting usage never affects a review; `enabled: false` turns off
    both collection and the interactive quota questions.
    """

    enabled: bool = True
    # Where token usage comes from: "opencode" (session export) or "none".
    source: str = "opencode"
    session_list_command: list[str] = dataclasses.field(
        default_factory=lambda: ["opencode", "session", "list"]
    )
    session_export_command: list[str] = dataclasses.field(
        default_factory=lambda: ["opencode", "session", "export", "{session_id}"]
    )
    # Subscription limit windows asked about in an interactive single-MR run.
    quota_windows: list[str] = dataclasses.field(default_factory=lambda: ["5h", "week"])
    # URL or local path of a LiteLLM-format price catalog, re-read by
    # every `review-agent usage`; never used during reviews.
    price_catalog: str = DEFAULT_PRICE_CATALOG
    # "provider/model" -> catalog-format entry; replaces the catalog entry.
    price_overrides: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class GitLabProjectConfig:
    # GitLab project path, e.g. "b2c/front-shopping"
    path: str
    # Local clone the review worktrees are created from. Never modified
    # beyond fetching the MR's branches (see repo_source.py). None = use a
    # managed copy in <work_dir>/repos/ instead.
    local_repo: str | None = None
    # Remote of `local_repo` pointing at this project; only valid together
    # with `local_repo` (a managed copy always uses "origin").
    remote: str | None = None
    enabled: bool = True
    # Per-project overrides: None = "not set, use the global value". A
    # set value REPLACES the global one as a whole (no merging), so
    # `skills: []` means "explicitly no skills" for this project.
    reviewers: list[str] | None = None
    review_drafts: bool | None = None
    provider: ProviderConfig | None = None
    skills: list[str] | None = None
    # Unlike the settings above, merged key by key with the global
    # `prompt` section (see merge_prompt).
    prompt: PromptOverrides | None = None

    @property
    def local_remote(self) -> str:
        """Remote of `local_repo` to fetch the MR's branches from."""
        return self.remote or "origin"


@dataclasses.dataclass(frozen=True)
class GitLabConfig:
    """Settings for `review-agent poll` only - the manual review command ignores them.

    No credentials here on purpose: `glab` must already be authenticated
    on the machine (`glab auth login --hostname <host>`).
    """

    hostname: str
    # Optional globally when every enabled project sets its own list.
    reviewers: list[str]
    projects: list[GitLabProjectConfig]
    review_drafts: bool = False
    min_report_chars: int = 200
    # A claim comment older than this is treated as abandoned (see
    # publishing.py). Must be at least the scheduled task's time limit.
    claim_ttl_minutes: int = DEFAULT_CLAIM_TTL_MINUTES


@dataclasses.dataclass(frozen=True)
class Config:
    provider: ProviderConfig
    harness: HarnessConfig
    report: ReportConfig
    skills: list[str] = dataclasses.field(default_factory=list)
    safety: SafetyConfig = dataclasses.field(default_factory=SafetyConfig)
    # Optional: absent for manual-only configs (Change 1), required by `poll`.
    gitlab: GitLabConfig | None = None
    storage: StorageConfig = dataclasses.field(default_factory=StorageConfig)
    usage: UsageConfig = dataclasses.field(default_factory=UsageConfig)
    prompt: PromptSettings = dataclasses.field(default_factory=PromptSettings)


@dataclasses.dataclass(frozen=True)
class ProjectSettings:
    """What actually applies to one project after falling back to global values."""

    reviewers: list[str]
    review_drafts: bool
    # Global config with provider/skills/prompt replaced by the project's
    # own, handed to the engine as is.
    config: Config


def merge_prompt(base: PromptSettings, overrides: PromptOverrides | None) -> PromptSettings:
    """Key by key: a key that is set replaces the value below, the rest are inherited."""
    if overrides is None:
        return base
    template = base.template
    if overrides.template is not None:
        template = None if overrides.template == BUILTIN_TEMPLATE else overrides.template
    return PromptSettings(
        template=template,
        instruction_files=(
            overrides.instruction_files
            if overrides.instruction_files is not None
            else base.instruction_files
        ),
        docs_dirs=overrides.docs_dirs if overrides.docs_dirs is not None else base.docs_dirs,
    )


def effective_settings(config: Config, project: GitLabProjectConfig) -> ProjectSettings:
    gitlab = config.gitlab
    if gitlab is None:
        raise ConfigError("effective_settings() needs a config with a 'gitlab' section")
    return ProjectSettings(
        reviewers=project.reviewers if project.reviewers is not None else gitlab.reviewers,
        review_drafts=(
            project.review_drafts if project.review_drafts is not None else gitlab.review_drafts
        ),
        config=dataclasses.replace(
            config,
            provider=project.provider if project.provider is not None else config.provider,
            skills=project.skills if project.skills is not None else config.skills,
            prompt=merge_prompt(config.prompt, project.prompt),
        ),
    )


def _load_provider(provider_data: Any, section: str) -> ProviderConfig:
    """Shared by the global `provider` and per-project `provider` overrides."""
    if not isinstance(provider_data, dict):
        raise ConfigError(f"'{section}' must be a mapping")
    # The key must be present (explicit per provider, even if null) - but
    # its value may be null for providers without defined variants (see
    # ProviderConfig.reasoning_effort). An empty string is rejected as
    # ambiguous: use null to mean "not applicable", not "".
    if "reasoning_effort" not in provider_data:
        raise ConfigError(
            f"Missing required field 'reasoning_effort' in '{section}' section "
            "(set it explicitly, or to null if the provider has no variants)"
        )
    reasoning_effort = provider_data["reasoning_effort"]
    if reasoning_effort == "":
        raise ConfigError(
            f"'{section}.reasoning_effort' must not be an empty string - use null "
            "to mean 'not applicable'"
        )
    if reasoning_effort is not None and not isinstance(reasoning_effort, str):
        raise ConfigError(f"'{section}.reasoning_effort' must be a string or null")
    return ProviderConfig(
        name=_require(provider_data, "name", section),
        model=_require(provider_data, "model", section),
        reasoning_effort=reasoning_effort,
        api_key_env=provider_data.get("api_key_env"),
    )


def _require(data: dict[str, Any], key: str, section: str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise ConfigError(f"Missing required field '{key}' in '{section}' section")
    return data[key]


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config file, raising ConfigError on any problem."""
    path = Path(path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read config file '{path}': {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file '{path}' is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Config file '{path}' must contain a YAML mapping at the top level")

    provider = _load_provider(_require(data, "provider", "<root>"), "provider")

    harness_data = _require(data, "harness", "<root>")
    if not isinstance(harness_data, dict):
        raise ConfigError("'harness' must be a mapping")
    command = _require(harness_data, "command", "harness")
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ConfigError("'harness.command' must be a list of strings")
    agent_name = harness_data.get("agent_name", "reviewer")
    if not isinstance(agent_name, str) or not agent_name:
        raise ConfigError("'harness.agent_name' must be a non-empty string")
    harness = HarnessConfig(command=command, agent_name=agent_name)

    report_data = _require(data, "report", "<root>")
    if not isinstance(report_data, dict):
        raise ConfigError("'report' must be a mapping")
    report = ReportConfig(output_path=_require(report_data, "output_path", "report"))

    skills = _skills_list(data.get("skills", []), "skills")

    safety_data = data.get("safety", {})
    if not isinstance(safety_data, dict):
        raise ConfigError("'safety' must be a mapping")
    safety_kwargs: dict[str, Any] = {}
    if "output_language" in safety_data:
        safety_kwargs["output_language"] = safety_data["output_language"]
    if "denied_bash_patterns" in safety_data:
        denied = safety_data["denied_bash_patterns"]
        if not isinstance(denied, list) or not all(isinstance(c, str) for c in denied):
            raise ConfigError("'safety.denied_bash_patterns' must be a list of strings")
        safety_kwargs["denied_bash_patterns"] = denied
    safety = SafetyConfig(**safety_kwargs)

    gitlab = _load_gitlab(data["gitlab"]) if data.get("gitlab") is not None else None
    storage = _load_storage(data.get("storage"))
    usage = _load_usage(data.get("usage"))
    # Built-in defaults <- global section; projects merge on top of this.
    prompt = merge_prompt(PromptSettings(), _load_prompt(data.get("prompt"), "prompt"))

    return Config(
        provider=provider,
        harness=harness,
        report=report,
        skills=skills,
        safety=safety,
        gitlab=gitlab,
        storage=storage,
        usage=usage,
        prompt=prompt,
    )


def _absolute(path: str) -> str:
    """A configured file path made absolute once, at load time.

    Relative paths still mean "from the process's current folder", but the
    harness works inside the worktree, so it must never see a relative one.
    """
    return os.path.abspath(path)


def _skills_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(s, str) and s for s in value):
        raise ConfigError(f"'{field}' must be a list of file paths")
    return [_absolute(s) for s in value]


_PROMPT_KEYS = ("template", "instruction_files", "docs_dirs")


def _load_prompt(prompt_data: Any, section: str) -> PromptOverrides | None:
    """A `prompt` section (global or a project's) as per-key overrides.

    Unknown keys are rejected: a typo would otherwise silently fall back
    to the default prompt settings.
    """
    if prompt_data is None:
        return None
    if not isinstance(prompt_data, dict):
        raise ConfigError(f"'{section}' must be a mapping")
    unknown = sorted(str(key) for key in set(prompt_data) - set(_PROMPT_KEYS))
    if unknown:
        raise ConfigError(
            f"'{section}' has unknown keys: {', '.join(unknown)} "
            f"(expected: {', '.join(_PROMPT_KEYS)})"
        )
    template: str | None = None
    if "template" in prompt_data:
        value = prompt_data["template"]
        if value is None or value == BUILTIN_TEMPLATE:
            template = BUILTIN_TEMPLATE
        elif isinstance(value, str) and value:
            template = _absolute(value)
        else:
            raise ConfigError(
                f"'{section}.template' must be a file path or null (the built-in template)"
            )
    lists: dict[str, list[str] | None] = {}
    for key in ("instruction_files", "docs_dirs"):
        value = prompt_data.get(key)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(v, str) and v for v in value)
        ):
            raise ConfigError(
                f"'{section}.{key}' must be a list of paths relative to the repository root"
            )
        lists[key] = value
    return PromptOverrides(
        template=template,
        instruction_files=lists["instruction_files"],
        docs_dirs=lists["docs_dirs"],
    )


def _positive_int(value: Any, field: str) -> int:
    # bool is an int subclass - reject `true` explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"'{field}' must be a positive integer")
    return value


def _load_storage(storage_data: Any) -> StorageConfig:
    if storage_data is None:
        return StorageConfig()
    if not isinstance(storage_data, dict):
        raise ConfigError("'storage' must be a mapping")
    work_dir = storage_data.get("work_dir", DEFAULT_WORK_DIR)
    if not isinstance(work_dir, str) or not work_dir:
        raise ConfigError("'storage.work_dir' must be a non-empty string")
    retention_days = storage_data.get("retention_days", DEFAULT_RETENTION_DAYS)
    if retention_days is not None:
        retention_days = _positive_int(retention_days, "storage.retention_days")
    repo_retention_days = storage_data.get("repo_retention_days", DEFAULT_REPO_RETENTION_DAYS)
    if repo_retention_days is not None:
        repo_retention_days = _positive_int(repo_retention_days, "storage.repo_retention_days")
    return StorageConfig(
        work_dir=work_dir,
        retention_days=retention_days,
        repo_retention_days=repo_retention_days,
    )


def _load_usage(usage_data: Any) -> UsageConfig:
    if usage_data is None:
        return UsageConfig()
    if not isinstance(usage_data, dict):
        raise ConfigError("'usage' must be a mapping")
    defaults = UsageConfig()

    enabled = usage_data.get("enabled", defaults.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError("'usage.enabled' must be true or false")

    source = usage_data.get("source", defaults.source)
    if source not in ("opencode", "none"):
        raise ConfigError("'usage.source' must be 'opencode' or 'none'")

    list_command = _non_empty_str_list(
        usage_data.get("session_list_command", defaults.session_list_command),
        "usage.session_list_command",
    )
    export_command = _non_empty_str_list(
        usage_data.get("session_export_command", defaults.session_export_command),
        "usage.session_export_command",
    )
    if not any("{session_id}" in part for part in export_command):
        raise ConfigError("'usage.session_export_command' must contain '{session_id}'")

    windows = _non_empty_str_list(
        usage_data.get("quota_windows", defaults.quota_windows), "usage.quota_windows"
    )
    if len(set(windows)) != len(windows):
        raise ConfigError("'usage.quota_windows' must not repeat a window")

    catalog = usage_data.get("price_catalog", defaults.price_catalog)
    if not isinstance(catalog, str) or not catalog:
        raise ConfigError("'usage.price_catalog' must be a non-empty string (URL or path)")

    overrides_data = usage_data.get("price_overrides") or {}
    if not isinstance(overrides_data, dict):
        raise ConfigError("'usage.price_overrides' must be a mapping")
    overrides: dict[str, dict[str, Any]] = {}
    for key, entry in overrides_data.items():
        field = f"usage.price_overrides[{key!r}]"
        if not isinstance(key, str) or "/" not in key:
            raise ConfigError(f"'{field}': key must be 'provider/model'")
        if not isinstance(entry, dict):
            raise ConfigError(f"'{field}' must be a mapping in the price catalog's format")
        for name in PRICE_FIELDS:
            if name in entry:
                value = entry[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ConfigError(f"'{field}.{name}' must be a non-negative number")
        if not any(name in entry for name in PRICE_FIELDS):
            raise ConfigError(
                f"'{field}' has no price field (expected some of: {', '.join(PRICE_FIELDS)})"
            )
        overrides[key] = dict(entry)

    return UsageConfig(
        enabled=enabled,
        source=source,
        session_list_command=list_command,
        session_export_command=export_command,
        quota_windows=windows,
        price_catalog=catalog,
        price_overrides=overrides,
    )


def best_effort_work_dir(path: str | Path) -> Path:
    """`storage.work_dir` from a config that may not load - for logging a config error.

    Falls back to the default whenever the file is missing, not YAML, or
    the value is not a usable string.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        work_dir = data["storage"]["work_dir"]
        if isinstance(work_dir, str) and work_dir:
            return Path(work_dir)
    except Exception:  # noqa: BLE001 - any problem means "use the default"
        pass
    return Path(DEFAULT_WORK_DIR)


def _non_empty_str_list(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ConfigError(f"'{field}' must be a non-empty list of non-empty strings")
    return value


def _load_gitlab(gitlab_data: Any) -> GitLabConfig:
    if not isinstance(gitlab_data, dict):
        raise ConfigError("'gitlab' must be a mapping")

    hostname = _require(gitlab_data, "hostname", "gitlab")
    if not isinstance(hostname, str):
        raise ConfigError("'gitlab.hostname' must be a string")

    reviewers_data = gitlab_data.get("reviewers")
    reviewers = (
        [] if reviewers_data is None else _non_empty_str_list(reviewers_data, "gitlab.reviewers")
    )

    projects_data = gitlab_data.get("projects")
    if not isinstance(projects_data, list) or not projects_data:
        raise ConfigError("'gitlab.projects' must be a non-empty list")
    projects = []
    for index, project_data in enumerate(projects_data):
        section = f"gitlab.projects[{index}]"
        if not isinstance(project_data, dict):
            raise ConfigError(f"'{section}' must be a mapping")
        path = _require(project_data, "path", section)
        local_repo = project_data.get("local_repo")
        remote = project_data.get("remote")
        optional = [v for v in (local_repo, remote) if v is not None]
        if not all(isinstance(v, str) and v for v in (path, *optional)):
            raise ConfigError(f"'{section}' path/local_repo/remote must be non-empty strings")
        if remote is not None and local_repo is None:
            raise ConfigError(
                f"'{section}' ({path}) sets 'remote' without 'local_repo': a remote is "
                "only meaningful for a local clone; remove it to use a managed copy"
            )
        enabled = project_data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"'{section}.enabled' must be true or false")
        project_reviewers = project_data.get("reviewers")
        if project_reviewers is not None:
            project_reviewers = _non_empty_str_list(project_reviewers, f"{section}.reviewers")
        project_drafts = project_data.get("review_drafts")
        if project_drafts is not None and not isinstance(project_drafts, bool):
            raise ConfigError(f"'{section}.review_drafts' must be true or false")
        project_provider = project_data.get("provider")
        if project_provider is not None:
            project_provider = _load_provider(project_provider, f"{section}.provider")
        project_skills = project_data.get("skills")
        if project_skills is not None:
            project_skills = _skills_list(project_skills, f"{section}.skills")
        project_prompt = _load_prompt(project_data.get("prompt"), f"{section}.prompt")
        if enabled and not (project_reviewers if project_reviewers is not None else reviewers):
            raise ConfigError(
                f"'{section}' ({path}) has no reviewers: set 'reviewers' for this "
                "project or a global 'gitlab.reviewers'"
            )
        projects.append(
            GitLabProjectConfig(
                path=path,
                local_repo=local_repo,
                remote=remote,
                enabled=enabled,
                reviewers=project_reviewers,
                review_drafts=project_drafts,
                provider=project_provider,
                skills=project_skills,
                prompt=project_prompt,
            )
        )

    review_drafts = gitlab_data.get("review_drafts", False)
    if not isinstance(review_drafts, bool):
        raise ConfigError("'gitlab.review_drafts' must be true or false")

    min_report_chars = gitlab_data.get("min_report_chars", 200)
    # bool is an int subclass - reject `min_report_chars: true` explicitly.
    if isinstance(min_report_chars, bool) or not isinstance(min_report_chars, int) or min_report_chars < 0:
        raise ConfigError("'gitlab.min_report_chars' must be a non-negative integer")

    claim_ttl_minutes = _positive_int(
        gitlab_data.get("claim_ttl_minutes", DEFAULT_CLAIM_TTL_MINUTES), "gitlab.claim_ttl_minutes"
    )

    return GitLabConfig(
        hostname=hostname,
        reviewers=reviewers,
        projects=projects,
        review_drafts=review_drafts,
        min_report_chars=min_report_chars,
        claim_ttl_minutes=claim_ttl_minutes,
    )
