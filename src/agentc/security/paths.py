"""Resolve relative paths and reject escapes from the project root."""

from __future__ import annotations

from pathlib import Path

from agentc.model.diagnostics import CODE_PATH_SECURITY, Diagnostic


def resolve_in_project(
    project_root: Path, relposix: str
) -> tuple[Path | None, Diagnostic | None]:
    parsed = Path(Path(relposix).as_posix())
    if parsed.is_absolute() or ".." in parsed.parts:
        return None, _escape_error(relposix)
    root = project_root.resolve()
    try:
        resolved = (root / relposix).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        return None, _escape_error(relposix)
    return resolved, None


def _escape_error(relposix: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_PATH_SECURITY,
        level="error",
        message="Path must stay inside the project root.",
        path=relposix,
        hint="Use a relative path without '..' or absolute prefixes.",
    )
