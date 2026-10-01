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
    assert "git diff*" in config.safety.allowed_bash_patterns
    assert config.safety.output_language == "ru"


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
