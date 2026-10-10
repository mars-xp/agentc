"""Parse overlays/<host>/*.json operation arrays. Check only; never apply."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentc.model.diagnostics import (
    CODE_OVERLAY_INVALID,
    CODE_OVERLAY_MISSING,
    CODE_OVERLAY_PATH,
    CODE_OVERLAY_REMOVE_REASON,
    Diagnostic,
)

OVERLAY_HOSTS = ("cursor", "codex", "claude-code")

_ALLOWED_OPS = frozenset({"set", "remove", "add-file"})


@dataclass(frozen=True)
class OverlayOp:
    host: str
    op: str
    json_path: str
    field: str | None = None
    value: object | None = None
    reason: str | None = None
    from_rel: str | None = None
    to_rel: str | None = None


def check_overlays(project_root: Path) -> list[Diagnostic]:
    """Scan overlays/<host>/*.json. No json → empty list. Never applies."""
    _ops, issues = parse_overlay_ops(project_root)
    return issues


def parse_overlay_ops(
    project_root: Path,
) -> tuple[tuple[OverlayOp, ...], list[Diagnostic]]:
    """Same scan as check_overlays; returns ops only when there is no error."""
    root = project_root.resolve()
    issues: list[Diagnostic] = []
    ops: list[OverlayOp] = []
    for json_path in _overlay_json_files(root):
        file_ops, file_issues = _parse_overlay_file(root, json_path)
        issues.extend(file_issues)
        ops.extend(file_ops)
    if any(issue.level == "error" for issue in issues):
        return (), issues
    return tuple(ops), issues


def add_files_for_host(
    project_root: Path, ops: tuple[OverlayOp, ...], host: str
) -> tuple[tuple[Path, str], ...]:
    """Resolved source file and dest relposix inside that host's artifact root."""
    overlay_dir = project_root.resolve() / "overlays" / host
    copies: list[tuple[Path, str]] = []
    for op in ops:
        if op.host != host or op.op != "add-file":
            continue
        if op.from_rel is None or op.to_rel is None:
            continue
        src = (overlay_dir / op.from_rel).resolve()
        copies.append((src, Path(op.to_rel).as_posix()))
    return tuple(copies)


def fields_for_host(
    ops: tuple[OverlayOp, ...], host: str
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Sequential set/remove for one host. Later ops override. Returns (fields, remove_keys)."""
    fields: dict[str, object] = {}
    removes: list[str] = []
    for op in ops:
        if op.host != host or op.op not in ("set", "remove"):
            continue
        field = op.field
        if field is None:
            continue
        if op.op == "set":
            fields[field] = op.value
            if field in removes:
                removes.remove(field)
        else:
            fields.pop(field, None)
            if field not in removes:
                removes.append(field)
    return fields, tuple(removes)


def _overlay_json_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for host in OVERLAY_HOSTS:
        overlay_dir = root / "overlays" / host
        if not overlay_dir.is_dir():
            continue
        for path in overlay_dir.glob("*.json"):
            if path.is_file():
                found.append(path)
    found.sort(key=lambda path: path.as_posix())
    return found


def _relposix(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def _issue(
    code: str,
    message: str,
    path: str,
    hint: str,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        level="error",
        message=message,
        path=path,
        hint=hint,
    )


def _invalid(path: str, message: str, hint: str) -> Diagnostic:
    return _issue(CODE_OVERLAY_INVALID, message, path, hint)


def _parse_overlay_file(
    root: Path, json_path: Path
) -> tuple[list[OverlayOp], list[Diagnostic]]:
    rel = _relposix(root, json_path)
    host = json_path.parent.name
    overlay_dir = json_path.parent
    try:
        text = json_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [], [
            _invalid(
                rel,
                "Overlay file is not readable JSON text: {0}.".format(exc),
                "Save the overlay as UTF-8 JSON.",
            )
        ]
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [
            _invalid(
                rel,
                "Overlay file is not valid JSON: {0}.".format(exc),
                "Fix the JSON syntax in this overlay file.",
            )
        ]
    if not isinstance(raw, list):
        return [], [
            _invalid(
                rel,
                "Overlay file must be a JSON array of operations.",
                "Use a JSON array of set, remove, or add-file objects.",
            )
        ]

    ops: list[OverlayOp] = []
    issues: list[Diagnostic] = []
    for item in raw:
        parsed, item_issues = _parse_op(host, rel, overlay_dir, item)
        issues.extend(item_issues)
        if parsed is not None:
            ops.append(parsed)
    return ops, issues


def _parse_op(
    host: str,
    rel: str,
    overlay_dir: Path,
    item: object,
) -> tuple[OverlayOp | None, list[Diagnostic]]:
    if not isinstance(item, dict):
        return None, [
            _invalid(
                rel,
                "Overlay operation must be an object.",
                "Use a JSON object with an op field.",
            )
        ]
    op = item.get("op")
    if not isinstance(op, str) or op not in _ALLOWED_OPS:
        return None, [
            _invalid(
                rel,
                "Unknown overlay op {0!r}.".format(op),
                "Use set, remove, or add-file.",
            )
        ]
    if op == "set":
        return _parse_set(host, rel, item)
    if op == "remove":
        return _parse_remove(host, rel, item)
    return _parse_add_file(host, rel, overlay_dir, item)


def _nonempty_field(item: dict, rel: str, op: str) -> tuple[str | None, list[Diagnostic]]:
    field = item.get("field")
    if not isinstance(field, str) or field == "":
        return None, [
            _invalid(
                rel,
                "Overlay op {0!r} requires a non-empty field string.".format(op),
                "Set field to a top-level manifest key.",
            )
        ]
    return field, []


def _parse_set(
    host: str, rel: str, item: dict
) -> tuple[OverlayOp | None, list[Diagnostic]]:
    field, issues = _nonempty_field(item, rel, "set")
    if field is None:
        return None, issues
    if "value" not in item:
        return None, [
            _invalid(
                rel,
                "Overlay op 'set' requires a value key.",
                "Add a value for the field being set.",
            )
        ]
    return (
        OverlayOp(host=host, op="set", json_path=rel, field=field, value=item["value"]),
        [],
    )


def _parse_remove(
    host: str, rel: str, item: dict
) -> tuple[OverlayOp | None, list[Diagnostic]]:
    field, issues = _nonempty_field(item, rel, "remove")
    if field is None:
        return None, issues
    reason = item.get("reason")
    if not isinstance(reason, str) or reason.strip() == "":
        return None, [
            _issue(
                CODE_OVERLAY_REMOVE_REASON,
                "remove requires a non-empty reason.",
                rel,
                "Add a reason string that explains why the field is removed.",
            )
        ]
    return (
        OverlayOp(
            host=host, op="remove", json_path=rel, field=field, reason=reason
        ),
        [],
    )


def _parse_add_file(
    host: str,
    rel: str,
    overlay_dir: Path,
    item: dict,
) -> tuple[OverlayOp | None, list[Diagnostic]]:
    from_rel = item.get("from")
    to_rel = item.get("to")
    if not isinstance(from_rel, str) or from_rel == "":
        return None, [
            _invalid(
                rel,
                "Overlay op 'add-file' requires a non-empty from string.",
                "Set from to a path under overlays/<host>/.",
            )
        ]
    if not isinstance(to_rel, str) or to_rel == "":
        return None, [
            _invalid(
                rel,
                "Overlay op 'add-file' requires a non-empty to string.",
                "Set to to a relative path inside the host artifact tree.",
            )
        ]
    to_path = Path(Path(to_rel).as_posix())
    if to_path.is_absolute() or to_rel.startswith("/") or ".." in to_path.parts:
        return None, [
            _issue(
                CODE_OVERLAY_PATH,
                "add-file to path must stay inside the host artifact tree.",
                rel,
                "Use a relative to path without '..' or absolute prefixes.",
            )
        ]
    from_path = Path(Path(from_rel).as_posix())
    overlay_root = overlay_dir.resolve()
    try:
        source = (overlay_dir / from_rel).resolve()
        source.relative_to(overlay_root)
    except (ValueError, OSError):
        return None, [
            _issue(
                CODE_OVERLAY_PATH,
                "add-file from path must stay inside the overlay host directory.",
                rel,
                "Use a relative from path under overlays/<host>/ without '..'.",
            )
        ]
    if ".." in from_path.parts or from_path.is_absolute() or from_rel.startswith("/"):
        return None, [
            _issue(
                CODE_OVERLAY_PATH,
                "add-file from path must stay inside the overlay host directory.",
                rel,
                "Use a relative from path under overlays/<host>/ without '..'.",
            )
        ]
    if not source.is_file():
        return None, [
            _issue(
                CODE_OVERLAY_MISSING,
                "add-file source {0!r} does not exist.".format(from_rel),
                rel,
                "Place the source file under overlays/<host>/.",
            )
        ]
    return (
        OverlayOp(
            host=host,
            op="add-file",
            json_path=rel,
            from_rel=from_rel,
            to_rel=to_rel,
        ),
        [],
    )
