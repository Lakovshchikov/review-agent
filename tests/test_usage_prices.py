import json
import os
import time

import pytest

from review_agent.usage_prices import Catalog, PriceBook, load_catalog
from review_agent.usage_source import TokenCounts

CATALOG = {
    "sample_spec": {"input_cost_per_token": "doc"},
    "gpt-5.5": {
        "input_cost_per_token": 1.25e-06,
        "cache_read_input_token_cost": 1.25e-07,
        "output_cost_per_token": 1e-05,
        "litellm_provider": "openai",
    },
    "anthropic/claude-x": {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05},
}
NEW_CATALOG = {"gpt-5.5": {"input_cost_per_token": 2.5e-06, "output_cost_per_token": 2e-05}}
URL = "https://example.invalid/prices.json"


def _fetch_ok(payload):
    return lambda url: json.dumps(payload).encode("utf-8")


def _fetch_fail(url):
    raise OSError("network is unreachable")


def test_fresh_download_is_used_and_saved(tmp_path):
    cache = tmp_path / "usage" / "price-catalog.json"
    catalog = load_catalog(URL, cache, fetch=_fetch_ok(CATALOG))
    assert catalog.warning is None and catalog.origin == URL
    assert "gpt-5.5" in catalog.entries
    assert json.loads(cache.read_text(encoding="utf-8")) == CATALOG

    # every run downloads again: a changed catalog replaces the copy
    catalog = load_catalog(URL, cache, fetch=_fetch_ok(NEW_CATALOG))
    assert catalog.entries["gpt-5.5"]["input_cost_per_token"] == 2.5e-06
    assert json.loads(cache.read_text(encoding="utf-8")) == NEW_CATALOG


def test_offline_uses_saved_copy_with_dated_warning(tmp_path):
    cache = tmp_path / "price-catalog.json"
    cache.write_text(json.dumps(CATALOG), encoding="utf-8")
    old = time.time() - 3 * 86400
    os.utime(cache, (old, old))

    catalog = load_catalog(URL, cache, fetch=_fetch_fail)
    assert "gpt-5.5" in catalog.entries
    assert catalog.warning and "устаревшими" in catalog.warning
    assert time.strftime("%Y-%m-%d", time.localtime(old)) in catalog.warning
    assert catalog.origin.startswith("копия от")


def test_offline_without_copy_warns_and_is_empty(tmp_path):
    catalog = load_catalog(URL, tmp_path / "none.json", fetch=_fetch_fail)
    assert catalog.entries == {}
    assert "price_overrides" in catalog.warning


def test_local_catalog_path(tmp_path):
    path = tmp_path / "litellm.json"
    path.write_text(json.dumps(CATALOG), encoding="utf-8")
    catalog = load_catalog(str(path), tmp_path / "cache.json")
    assert catalog.warning is None and "gpt-5.5" in catalog.entries


def _book(overrides=None, catalog=None):
    return PriceBook(catalog or Catalog(entries=CATALOG, origin=URL, as_of=None), overrides or {})


TOKENS = TokenCounts(input=200_000, cache_read=100_000, cache_write=0, output=4_000, reasoning=1_000)


def test_cost_matches_manual_calculation():
    price = _book().lookup("openai", "gpt-5.5")
    assert price.source == "catalog"
    expected = 200_000 * 1.25e-06 + 100_000 * 1.25e-07 + (4_000 + 1_000) * 1e-05
    assert price.cost(TOKENS) == pytest.approx(expected)
    assert price.missing_fields == ("cache_creation_input_token_cost",)


def test_override_replaces_catalog_entry_as_a_whole():
    book = _book({"openai/gpt-5.5": {"input_cost_per_token": 1e-06, "output_cost_per_token": 1e-06}})
    price = book.lookup("openai", "gpt-5.5")
    assert price.source == "override"
    # no cache_read price in the override -> 0, not inherited from the catalog
    assert price.cost(TOKENS) == pytest.approx(200_000 * 1e-06 + 5_000 * 1e-06)


def test_lookup_by_provider_prefixed_key_and_unpriced_model():
    book = _book()
    assert book.lookup("anthropic", "claude-x").source == "catalog"
    assert book.lookup("openai", "gpt-5.6-terra") is None
    assert book.lookup("openai", "sample_spec") is None


def test_stale_catalog_marks_source():
    stale = Catalog(entries=CATALOG, origin="копия от 2026-10-01 10:00", as_of=None, warning="old")
    assert _book(catalog=stale).lookup("openai", "gpt-5.5").source == "catalog (копия от 2026-10-01 10:00)"


def test_unknown_token_kind_gives_no_cost():
    price = _book().lookup("openai", "gpt-5.5")
    assert price.cost(TokenCounts(input=1, cache_read=None, cache_write=0, output=1, reasoning=0)) is None
