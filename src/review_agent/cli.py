"""CLI entrypoint: repository + base/head commits in, markdown report out.

A single invocation does the full review (checkout, harness invocation,
report generation, cleanup) and exits - no GitLab calls, no scheduling.
Those are out of scope for this change (see AGENTS.md roadmap).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-agent",
        description=(
            "Run an agentic code review of a merge request against an isolated "
            "git worktree and write the findings as a markdown report."
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
    parser.add_argument(
        "--scratch-dir",
        default=".review-agent-scratch",
        help="Directory for isolated worktrees and per-run scratch files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # Deferred import: keeps `--help` fast and avoids importing the full
    # pipeline (and its dependencies) just to print usage.
    from review_agent.pipeline import run_review

    report_path = run_review(
        repo_path=Path(args.repo),
        base_sha=args.base,
        head_sha=args.head,
        mr_title=args.mr_title,
        mr_description=args.mr_description,
        config_path=Path(args.config),
        scratch_dir=Path(args.scratch_dir),
    )
    print(f"Review report written to: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
