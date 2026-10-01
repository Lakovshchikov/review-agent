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
    reasoning_effort: str
    api_key_env: str | None = None


@dataclasses.dataclass(frozen=True)
class HarnessConfig:
    # argv template; placeholders substituted by the harness adapter:
    # {model}, {prompt_file}, {agents_file}
    command: list[str]


@dataclasses.dataclass(frozen=True)
class ReportConfig:
    # output path template; placeholders: {run_id}
    output_path: str


@dataclasses.dataclass(frozen=True)
class SafetyConfig:
    output_language: str = "ru"
    forbidden_commands: list[str] = dataclasses.field(
        default_factory=lambda: [
            "yarn",
            "npm",
            "pnpm",
            "npx",
            "test",
            "lint",
            "stylelint",
            "build",
            "typecheck",
            "tsc",
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
    provider = ProviderConfig(
        name=_require(provider_data, "name", "provider"),
        model=_require(provider_data, "model", "provider"),
        reasoning_effort=_require(provider_data, "reasoning_effort", "provider"),
        api_key_env=provider_data.get("api_key_env"),
    )

    harness_data = _require(data, "harness", "<root>")
    if not isinstance(harness_data, dict):
        raise ConfigError("'harness' must be a mapping")
    command = _require(harness_data, "command", "harness")
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ConfigError("'harness.command' must be a list of strings")
    harness = HarnessConfig(command=command)

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
    if "forbidden_commands" in safety_data:
        forbidden = safety_data["forbidden_commands"]
        if not isinstance(forbidden, list) or not all(isinstance(c, str) for c in forbidden):
            raise ConfigError("'safety.forbidden_commands' must be a list of strings")
        safety_kwargs["forbidden_commands"] = forbidden
    safety = SafetyConfig(**safety_kwargs)

    return Config(provider=provider, harness=harness, report=report, skills=skills, safety=safety)
