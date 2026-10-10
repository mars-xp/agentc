from __future__ import annotations

import re
from pathlib import Path

from plugin_packaging import AuditIssue

CURSOR_RUNTIME_BLOCK = re.compile(
    r"state\.vscdb|SwitchMode\s*\(\s*plan\s*\)|"
    r"~/\.cursor/settings\.json|"
    r"AppData/Roaming/Cursor|"
    r"Library/Application Support/Cursor|"
    r"\.cursor-plugin|"
    r"\.cursor/skills|"
    r"~/\.cursor/plugins|"
    r"JXP_CURSOR_PLUGIN_ROOT",
    re.I,
)
_CURSOR_RUNTIME_HINT = re.compile(
    r"\b(?:Cursor|Read|Task|AskQuestion|TodoWrite)\b",
    re.I,
)


def audit_cursor_runtime_in_files(
    plugin_dir: Path,
    files: tuple[Path, ...] | list[Path],
) -> list[AuditIssue]:
    """Scan staged pack files for Cursor runtime/layout dependencies."""
    plugin_dir = plugin_dir.resolve()
    issues: list[AuditIssue] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(plugin_dir).as_posix()
        for line_number, line in enumerate(lines, start=1):
            match = CURSOR_RUNTIME_BLOCK.search(line)
            if match is not None:
                issues.append(
                    AuditIssue(
                        severity="block",
                        path=rel,
                        line=line_number,
                        message="Cursor runtime dependency: {0}".format(
                            match.group(0)
                        ),
                    )
                )
                continue
            hint = _CURSOR_RUNTIME_HINT.search(line)
            if hint is not None:
                issues.append(
                    AuditIssue(
                        severity="hint",
                        path=rel,
                        line=line_number,
                        message="review Cursor-specific term: {0}".format(
                            hint.group(0)
                        ),
                    )
                )
    return issues
