import json
from datetime import datetime, timezone

from review_agent.config import ProviderConfig
from review_agent.review_stats import ChangeSize
from review_agent.usage_ledger import (
    SCHEMA,
    QuotaReading,
    RunLabels,
    append_record,
    build_review_record,
    read_records,
)
from review_agent.usage_source import RunUsage, SessionUsage, TokenCounts

TITLE = "PLATCHCKT-762: секретный заголовок MR"
DESCRIPTION = "Описание MR, которое не должно попасть в журнал"


def _usage(**tokens):
    base = dict(input=218796, cache_read=121856, cache_write=0, output=3696, reasoning=2269)
    base.update(tokens)
    return RunUsage(
        harness_name="opencode",
        harness_version="2.0.21",
        sessions=[
            SessionUsage(
                id="ses_root",
                parent=None,
                agent="reviewer",
                tokens=TokenCounts(**base),
                duration_ms=290000,
                steps=4,
            )
        ],
    )


def _record(provider=None, usage=None, **overrides):
    kwargs = dict(
        time=datetime(2026, 10, 4, 9, 51, 53, tzinfo=timezone.utc),
        run_id="1791107508-472e2701",
        labels=RunLabels(source="poll", project="b2c/front-shopping", mr_iid=544),
        base_sha="b" * 40,
        head_sha="h" * 40,
        provider=provider or ProviderConfig(name="openai", model="gpt-5.6-terra", reasoning_effort="medium"),
        skills=["skills/frontend-pitfalls.md"],
        outcome="succeeded",
        duration_ms=312000,
        usage=usage if usage is not None else _usage(),
        change=ChangeSize(files=12, lines_added=340, lines_deleted=85, commits=3),
        findings={"blocker": 0, "major": 2, "minor": 1},
        quota=QuotaReading(provider="openai", before={"5h": 12, "week": 40}, after={"5h": 21, "week": 42}),
    )
    kwargs.update(overrides)
    return build_review_record(**kwargs)


def test_record_fields_match_the_documented_example():
    record = _record()
    assert record["schema"] == SCHEMA
    assert record["review.run_id"] == "1791107508-472e2701"
    assert (record["review.source"], record["review.project"], record["review.mr.iid"]) == (
        "poll",
        "b2c/front-shopping",
        544,
    )
    assert record["gen_ai.provider.name"] == "openai"
    assert record["gen_ai.request.model"] == "gpt-5.6-terra"
    assert record["review.reasoning_effort"] == "medium"
    assert record["review.skills"] == ["frontend-pitfalls.md"]
    assert record["review.harness.version"] == "2.0.21"
    # input_tokens is ALL input (uncached + cache read + cache write)
    assert record["gen_ai.usage.input_tokens"] == 218796 + 121856
    assert record["gen_ai.usage.cache_read.input_tokens"] == 121856
    assert record["gen_ai.usage.cache_creation.input_tokens"] == 0
    assert record["gen_ai.usage.output_tokens"] == 3696
    assert record["review.usage.reasoning_tokens"] == 2269
    assert record["review.usage.steps"] == 4
    assert record["review.duration_ms"] == 312000
    assert record["review.sessions"][0]["duration_ms"] == 290000
    assert record["review.change.files"] == 12
    assert record["review.findings"] == {"blocker": 0, "major": 2, "minor": 1}
    assert record["review.quota"] == {
        "provider": "openai",
        "before": {"5h": 12, "week": 40},
        "after": {"5h": 21, "week": 42},
    }
    assert record["review.usage.missing_reason"] is None


def test_totals_include_subagent_sessions():
    usage = _usage(input=100000, cache_read=0, output=10, reasoning=0)
    usage.sessions.append(
        SessionUsage(
            id="ses_child",
            parent="ses_root",
            agent="explore",
            tokens=TokenCounts(input=40000, cache_read=0, cache_write=0, output=5, reasoning=0),
        )
    )
    record = _record(usage=usage)
    assert record["gen_ai.usage.input_tokens"] == 140000
    assert [s["parent"] for s in record["review.sessions"]] == [None, "ses_root"]


def test_records_of_different_providers_have_the_same_keys():
    cloud = _record()
    local = _record(
        provider=ProviderConfig(name="ollama", model="qwen3-14b", reasoning_effort=None),
        usage=RunUsage(harness_name="opencode", missing_reason="session not found"),
        change=None,
        findings=None,
        quota=None,
    )
    assert cloud.keys() == local.keys()
    assert local["gen_ai.usage.input_tokens"] is None
    assert local["review.usage.missing_reason"] == "session not found"


def test_record_contains_no_text_of_the_mr(tmp_path):
    record = _record()
    ledger = tmp_path / "usage" / "ledger.jsonl"
    assert append_record(ledger, record, warn=lambda m: None)
    text = ledger.read_text(encoding="utf-8")
    assert TITLE not in text and DESCRIPTION not in text
    assert len(text.splitlines()) == 1


def test_append_and_read_back(tmp_path):
    ledger = tmp_path / "usage" / "ledger.jsonl"
    append_record(ledger, _record(), warn=lambda m: None)
    append_record(ledger, _record(outcome="failed"), warn=lambda m: None)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    records, skipped = read_records(ledger)
    assert [r["review.outcome"] for r in records] == ["succeeded", "failed"]
    assert skipped == 1
    assert json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])["schema"] == SCHEMA


def test_unwritable_ledger_warns_instead_of_raising(tmp_path):
    blocker = tmp_path / "usage"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    warnings = []
    assert append_record(blocker / "ledger.jsonl", _record(), warn=warnings.append) is False
    assert warnings and "журнал" in warnings[0]


def test_missing_ledger_reads_as_empty(tmp_path):
    assert read_records(tmp_path / "nope.jsonl") == ([], 0)


# -- prompt template in the record ---------------------------------------------

import hashlib
from importlib import resources

from review_agent.prompt import BUILTIN_TEMPLATE


def test_record_names_builtin_template_by_default():
    record = _record()
    assert record["review.prompt.template"] == BUILTIN_TEMPLATE
    builtin = resources.files("review_agent").joinpath("prompts", "default.md.j2").read_bytes()
    assert record["review.prompt.sha256"] == hashlib.sha256(builtin).hexdigest()


def test_record_names_custom_template_with_its_hash(tmp_path):
    template = tmp_path / "backend.md.j2"
    template.write_text('{% extends "builtin/default.md.j2" %}', encoding="utf-8")
    record = _record(prompt_template=str(template))
    assert record["review.prompt.template"] == str(template)
    assert record["review.prompt.sha256"] == hashlib.sha256(template.read_bytes()).hexdigest()


def test_record_with_unreadable_template_has_no_hash(tmp_path):
    record = _record(prompt_template=str(tmp_path / "missing.md.j2"))
    assert record["review.prompt.sha256"] is None
