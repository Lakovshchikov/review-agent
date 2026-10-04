"""CLI entrypoints.

- `review-agent --repo ... --base ... --head ...` - manual review: one
  full review (checkout, harness invocation, report, cleanup) and exit,
  with no GitLab calls at all.
- `review-agent poll [--all] [--dry-run] [--debug]` - one polling pass
  against GitLab (see polling.py). The only command that talks to GitLab.
- `review-agent usage [--since] [--until] [--by] [--format] [--wide]` - summary of
  the usage ledger (usage_summary.py); reads the ledger and downloads the
  price catalog, nothing else.

Neither starts a scheduler or daemon (see AGENTS.md section 5). Both
write only under `storage.work_dir` from the config (housekeeping.py),
except the manual report, and both hold `<work_dir>/poll.lock` while
running.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _add_debug_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Keep all files of each review run (prompt, safety note, report, "
            "harness stderr, publication bodies) in <work_dir>/debug/ instead of "
            "deleting them; they expire after storage.retention_days."
        ),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-agent",
        description=(
            "Run an agentic code review of a merge request against an isolated "
            "git worktree and write the findings as a markdown report. "
            "See also: 'review-agent poll --help' for the GitLab polling pass."
        ),
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to the local git repository containing the merge request.",
    )
    parser.add_argument(
        "--base",
        required=True,
        help="Base commit SHA of the merge request.",
    )
    parser.add_argument(
        "--head",
        required=True,
        help="Head commit SHA of the merge request.",
    )
    parser.add_argument(
        "--mr-title",
        default="",
        help="Merge request title, passed to the review prompt.",
    )
    parser.add_argument(
        "--mr-description",
        default="",
        help="Merge request description, passed to the review prompt.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the review-agent YAML config (default: config.yaml).",
    )
    _add_debug_flag(parser)
    return parser


def build_poll_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-agent poll",
        description=(
            "One polling pass: find open GitLab merge requests where a configured "
            "reviewer is assigned, review them, and publish each report as an MR "
            "comment. Interactive by default: lists the MRs found (with links) and "
            "reviews only the one you pick."
        ),
    )
    parser.add_argument(
        "--all",
        dest="review_all",
        action="store_true",
        help="Automatic mode: review every MR found, one after another, without prompting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except writing to GitLab: reports and would-be comments stay local.",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help=(
            "Also find closed and merged MRs, not only open ones - for testing on "
            "MRs where a bot comment bothers nobody."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the review-agent YAML config with a 'gitlab' section (default: config.yaml).",
    )
    _add_debug_flag(parser)
    return parser


def poll_main(argv: list[str]) -> int:
    args = build_poll_arg_parser().parse_args(argv)

    from review_agent.polling import run_poll

    return run_poll(
        config_path=Path(args.config),
        review_all=args.review_all,
        dry_run=args.dry_run,
        include_closed=args.include_closed,
        debug=args.debug,
    )


def build_usage_arg_parser() -> argparse.ArgumentParser:
    from review_agent.usage_summary import FORMATS, GROUPINGS

    parser = argparse.ArgumentParser(
        prog="review-agent usage",
        description=(
            "Summary of the usage ledger (<work_dir>/usage/ledger.jsonl): tokens, time, "
            "change size, findings, API-equivalent cost from the current price catalog "
            "and the share of subscription limits (measured or estimated)."
        ),
    )
    parser.add_argument(
        "--since", default=None, help="Start: '<N>d' (last N days) or YYYY-MM-DD (default: the whole ledger)."
    )
    parser.add_argument("--until", default=None, help="End date YYYY-MM-DD, inclusive (default: now).")
    parser.add_argument("--by", choices=GROUPINGS, default="review", help="Grouping (default: review).")
    parser.add_argument("--format", choices=FORMATS, default="table", help="Output format (default: table).")
    parser.add_argument(
        "--wide",
        action="store_true",
        help="Table with every column (tokens by kind, change size, findings, price source...). "
        "CSV and JSON always have every column.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the review-agent YAML config (default: config.yaml).",
    )
    return parser


def _print_safe(text: str) -> None:
    """print() that survives a console code page without Cyrillic."""
    try:
        print(text)
    except UnicodeEncodeError:
        stream = sys.stdout
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")


def usage_main(argv: list[str], *, fetch=None) -> int:
    args = build_usage_arg_parser().parse_args(argv)

    from review_agent.config import ConfigError, load_config
    from review_agent.housekeeping import WorkDir
    from review_agent.usage_ledger import LEDGER_NAME, read_records
    from review_agent.usage_prices import CATALOG_CACHE_NAME, PriceBook, load_catalog
    from review_agent.usage_summary import PeriodError, parse_since, parse_until, render, summarize

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    try:
        since = parse_since(args.since) if args.since else None
        until = parse_until(args.until) if args.until else None
    except PeriodError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    usage_dir = WorkDir.from_config(config).usage
    records, skipped = read_records(usage_dir / LEDGER_NAME)
    if not records:
        _print_safe(f"Нет данных: журнал расхода {usage_dir / LEDGER_NAME} пуст или не существует.")
        return 0
    catalog_kwargs = {"fetch": fetch} if fetch is not None else {}
    catalog = load_catalog(config.usage.price_catalog, usage_dir / CATALOG_CACHE_NAME, **catalog_kwargs)
    summary = summarize(
        records,
        prices=PriceBook(catalog, config.usage.price_overrides),
        since=since,
        until=until,
        configured_windows=config.usage.quota_windows,
        skipped_lines=skipped,
    )
    if not summary.rows and args.format == "table":
        period = f"{args.since or 'всё время'}{' — ' + args.until if args.until else ''}"
        _print_safe(f"Нет данных за период ({period}).")
        return 0
    _print_safe(render(summary, args.by, args.format, wide=args.wide))
    return 0


def _ensure_streams() -> None:
    """Under pythonw.exe (scheduled task without a window) there is no stdout/stderr.

    The pass log does not need them, but print() and argparse errors would
    crash on None - send that output nowhere instead.
    """
    import os

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _ensure_streams()
    if argv is None:
        argv = sys.argv[1:]
    # Subcommand detection by first token keeps the validated manual form
    # (`review-agent --repo ...`) working unchanged - design.md decision 6.
    if argv and argv[0] == "poll":
        return poll_main(argv[1:])
    if argv and argv[0] == "usage":
        return usage_main(argv[1:])

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # Deferred imports: keep `--help` fast and avoid importing the full
    # pipeline (and its dependencies) just to print usage.
    from review_agent import pipeline
    from review_agent.config import ConfigError, load_config
    from review_agent.housekeeping import WorkDir, stamp
    from review_agent.lock import PollLockBusy, poll_lock
    from review_agent.polling import stdin_is_interactive
    from review_agent.quota_prompt import make_quota_prompt
    from review_agent.report import write_report
    from review_agent.usage_ledger import RunLabels, make_recorder

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    work_dir = WorkDir.from_config(config)
    debug_dir = work_dir.debug / f"{stamp()}-manual-{args.head[:12]}" if args.debug else None

    def to_stderr(message: str) -> None:
        print(f"Предупреждение: {message}", file=sys.stderr)

    usage = make_recorder(config, warn=to_stderr, note=to_stderr)
    quota_prompt = (
        make_quota_prompt(config.usage.quota_windows, config.provider.name)
        if usage is not None and stdin_is_interactive()
        else None
    )

    try:
        with poll_lock(work_dir.root):
            result = pipeline.run_review(
                repo_path=Path(args.repo),
                base_sha=args.base,
                head_sha=args.head,
                mr_title=args.mr_title,
                mr_description=args.mr_description,
                config=config,
                debug_dir=debug_dir,
                usage=usage,
                labels=RunLabels(source="manual"),
                quota_prompt=quota_prompt,
            )
    except PollLockBusy as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report_path = write_report(
        result.report, Path(config.report.output_path.format(run_id=result.run_id))
    )
    print(f"Review report written to: {report_path}")
    if debug_dir is not None:
        print(f"Debug artifacts kept in: {debug_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
