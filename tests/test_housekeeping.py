import os
import time

from review_agent.housekeeping import WorkDir, cleanup_expired, clear_tmp

DAY = 86400


def _make(path, age_days, now, *, folder=False):
    if folder:
        path.mkdir(parents=True)
        (path / "prompt.md").write_text("x", encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    moment = now - age_days * DAY
    os.utime(path, (moment, moment))
    return path


def test_expired_entries_removed_recent_kept(tmp_path):
    now = time.time()
    work = WorkDir(tmp_path / "work")
    old = [
        _make(work.logs / "poll-20260101-000000-1.log", 10, now),
        _make(work.logs / "20260101-000000-b2c-front-1.harness-stderr.log", 8, now),
        _make(work.dry_run / "20260101-000000-b2c-front-1.comment.md", 30, now),
        _make(work.debug / "20260101-000000-b2c-front-1", 9, now, folder=True),
    ]
    fresh = [
        _make(work.logs / "poll-20261001-000000-1.log", 1, now),
        _make(work.debug / "20261001-000000-b2c-front-2", 6, now, folder=True),
    ]
    lock = _make(work.root / "poll.lock", 100, now)
    outside = _make(tmp_path / "old-unrelated.txt", 100, now)
    warnings = []

    removed = cleanup_expired(work, 7, warn=warnings.append, now=now)

    assert removed == 4
    assert not any(p.exists() for p in old)
    assert all(p.exists() for p in fresh)
    assert lock.exists() and outside.exists()
    assert warnings == []


def test_retention_disabled_deletes_nothing(tmp_path):
    now = time.time()
    work = WorkDir(tmp_path)
    old = _make(work.logs / "poll-1.log", 365, now)
    assert cleanup_expired(work, None, warn=lambda m: None, now=now) == 0
    assert old.exists()


def test_missing_areas_are_fine(tmp_path):
    assert cleanup_expired(WorkDir(tmp_path / "nothing-here"), 7, warn=lambda m: None) == 0
    assert clear_tmp(WorkDir(tmp_path / "nothing-here"), lambda m: None) == 0


def test_one_failed_deletion_does_not_stop_others(tmp_path, monkeypatch):
    from review_agent import housekeeping

    now = time.time()
    work = WorkDir(tmp_path)
    locked = _make(work.logs / "poll-a.log", 10, now)
    other = _make(work.logs / "poll-b.log", 10, now)
    real_remove = housekeeping.remove_path

    def flaky_remove(path):
        if path == locked:
            raise PermissionError("файл открыт другим процессом")
        real_remove(path)

    monkeypatch.setattr(housekeeping, "remove_path", flaky_remove)
    warnings = []

    assert cleanup_expired(work, 7, warn=warnings.append, now=now) == 1
    assert locked.exists() and not other.exists()
    assert len(warnings) == 1 and str(locked) in warnings[0]


def test_clear_tmp_removes_everything_including_read_only_files(tmp_path):
    work = WorkDir(tmp_path)
    run = work.tmp / "1700000000-deadbeef"
    (run / "worktree" / ".git").mkdir(parents=True)
    ro = run / "worktree" / ".git" / "object"
    ro.write_text("x", encoding="utf-8")
    os.chmod(ro, 0o444)  # git object files are read-only
    (work.tmp / "mr-x").mkdir()

    assert clear_tmp(work, lambda m: None) == 2
    assert list(work.tmp.iterdir()) == []


def test_usage_area_survives_every_cleanup(tmp_path):
    now = time.time()
    work = WorkDir(tmp_path / "work")
    ledger = _make(work.usage / "ledger.jsonl", 400, now)
    catalog = _make(work.usage / "price-catalog.json", 400, now)
    ledger.write_text('{"schema": "review-agent.usage/1"}\n', encoding="utf-8")
    os.utime(ledger, (now - 400 * DAY, now - 400 * DAY))
    _make(work.tmp / "leftover-run", 1, now, folder=True)

    cleanup_expired(work, 1, warn=lambda m: None, now=now)
    clear_tmp(work, warn=lambda m: None)

    assert ledger.read_text(encoding="utf-8") == '{"schema": "review-agent.usage/1"}\n'
    assert catalog.exists()
    assert not any(work.tmp.iterdir())
