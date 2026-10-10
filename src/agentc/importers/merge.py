"""Compose multiple import plans by destination path."""

from __future__ import annotations

import os
from pathlib import Path

from agentc.importers.pipeline import ConvertItem, ImportPlan, OverlayAddFile
from agentc.model.diagnostics import (
    CODE_MERGE_CONFLICT,
    CODE_MERGE_SOURCES,
    Diagnostic,
)
from agentc.security.redact import redact_portable_text

MERGE_SOURCE_ORDER = (
    "cursor",
    "cursor-workspace",
    "cursor-user",
    "codex",
    "claude-code",
)

_OVERLAY_KINDS = frozenset({"skill", "rule", "command", "agent"})


def host_for_kind(kind: str) -> str:
    if kind in ("codex", "claude-code"):
        return kind
    return "cursor"


def _portable_file_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    redacted, _reasons = redact_portable_text(text)
    return redacted.encode("utf-8")


def portable_item_fingerprint(
    item: ConvertItem,
) -> tuple[str, tuple[tuple[str, bytes], ...]]:
    if item.kind == "skill":
        root = item.source_resolved
        files: list[tuple[str, bytes]] = []
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            for name in filenames:
                path = current / name
                if path.is_symlink():
                    continue
                rel = path.relative_to(root).as_posix()
                files.append((rel, _portable_file_bytes(path)))
        files.sort(key=lambda pair: pair[0])
        return (item.kind, tuple(files))
    return (item.kind, (("", _portable_file_bytes(item.source_resolved)),))


def _file_ops_for_item(item: ConvertItem, host: str) -> list[OverlayAddFile]:
    ops: list[OverlayAddFile] = []
    if item.kind == "skill":
        root = item.source_resolved
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            for name in filenames:
                path = current / name
                if path.is_symlink():
                    continue
                rel = path.relative_to(root).as_posix()
                dest = "{0}/{1}".format(item.dest_rel, rel)
                ops.append(
                    OverlayAddFile(
                        host=host,
                        from_rel=dest,
                        to_rel=dest,
                        source_resolved=path,
                    )
                )
        return ops
    return [
        OverlayAddFile(
            host=host,
            from_rel=item.dest_rel,
            to_rel=item.dest_rel,
            source_resolved=item.source_resolved,
        )
    ]


def compose_plans(
    sourced: tuple[tuple[str, ImportPlan], ...],
) -> tuple[ImportPlan | None, list[Diagnostic]]:
    if not sourced:
        return None, [
            Diagnostic(
                code=CODE_MERGE_SOURCES,
                level="error",
                message="Import merge requires at least two source directories.",
                path=None,
                hint=(
                    "Pass two or more of --cursor, --cursor-workspace, "
                    "--cursor-user, --codex, --claude-code."
                ),
            )
        ]

    convertibles: list[ConvertItem] = []
    dest_seen: dict[str, ConvertItem] = {}
    dest_hosts: dict[str, set[str]] = {}
    unresolved = []
    redactions: list[tuple[str, str]] = []
    hosts: list[str] = []
    seen_hosts: set[str] = set()

    for kind, plan in sourced:
        host = host_for_kind(kind)
        if host not in seen_hosts:
            seen_hosts.add(host)
            hosts.append(host)
        for item in plan.convertibles:
            dest_hosts.setdefault(item.dest_rel, set()).add(host)
            existing = dest_seen.get(item.dest_rel)
            if existing is None:
                dest_seen[item.dest_rel] = item
                convertibles.append(item)
                continue
            if portable_item_fingerprint(existing) != portable_item_fingerprint(item):
                dest_rel = item.dest_rel
                return None, [
                    Diagnostic(
                        code=CODE_MERGE_CONFLICT,
                        level="error",
                        message=f"Merge conflict at destination path: {dest_rel}",
                        path=dest_rel,
                        hint="",
                    )
                ]
        unresolved.extend(plan.unresolved)
        redactions.extend(plan.redactions)

    kept: list[ConvertItem] = []
    ops: list[OverlayAddFile] = []
    for item in convertibles:
        hosts_for_dest = dest_hosts.get(item.dest_rel, set())
        if len(hosts_for_dest) == 1 and item.kind in _OVERLAY_KINDS:
            host = next(iter(hosts_for_dest))
            item_ops = _file_ops_for_item(item, host)
            if item_ops:
                ops.extend(item_ops)
            else:
                kept.append(item)
        else:
            kept.append(item)

    first = sourced[0][1]
    enabled_hosts = tuple(hosts)
    merged = ImportPlan(
        source_root=first.source_root,
        project_id=first.project_id,
        project_name=first.project_name,
        version=first.version,
        license_id=first.license_id,
        enabled_host=enabled_hosts[0],
        enabled_hosts=enabled_hosts,
        convertibles=tuple(kept),
        unresolved=tuple(unresolved),
        redactions=tuple(redactions),
        overlay_ops=tuple(sorted(ops, key=lambda op: (op.host, op.to_rel))),
    )
    return merged, []
