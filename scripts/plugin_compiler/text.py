from __future__ import annotations

from pathlib import Path


def write_text_utf8_lf(path: Path, text: str) -> None:
    """Write UTF-8 text with LF newlines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
