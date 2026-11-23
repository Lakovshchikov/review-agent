import pytest

from review_agent.publishing import (
    build_marker,
    find_marker_shas,
    format_comment,
    is_already_reviewed,
    is_report_usable,
    latest_report,
    newer_report_exists,
    replies_after,
    round_number,
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
    # An ordinary pass never re-reviews; a further round is `poll --again`.
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


# -- rounds: previous report, replies, round number ---------------------------


def _n(note_id, body="", username="ai-reviewer", created="2026-11-20T10:00:00Z", system=False):
    return {
        "id": note_id,
        "body": body,
        "author": {"username": username},
        "created_at": created,
        "system": system,
    }


def test_latest_report_is_the_newest_marker_comment_of_the_bot():
    notes = [
        _n(1, f"first {build_marker(OLD_HEAD)}"),
        _n(5, f"second {build_marker(HEAD)}"),
        _n(3, "chat"),
    ]
    report = latest_report(notes, "ai-reviewer")
    assert report.note_id == 5 and report.head_sha == HEAD
    assert build_marker(HEAD) in report.body


def test_latest_report_ignores_foreign_markers_and_claims():
    notes = [
        _n(1, build_marker(HEAD), username="someone"),
        _n(2, "<!-- ai-review-claim: sha=" + HEAD + " started=2026-11-20T10:00:00Z -->"),
    ]
    assert latest_report(notes, "ai-reviewer") is None


def test_latest_report_none_without_notes():
    assert latest_report([], "ai-reviewer") is None


def test_replies_are_other_users_comments_after_the_report_in_order():
    notes = [
        _n(1, "early chat", username="dev"),
        _n(2, build_marker(HEAD)),
        _n(5, "fixed it", username="dev", created="2026-11-21T09:00:00Z"),
        _n(4, "added 1 commit", username="dev", system=True),
        _n(3, "disagree with #2", username="alice"),
        _n(6, "bot chatter without marker"),
    ]
    report = latest_report(notes, "ai-reviewer")
    replies = replies_after(notes, "ai-reviewer", report)
    assert [(r.author, r.body) for r in replies] == [
        ("alice", "disagree with #2"),
        ("dev", "fixed it"),
    ]
    assert replies[1].created_at == "2026-11-21T09:00:00Z"


def test_replies_only_after_the_latest_of_several_reports():
    notes = [
        _n(1, build_marker(OLD_HEAD)),
        _n(2, "old reply", username="dev"),
        _n(3, build_marker(HEAD)),
        _n(4, "new reply", username="dev"),
    ]
    report = latest_report(notes, "ai-reviewer")
    assert [r.body for r in replies_after(notes, "ai-reviewer", report)] == ["new reply"]


def test_newer_report_exists_compares_note_ids():
    notes = [_n(2, build_marker(OLD_HEAD)), _n(7, build_marker(HEAD))]
    assert newer_report_exists(notes, "ai-reviewer", 2)
    assert not newer_report_exists(notes, "ai-reviewer", 7)
    assert not newer_report_exists([_n(9, build_marker(HEAD), username="x")], "ai-reviewer", 2)


def test_round_number_counts_our_reports_plus_one():
    assert round_number([], "ai-reviewer") == 1
    notes = [_n(1, build_marker(OLD_HEAD)), _n(2, build_marker(HEAD)), _n(3, "x", username="dev")]
    assert round_number(notes, "ai-reviewer") == 3


def test_first_round_comment_is_exactly_the_old_one():
    first = format_comment(report="# R " + "x" * 300, head_sha=HEAD, base_sha=BASE, model="m")
    explicit = format_comment(
        report="# R " + "x" * 300, head_sha=HEAD, base_sha=BASE, model="m", round=1
    )
    assert first == explicit
    assert first.startswith("### 🤖 AI code review\n\nАвтоматический отчёт по коммиту")
    assert "повторное" not in first


def test_later_round_comment_says_so_and_names_previous_commit():
    body = format_comment(
        report="# R", head_sha=HEAD, base_sha=BASE, model="m", round=2, previous_sha=OLD_HEAD
    )
    assert "повторное ревью (раунд 2)" in body
    assert OLD_HEAD[:12] in body and HEAD[:12] in body
    assert body.rstrip().endswith(build_marker(HEAD))


def test_later_round_comment_without_previous_commit():
    body = format_comment(report="# R", head_sha=HEAD, base_sha=BASE, model="m", round=3)
    assert "раунд 3" in body and "Предыдущий отчёт" not in body
