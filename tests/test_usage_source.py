"""OpenCode usage source on a stub `opencode`.

Session shapes follow what `opencode session export` printed on v2.0.21
during the spike (info: tokens/model/location/outcome/time; messages:
id/time/type/agent/previous). How subagent sessions are linked was not
observed yet (tasks 1.1), so both plausible forms are exercised.
"""

import json
import subprocess
from pathlib import Path

from review_agent.usage_source import MAX_SESSIONS, NoUsageSource, OpenCodeUsageSource


def info(session_id, directory, *, parent=None, tokens=None, extra=None):
    data = {
        "id": session_id,
        "projectID": "98cee2e2",
        "agent": "reviewer" if parent is None else "explore",
        "model": {"id": "gpt-5.6-terra", "providerID": "openai", "variant": "medium"},
        "cost": 0,
        "tokens": tokens
        or {"input": 218796, "output": 3696, "reasoning": 2269, "cache": {"read": 121856, "write": 0}},
        "outcome": "succeeded",
        "time": {"created": 1_000_000, "updated": 1_290_000, "idle": 1_280_000},
        "location": {"directory": str(directory)},
    }
    if parent is not None:
        data["parentID"] = parent
    data.update(extra or {})
    return data


def export(info_data, messages=None):
    return json.dumps(
        {
            "info": info_data,
            "messages": messages
            if messages is not None
            else [
                {"id": "msg_1", "time": {}, "type": "user", "agent": "reviewer", "text": "СЕКРЕТНЫЙ ПРОМПТ"},
                {"id": "msg_2", "time": {}, "type": "assistant", "agent": "reviewer"},
                {"id": "msg_3", "time": {}, "type": "assistant", "agent": "reviewer"},
            ],
        },
        ensure_ascii=False,
    )


class FakeOpenCode:
    def __init__(self, sessions, *, listed=None, version="opencode v2.0.21", list_output=None):
        self.sessions = sessions  # id -> export JSON text
        self.listed = listed if listed is not None else list(sessions)
        self.version = version
        self.list_output = list_output
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        args = argv[1:]
        if args == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, self.version + "\n", "")
        if args[:2] == ["session", "list"]:
            out = self.list_output
            if out is None:
                out = "".join(f"{sid}  Код-ревью  04.10.2026, 12:51:53\n" for sid in self.listed)
            return subprocess.CompletedProcess(argv, 0, out, "")
        if args[:2] == ["session", "export"]:
            sid = args[2]
            if sid not in self.sessions:
                return subprocess.CompletedProcess(argv, 1, "", "not found")
            return subprocess.CompletedProcess(argv, 0, self.sessions[sid], "")
        raise AssertionError(argv)


def source(fake):
    return OpenCodeUsageSource(
        ["opencode", "session", "list"],
        ["opencode", "session", "export", "{session_id}"],
        runner=fake,
        which=lambda name: None,
    )


def test_root_session_found_by_worktree_among_others(tmp_path):
    worktree = tmp_path / "run-1" / "worktree"
    worktree.mkdir(parents=True)
    other = tmp_path / "run-0" / "worktree"
    fake = FakeOpenCode(
        {
            "ses_newer": export(info("ses_newer", other)),
            "ses_mine": export(info("ses_mine", worktree)),
        }
    )
    usage = source(fake).collect(worktree)
    assert usage.missing_reason is None
    assert usage.format_problems == []
    assert usage.harness_version == "2.0.21"
    assert [s.id for s in usage.sessions] == ["ses_mine"]
    session = usage.sessions[0]
    assert (session.tokens.input, session.tokens.cache_read, session.tokens.output) == (218796, 121856, 3696)
    assert session.duration_ms == 290_000
    assert session.steps == 2
    # no text of the export survives
    assert "СЕКРЕТНЫЙ" not in repr(usage)


def test_subagent_by_parent_id_reference(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    child_tokens = {"input": 40000, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    root_tokens = {"input": 100000, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    root_messages = [
        {"id": "msg_1", "type": "assistant", "parts": [{"tool": "task", "state": {"metadata": {"sessionId": "ses_child"}}}]}
    ]
    fake = FakeOpenCode(
        {
            "ses_root": export(info("ses_root", worktree, tokens=root_tokens), root_messages),
            "ses_child": export(info("ses_child", worktree, parent="ses_root", tokens=child_tokens), []),
        },
        listed=["ses_root"],  # children are not in `session list`
    )
    usage = source(fake).collect(worktree)
    assert [(s.id, s.parent) for s in usage.sessions] == [("ses_root", None), ("ses_child", "ses_root")]
    assert usage.totals.input == 140000


def test_nested_subagents_and_child_without_parent_field(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    grandchild = info("ses_grand", worktree)
    grandchild.pop("location")
    fake = FakeOpenCode(
        {
            "ses_root": export(info("ses_root", worktree), [{"type": "assistant", "ref": "ses_child"}]),
            "ses_child": export(info("ses_child", worktree, parent="ses_root"), [{"type": "assistant", "x": "see ses_grand"}]),
            "ses_grand": export(grandchild, []),  # no parentID: the reference is the link
        },
        listed=["ses_root"],
    )
    usage = source(fake).collect(worktree)
    assert [(s.id, s.parent) for s in usage.sessions] == [
        ("ses_root", None),
        ("ses_child", "ses_root"),
        ("ses_grand", "ses_child"),
    ]


def test_reference_to_foreign_session_is_not_a_child(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    fake = FakeOpenCode(
        {
            "ses_root": export(info("ses_root", worktree), [{"type": "assistant", "text": "ses_other ses_ghost"}]),
            "ses_other": export(info("ses_other", worktree, parent="ses_someone_else"), []),
        },
        listed=["ses_root"],
    )
    usage = source(fake).collect(worktree)
    assert [s.id for s in usage.sessions] == ["ses_root"]
    assert usage.format_problems == []  # ses_ghost failing to export is not a problem


def test_reference_cycle_terminates(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    fake = FakeOpenCode(
        {
            "ses_root": export(info("ses_root", worktree), [{"r": "ses_a"}]),
            "ses_a": export(info("ses_a", worktree, parent="ses_root"), [{"r": "ses_root ses_a"}]),
        },
        listed=["ses_root"],
    )
    assert [s.id for s in source(fake).collect(worktree).sessions] == ["ses_root", "ses_a"]


def test_too_many_sessions_gives_partial_total_and_problem(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    ids = [f"ses_c{i}" for i in range(MAX_SESSIONS + 5)]
    sessions = {"ses_root": export(info("ses_root", worktree), [{"refs": " ".join(ids)}])}
    sessions.update({sid: export(info(sid, worktree, parent="ses_root"), []) for sid in ids})
    usage = source(FakeOpenCode(sessions, listed=["ses_root"])).collect(worktree)
    assert len(usage.sessions) == MAX_SESSIONS
    assert any(str(MAX_SESSIONS) in p for p in usage.format_problems)


def test_session_not_found(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    fake = FakeOpenCode({"ses_x": export(info("ses_x", tmp_path / "elsewhere"))})
    usage = source(fake).collect(worktree)
    assert usage.sessions == []
    assert "не найдена" in usage.missing_reason
    assert usage.format_problems == []


# -- drift detection (tasks 3.2) ----------------------------------------------


def test_unverified_version_is_a_problem_but_usage_is_kept(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    fake = FakeOpenCode({"ses_1": export(info("ses_1", worktree))}, version="opencode v2.1.0")
    usage = source(fake).collect(worktree)
    assert usage.harness_version == "2.1.0"
    assert any("2.1.0" in p and "2.0.21" in p for p in usage.format_problems)
    assert usage.totals.input == 218796


def test_version_is_asked_once_per_source(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    fake = FakeOpenCode({"ses_1": export(info("ses_1", worktree))})
    src = source(fake)
    src.collect(worktree)
    src.collect(worktree)
    assert sum(1 for c in fake.calls if c[1:] == ["--version"]) == 1


def test_missing_token_field_is_named_and_others_kept(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    tokens = {"input": 10, "output": 2, "reasoning": 1, "cache": {"write": 0}}
    usage = source(FakeOpenCode({"ses_1": export(info("ses_1", worktree, tokens=tokens))})).collect(worktree)
    assert any("tokens.cache.read" in p for p in usage.format_problems)
    t = usage.sessions[0].tokens
    assert (t.input, t.cache_read, t.output) == (10, None, 2)


def test_string_instead_of_number_is_a_problem(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    tokens = {"input": "10", "output": 2, "reasoning": 1, "cache": {"read": 0, "write": 0}}
    usage = source(FakeOpenCode({"ses_1": export(info("ses_1", worktree, tokens=tokens))})).collect(worktree)
    assert any("tokens.input" in p and "str" in p for p in usage.format_problems)


def test_garbage_list_and_broken_json_are_problems_not_exceptions(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    usage = source(FakeOpenCode({}, list_output="Error: something changed\n")).collect(worktree)
    assert any("ses_" in p for p in usage.format_problems)
    assert usage.missing_reason

    usage = source(FakeOpenCode({"ses_1": "{not json"})).collect(worktree)
    assert any("не JSON" in p for p in usage.format_problems)
    assert usage.missing_reason


def test_failing_list_command(tmp_path):
    def broken(argv, **kwargs):
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "opencode v2.0.21", "")
        raise FileNotFoundError("opencode")

    usage = source(broken).collect(tmp_path)
    assert usage.missing_reason == "список сессий не получен"
    assert usage.format_problems


def test_none_source():
    usage = NoUsageSource().collect(Path("."))
    assert usage.sessions == [] and usage.missing_reason
