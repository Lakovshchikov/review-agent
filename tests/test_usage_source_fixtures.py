"""OpenCode usage source against REAL (sanitized) OpenCode v2.0.21 output.

tests/fixtures/opencode/ was captured on the author's machine (tasks 1.1):
a real review session, and a real session that spawned a subagent. All
text was replaced by "<text ...>" (session ids inside kept), paths by
"{worktree}". Facts these samples established:
- a child session has `info.parentID`; the parent's `subagent` tool call
  carries the child id in `state.metadata.sessionID`;
- the parent's `info.tokens` does NOT include the child's - summing is right;
- steps = messages of type "assistant" (each has its own tokens);
- session duration = time.idle - time.created (time.updated is set
  seconds after creation and stays there).
"""

import json
import subprocess
from pathlib import Path

from review_agent.usage_source import OpenCodeUsageSource

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"


def _load(name, worktree):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text.replace("{worktree}", json.dumps(str(worktree))[1:-1])


def _runner(worktree, exports, listed):
    def run(argv, **kwargs):
        args = argv[1:]
        if args == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, (FIXTURES / "version.txt").read_text(encoding="utf-8"), "")
        if args[:2] == ["session", "list"]:
            return subprocess.CompletedProcess(argv, 0, listed, "")
        if args[:2] == ["session", "export"] and args[2] in exports:
            return subprocess.CompletedProcess(argv, 0, _load(exports[args[2]], worktree), "")
        return subprocess.CompletedProcess(argv, 1, "", "Session not found")

    return run


def _source(runner):
    return OpenCodeUsageSource(
        ["opencode", "session", "list"],
        ["opencode", "session", "export", "{session_id}"],
        runner=runner,
        which=lambda name: None,
    )


def test_real_session_list_format_parses():
    listing = (FIXTURES / "session-list.txt").read_text(encoding="utf-8")
    ids = [line.split("\t")[0] for line in listing.splitlines()]
    source = _source(_runner(Path("."), {}, listing))
    problems = []
    assert source._list_ids(Path("."), problems) == ids[:5]
    assert problems == []


def test_real_review_session(tmp_path):
    sid = "ses_f0344a877ffemdWd4lWd2BI520"
    listing = f"ses_otherSession1\t<title>\t04.10.2026, 12:51:53\n{sid}\t<title>\t02.10.2026, 16:08:54\n"
    usage = _source(_runner(tmp_path, {sid: "review-session.json"}, listing)).collect(tmp_path)

    assert usage.format_problems == []
    assert usage.missing_reason is None
    assert usage.harness_version == "2.0.21"
    (session,) = usage.sessions
    assert session.id == sid and session.agent == "reviewer" and session.parent is None
    t = session.tokens
    assert (t.input, t.cache_read, t.cache_write, t.output, t.reasoning) == (218796, 121856, 0, 3696, 2269)
    assert session.steps == 9
    assert session.duration_ms == 1790946686644 - 1790946531542


def test_real_subagent_session_is_found_and_summed(tmp_path):
    parent, child = "ses_ef8dfe794ffe6I30YiAUi4MSFE", "ses_ef8dfd57dffeS279sNFlWQr7wk"
    exports = {parent: "subagent-parent.json", child: "subagent-child.json"}
    listing = f"{parent}\t<title>\t04.10.2026, 16:35:09\n"  # children are not listed
    usage = _source(_runner(tmp_path, exports, listing)).collect(tmp_path)

    assert usage.format_problems == []
    assert [(s.id, s.parent, s.agent) for s in usage.sessions] == [
        (parent, None, None),  # the default agent of a plain `opencode run` has no info.agent
        (child, parent, "general"),
    ]
    assert usage.totals.input == 12484 + 10636
    assert usage.totals.output == 78 + 94
    assert usage.steps == 4
