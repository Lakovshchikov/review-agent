from pathlib import Path

import pytest

from review_agent.config import ConfigError, load_config

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.yaml"


def test_example_config_loads_without_error():
    config = load_config(EXAMPLE_CONFIG)
    assert config.provider.name == "anthropic"
    assert config.provider.reasoning_effort == "medium"
    assert config.harness.command[0] == "opencode"
    assert config.harness.agent_name == "reviewer"
    assert "{run_id}" in config.report.output_path
    assert config.skills == []
    assert "npm *" in config.safety.denied_bash_patterns
    assert config.safety.output_language == "ru"
    assert config.gitlab is not None
    assert config.gitlab.reviewers == ["ai-reviewer"]
    assert config.gitlab.min_report_chars == 200
    assert config.gitlab.projects[0].path == "b2c/front-shopping"
    assert config.gitlab.claim_ttl_minutes == 240
    assert config.storage.work_dir == "./.review-agent"
    assert config.storage.retention_days == 7
    assert config.gitlab.projects[1].provider.reasoning_effort == "high"
    assert config.gitlab.projects[1].skills == []
    assert config.gitlab.projects[2].enabled is False


def test_missing_required_field_raises(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        "provider:\n  name: anthropic\n"  # missing model, reasoning_effort
        "harness:\n  command: [opencode]\n"
        "report:\n  output_path: out.md\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(bad_config)


def test_reasoning_effort_null_is_valid_for_providers_without_variants(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "provider:\n  name: ollama\n  model: qwen3-14b\n  reasoning_effort: null\n"
        "harness:\n  command: [opencode, run, '{model}', '{prompt}']\n"
        "report:\n  output_path: out-{run_id}.md\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.provider.reasoning_effort is None


def test_reasoning_effort_key_missing_entirely_raises(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        "provider:\n  name: anthropic\n  model: claude-sonnet-4-5\n"  # key absent, not even null
        "harness:\n  command: [opencode]\n"
        "report:\n  output_path: out.md\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="reasoning_effort"):
        load_config(bad_config)


def test_reasoning_effort_empty_string_raises(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        "provider:\n  name: anthropic\n  model: claude-sonnet-4-5\n  reasoning_effort: ''\n"
        "harness:\n  command: [opencode]\n"
        "report:\n  output_path: out.md\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="reasoning_effort"):
        load_config(bad_config)


def test_non_mapping_config_raises(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad_config)


def test_invalid_yaml_raises(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text("provider: [unterminated\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad_config)


_BASE_CONFIG = (
    "provider:\n  name: anthropic\n  model: m\n  reasoning_effort: medium\n"
    "harness:\n  command: [opencode]\n"
    "report:\n  output_path: out-{run_id}.md\n"
)


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_config_without_gitlab_section_still_loads(tmp_path):
    config = load_config(_write(tmp_path, _BASE_CONFIG))
    assert config.gitlab is None


def test_valid_gitlab_section_loads_with_defaults(tmp_path):
    config = load_config(
        _write(
            tmp_path,
            _BASE_CONFIG
            + "gitlab:\n  hostname: gitlab.local\n  reviewers: [ai-reviewer, petrov]\n"
            "  projects:\n    - path: b2c/front-shopping\n      local_repo: C:/repos/front\n",
        )
    )
    assert config.gitlab.hostname == "gitlab.local"
    assert config.gitlab.reviewers == ["ai-reviewer", "petrov"]
    assert config.gitlab.review_drafts is False
    assert config.gitlab.min_report_chars == 200
    project = config.gitlab.projects[0]
    assert (project.path, project.local_repo, project.remote) == (
        "b2c/front-shopping",
        "C:/repos/front",
        "origin",
    )


@pytest.mark.parametrize(
    "gitlab_yaml, match",
    [
        ("gitlab:\n  hostname: h\n  reviewers: []\n  projects:\n    - {path: a/b, local_repo: r}\n", "reviewers"),
        ("gitlab:\n  hostname: h\n  reviewers: [u]\n  projects: []\n", "projects"),
        ("gitlab:\n  hostname: h\n  reviewers: [u]\n  projects:\n    - {path: a/b}\n", "local_repo"),
        ("gitlab:\n  reviewers: [u]\n  projects:\n    - {path: a/b, local_repo: r}\n", "hostname"),
        (
            "gitlab:\n  hostname: h\n  reviewers: [u]\n  min_report_chars: -1\n"
            "  projects:\n    - {path: a/b, local_repo: r}\n",
            "min_report_chars",
        ),
    ],
)
def test_invalid_gitlab_section_raises(tmp_path, gitlab_yaml, match):
    with pytest.raises(ConfigError, match=match):
        load_config(_write(tmp_path, _BASE_CONFIG + gitlab_yaml))


# -- per-project overrides, storage, claim TTL (Change 3) ---------------------

import dataclasses

import yaml

from review_agent.config import (
    DEFAULT_WORK_DIR,
    best_effort_work_dir,
    effective_settings,
)


def _write_cfg(tmp_path, gitlab=None, **extra):
    data = {
        "provider": {"name": "openai", "model": "gpt", "reasoning_effort": "medium"},
        "harness": {"command": ["opencode"]},
        "report": {"output_path": "out-{run_id}.md"},
        "skills": ["global-skill.md"],
        **extra,
    }
    if gitlab is not None:
        data["gitlab"] = {"hostname": "h", **gitlab}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_config_without_new_fields_keeps_old_behaviour(tmp_path):
    config = load_config(
        _write_cfg(tmp_path, gitlab={"reviewers": ["bot"], "projects": [{"path": "a/b", "local_repo": "c"}]})
    )
    project = config.gitlab.projects[0]
    assert project.enabled is True
    assert project.reviewers is project.review_drafts is project.provider is project.skills is None
    assert config.gitlab.claim_ttl_minutes == 240
    assert config.storage.work_dir == DEFAULT_WORK_DIR
    assert config.storage.retention_days == 7

    settings = effective_settings(config, project)
    assert settings.reviewers == ["bot"]
    assert settings.review_drafts is False
    assert settings.config.provider == config.provider
    assert settings.config.skills == ["global-skill.md"]


def test_project_overrides_replace_global_values(tmp_path):
    config = load_config(
        _write_cfg(
            tmp_path,
            gitlab={
                "reviewers": ["bot"],
                "review_drafts": True,
                "projects": [
                    {
                        "path": "a/b",
                        "local_repo": "c",
                        "reviewers": ["front-bot"],
                        "review_drafts": False,
                        "provider": {"name": "anthropic", "model": "claude", "reasoning_effort": None},
                        "skills": [],
                    }
                ],
            },
        )
    )
    settings = effective_settings(config, config.gitlab.projects[0])
    assert settings.reviewers == ["front-bot"]
    assert settings.review_drafts is False
    assert settings.config.provider.name == "anthropic"
    assert settings.config.provider.reasoning_effort is None
    assert settings.config.provider.api_key_env is None  # replaced whole, not merged
    assert settings.config.skills == []
    # The global config itself is untouched.
    assert config.provider.name == "openai" and config.skills == ["global-skill.md"]
    assert dataclasses.replace(settings.config, provider=config.provider, skills=config.skills) == config


def test_project_provider_without_reasoning_effort_names_project(tmp_path):
    path = _write_cfg(
        tmp_path,
        gitlab={
            "reviewers": ["bot"],
            "projects": [
                {"path": "a/b", "local_repo": "c"},
                {"path": "x/y", "local_repo": "d", "provider": {"name": "p", "model": "m"}},
            ],
        },
    )
    with pytest.raises(ConfigError, match=r"gitlab\.projects\[1\]\.provider.*reasoning_effort|reasoning_effort.*gitlab\.projects\[1\]\.provider"):
        load_config(path)


def test_no_reviewers_anywhere_is_an_error_naming_the_project(tmp_path):
    path = _write_cfg(tmp_path, gitlab={"projects": [{"path": "a/b", "local_repo": "c"}]})
    with pytest.raises(ConfigError, match=r"gitlab\.projects\[0\].*a/b"):
        load_config(path)


def test_global_reviewers_optional_when_every_project_has_its_own(tmp_path):
    config = load_config(
        _write_cfg(
            tmp_path,
            gitlab={
                "projects": [
                    {"path": "a/b", "local_repo": "c", "reviewers": ["one"]},
                    {"path": "x/y", "local_repo": "d", "reviewers": ["two"]},
                ]
            },
        )
    )
    assert config.gitlab.reviewers == []
    assert effective_settings(config, config.gitlab.projects[1]).reviewers == ["two"]


def test_disabled_project_needs_no_reviewers(tmp_path):
    config = load_config(
        _write_cfg(
            tmp_path,
            gitlab={
                "projects": [
                    {"path": "a/b", "local_repo": "c", "reviewers": ["one"]},
                    {"path": "x/y", "local_repo": "missing", "enabled": False},
                ]
            },
        )
    )
    assert config.gitlab.projects[1].enabled is False


@pytest.mark.parametrize(
    "project_extra, message",
    [
        ({"enabled": "no"}, "enabled"),
        ({"reviewers": []}, "reviewers"),
        ({"review_drafts": "yes"}, "review_drafts"),
        ({"skills": "a.md"}, "skills"),
        ({"provider": "openai"}, "provider"),
    ],
)
def test_invalid_project_overrides(tmp_path, project_extra, message):
    path = _write_cfg(
        tmp_path,
        gitlab={"reviewers": ["bot"], "projects": [{"path": "a/b", "local_repo": "c", **project_extra}]},
    )
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_storage_section(tmp_path):
    config = load_config(_write_cfg(tmp_path, storage={"work_dir": "D:/ra", "retention_days": 30}))
    assert config.storage.work_dir == "D:/ra"
    assert config.storage.retention_days == 30

    config = load_config(_write_cfg(tmp_path, storage={"retention_days": None}))
    assert config.storage.retention_days is None
    assert config.storage.work_dir == DEFAULT_WORK_DIR


@pytest.mark.parametrize("bad", [0, -1, True, "7"])
def test_invalid_retention_days(tmp_path, bad):
    with pytest.raises(ConfigError, match="retention_days"):
        load_config(_write_cfg(tmp_path, storage={"retention_days": bad}))


@pytest.mark.parametrize("bad", [0, -5, True, "240"])
def test_invalid_claim_ttl(tmp_path, bad):
    path = _write_cfg(
        tmp_path,
        gitlab={"reviewers": ["bot"], "claim_ttl_minutes": bad, "projects": [{"path": "a/b", "local_repo": "c"}]},
    )
    with pytest.raises(ConfigError, match="claim_ttl_minutes"):
        load_config(path)


def test_best_effort_work_dir(tmp_path):
    good = tmp_path / "good.yaml"
    good.write_text("storage:\n  work_dir: ./my-work\nprovider: broken\n", encoding="utf-8")
    assert best_effort_work_dir(good) == Path("./my-work")

    broken = tmp_path / "broken.yaml"
    broken.write_text("provider: [unclosed\n", encoding="utf-8")
    assert best_effort_work_dir(broken) == Path(DEFAULT_WORK_DIR)
    assert best_effort_work_dir(tmp_path / "missing.yaml") == Path(DEFAULT_WORK_DIR)
