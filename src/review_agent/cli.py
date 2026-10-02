"""CLI entrypoints.

- `review-agent --repo ... --base ... --head ...` - manual review: one
  full review (checkout, harness invocation, report, cleanup) and exit,
  with no GitLab calls at all.
- `review-agent poll [--all] [--dry-run] [--debug]` - one polling pass
  against GitLab (see polling.py). The only command that talks to GitLab.

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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # Subcommand detection by first token keeps the validated manual form
    # (`review-agent --repo ...`) working unchanged - design.md decision 6.
    if argv and argv[0] == "poll":
        return poll_main(argv[1:])

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # Deferred imports: keep `--help` fast and avoid importing the full
    # pipeline (and its dependencies) just to print usage.
    from review_agent import pipeline
    from review_agent.config import ConfigError, load_config
    from review_agent.housekeeping import WorkDir, stamp
    from review_agent.lock import PollLockBusy, poll_lock
    from review_agent.report import write_report

    try:
        config = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    work_dir = WorkDir.from_config(config)
    debug_dir = work_dir.debug / f"{stamp()}-manual-{args.head[:12]}" if args.debug else None

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
