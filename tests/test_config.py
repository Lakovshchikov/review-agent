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
