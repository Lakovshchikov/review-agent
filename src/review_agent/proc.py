"""Flags for every child process review-agent starts (git, glab, the harness).

A scheduled task may run review-agent through `pythonw.exe` - Python
without a console, so no window pops up (scripts/register-task.ps1).
Without a console of its own, every console child (git, glab, opencode)
would make Windows open a NEW visible window for it. CREATE_NO_WINDOW
prevents that.

The flag is applied only when this process really has no console: in a
normal terminal children keep sharing it, so Ctrl+C still reaches the
whole tree (opencode runs as cmd.exe -> node, and killing only cmd.exe
would orphan node).
"""

from __future__ import annotations

import os
import subprocess


def no_window_flags() -> int:
    """`creationflags` for subprocess calls: CREATE_NO_WINDOW only on Windows without a console."""
    if os.name != "nt":
        return 0
    import ctypes

    if ctypes.windll.kernel32.GetConsoleWindow():  # type: ignore[attr-defined]
        return 0
    return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
