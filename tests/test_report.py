from review_agent.report import write_report


def test_write_report_creates_file_with_content(tmp_path):
    output_path = tmp_path / "reports" / "review-123.md"

    result = write_report("# Review\n\nfound 2 issues", output_path)

    assert result == output_path
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == "# Review\n\nfound 2 issues"
