"""Log file of one polling pass: `<work_dir>/logs/poll-<time>-<pid>.log`.

Under Task Scheduler there is no console to read, so everything a pass
reports is written both to the console (when there is one) and to this
file - with timestamps, in UTF-8. Console problems never break a pass:
a missing stream (sys.stdout is None) is skipped and write errors (e.g.
UnicodeEncodeError on an odd console code page) are swallowed.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from review_agent.housekeeping import stamp

_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def _safe(write: Callable[[str], object], message: str) -> None:
    try:
        write(message)
    except Exception:  # noqa: BLE001 - console output is best effort only
        pass


def _print_to_stderr(message: str) -> None:
    stream = sys.stderr
    if stream is not None:
        print(message, file=stream)


class PassLog:
    def __init__(
        self,
        path: Path,
        logger: logging.Logger,
        *,
        output_fn: Callable[[str], object],
        error_fn: Callable[[str], object],
    ) -> None:
        self.path = path
        self._logger = logger
        self._output_fn = output_fn
        self._error_fn = error_fn

    def info(self, message: str) -> None:
        self._logger.info(message)
        _safe(self._output_fn, message)

    def warning(self, message: str) -> None:
        self._logger.warning(message)
        _safe(self._output_fn, f"Предупреждение: {message}")

    def error(self, message: str) -> None:
        self._logger.error(message)
        _safe(self._error_fn, message)

    def file_only(self, message: str) -> None:
        """For the log only - e.g. the interactive choice, already on screen."""
        self._logger.info(message)


@contextmanager
def pass_log(
    logs_dir: Path,
    *,
    output_fn: Callable[[str], object] = print,
    error_fn: Callable[[str], object] = _print_to_stderr,
) -> Iterator[PassLog]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"poll-{stamp()}-{os.getpid()}.log"
    # A logger of our own per pass: no propagation to the root logger, so
    # nothing else in the process (or a test runner) duplicates the output.
    logger = logging.getLogger(f"review_agent.pass.{id(path)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    try:
        yield PassLog(path, logger, output_fn=output_fn, error_fn=error_fn)
    finally:
        logger.removeHandler(handler)
        handler.close()
