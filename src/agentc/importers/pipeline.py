"""Shared scan → classify → plan for Cursor plugin import."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from plugin_compiler.manifests import identity_from_manifest_dict
from plugin_compiler.model import CapabilityKind
from plugin_compiler.paths import require_inside
from plugin_compiler.scanner import scan_plugin

from agentc.importers.detect import fingerprint_hits, resolve_kind
from agentc.model.diagnostics import (
    CODE_IMPORT_NOTE,
    CODE_KIND_MISMATCH,
    CODE_KIND_UNKNOWN,
    CODE_NOT_PLUGIN,
    CODE_SOURCE_UNREADABLE,
    Diagnostic,
)
from agentc.security.redact import redact_portable_text

_PORTABLE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MANIFEST_REL = ".cursor-plugin/plugin.json"
_PLUGIN_MANIFEST = {
    "cursor": Path(".cursor-plugin") / "plugin.json",
    "codex": Path(".codex-plugin") / "plugin.json",
    "claude-code": Path(".claude-plugin") / "plugin.json",
}
_HOST_CONFIG_ROOTS = frozenset({"extensions", "plugins"})
_HOST_CONFIG_FILES = frozenset({"ide_state.json", "argv.json"})


@dataclass(frozen=True)
class ConvertItem:
    kind: str
    source_rel: str
    dest_rel: str
    source_resolved: Path


@dataclass(frozen=True)
class UnresolvedItem:
    source_rel: str
    reason: str


@dataclass(frozen=True)
class OverlayAddFile:
    host: str
    from_rel: str
    to_rel: str
    source_resolved: Path


@dataclass(frozen=True)
class ImportPlan:
    source_root: Path
    project_id: str
    project_name: str
    version: str
    license_id: str
    enabled_host: str
    enabled_hosts: tuple[str, ...]
    convertibles: tuple[ConvertItem, ...]
    unresolved: tuple[UnresolvedItem, ...]
    redactions: tuple[tuple[str, str], ...]
    overlay_ops: tuple[OverlayAddFile, ...] = ()


def plan_note_diagnostics(plan: ImportPlan) -> list[Diagnostic]:
    """unresolved → warning AGC-P4-0006；redactions → notice AGC-P4-0006。"""
    diags: list[Diagnostic] = []
    for item in plan.unresolved:
        diags.append(
            Diagnostic(
                code=CODE_IMPORT_NOTE,
                level="warning",
                message=f"Unresolved import item ({item.reason}): {item.source_rel}",
                path=item.source_rel,
                hint="",
            )
        )
    for source_rel, reason in plan.redactions:
        diags.append(
            Diagnostic(
                code=CODE_IMPORT_NOTE,
                level="notice",
                message=f"Portable text redaction ({reason}): {source_rel}",
                path=source_rel,
                hint="",
            )
        )
    return diags


def overlay_note_diagnostics(plan: ImportPlan) -> list[Diagnostic]:
    """Each overlay add-file → notice AGC-P4-0006."""
    diags: list[Diagnostic] = []
    for op in plan.overlay_ops:
        diags.append(
            Diagnostic(
                code=CODE_IMPORT_NOTE,
                level="notice",
                message=f"Import overlay add-file ({op.host}): {op.from_rel}",
                path=f"overlays/{op.host}/{op.from_rel}",
                hint="",
            )
        )
    return diags


def _posix_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _to_portable(raw: str) -> str:
    lowered = raw.lower().replace("_", "-")
    collapsed = re.sub(r"[^a-z0-9-]+", "-", lowered)
    return re.sub(r"-+", "-", collapsed).strip("-")


def _source_unreadable(message: str, path: str | None = None) -> Diagnostic:
    return Diagnostic(
        code=CODE_SOURCE_UNREADABLE,
        level="error",
        message=message,
        path=path,
        hint="",
    )


def _not_plugin() -> Diagnostic:
    return Diagnostic(
        code=CODE_NOT_PLUGIN,
        level="error",
        message="Source is not a Cursor plugin (.cursor-plugin/plugin.json missing).",
        path=None,
        hint="",
    )


def scan_root_for(kind: str, source_root: Path) -> Path:
    if kind == "cursor-workspace" and (source_root / ".cursor").is_dir():
        return source_root / ".cursor"
    return source_root


def kind_error_diagnostic(
    code: str | None, requested: str | None, root: Path
) -> Diagnostic:
    hits = fingerprint_hits(root)
    requested_text = "none" if requested is None else requested
    hits_text = ",".join(hits) if hits else "(none)"
    if code == CODE_KIND_MISMATCH:
        message = (
            f"Requested kind {requested_text} does not match fingerprint hits: {hits_text}."
        )
        resolved_code = CODE_KIND_MISMATCH
    else:
        message = (
            "Could not uniquely identify import kind "
            f"(requested={requested_text}, hits={hits_text})."
        )
        resolved_code = code or CODE_KIND_UNKNOWN
    return Diagnostic(
        code=resolved_code,
        level="error",
        message=message,
        path=None,
        hint="",
    )


def _enabled_host_for(kind: str) -> str:
    if kind in ("codex", "claude-code"):
        return kind
    return "cursor"


def _scan_dialect(kind: str) -> str:
    if kind in ("codex", "claude-code"):
        return kind
    return "cursor"


def _identity_fields(kind: str, scan_root: Path) -> tuple[str, str, str, str]:
    if kind in ("cursor-workspace", "cursor-user"):
        portable = _to_portable(scan_root.name)
        if not portable:
            portable = "imported"
        return portable, portable, "0.1.0", "Apache-2.0"
    rel = _PLUGIN_MANIFEST[kind]
    path = scan_root / rel
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"{rel.as_posix()}: root must be an object")
    identity = identity_from_manifest_dict(raw, path=path)
    version = identity.version if identity.version else "0.1.0"
    license_id = identity.license if identity.license else "Apache-2.0"
    return identity.name, identity.name, version, license_id


def _extra_unresolved_reason(source_rel: str) -> str:
    posix = source_rel.replace("\\", "/")
    name = posix.rsplit("/", 1)[-1]
    if name == "marketplace.json":
        return "marketplace"
    if posix in _HOST_CONFIG_FILES:
        return "host-config"
    first = posix.split("/", 1)[0]
    if first in _HOST_CONFIG_ROOTS:
        return "host-config"
    return "unknown"


def _check_tree_safe(root: Path) -> None:
    resolved_root = root.resolve()
    for dirpath, _dirnames, filenames in os.walk(resolved_root, followlinks=False):
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            if path.is_symlink():
                require_inside(resolved_root, path)
            else:
                require_inside(resolved_root, path)
        for name in _dirnames:
            path = current / name
            if path.is_symlink():
                require_inside(resolved_root, path)


def _is_covered(source_rel: str, covered: set[str], skill_dirs: set[str]) -> bool:
    posix = source_rel.replace("\\", "/")
    if posix in covered:
        return True
    for skill_dir in skill_dirs:
        if posix == skill_dir or posix.startswith(skill_dir + "/"):
            return True
    return False


def _iter_skill_utf8_files(skill_dir: Path, source_root: Path) -> list[Path]:
    """Same UTF-8 text files _copy_skill_tree would redact (followlinks=False)."""
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(skill_dir, followlinks=False):
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            if path.is_symlink():
                continue
            try:
                require_inside(source_root, path)
            except ValueError:
                continue
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            files.append(path)
    return files


def _collect_redactions(
    convertibles: tuple[ConvertItem, ...],
    source_root: Path,
) -> tuple[tuple[str, str], ...]:
    redactions: list[tuple[str, str]] = []
    for item in convertibles:
        path = item.source_resolved
        if item.kind == "skill":
            paths_to_scan = _iter_skill_utf8_files(path, source_root)
        elif path.is_file():
            paths_to_scan = [path]
        else:
            continue
        for file_path in paths_to_scan:
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            _, reasons = redact_portable_text(text)
            if not reasons:
                continue
            source_rel = _posix_rel(file_path, source_root)
            for reason in reasons:
                redactions.append((source_rel, reason))
    return tuple(redactions)


def build_plan(
    source_dir: Path, kind: str | None = "cursor"
) -> tuple[ImportPlan | None, list[Diagnostic]]:
    """只读。失败时 plan 为 None，diagnostics 含 error。成功时 diagnostics 含 plan_note_diagnostics(plan)，无 error。"""
    source_root = source_dir.resolve()
    if kind == "cursor":
        manifest_path = source_root / ".cursor-plugin" / "plugin.json"
        if not manifest_path.is_file():
            return None, [_not_plugin()]
        resolved_kind = "cursor"
    else:
        resolved_kind, code = resolve_kind(source_root, kind)
        if resolved_kind is None:
            return None, [kind_error_diagnostic(code, kind, source_root)]

    scan_root = scan_root_for(resolved_kind, source_root)
    try:
        require_inside(source_root, scan_root)
        _check_tree_safe(scan_root)
        project_id, project_name, version, license_id = _identity_fields(
            resolved_kind, scan_root
        )
        refs = scan_plugin(scan_root, _scan_dialect(resolved_kind))
    except (OSError, RuntimeError, ValueError) as exc:
        path = None
        if isinstance(exc, RuntimeError) and "plugin.json" in str(exc):
            manifest = _PLUGIN_MANIFEST.get(resolved_kind)
            path = manifest.as_posix() if manifest is not None else _MANIFEST_REL
        return None, [_source_unreadable(str(exc), path)]

    convertibles: list[ConvertItem] = []
    unresolved: list[UnresolvedItem] = []
    manifest = _PLUGIN_MANIFEST.get(resolved_kind)
    covered: set[str] = {manifest.as_posix()} if manifest is not None else set()
    skill_dirs: set[str] = set()
    dest_seen: dict[str, str] = {}

    for ref in refs:
        source_rel = ref.source_path.replace("\\", "/")
        covered.add(source_rel)
        source_path = scan_root / source_rel

        try:
            source_resolved = require_inside(scan_root, source_path)
        except ValueError:
            return None, [
                _source_unreadable(
                    f"Path escapes source root: {source_rel}", source_rel
                )
            ]

        if ref.kind is CapabilityKind.PLUGIN_METADATA:
            continue

        if ref.kind is CapabilityKind.HOOK:
            unresolved.append(UnresolvedItem(source_rel=source_rel, reason="hook"))
            continue

        if ref.kind is CapabilityKind.MCP:
            unresolved.append(UnresolvedItem(source_rel=source_rel, reason="mcp"))
            continue

        if ref.kind is CapabilityKind.SKILL:
            portable = _to_portable(source_path.name)
            if _PORTABLE_NAME.fullmatch(portable) is None:
                unresolved.append(
                    UnresolvedItem(source_rel=source_rel, reason="invalid-name")
                )
                continue
            dest_rel = f"skills/{portable}"
            item_kind = "skill"
            skill_dirs.add(source_rel)
        elif ref.kind is CapabilityKind.RULE:
            portable = _to_portable(source_path.stem)
            if _PORTABLE_NAME.fullmatch(portable) is None:
                unresolved.append(
                    UnresolvedItem(source_rel=source_rel, reason="invalid-name")
                )
                continue
            dest_rel = f"extensions/rules/{portable}.md"
            item_kind = "rule"
        elif ref.kind is CapabilityKind.COMMAND:
            portable = _to_portable(source_path.stem)
            if _PORTABLE_NAME.fullmatch(portable) is None:
                unresolved.append(
                    UnresolvedItem(source_rel=source_rel, reason="invalid-name")
                )
                continue
            dest_rel = f"extensions/commands/{portable}.md"
            item_kind = "command"
        elif ref.kind is CapabilityKind.AGENT:
            portable = _to_portable(source_path.stem)
            if _PORTABLE_NAME.fullmatch(portable) is None:
                unresolved.append(
                    UnresolvedItem(source_rel=source_rel, reason="invalid-name")
                )
                continue
            dest_rel = f"extensions/agents/{portable}.md"
            item_kind = "agent"
        else:
            unresolved.append(UnresolvedItem(source_rel=source_rel, reason="unknown"))
            continue

        if dest_rel in dest_seen:
            unresolved.append(
                UnresolvedItem(source_rel=source_rel, reason="name-collision")
            )
            continue

        dest_seen[dest_rel] = source_rel
        convertibles.append(
            ConvertItem(
                kind=item_kind,
                source_rel=source_rel,
                dest_rel=dest_rel,
                source_resolved=source_resolved,
            )
        )

    for dirpath, _dirnames, filenames in os.walk(scan_root, followlinks=False):
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            if path.is_symlink():
                try:
                    require_inside(scan_root, path)
                except ValueError:
                    return None, [
                        _source_unreadable(
                            f"Symlink escapes source root: {_posix_rel(path, scan_root)}",
                            _posix_rel(path, scan_root),
                        )
                    ]
                continue
            try:
                require_inside(scan_root, path)
            except ValueError:
                return None, [
                    _source_unreadable(
                        f"Path escapes source root: {_posix_rel(path, scan_root)}",
                        _posix_rel(path, scan_root),
                    )
                ]
            source_rel = _posix_rel(path, scan_root)
            if _is_covered(source_rel, covered, skill_dirs):
                continue
            unresolved.append(
                UnresolvedItem(
                    source_rel=source_rel,
                    reason=_extra_unresolved_reason(source_rel),
                )
            )
            covered.add(source_rel)

    redactions = _collect_redactions(tuple(convertibles), scan_root)

    plan = ImportPlan(
        source_root=scan_root,
        project_id=project_id,
        project_name=project_name,
        version=version,
        license_id=license_id,
        enabled_host=_enabled_host_for(resolved_kind),
        enabled_hosts=(_enabled_host_for(resolved_kind),),
        convertibles=tuple(convertibles),
        unresolved=tuple(unresolved),
        redactions=redactions,
    )
    return plan, plan_note_diagnostics(plan)
