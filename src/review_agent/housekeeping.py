"""The working directory: its layout, and deleting what is no longer needed.

Everything review-agent writes to disk lives under `storage.work_dir`
(see config.StorageConfig), so "did we leave anything behind?" is answered
by looking at one folder:

    <work_dir>/
      poll.lock     one run at a time per working directory (lock.py)
      tmp/          transient: one folder per review run (worktree, prompt,
                    safety note, report, harness stderr) and per MR
                    (publication request bodies). Deleted right after each
                    review; empty after a pass. Leftovers of a killed run
                    are deleted at the start of the next pass.
      logs/         one log per polling pass + harness stderr of FAILED
                    reviews - kept, deleted by age
      dry-run/      would-be comments of `poll --dry-run` - kept, by age
      debug/        all artifacts of runs made with --debug - kept, by age

Nothing outside `work_dir` is ever deleted from here.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from review_agent.config import Config

Warn = Callable[[str], None]

# Folders whose entries expire by age; tmp/ is emptied unconditionally instead.
RETAINED_AREAS = ("logs", "dry-run", "debug")


@dataclasses.dataclass(frozen=True)
class WorkDir:
    root: Path

    @classmethod
    def from_config(cls, config: Config) -> "WorkDir":
        return cls(Path(config.storage.work_dir))

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def dry_run(self) -> Path:
        return self.root / "dry-run"

    @property
    def debug(self) -> Path:
        return self.root / "debug"


def stamp(moment: datetime | None = None) -> str:
    """File-name timestamp, local time: 20261002-143005."""
    return (moment or datetime.now()).strftime("%Y%m%d-%H%M%S")


def _make_writable_and_retry(function, path, _exc_info) -> None:
    # Git marks object files read-only; on Windows rmtree cannot delete
    # those until the read-only bit is cleared.
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove_path(path: Path) -> None:
    """Delete a file or a whole folder tree; raises OSError if that fails."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=_make_writable_and_retry)
    else:
        path.unlink(missing_ok=True)


def copy_artifacts(source: Path, destination: Path) -> None:
    """Copy a run folder for --debug, without the (huge) worktree checkout."""
    if not source.is_dir():
        return
    shutil.copytree(
        source, destination, ignore=shutil.ignore_patterns("worktree"), dirs_exist_ok=True
    )


def clear_tmp(work_dir: WorkDir, warn: Warn) -> int:
    """Delete everything in tmp/ - only call while holding the work-dir lock.

    Under the lock no other run of this working directory can be alive,
    so whatever is in tmp/ is a leftover of a run that did not exit
    cleanly. Returns how many entries were removed.
    """
    if not work_dir.tmp.is_dir():
        return 0
    removed = 0
    for entry in sorted(work_dir.tmp.iterdir()):
        try:
            remove_path(entry)
            removed += 1
        except OSError as exc:
            warn(f"Не удалось удалить остаток прогона {entry}: {exc}")
    return removed


def cleanup_expired(
    work_dir: WorkDir,
    retention_days: int | None,
    *,
    warn: Warn,
    now: float | None = None,
) -> int:
    """Delete entries of logs/, dry-run/ and debug/ older than `retention_days`.

    Age is the entry's own mtime (a debug folder's mtime is when it was
    filled). Only direct children of those three folders are considered;
    nothing else in or outside `work_dir` is touched. A failure to delete
    one entry is reported through `warn` and does not stop the others.
    Returns how many entries were removed.
    """
    if retention_days is None:
        return 0
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = 0
    for area in RETAINED_AREAS:
        folder = work_dir.root / area
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
                remove_path(entry)
                removed += 1
            except OSError as exc:
                warn(f"Не удалось удалить устаревший файл {entry}: {exc}")
    return removed
