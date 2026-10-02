"""One run at a time per working directory: `<work_dir>/poll.lock`."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

LOCK_FILE_NAME = "poll.lock"


class PollLockBusy(RuntimeError):
    """Another run (a poll pass or a manual review) holds the lock for this working directory."""


def pid_alive(pid: int) -> bool:
    """Cross-platform "is this process still running" without new dependencies."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


@contextmanager
def poll_lock(
    lock_dir: Path, *, is_alive: Callable[[int], bool] = pid_alive
) -> Iterator[Path]:
    """Hold `<lock_dir>/poll.lock` (lock_dir = the working directory) for a run.

    Both `poll` and the manual review take it: they share `<work_dir>/tmp`,
    and a run's start-up cleanup would otherwise delete the other's
    worktree.

    Created with exclusive-create ("x"), which is atomic on both Windows
    and Linux. A lock left behind by a killed pass (its PID no longer
    running) is taken over; a live one makes this raise PollLockBusy.
    """
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / LOCK_FILE_NAME
    for attempt in range(2):
        try:
            with open(lock_path, "x", encoding="utf-8") as lock_file:
                lock_file.write(str(os.getpid()))
            break
        except FileExistsError:
            try:
                owner = int(lock_path.read_text(encoding="utf-8").strip() or "0")
            except (OSError, ValueError):
                owner = 0
            if attempt == 0 and not is_alive(owner):
                lock_path.unlink(missing_ok=True)  # stale: previous pass was killed
                continue
            raise PollLockBusy(
                f"Другой прогон review-agent (проход poll или ручное ревью) уже "
                f"выполняется (PID {owner}). "
                f"Если это не так, удалите lock-файл вручную: {lock_path}"
            ) from None
    try:
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)
