import re
import subprocess

import yaml

from review_agent.pipeline import run_review


def test_run_review_end_to_end_with_stub_harness(git_repo_with_base_and_head, tmp_path):
    fixture = git_repo_with_base_and_head
    scratch_dir = tmp_path / "scratch"
    reports_dir = tmp_path / "reports"

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "provider": {
                    "name": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "reasoning_effort": "medium",
                },
                "harness": {"command": ["stub-harness", "{prompt_file}", "{agents_file}"]},
                "report": {"output_path": str(reports_dir / "review-{run_id}.md")},
            }
        ),
        encoding="utf-8",
    )

    captured_argv = {}

    def stub_runner(argv, **kwargs):
        captured_argv["argv"] = argv
        captured_argv["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="# Review\n\nNo issues found.", stderr=""
        )

    report_path = run_review(
        repo_path=fixture["repo"],
        base_sha=fixture["base_sha"],
        head_sha=fixture["head_sha"],
        mr_title="Test MR",
        mr_description="Test description",
        config_path=config_path,
        scratch_dir=scratch_dir,
        harness_runner=stub_runner,
    )

    # Report was produced with the harness's output.
    assert report_path.exists()
    assert report_path.read_text(encoding="utf-8") == "# Review\n\nNo issues found."

    # The harness was actually invoked, inside the isolated worktree.
    assert captured_argv["argv"][0] == "stub-harness"
    assert captured_argv["cwd"] is not None

    # The worktree is gone afterward.
    run_id_match = re.search(r"review-(.+)\.md$", report_path.name)
    assert run_id_match is not None
    run_id = run_id_match.group(1)
    assert not (scratch_dir / run_id / "worktree").exists()

    # The primary repo was never touched.
    assert (fixture["repo"] / "file.txt").read_text(encoding="utf-8") == "v2\n"
