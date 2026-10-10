"""Exclusive host-kind fingerprints for inspect/import."""

from __future__ import annotations

from pathlib import Path

from agentc.model.diagnostics import CODE_KIND_MISMATCH, CODE_KIND_UNKNOWN

IMPORT_KINDS = ("cursor", "cursor-workspace", "cursor-user", "codex", "claude-code")

_PLUGIN_FILES = {
    "cursor": Path(".cursor-plugin") / "plugin.json",
    "codex": Path(".codex-plugin") / "plugin.json",
    "claude-code": Path(".claude-plugin") / "plugin.json",
}
_WORKSPACE_DIRS = ("rules", "skills", "commands", "agents")


def _has_plugin_manifest(root: Path, kind: str) -> bool:
    return (root / _PLUGIN_FILES[kind]).is_file()


def _has_any_plugin_manifest(root: Path) -> bool:
    return any(
        _has_plugin_manifest(root, kind) for kind in ("cursor", "codex", "claude-code")
    )


def _is_cursor_user(root: Path) -> bool:
    return (root / "ide_state.json").is_file() or (root / "argv.json").is_file()


def _is_cursor_workspace(root: Path) -> bool:
    if (root / ".cursor").is_dir():
        return True
    if root.name != ".cursor":
        return False
    return any((root / name).is_dir() for name in _WORKSPACE_DIRS)


def fingerprint_hits(root: Path) -> tuple[str, ...]:
    """Return kinds whose fingerprints hit `root`, ordered by IMPORT_KINDS."""
    plugin_present = _has_any_plugin_manifest(root)
    is_user = (not plugin_present) and _is_cursor_user(root)
    is_workspace = (not plugin_present) and (not is_user) and _is_cursor_workspace(root)
    present = {
        "cursor": _has_plugin_manifest(root, "cursor"),
        "cursor-workspace": is_workspace,
        "cursor-user": is_user,
        "codex": _has_plugin_manifest(root, "codex"),
        "claude-code": _has_plugin_manifest(root, "claude-code"),
    }
    return tuple(kind for kind in IMPORT_KINDS if present[kind])


def resolve_kind(root: Path, requested: str | None) -> tuple[str | None, str | None]:
    """Resolve a requested kind against fingerprints. Never emits AGC-P4-0001."""
    hits = fingerprint_hits(root)
    if requested is None:
        if len(hits) == 1:
            return hits[0], None
        return None, CODE_KIND_UNKNOWN
    if requested in hits:
        return requested, None
    return None, CODE_KIND_MISMATCH
