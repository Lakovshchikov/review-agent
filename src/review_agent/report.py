"""Writes the harness's review output as a single markdown report file."""

from __future__ import annotations

from pathlib import Path


def write_report(content: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
