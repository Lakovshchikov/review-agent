"""The usage ledger: one JSON line per review run (design.md decision 3).

`<work_dir>/usage/ledger.jsonl`, append-only, UTF-8. Field names follow
OpenTelemetry's generative-AI conventions (`gen_ai.*`) where one exists,
everything review-specific lives under `review.*`; no field is specific
to one model provider. Only numbers and identifiers are written - never
prompt, report, MR title/description or code.

Writing never raises: accounting must not affect a review
(spec "Usage collection never affects the review").
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from review_agent.config import Config, ProviderConfig
from review_agent.review_stats import ChangeSize, count_findings, measure_change
from review_agent.usage_source import RunUsage, SessionUsage, UsageSource

SCHEMA = "review-agent.usage/1"
LEDGER_NAME = "ledger.jsonl"

Warn = Callable[[str], None]


@dataclasses.dataclass(frozen=True)
class RunLabels:
    """Who started the run and for what - known to the caller, not the engine."""

    source: str  # "poll" | "manual"
    project: str | None = None
    mr_iid: int | None = None


@dataclasses.dataclass(frozen=True)
class QuotaReading:
    """Interactive readings of subscription limit windows, percent used."""

    provider: str
    before: dict[str, float] = dataclasses.field(default_factory=dict)
    after: dict[str, float] = dataclasses.field(default_factory=dict)


def _session_entry(session: SessionUsage) -> dict[str, Any]:
    t = session.tokens
    return {
        "id": session.id,
        "parent": session.parent,
        "agent": session.agent,
        "input_tokens": t.total_input,
        "cache_read_tokens": t.cache_read,
        "cache_write_tokens": t.cache_write,
        "output_tokens": t.output,
        "reasoning_tokens": t.reasoning,
        "steps": session.steps,
        "duration_ms": session.duration_ms,
    }


def build_review_record(
    *,
    time: datetime,
    run_id: str,
    labels: RunLabels,
    base_sha: str,
    head_sha: str,
    provider: ProviderConfig,
    skills: list[str],
    outcome: str,
    duration_ms: int,
    usage: RunUsage | None,
    change: ChangeSize | None,
    findings: dict[str, int] | None,
    quota: QuotaReading | None,
) -> dict[str, Any]:
    """The ledger record of one review run; every key is always present."""
    totals = usage.totals if usage is not None else None
    missing_reason = usage.missing_reason if usage is not None else "usage not collected"
    return {
        "schema": SCHEMA,
        "time": time.astimezone().isoformat(timespec="seconds"),
        "review.run_id": run_id,
        "review.source": labels.source,
        "review.project": labels.project,
        "review.mr.iid": labels.mr_iid,
        "review.base_sha": base_sha,
        "review.head_sha": head_sha,
        "gen_ai.provider.name": provider.name,
        "gen_ai.request.model": provider.model,
        "review.reasoning_effort": provider.reasoning_effort,
        "review.skills": [Path(s).name for s in skills],
        "review.harness.name": usage.harness_name if usage is not None else None,
        "review.harness.version": usage.harness_version if usage is not None else None,
        "review.outcome": outcome,
        "review.duration_ms": duration_ms,
        # OpenTelemetry's input_tokens is ALL input, cached included.
        "gen_ai.usage.input_tokens": totals.total_input if totals else None,
        "gen_ai.usage.cache_read.input_tokens": totals.cache_read if totals else None,
        "gen_ai.usage.cache_creation.input_tokens": totals.cache_write if totals else None,
        "gen_ai.usage.output_tokens": totals.output if totals else None,
        "review.usage.reasoning_tokens": totals.reasoning if totals else None,
        "review.usage.steps": usage.steps if usage is not None else None,
        "review.usage.missing_reason": missing_reason,
        "review.usage.format_problems": list(usage.format_problems) if usage else [],
        "review.sessions": [_session_entry(s) for s in usage.sessions] if usage else [],
        "review.change.files": change.files if change else None,
        "review.change.lines_added": change.lines_added if change else None,
        "review.change.lines_deleted": change.lines_deleted if change else None,
        "review.change.commits": change.commits if change else None,
        "review.findings": findings,
        "review.quota": (
            {"provider": quota.provider, "before": quota.before, "after": quota.after}
            if quota is not None and (quota.before or quota.after)
            else None
        ),
    }


def append_record(ledger_path: Path, record: dict[str, Any], warn: Warn) -> bool:
    """Append one record as one line; a failure is reported through `warn`, never raised."""
    try:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        return True
    except (OSError, TypeError, ValueError) as exc:
        warn(f"Учёт расхода: не удалось записать журнал {ledger_path}: {exc}")
        return False


def read_records(ledger_path: Path) -> tuple[list[dict[str, Any]], int]:
    """All parseable records of the ledger and how many lines were skipped as unreadable."""
    if not ledger_path.is_file():
        return [], 0
    records: list[dict[str, Any]] = []
    skipped = 0
    with ledger_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if isinstance(record, dict) and str(record.get("schema", "")).startswith(
                "review-agent.usage/"
            ):
                records.append(record)
            else:
                skipped += 1
    return records, skipped


QuotaPrompt = Callable[[str], dict[str, float]]
"""Asks the user for the current percent used of each limit window.

Called with "before" or "after"; returns only the windows answered.
"""


class UsageRecorder:
    """Collects one run's usage and appends its record; never raises (design.md decision 2).

    Two warning channels: `warn` for problems the user must notice (harness
    output in an unexpected shape, unverified harness version, ledger not
    writable) - shown on the console and in the pass log, each distinct
    message once per process; `note` for routine gaps (session not found,
    e.g. a run interrupted before the harness started) - log file only.
    """

    def __init__(
        self,
        ledger_path: Path,
        source: UsageSource,
        *,
        warn: Warn,
        note: Warn,
        now_fn: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.ledger_path = ledger_path
        self.source = source
        self._warn = warn
        self._note = note
        self._now_fn = now_fn
        self._seen: set[str] = set()
        self.suppressed = 0

    def warn_once(self, message: str) -> None:
        if message in self._seen:
            self.suppressed += 1
            return
        self._seen.add(message)
        self._warn(message)

    def record_run(
        self,
        *,
        worktree_path: Path,
        run_id: str,
        labels: RunLabels,
        base_sha: str,
        head_sha: str,
        provider: ProviderConfig,
        skills: list[str],
        outcome: str,
        duration_ms: int,
        report: str | None,
        quota: QuotaReading | None,
    ) -> None:
        try:
            change = measure_change(worktree_path, base_sha, head_sha)
            usage = self.source.collect(worktree_path)
            for problem in usage.format_problems:
                self.warn_once(f"Учёт расхода: неожиданный ответ {usage.harness_name}: {problem}")
            where = f"{labels.project} !{labels.mr_iid}" if labels.project else run_id
            if usage.missing_reason:
                self._note(f"Учёт расхода {where}: токенов нет — {usage.missing_reason}")
            record = build_review_record(
                time=self._now_fn(),
                run_id=run_id,
                labels=labels,
                base_sha=base_sha,
                head_sha=head_sha,
                provider=provider,
                skills=skills,
                outcome=outcome,
                duration_ms=duration_ms,
                usage=usage,
                change=change,
                findings=count_findings(report),
                quota=quota,
            )
            append_record(self.ledger_path, record, self.warn_once)
        except Exception as exc:  # noqa: BLE001 - accounting never affects the review
            self.warn_once(f"Учёт расхода: запись для прогона {run_id} не сделана: {exc}")


def make_recorder(config: Config, *, warn: Warn, note: Warn) -> UsageRecorder | None:
    """The recorder for `config`, or None when usage accounting is disabled."""
    from review_agent.housekeeping import WorkDir
    from review_agent.usage_source import make_usage_source

    if not config.usage.enabled:
        return None
    return UsageRecorder(
        WorkDir.from_config(config).usage / LEDGER_NAME,
        make_usage_source(config),
        warn=warn,
        note=note,
    )
