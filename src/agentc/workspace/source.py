"""Source-kind detection for workspace builds.

This module does not choose which files are inventoried and does not change
import fingerprints.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from agentc.model.diagnostics import (
    CODE_KIND_MISMATCH,
    CODE_KIND_UNKNOWN,
    CODE_SOURCE_UNREADABLE,
    Diagnostic,
)

SOURCE_KINDS = (
    "cursor",
    "cursor-workspace",
    "codex",
    "codex-workspace",
    "claude-code",
    "claude-code-workspace",
)

_ALLOWED = frozenset(SOURCE_KINDS)


def detect_source(
    root: Path, requested: str | None = None
) -> tuple[str | None, tuple[Diagnostic, ...]]:
    """Return the single matching kind, or an error. Never guesses."""
    hits, problems = _fingerprint_hits(root)
    if problems:
        return None, problems
    if requested is not None and requested not in _ALLOWED:
        return None, (_kind_diagnostic(CODE_KIND_MISMATCH, requested, hits),)
    if len(hits) != 1:
        if requested is not None and requested not in hits:
            return None, (_kind_diagnostic(CODE_KIND_MISMATCH, requested, hits),)
        return None, (_kind_diagnostic(CODE_KIND_UNKNOWN, requested, hits),)
    kind = hits[0]
    if requested is not None and requested != kind:
        return None, (_kind_diagnostic(CODE_KIND_MISMATCH, requested, hits),)
    return kind, ()


def _fingerprint_hits(
    root: Path,
) -> tuple[tuple[str, ...], tuple[Diagnostic, ...]]:
    problems: list[Diagnostic] = []
    hits: list[str] = []
    probes = (
        ("cursor", _file_clue, root / ".cursor-plugin" / "plugin.json", ".cursor-plugin/plugin.json"),
        ("cursor-workspace", _dir_clue, root / ".cursor", ".cursor"),
        ("codex", _file_clue, root / ".codex-plugin" / "plugin.json", ".codex-plugin/plugin.json"),
        ("codex-workspace", _skill_dir_clue, root / ".agents" / "skills", ".agents/skills"),
        ("claude-code", _file_clue, root / ".claude-plugin" / "plugin.json", ".claude-plugin/plugin.json"),
        ("claude-code-workspace", _skill_dir_clue, root / ".claude" / "skills", ".claude/skills"),
    )
    for kind, probe, path, rel in probes:
        result = probe(path, rel)
        if isinstance(result, Diagnostic):
            problems.append(result)
            continue
        if result:
            hits.append(kind)
    return tuple(hits), tuple(problems)


def _file_clue(path: Path, rel: str) -> bool | Diagnostic:
    info = _stat_clue(path, rel)
    if isinstance(info, Diagnostic):
        return info
    if info is None:
        return False
    return stat.S_ISREG(info.st_mode)


def _dir_clue(path: Path, rel: str) -> bool | Diagnostic:
    info = _stat_clue(path, rel)
    if isinstance(info, Diagnostic):
        return info
    if info is None or not stat.S_ISDIR(info.st_mode):
        return False
    listed = _list_clue(path, rel)
    if isinstance(listed, Diagnostic):
        return listed
    return True


def _skill_dir_clue(path: Path, rel: str) -> bool | Diagnostic:
    info = _stat_clue(path, rel)
    if isinstance(info, Diagnostic):
        return info
    if info is None or not stat.S_ISDIR(info.st_mode):
        return False
    children = _list_clue(path, rel)
    if isinstance(children, Diagnostic):
        return children
    for child in children:
        child_info = _stat_clue(child, rel)
        if isinstance(child_info, Diagnostic):
            return child_info
        if child_info is None or not stat.S_ISDIR(child_info.st_mode):
            continue
        skill = child / "SKILL.md"
        skill_info = _stat_clue(skill, rel)
        if isinstance(skill_info, Diagnostic):
            return skill_info
        if skill_info is not None and stat.S_ISREG(skill_info.st_mode):
            return True
    return False


def _stat_clue(path: Path, rel: str) -> os.stat_result | Diagnostic | None:
    try:
        return path.stat()
    except OSError as exc:
        if _is_permission(exc):
            return _clue_unreadable(rel)
        return None


def _list_clue(path: Path, rel: str) -> list[Path] | Diagnostic:
    try:
        return list(path.iterdir())
    except OSError as exc:
        if _is_permission(exc):
            return _clue_unreadable(rel)
        return []


def _is_permission(exc: OSError) -> bool:
    return exc.errno in (errno.EACCES, errno.EPERM)


def _clue_unreadable(rel: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_SOURCE_UNREADABLE,
        level="error",
        message=f"Source clue is unreadable: {rel}",
        path=rel,
        hint="Restore permission to read this source clue and retry.",
    )


def _kind_diagnostic(
    code: str, requested: str | None, hits: tuple[str, ...]
) -> Diagnostic:
    requested_text = "none" if requested is None else requested
    hits_text = ",".join(hits) if hits else "(none)"
    if code == CODE_KIND_MISMATCH:
        message = (
            f"Requested source kind {requested_text} does not match "
            f"fingerprint hits: {hits_text}."
        )
    else:
        message = (
            "Could not uniquely identify source kind "
            f"(requested={requested_text}, hits={hits_text})."
        )
    return Diagnostic(
        code=code,
        level="error",
        message=message,
        path=".",
        hint="Use a source directory with exactly one supported source kind.",
    )
