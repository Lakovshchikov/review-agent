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


# -- claims (design.md decision 7) -------------------------------------------

from datetime import datetime, timedelta, timezone

from review_agent.publishing import (
    build_claim_marker,
    find_claims,
    format_claim_comment,
    live_claims,
    stale_claims,
)

_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
_TTL = timedelta(minutes=240)


def _claim_note(note_id, age, *, author="bot", created_at=True):
    started = _NOW - age
    note = {"id": note_id, "author": {"username": author}, "body": format_claim_comment(head_sha="a" * 40, started_at=started)}
    if created_at:
        note["created_at"] = started.strftime("%Y-%m-%dT%H:%M:%S.123Z")
    return note


def test_claim_comment_has_hidden_marker_and_text():
    body = format_claim_comment(head_sha="a" * 40, started_at=_NOW)
    assert build_claim_marker("a" * 40, _NOW) in body
    assert "<!-- ai-review-claim: sha=" + "a" * 40 + " started=2026-10-02T12:00:00Z -->" in body
    assert "выполняется" in body and "aaaaaaaaaaaa" in body


def test_claim_is_not_a_review_marker():
    notes = [_claim_note(1, timedelta(minutes=1))]
    assert not is_already_reviewed(notes, "bot")
    assert find_marker_shas(notes[0]["body"]) == []


def test_live_and_stale_claims_split_by_ttl():
    notes = [
        _claim_note(5, timedelta(minutes=10)),
        _claim_note(3, timedelta(hours=5)),
        _claim_note(4, timedelta(minutes=1)),
    ]
    assert [c.note_id for c in live_claims(notes, "bot", now=_NOW, ttl=_TTL)] == [4, 5]  # oldest id first
    assert [c.note_id for c in stale_claims(notes, "bot", now=_NOW, ttl=_TTL)] == [3]


def test_claims_of_other_authors_are_ignored():
    notes = [_claim_note(1, timedelta(minutes=1), author="ivanov")]
    assert find_claims(notes, "bot") == []


def test_claim_time_falls_back_to_marker_then_counts_as_stale():
    without_created_at = _claim_note(1, timedelta(minutes=1), created_at=False)
    assert [c.note_id for c in live_claims([without_created_at], "bot", now=_NOW, ttl=_TTL)] == [1]

    broken = {"id": 2, "author": {"username": "bot"}, "body": "<!-- ai-review-claim: sha=" + "a" * 40 + " started=garbage -->"}
    assert live_claims([broken], "bot", now=_NOW, ttl=_TTL) == []
    assert [c.note_id for c in stale_claims([broken], "bot", now=_NOW, ttl=_TTL)] == [2]


def test_final_comment_carries_no_claim_marker():
    body = format_comment(report="# Отчёт " + "x" * 300, head_sha="a" * 40, base_sha="b" * 40, model="m")
    assert "ai-review-claim" not in body
