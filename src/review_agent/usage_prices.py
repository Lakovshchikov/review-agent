"""API-equivalent prices for the usage summary (design.md decision 5).

Prices are never stored in the ledger: the summary prices token counts
at summary time from

1. `usage.price_overrides` - entries in the catalog's own format,
   replacing the catalog entry of that model as a whole;
2. the price catalog (LiteLLM `model_prices_and_context_window.json`)
   downloaded from `usage.price_catalog` on EVERY summary run, so costs
   follow the current catalog; the last good download is kept in
   `<work_dir>/usage/price-catalog.json` and used, with a warning, when
   the catalog cannot be fetched.

Reviews never touch any of this - only `review-agent usage` does.
"""

from __future__ import annotations

import dataclasses
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from review_agent.config import PRICE_FIELDS
from review_agent.usage_source import TokenCounts

CATALOG_CACHE_NAME = "price-catalog.json"
DOWNLOAD_TIMEOUT_SECONDS = 30

Fetch = Callable[[str], bytes]


def _http_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "review-agent"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read()


@dataclasses.dataclass(frozen=True)
class Catalog:
    entries: dict[str, dict[str, Any]]
    # Human-readable origin for the summary header: URL, path, or "копия от ...".
    origin: str
    # When this data was obtained (download time, file mtime, cache mtime); None if none.
    as_of: datetime | None
    warning: str | None = None


def _parse(raw: bytes) -> dict[str, dict[str, Any]]:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("price catalog is not a JSON object")
    return {key: value for key, value in data.items() if isinstance(value, dict)}


def _is_url(location: str) -> bool:
    return location.startswith(("http://", "https://"))


def load_catalog(location: str, cache_path: Path, *, fetch: Fetch = _http_fetch) -> Catalog:
    """The current catalog, else the saved copy (with a warning), else an empty one (warning)."""
    try:
        if _is_url(location):
            raw = fetch(location)
            entries = _parse(raw)
            _save_copy(cache_path, raw)
            return Catalog(entries=entries, origin=location, as_of=datetime.now())
        path = Path(location)
        raw = path.read_bytes()
        entries = _parse(raw)
        _save_copy(cache_path, raw)
        return Catalog(
            entries=entries, origin=str(path), as_of=datetime.fromtimestamp(path.stat().st_mtime)
        )
    except Exception as exc:  # noqa: BLE001 - any failure falls back to the saved copy
        problem = f"справочник цен недоступен ({location}): {exc}"

    try:
        raw = cache_path.read_bytes()
        entries = _parse(raw)
        as_of = datetime.fromtimestamp(cache_path.stat().st_mtime)
    except Exception:  # noqa: BLE001 - no usable copy either
        return Catalog(
            entries={},
            origin="нет",
            as_of=None,
            warning=f"{problem}; сохранённой копии нет — цены только из usage.price_overrides",
        )
    stamp = as_of.strftime("%Y-%m-%d %H:%M")
    return Catalog(
        entries=entries,
        origin=f"копия от {stamp}",
        as_of=as_of,
        warning=f"{problem}; используется сохранённая копия от {stamp} — цены могут быть устаревшими",
    )


def _save_copy(cache_path: Path, raw: bytes) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        partial = cache_path.with_name(cache_path.name + ".partial")
        partial.write_bytes(raw)
        os.replace(partial, cache_path)
    except OSError:
        pass  # the summary still works; only the fallback copy is not refreshed


@dataclasses.dataclass(frozen=True)
class Price:
    entry: dict[str, Any]
    # "override" | "catalog" | "catalog (копия от ...)"
    source: str
    # Price fields this entry lacks (counted as 0).
    missing_fields: tuple[str, ...]

    def cost(self, tokens: TokenCounts) -> float | None:
        """USD for `tokens`; reasoning is priced as output. None if a token kind is unknown."""
        if any(
            v is None
            for v in (tokens.input, tokens.cache_read, tokens.cache_write, tokens.output)
        ):
            return None

        def rate(name: str) -> float:
            value = self.entry.get(name)
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

        return (
            tokens.input * rate("input_cost_per_token")  # type: ignore[operator]
            + tokens.cache_read * rate("cache_read_input_token_cost")  # type: ignore[operator]
            + tokens.cache_write * rate("cache_creation_input_token_cost")  # type: ignore[operator]
            + (tokens.output + (tokens.reasoning or 0)) * rate("output_cost_per_token")  # type: ignore[operator]
        )


class PriceBook:
    def __init__(self, catalog: Catalog, overrides: dict[str, dict[str, Any]]):
        self.catalog = catalog
        self.overrides = overrides

    def lookup(self, provider: str | None, model: str | None) -> Price | None:
        if not model:
            return None
        full = f"{provider}/{model}" if provider else model
        if full in self.overrides:
            return self._price(self.overrides[full], "override")
        catalog_source = "catalog" if self.catalog.warning is None else f"catalog ({self.catalog.origin})"
        for key in (full, model):
            entry = self.catalog.entries.get(key)
            if entry is not None and any(
                isinstance(entry.get(name), (int, float)) for name in PRICE_FIELDS
            ):
                return self._price(entry, catalog_source)
        return None

    @staticmethod
    def _price(entry: dict[str, Any], source: str) -> Price:
        missing = tuple(name for name in PRICE_FIELDS if not isinstance(entry.get(name), (int, float)))
        return Price(entry=entry, source=source, missing_fields=missing)
