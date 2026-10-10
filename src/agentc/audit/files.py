"""Read-only listing of UTF-8 text files under an audit directory."""

from __future__ import annotations

import os
from pathlib import Path

from agentc.model.diagnostics import CODE_AUDIT_UNREADABLE, Diagnostic


def _unreadable(path: Path, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_AUDIT_UNREADABLE,
        level="error",
        message=message,
        path=path.as_posix(),
        hint="Pass a readable directory that stays inside the given path.",
    )


def _symlink_escapes(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except (ValueError, OSError):
        return True
    return False


def list_text_files(root: Path) -> list[str] | Diagnostic:
    try:
        root_resolved = root.resolve()
    except OSError:
        return _unreadable(root, "audit directory is unreadable")
    if not root_resolved.is_dir():
        return _unreadable(root, "audit directory is unreadable")

    text_files: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(
            str(root_resolved), followlinks=False
        ):
            dirnames[:] = [name for name in dirnames if name != ".git"]
            current = Path(dirpath)
            for name in list(dirnames) + list(filenames):
                path = current / name
                if path.is_symlink() and _symlink_escapes(path, root_resolved):
                    return _unreadable(
                        path, "audit path escapes the given directory"
                    )
            for name in filenames:
                path = current / name
                try:
                    path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                text_files.append(path.relative_to(root_resolved).as_posix())
    except OSError:
        return _unreadable(root, "audit directory is unreadable")
    text_files.sort()
    return text_files
