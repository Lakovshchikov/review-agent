import json
import subprocess

import pytest

from review_agent.gitlab import GitLabClient, GitLabError, NoteNotFound, encode_project


class StubGlab:
    """Records argv and answers by matching a substring of the joined argv."""

    def __init__(self, responses):
        self.responses = responses  # list of (substring, returncode, stdout, stderr)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, "kwargs": kwargs})
        joined = " ".join(argv)
        for needle, code, out, err in self.responses:
            if needle in joined:
                return subprocess.CompletedProcess(argv, code, out, err)
        raise AssertionError(f"unexpected glab call: {joined}")


def _client(stub):
    return GitLabClient("gitlab.local", runner=stub, which=lambda name: f"/usr/bin/{name}")


def test_api_call_builds_argv_with_hostname_and_utf8():
    stub = StubGlab([("api", 0, json.dumps({"username": "ai-reviewer"}), "")])
    assert _client(stub).current_user() == "ai-reviewer"

    call = stub.calls[0]
    assert call["argv"] == ["/usr/bin/glab", "api", "--hostname", "gitlab.local", "user"]
    assert call["kwargs"]["encoding"] == "utf-8"
    assert call["kwargs"]["env"]["GITLAB_HOST"] == "gitlab.local"


def test_project_path_is_url_encoded():
    assert encode_project("b2c/front-shopping") == "b2c%2Ffront-shopping"
    stub = StubGlab(
        [("merge_requests/544", 0, json.dumps({"diff_refs": {"base_sha": "b" * 40, "head_sha": "h" * 40}}), "")]
    )
    refs = _client(stub).get_diff_refs("b2c/front-shopping", 544)
    assert (refs.base_sha, refs.head_sha) == ("b" * 40, "h" * 40)
    assert "projects/b2c%2Ffront-shopping/merge_requests/544" in stub.calls[0]["argv"]


def test_glab_failure_raises_typed_error_with_stderr():
    stub = StubGlab([("api", 1, "", "401 Unauthorized")])
    with pytest.raises(GitLabError, match="401 Unauthorized"):
        _client(stub).current_user()


def test_paginated_output_with_several_pages_is_flattened():
    pages = json.dumps([{"id": 1, "body": "a"}]) + json.dumps([{"id": 2, "body": "b"}])
    stub = StubGlab([("notes", 0, pages, "")])
    notes = _client(stub).list_notes("a/b", 1)
    assert [n["id"] for n in notes] == [1, 2]
    assert "--paginate" in stub.calls[0]["argv"]


def test_mr_metadata_via_mr_view_with_cyrillic():
    payload = {
        "iid": 544,
        "title": "PLATVTRN-1258: Процесс оставления отзыва",
        "description": "Описание — с тире",
        "author": {"username": "ivanov"},
        "web_url": "https://gitlab.local/b2c/front-shopping/-/merge_requests/544",
        "draft": False,
    }
    stub = StubGlab([("mr view", 0, json.dumps(payload, ensure_ascii=False), "")])
    meta = _client(stub).get_mr_metadata("b2c/front-shopping", 544)

    assert stub.calls[0]["argv"][1:] == ["mr", "view", "544", "-R", "b2c/front-shopping", "--output", "json"]
    assert meta.title == payload["title"]
    assert meta.description == payload["description"]
    assert meta.author == "ivanov"
    assert meta.web_url == payload["web_url"]


def test_mr_metadata_missing_field_is_clear_error():
    stub = StubGlab([("mr view", 0, json.dumps({"iid": 1, "title": "t", "author": {"username": "u"}}), "")])
    with pytest.raises(GitLabError, match="web_url"):
        _client(stub).get_mr_metadata("a/b", 1)


def test_candidates_merged_by_iid_and_drafts_filtered():
    responses = [
        (
            "reviewer_username=ai-reviewer",
            0,
            json.dumps([{"iid": 5, "title": "A", "draft": False}, {"iid": 7, "title": "Draft", "draft": True}]),
            "",
        ),
        ("reviewer_username=petrov", 0, json.dumps([{"iid": 5, "title": "A", "draft": False}]), ""),
    ]
    client = _client(StubGlab(responses))

    default = client.list_review_candidates("a/b", ["ai-reviewer", "petrov"], review_drafts=False)
    assert [c.iid for c in default] == [5]

    with_drafts = client.list_review_candidates("a/b", ["ai-reviewer", "petrov"], review_drafts=True)
    assert [c.iid for c in with_drafts] == [5, 7]


def test_candidate_query_filters_by_state_and_reviewer():
    stub = StubGlab([("merge_requests?", 0, "[]", "")])
    _client(stub).list_review_candidates("a/b", ["ai-reviewer"], review_drafts=False)
    endpoint = stub.calls[0]["argv"][-1]
    assert endpoint.startswith("projects/a%2Fb/merge_requests?")
    assert "state=opened" in endpoint and "reviewer_username=ai-reviewer" in endpoint


def test_include_closed_queries_all_states_and_keeps_state():
    stub = StubGlab([("merge_requests?", 0, json.dumps([{"iid": 3, "title": "t", "state": "merged"}]), "")])
    candidates = _client(stub).list_review_candidates(
        "a/b", ["ai-reviewer"], review_drafts=False, include_closed=True
    )
    assert "state=all" in stub.calls[0]["argv"][-1]
    assert candidates[0].state == "merged"


def test_post_note_sends_body_from_file_not_argv(tmp_path):
    stub = StubGlab([("--method POST", 0, json.dumps({"id": 4242, "body": "..."}), "")])
    report = "## Находки\n\n" + "очень длинный отчёт\n" * 2000
    body_file = tmp_path / "note.json"

    assert _client(stub).post_note("a/b", 3, report, body_file=body_file) == 4242

    argv = stub.calls[0]["argv"]
    assert report not in " ".join(argv)
    assert argv[argv.index("--input") + 1] == str(body_file)
    assert argv[-1] == "projects/a%2Fb/merge_requests/3/notes"
    assert json.loads(body_file.read_text(encoding="utf-8")) == {"body": report}


def test_preflight_missing_glab():
    client = GitLabClient("gitlab.local", runner=StubGlab([]), which=lambda name: None)
    with pytest.raises(GitLabError, match="not found in PATH"):
        client.preflight()


def test_preflight_auth_failure_names_cause():
    stub = StubGlab([("api", 1, "", "has not been authenticated with glab")])
    with pytest.raises(GitLabError, match="glab auth login --hostname gitlab.local"):
        _client(stub).preflight()


def test_preflight_success_returns_username():
    stub = StubGlab([("api", 0, json.dumps({"username": "ai-reviewer"}), "")])
    assert _client(stub).preflight() == "ai-reviewer"


def test_post_note_without_id_in_response_is_an_error(tmp_path):
    stub = StubGlab([("--method POST", 0, "{}", "")])
    with pytest.raises(GitLabError, match="note id"):
        _client(stub).post_note("a/b", 3, "x", body_file=tmp_path / "n.json")


def test_update_note_puts_body_from_file(tmp_path):
    stub = StubGlab([("--method PUT", 0, "{}", "")])
    body_file = tmp_path / "upd.json"
    report = "## Отчёт\n" * 500

    _client(stub).update_note("a/b", 3, 77, report, body_file=body_file)

    argv = stub.calls[0]["argv"]
    assert report not in " ".join(argv)
    assert argv[argv.index("--method") + 1] == "PUT"
    assert argv[argv.index("--input") + 1] == str(body_file)
    assert argv[-1] == "projects/a%2Fb/merge_requests/3/notes/77"
    assert json.loads(body_file.read_text(encoding="utf-8")) == {"body": report}


def test_delete_note(tmp_path):
    stub = StubGlab([("--method DELETE", 0, "", "")])
    _client(stub).delete_note("a/b", 3, 77)
    argv = stub.calls[0]["argv"]
    assert argv[argv.index("--method") + 1] == "DELETE"
    assert "--input" not in argv
    assert argv[-1] == "projects/a%2Fb/merge_requests/3/notes/77"


@pytest.mark.parametrize("call", ["update", "delete"])
def test_missing_note_raises_note_not_found(tmp_path, call):
    stub = StubGlab([("--method", 1, "", "HTTP 404: 404 Not found")])
    client = _client(stub)
    with pytest.raises(NoteNotFound):
        if call == "update":
            client.update_note("a/b", 3, 77, "x", body_file=tmp_path / "u.json")
        else:
            client.delete_note("a/b", 3, 77)


def test_other_write_errors_stay_generic(tmp_path):
    stub = StubGlab([("--method", 1, "", "HTTP 403: Forbidden")])
    with pytest.raises(GitLabError) as info:
        _client(stub).delete_note("a/b", 3, 77)
    assert not isinstance(info.value, NoteNotFound)
