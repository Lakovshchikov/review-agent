import csv
import io
import json
from datetime import datetime

import pytest

from review_agent.usage_prices import Catalog, PriceBook
from review_agent.usage_summary import (
    PeriodError,
    parse_since,
    parse_until,
    render,
    summarize,
)

PRICES = {"gpt-5.5": {"input_cost_per_token": 1e-06, "output_cost_per_token": 0.0}}
SINCE = datetime(2026, 10, 1)


def rec(run_id, *, model="gpt-5.5", effort="medium", fresh=None, cost_usd=None, quota=None,
        time="2026-10-03T10:00:00", mr=544, problems=(), usage=True, provider="openai"):
    """A ledger record; `cost_usd` sets uncached input so that cost = cost_usd at $1/1M."""
    if cost_usd is not None:
        fresh = int(cost_usd * 1_000_000)
    fresh = 1000 if fresh is None else fresh
    return {
        "schema": "review-agent.usage/1",
        "time": time,
        "review.run_id": run_id,
        "review.source": "poll",
        "review.project": "b2c/front-shopping",
        "review.mr.iid": mr,
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "review.reasoning_effort": effort,
        "review.outcome": "succeeded",
        "review.duration_ms": 120000,
        "gen_ai.usage.input_tokens": fresh if usage else None,
        "gen_ai.usage.cache_read.input_tokens": 0 if usage else None,
        "gen_ai.usage.cache_creation.input_tokens": 0 if usage else None,
        "gen_ai.usage.output_tokens": 10 if usage else None,
        "review.usage.reasoning_tokens": 0 if usage else None,
        "review.usage.format_problems": list(problems),
        "review.change.files": 3,
        "review.change.lines_added": 10,
        "review.change.lines_deleted": 2,
        "review.findings": {"blocker": 0, "major": 1, "minor": 1},
        "review.quota": quota,
    }


def _summary(records, catalog=None, windows=("5h", "week")):
    book = PriceBook(catalog or Catalog(entries=PRICES, origin="test", as_of=None), {})
    return summarize(records, prices=book, since=SINCE, until=None, configured_windows=list(windows))


def _share(summary, run_id, window):
    row = next(r for r in summary.rows if r.record["review.run_id"] == run_id)
    return row.shares[window]


def test_measured_share():
    s = _summary([rec("a", quota={"provider": "openai", "before": {"week": 40}, "after": {"week": 42}})])
    share = _share(s, "a", "week")
    assert (share.value, share.kind) == (2.0, "measured")


def test_estimate_from_same_group():
    s = _summary(
        [
            rec("measured", cost_usd=8, quota={"provider": "openai", "before": {"week": 40}, "after": {"week": 44}}),
            rec("other", cost_usd=2),
        ]
    )
    share = _share(s, "other", "week")
    assert share.kind == "estimate" and share.basis == 1
    assert share.value == pytest.approx(1.0, rel=1e-3)


def test_window_reset_is_invalid_and_not_calibrated():
    s = _summary(
        [
            rec("reset", cost_usd=8, quota={"provider": "openai", "before": {"5h": 90}, "after": {"5h": 3}}),
            rec("other", cost_usd=2),
        ]
    )
    assert _share(s, "reset", "5h").kind == "invalid"
    assert _share(s, "other", "5h").kind == "none"


def test_other_model_gets_no_estimate():
    s = _summary(
        [
            rec("terra", cost_usd=8, quota={"provider": "openai", "before": {"week": 40}, "after": {"week": 44}}),
            rec("luna", model="gpt-5.6-luna", effort="low"),
        ]
    )
    assert _share(s, "luna", "week").kind == "none"
    assert ("openai", "gpt-5.6-luna", "low") in s.groups_without_measurements


def test_same_model_other_effort_is_another_group():
    s = _summary(
        [
            rec("medium", cost_usd=8, quota={"provider": "openai", "before": {"week": 40}, "after": {"week": 44}}),
            rec("high", effort="high", cost_usd=2),
        ]
    )
    assert _share(s, "high", "week").kind == "none"


def test_unpriced_model_calibrates_on_tokens():
    s = _summary(
        [
            rec("m", model="terra", fresh=990, quota={"provider": "openai", "before": {"week": 10}, "after": {"week": 11}}),
            rec("o", model="terra", fresh=1990),
        ]
    )
    # weights: 990+10 = 1000 and 1990+10 = 2000 tokens -> 1% per 1000 tokens
    assert _share(s, "o", "week").value == pytest.approx(2.0)
    assert s.coefficients[("openai", "terra", "medium", "week")].basis == "tokens"
    assert s.unpriced == 2


def test_by_model_grouping_and_header_counters():
    s = _summary(
        [
            rec("a"),
            rec("b"),
            rec("c", model="gpt-5.6-luna", effort="low"),
            rec("d", usage=False),
            rec("e", problems=["info.tokens.cache.read: missing"]),
            rec("old", time="2026-09-01T10:00:00"),
        ]
    )
    assert len(s.rows) == 5
    assert s.without_usage == 1
    assert s.with_format_problems == 1
    data = json.loads(render(s, "model", "json"))
    groups = {(r["provider"], r["model"], r["effort"]): r["reviews"] for r in data["rows"]}
    assert groups == {("openai", "gpt-5.5", "medium"): 4, ("openai", "gpt-5.6-luna", "low"): 1}
    assert data["format_problems"] == 1


def test_table_header_mentions_problems_and_catalog_warning():
    stale = Catalog(entries=PRICES, origin="копия от 2026-10-01 10:00", as_of=datetime(2026, 10, 1, 10), warning="старое")
    text = render(_summary([rec("e", problems=["x"])], catalog=stale), "review", "table")
    assert "с проблемами формата харнесса: 1" in text
    assert "Предупреждение: старое" in text


def test_csv_has_expected_columns():
    out = render(_summary([rec("a"), rec("b", mr=595)]), "mr", "csv")
    rows = list(csv.DictReader(io.StringIO(out)))
    assert [r["mr"] for r in rows] == ["544", "595"]
    assert {"reviews", "fresh_input", "cost_usd", "share_week_pct", "share_week_note"} <= set(rows[0])


def test_empty_ledger_renders_without_error():
    s = _summary([])
    assert "Ревью: 0" in render(s, "review", "table")


def test_period_parsing():
    now = datetime(2026, 10, 8, 12, 0)
    assert parse_since("7d", now=now) == datetime(2026, 10, 1, 12, 0)
    assert parse_since("2026-10-01") == datetime(2026, 10, 1)
    assert parse_until("2026-10-03") == datetime(2026, 10, 4)
    with pytest.raises(PeriodError):
        parse_since("week")


def test_default_table_is_compact_and_wide_has_everything():
    s = _summary([rec("a", quota={"provider": "openai", "before": {"week": 40}, "after": {"week": 42}})])
    compact = render(s, "review", "table")
    header = next(line for line in compact.splitlines() if line.startswith("when"))
    assert header.split() == ["when", "mr", "model", "effort", "tokens_k", "min", "lines", "B/M/m", "usd", "5h", "%", "week", "%"]
    assert "2 (изм.)" in compact and "0/1/1" in compact
    assert "--wide" in compact  # the legend points to the full table
    wide = render(s, "review", "table", wide=True)
    assert "fresh_input" in wide and "price_source" in wide and "share_week_note" in wide


def test_compact_estimate_note_and_group_count():
    s = _summary(
        [
            rec("m", cost_usd=8, quota={"provider": "openai", "before": {"week": 40}, "after": {"week": 44}}),
            rec("o", cost_usd=2),
        ]
    )
    by_review = render(s, "review", "table")
    assert "1 (оц., n=1)" in by_review
    by_model = render(s, "model", "table")
    assert "reviews" in by_model.splitlines()[-3]


def test_whole_ledger_when_no_since():
    from review_agent.usage_prices import Catalog, PriceBook

    book = PriceBook(Catalog(entries=PRICES, origin="test", as_of=None), {})
    records = [rec("old", time="2020-01-01T10:00:00"), rec("new")]
    s = summarize(records, prices=book, since=None, until=None, configured_windows=["week"])
    assert len(s.rows) == 2
    assert "Период: всё время" in render(s, "review", "table")
