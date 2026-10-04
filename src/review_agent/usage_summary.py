"""`review-agent usage`: summary of the usage ledger (design.md decisions 5, 7, 8).

Everything here is computed at summary time from the ledger's token
counts: API-equivalent cost from the current price catalog
(usage_prices.py), and subscription limit shares from interactive quota
readings - measured for the reviews that have readings, estimated for
other reviews of the same (provider, model, reasoning effort) group.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from review_agent.usage_prices import Catalog, Price, PriceBook
from review_agent.usage_source import TokenCounts

GROUPINGS = ("review", "mr", "model", "day")
FORMATS = ("table", "csv", "json")


class PeriodError(ValueError):
    pass


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """`7d` (last N days) or `YYYY-MM-DD` -> start of the period (local time)."""
    now = now or datetime.now()
    match = re.fullmatch(r"(\d+)d", value.strip())
    if match:
        return now - timedelta(days=int(match.group(1)))
    try:
        return datetime.combine(date.fromisoformat(value.strip()), datetime.min.time())
    except ValueError:
        raise PeriodError(f"период '{value}': ожидается '<N>d' или 'YYYY-MM-DD'") from None


def parse_until(value: str) -> datetime:
    """`YYYY-MM-DD` -> end of that day (exclusive bound is the next midnight)."""
    try:
        return datetime.combine(date.fromisoformat(value.strip()), datetime.min.time()) + timedelta(days=1)
    except ValueError:
        raise PeriodError(f"дата '{value}': ожидается 'YYYY-MM-DD'") from None


def _record_time(record: dict[str, Any]) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(record.get("time")))
    except ValueError:
        return None
    # Compare in local naive time, like the period bounds.
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def record_tokens(record: dict[str, Any]) -> TokenCounts:
    """Token counts of a record; `input` back to UNCACHED (the ledger keeps OTel totals)."""
    total_input = _int(record.get("gen_ai.usage.input_tokens"))
    cache_read = _int(record.get("gen_ai.usage.cache_read.input_tokens"))
    cache_write = _int(record.get("gen_ai.usage.cache_creation.input_tokens"))
    uncached = (
        total_input - cache_read - cache_write
        if None not in (total_input, cache_read, cache_write)
        else None
    )
    return TokenCounts(
        input=uncached,
        cache_read=cache_read,
        cache_write=cache_write,
        output=_int(record.get("gen_ai.usage.output_tokens")),
        reasoning=_int(record.get("review.usage.reasoning_tokens")),
    )


@dataclasses.dataclass
class Share:
    value: float | None
    kind: str  # "measured" | "estimate" | "invalid" | "none"
    basis: int = 0  # number of measurements behind an estimate


@dataclasses.dataclass
class ReviewRow:
    record: dict[str, Any]
    tokens: TokenCounts
    price: Price | None
    cost: float | None
    shares: dict[str, Share] = dataclasses.field(default_factory=dict)

    @property
    def group(self) -> tuple[str, str, str]:
        r = self.record
        return (
            str(r.get("gen_ai.provider.name") or "?"),
            str(r.get("gen_ai.request.model") or "?"),
            str(r.get("review.reasoning_effort") or "-"),
        )

    @property
    def has_usage(self) -> bool:
        return self.tokens.total_input is not None and self.tokens.output is not None

    @property
    def token_weight(self) -> int | None:
        t = self.tokens
        if t.total_input is None or t.output is None:
            return None
        return t.total_input + t.output + (t.reasoning or 0)

    def weight(self, basis: str) -> float | None:
        return self.cost if basis == "usd" else self.token_weight

    def measured(self, window: str) -> float | None | str:
        """after - before for `window`; "invalid" if the window reset; None if not measured."""
        quota = self.record.get("review.quota")
        if not isinstance(quota, dict):
            return None
        before = (quota.get("before") or {}).get(window)
        after = (quota.get("after") or {}).get(window)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            return None
        if after < before:
            return "invalid"
        return float(after - before)


@dataclasses.dataclass
class Coefficient:
    value: float  # share percent per weight unit
    basis: str  # "usd" | "tokens"
    measurements: int


def build_rows(records: list[dict[str, Any]], prices: PriceBook) -> list[ReviewRow]:
    rows = []
    for record in records:
        tokens = record_tokens(record)
        price = prices.lookup(record.get("gen_ai.provider.name"), record.get("gen_ai.request.model"))
        cost = price.cost(tokens) if price is not None else None
        rows.append(ReviewRow(record=record, tokens=tokens, price=price, cost=cost))
    return rows


def windows_of(rows: list[ReviewRow], configured: list[str]) -> list[str]:
    seen = list(configured)
    for row in rows:
        quota = row.record.get("review.quota")
        if isinstance(quota, dict):
            for part in ("before", "after"):
                for window in quota.get(part) or {}:
                    if window not in seen:
                        seen.append(window)
    return seen


def calibrate(rows: list[ReviewRow], windows: list[str]) -> dict[tuple, Coefficient]:
    """Per (provider, model, effort, window): sum of measured shares / sum of weights."""
    groups: dict[tuple, list[ReviewRow]] = {}
    for row in rows:
        groups.setdefault(row.group, []).append(row)
    result: dict[tuple, Coefficient] = {}
    for group, members in groups.items():
        basis = "usd" if any(m.price is not None for m in members) else "tokens"
        for window in windows:
            share_sum = weight_sum = 0.0
            count = 0
            for member in members:
                measured = member.measured(window)
                weight = member.weight(basis)
                if not isinstance(measured, float) or weight is None or weight <= 0:
                    continue
                share_sum += measured
                weight_sum += weight
                count += 1
            if count:
                result[(*group, window)] = Coefficient(share_sum / weight_sum, basis, count)
    return result


def apply_shares(rows: list[ReviewRow], windows: list[str]) -> dict[tuple, Coefficient]:
    coefficients = calibrate(rows, windows)
    for row in rows:
        for window in windows:
            measured = row.measured(window)
            if isinstance(measured, float):
                row.shares[window] = Share(measured, "measured", 1)
                continue
            if measured == "invalid":
                row.shares[window] = Share(None, "invalid")
                continue
            coefficient = coefficients.get((*row.group, window))
            weight = row.weight(coefficient.basis) if coefficient else None
            if coefficient is None or weight is None:
                row.shares[window] = Share(None, "none")
            else:
                row.shares[window] = Share(weight * coefficient.value, "estimate", coefficient.measurements)
    return coefficients


@dataclasses.dataclass
class Summary:
    since: datetime
    until: datetime | None
    rows: list[ReviewRow]
    windows: list[str]
    coefficients: dict[tuple, Coefficient]
    catalog: Catalog
    skipped_lines: int

    @property
    def without_usage(self) -> int:
        return sum(1 for r in self.rows if not r.has_usage)

    @property
    def unpriced(self) -> int:
        return sum(1 for r in self.rows if r.has_usage and r.cost is None)

    @property
    def with_format_problems(self) -> int:
        return sum(1 for r in self.rows if r.record.get("review.usage.format_problems"))

    @property
    def groups_without_measurements(self) -> list[tuple[str, str, str]]:
        measured = {key[:3] for key in self.coefficients}
        return sorted({r.group for r in self.rows} - measured)


def summarize(
    records: list[dict[str, Any]],
    *,
    prices: PriceBook,
    since: datetime,
    until: datetime | None,
    configured_windows: list[str],
    skipped_lines: int = 0,
) -> Summary:
    in_period = []
    for record in records:
        moment = _record_time(record)
        if moment is None or moment < since or (until is not None and moment >= until):
            continue
        in_period.append(record)
    rows = build_rows(in_period, prices)
    windows = windows_of(rows, configured_windows)
    coefficients = apply_shares(rows, windows)
    return Summary(
        since=since,
        until=until,
        rows=rows,
        windows=windows,
        coefficients=coefficients,
        catalog=prices.catalog,
        skipped_lines=skipped_lines,
    )


# -- grouping -----------------------------------------------------------------


def _key(row: ReviewRow, by: str) -> tuple:
    r = row.record
    if by == "review":
        return (r.get("time"), r.get("review.run_id"))
    if by == "mr":
        return (r.get("review.project") or "(manual)", r.get("review.mr.iid"))
    if by == "model":
        return row.group
    moment = _record_time(r)
    return (moment.date().isoformat() if moment else "?",)


def _sum(values: list[Any]) -> Any:
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(numbers) if numbers else None


def _share_cell(shares: list[Share]) -> tuple[float | None, str]:
    """Total share of a group and how it was obtained."""
    usable = [s for s in shares if s.value is not None]
    if not usable:
        kinds = {s.kind for s in shares}
        return None, ("сброс окна" if kinds == {"invalid"} else "")
    total = sum(s.value for s in usable)  # type: ignore[misc]
    measured = sum(1 for s in usable if s.kind == "measured")
    estimated = len(usable) - measured
    missing = len(shares) - len(usable)
    if estimated == 0:
        note = "измерено"
    else:
        basis = max(s.basis for s in usable if s.kind == "estimate")
        note = "оценка" if measured == 0 else "измерено+оценка"
        note += f", замеров {basis}"
    if missing:
        note += f", без данных {missing}"
    return total, note


def group_rows(summary: Summary, by: str) -> list[dict[str, Any]]:
    buckets: dict[tuple, list[ReviewRow]] = {}
    for row in summary.rows:
        buckets.setdefault(_key(row, by), []).append(row)
    result = []
    for key, members in sorted(buckets.items(), key=lambda item: tuple(str(k) for k in item[0])):
        first = members[0].record
        line: dict[str, Any] = {}
        if by == "review":
            line.update(
                {
                    "time": first.get("time"),
                    "run_id": first.get("review.run_id"),
                    "project": first.get("review.project"),
                    "mr": first.get("review.mr.iid"),
                    "outcome": first.get("review.outcome"),
                }
            )
        elif by == "mr":
            line.update({"project": key[0], "mr": key[1]})
        elif by == "day":
            line["day"] = key[0]
        if by in ("review", "mr", "model"):
            line.update(
                {
                    "provider": first.get("gen_ai.provider.name") if by != "model" else key[0],
                    "model": first.get("gen_ai.request.model") if by != "model" else key[1],
                    "effort": first.get("review.reasoning_effort") if by != "model" else key[2],
                }
            )
        line["reviews"] = len(members)
        line["fresh_input"] = _sum([m.tokens.input for m in members])
        line["cache_read"] = _sum([m.tokens.cache_read for m in members])
        line["cache_write"] = _sum([m.tokens.cache_write for m in members])
        line["output"] = _sum([m.tokens.output for m in members])
        line["reasoning"] = _sum([m.tokens.reasoning for m in members])
        duration_ms = _sum([m.record.get("review.duration_ms") for m in members])
        line["duration_min"] = round(duration_ms / 60000, 1) if duration_ms is not None else None
        line["files"] = _sum([m.record.get("review.change.files") for m in members])
        line["lines"] = _sum(
            [
                (m.record.get("review.change.lines_added") or 0)
                + (m.record.get("review.change.lines_deleted") or 0)
                if m.record.get("review.change.files") is not None
                else None
                for m in members
            ]
        )
        findings = [m.record.get("review.findings") for m in members]
        for severity in ("blocker", "major", "minor"):
            line[severity] = _sum([f.get(severity) for f in findings if isinstance(f, dict)])
        cost = _sum([m.cost for m in members])
        line["cost_usd"] = round(cost, 4) if cost is not None else None
        sources = sorted({m.price.source for m in members if m.price is not None})
        line["price_source"] = ", ".join(sources) if sources else ("n/a" if any(m.has_usage for m in members) else "")
        for window in summary.windows:
            value, note = _share_cell([m.shares[window] for m in members])
            line[f"share_{window}_pct"] = round(value, 2) if value is not None else None
            line[f"share_{window}_note"] = note
        result.append(line)
    return result


# -- output -------------------------------------------------------------------


def header_lines(summary: Summary) -> list[str]:
    until = summary.until.strftime("%Y-%m-%d") if summary.until else "сейчас"
    lines = [
        f"Период: {summary.since.strftime('%Y-%m-%d %H:%M')} — {until}",
        f"Ревью: {len(summary.rows)}; без данных о расходе: {summary.without_usage}; "
        f"без цены (не вошли в сумму $): {summary.unpriced}; "
        f"с проблемами формата харнесса: {summary.with_format_problems}",
    ]
    catalog = summary.catalog
    stamp = catalog.as_of.strftime("%Y-%m-%d %H:%M") if catalog.as_of else "—"
    lines.append(f"Справочник цен: {catalog.origin} ({stamp})")
    if catalog.warning:
        lines.append(f"Предупреждение: {catalog.warning}")
    if summary.skipped_lines:
        lines.append(f"Предупреждение: нечитаемых строк журнала пропущено: {summary.skipped_lines}")
    if summary.with_format_problems:
        lines.append(
            "Предупреждение: у части записей формат ответа харнесса был неожиданным — "
            "их токены могут быть неполными (см. review.usage.format_problems в журнале)"
        )
    missing = summary.groups_without_measurements
    if missing and summary.windows:
        names = ", ".join(f"{p}/{m} ({e})" for p, m, e in missing)
        lines.append(
            f"Доля лимитов не оценена для: {names} — нужны интерактивные прогоны "
            "с замером квоты этой модели"
        )
    lines.append(
        "Доля лимитов «оценка» — пересчёт по замерам той же модели; параллельная "
        "работа в ChatGPT/Codex во время замера её завышает."
    )
    return lines


def render(summary: Summary, by: str, fmt: str) -> str:
    table = group_rows(summary, by)
    if fmt == "json":
        return json.dumps(
            {
                "period": {
                    "since": summary.since.isoformat(timespec="seconds"),
                    "until": summary.until.isoformat(timespec="seconds") if summary.until else None,
                },
                "reviews": len(summary.rows),
                "without_usage": summary.without_usage,
                "unpriced": summary.unpriced,
                "format_problems": summary.with_format_problems,
                "skipped_lines": summary.skipped_lines,
                "price_catalog": {
                    "origin": summary.catalog.origin,
                    "as_of": summary.catalog.as_of.isoformat(timespec="seconds") if summary.catalog.as_of else None,
                    "warning": summary.catalog.warning,
                },
                "rows": table,
            },
            ensure_ascii=False,
            indent=2,
        )
    if fmt == "csv":
        buffer = io.StringIO()
        columns = list(table[0].keys()) if table else []
        writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(table)
        return buffer.getvalue()
    return "\n".join([*header_lines(summary), "", _text_table(table)])


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _text_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(нет строк)"
    columns = list(rows[0].keys())
    cells = [[_cell(row.get(c)) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths))]
    lines.append("  ".join("-" * w for w in widths))
    lines.extend("  ".join(v.ljust(w) for v, w in zip(r, widths)) for r in cells)
    return "\n".join(lines)
