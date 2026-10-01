import subprocess
import sys


def test_help_runs_and_prints_usage():
    result = subprocess.run(
        [sys.executable, "-m", "review_agent", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "--repo" in result.stdout
