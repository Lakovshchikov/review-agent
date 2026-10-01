"""Loading and validating the review engine's YAML configuration.

The config is the only place a model provider, its reasoning effort, the
harness command, the report output path, and attached knowledge-skill files
are set - nothing here is hardcoded in the rest of the engine, so switching
providers or harnesses never requires a code change.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml


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

    Verified against a real OpenCode install: `bash` permission is
    allowlist-by-pattern (deny-by-default is safer and more robust than a
    denylist of known-bad command names, which is trivially bypassed by a
    slightly different invocation). `allowed_bash_patterns` lists the
    read-only inspection commands the agent may run; everything else is
    denied. Patterns are OpenCode glob-style strings matched against the
    full command line (see harness_config.py).
    """

    output_language: str = "ru"
    allowed_bash_patterns: list[str] = dataclasses.field(
        default_factory=lambda: [
            "git diff*",
            "git show*",
            "git log*",
            "git blame*",
            "git grep*",
            "git ls-files*",
            "git status*",
            "git branch*",
            "rg *",
        ]
    )


@dataclasses.dataclass(frozen=True)
class Config:
    provider: ProviderConfig
    harness: HarnessConfig
    report: ReportConfig
    skills: list[str] = dataclasses.field(default_factory=list)
    safety: SafetyConfig = dataclasses.field(default_factory=SafetyConfig)


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

    provider_data = _require(data, "provider", "<root>")
    if not isinstance(provider_data, dict):
        raise ConfigError("'provider' must be a mapping")
    # The key must be present (explicit per provider, even if null) - but
    # its value may be null for providers without defined variants (see
    # ProviderConfig.reasoning_effort). An empty string is rejected as
    # ambiguous: use null to mean "not applicable", not "".
    if "reasoning_effort" not in provider_data:
        raise ConfigError(
            "Missing required field 'reasoning_effort' in 'provider' section "
            "(set it explicitly, or to null if the provider has no variants)"
        )
    reasoning_effort = provider_data["reasoning_effort"]
    if reasoning_effort == "":
        raise ConfigError(
            "'provider.reasoning_effort' must not be an empty string - use null "
            "to mean 'not applicable'"
        )
    if reasoning_effort is not None and not isinstance(reasoning_effort, str):
        raise ConfigError("'provider.reasoning_effort' must be a string or null")
    provider = ProviderConfig(
        name=_require(provider_data, "name", "provider"),
        model=_require(provider_data, "model", "provider"),
        reasoning_effort=reasoning_effort,
        api_key_env=provider_data.get("api_key_env"),
    )

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

    skills = data.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        raise ConfigError("'skills' must be a list of file paths")

    safety_data = data.get("safety", {})
    if not isinstance(safety_data, dict):
        raise ConfigError("'safety' must be a mapping")
    safety_kwargs: dict[str, Any] = {}
    if "output_language" in safety_data:
        safety_kwargs["output_language"] = safety_data["output_language"]
    if "allowed_bash_patterns" in safety_data:
        allowed = safety_data["allowed_bash_patterns"]
        if not isinstance(allowed, list) or not all(isinstance(c, str) for c in allowed):
            raise ConfigError("'safety.allowed_bash_patterns' must be a list of strings")
        safety_kwargs["allowed_bash_patterns"] = allowed
    safety = SafetyConfig(**safety_kwargs)

    return Config(provider=provider, harness=harness, report=report, skills=skills, safety=safety)
