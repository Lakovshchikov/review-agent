import pytest

from review_agent.publishing import (
    build_marker,
    find_marker_shas,
    format_comment,
    is_already_reviewed,
    is_report_usable,
)

HEAD = "eb73b471e4292dc40a78203b3c9f68c91ae4dec1"
OLD_HEAD = "1111111111111111111111111111111111111111"
BASE = "d6a9078821e467208acad0791586e577456c6c96"


def _note(body, username="ai-reviewer"):
    return {"body": body, "author": {"username": username}}


def test_marker_round_trip():
    marker = build_marker(HEAD)
    assert marker == f"<!-- ai-review: sha={HEAD} -->"
    assert find_marker_shas(f"text\n{marker}\n") == [HEAD]


def test_marker_for_current_head_means_reviewed():
    assert is_already_reviewed([_note(build_marker(HEAD))], "ai-reviewer")


def test_marker_for_older_head_still_means_reviewed():
    # Re-review on new commits is out of scope for this change.
    assert is_already_reviewed([_note(f"old review {build_marker(OLD_HEAD)}")], "ai-reviewer")


def test_marker_from_another_author_is_ignored():
    assert not is_already_reviewed([_note(build_marker(HEAD), username="someone")], "ai-reviewer")


def test_notes_without_marker_mean_not_reviewed():
    notes = [_note("LGTM"), _note("<!-- some other comment -->"), {"body": "no author"}]
    assert not is_already_reviewed(notes, "ai-reviewer")


@pytest.mark.parametrize("report", ["", "   \n\n ", "Understood."])
def test_empty_or_short_report_is_unusable(report):
    assert not is_report_usable(report, min_chars=200)


def test_long_enough_report_is_usable():
    assert is_report_usable("# Находки\n" + "x" * 200, min_chars=200)


def test_comment_contains_reviewed_sha_marker_and_report_unchanged():
    report = "## Major\n\n- Гонка в `reset()` пагинации — «старый» запрос перетирает новый.\n\n## Minor\n- …"
    body = format_comment(report=report, head_sha=HEAD, base_sha=BASE, model="openai/gpt#medium")

    assert report in body
    assert body.rstrip().endswith(build_marker(HEAD))
    assert find_marker_shas(body) == [HEAD]
    assert HEAD[:12] in body and BASE[:12] in body
    assert "openai/gpt#medium" in body
