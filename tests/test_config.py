from pathlib import Path

import pytest

from review_agent.config import ConfigError, load_config

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.yaml"


def test_example_config_loads_without_error():
    config = load_config(EXAMPLE_CONFIG)
    assert config.provider.name == "anthropic"
    assert config.provider.reasoning_effort == "medium"
    assert config.harness.command[0] == "opencode"
    assert "{run_id}" in config.report.output_path
    assert config.skills == []
    assert "npm" in config.safety.forbidden_commands
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
