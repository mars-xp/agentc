"""Complete read-only inventory of a workspace source tree."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from agentc.model.diagnostics import (
    CODE_INVALID_UTF8,
    CODE_SOURCE_UNREADABLE,
    CODE_WORKSPACE_CHANGED,
    CODE_WORKSPACE_OVERLAP,
    Diagnostic,
)

FIXED_EXCLUSIONS = (".git/",)

_MAX_SYMLINK_HOPS = 64


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    kind: str
    size: int
    digest: str


@dataclass(frozen=True)
class Inventory:
    exclusions: tuple[str, ...]
    entries: tuple[InventoryEntry, ...]
    fingerprint: str


def inventory_source(
    root: Path,
) -> tuple[Inventory | None, tuple[Diagnostic, ...]]:
    """List files, directories, and symlinks. Never writes or executes."""
    try:
        resolved = root.resolve()
    except OSError:
        return None, (_unreadable("."),)
    if not resolved.is_dir():
        return None, (_unreadable("."),)
    scanned = _read_directory(resolved, "", resolved)
    if isinstance(scanned, Diagnostic):
        return None, (scanned,)
    entries, _root_digest = scanned
    ordered = tuple(sorted(entries, key=lambda entry: entry.path))
    return Inventory(FIXED_EXCLUSIONS, ordered, _fingerprint(ordered)), ()


def recheck_inventory(
    root: Path, previous: Inventory
) -> tuple[Diagnostic, ...]:
    """Fail when the tree gained, lost, or changed entries since `previous`."""
    current, diags = inventory_source(root)
    if current is None:
        return diags
    old = {entry.path: entry for entry in previous.entries}
    new = {entry.path: entry for entry in current.entries}
    changed: list[Diagnostic] = []
    for path in sorted(set(old) | set(new)):
        if old.get(path) != new.get(path):
            changed.append(
                Diagnostic(
                    code=CODE_WORKSPACE_CHANGED,
                    level="error",
                    message=f"Source entry changed: {path}",
                    path=path,
                    hint="Retry after the source tree stops changing.",
                )
            )
    return tuple(changed)


def output_overlap(source: Path, output: Path) -> Diagnostic | None:
    """Reject an output path inside the input tree or containing it."""
    try:
        src = source.resolve()
        out = output.resolve()
    except OSError:
        return _overlap()
    if src == out or _is_inside(out, src) or _is_inside(src, out):
        return _overlap()
    return None


def _read_directory(
    directory: Path, rel: str, root: Path
) -> tuple[list[InventoryEntry], str] | Diagnostic:
    try:
        with os.scandir(directory) as iterator:
            listed = sorted(iterator, key=lambda item: item.name)
    except OSError:
        return _unreadable(rel or ".")

    entries: list[InventoryEntry] = []
    records: list[tuple[str, str, str]] = []
    for item in listed:
        name = item.name
        child_rel = name if rel == "" else f"{rel}/{name}"
        if not _is_utf8_text(name):
            return _invalid_utf8(child_rel)
        if rel == "" and name == ".git" and item.is_dir(follow_symlinks=False):
            continue
        full = directory / name
        if item.is_symlink():
            problem = _symlink_problem(full, child_rel, root)
            if problem is not None:
                return problem
            raw = os.fsencode(os.readlink(full))
            digest = _sha256(b"symlink\0" + raw)
            entries.append(InventoryEntry(child_rel, "symlink", len(raw), digest))
            records.append((name, "symlink", digest))
            continue
        if item.is_dir(follow_symlinks=False):
            scanned = _read_directory(full, child_rel, root)
            if isinstance(scanned, Diagnostic):
                return scanned
            sub_entries, sub_digest = scanned
            entries.extend(sub_entries)
            entries.append(InventoryEntry(child_rel, "dir", 0, sub_digest))
            records.append((name, "dir", sub_digest))
            continue
        if item.is_file(follow_symlinks=False):
            try:
                data = full.read_bytes()
            except OSError:
                return _unreadable(child_rel)
            digest = _sha256(b"file\0" + data)
            entries.append(InventoryEntry(child_rel, "file", len(data), digest))
            records.append((name, "file", digest))
            continue
        return _unreadable(child_rel)
    return entries, _dir_digest(records)


def _symlink_problem(path: Path, rel: str, root: Path) -> Diagnostic | None:
    seen: set[tuple[int, int]] = set()
    current = path
    for _ in range(_MAX_SYMLINK_HOPS):
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        except OSError:
            return _unreadable(rel)
        if not current.is_symlink():
            break
        key = (info.st_dev, info.st_ino)
        if key in seen:
            return Diagnostic(
                code=CODE_SOURCE_UNREADABLE,
                level="error",
                message=f"Symlink cycle: {rel}",
                path=rel,
                hint="Remove the symlink cycle and retry.",
            )
        seen.add(key)
        try:
            raw = os.fsencode(os.readlink(current))
        except OSError:
            return _unreadable(rel)
        if not _is_utf8_bytes(raw):
            return Diagnostic(
                code=CODE_INVALID_UTF8,
                level="error",
                message=f"Symlink target is not valid UTF-8: {rel}",
                path=rel,
                hint="Point the symlink at a UTF-8 path.",
            )
        target = os.fsdecode(raw)
        if os.path.isabs(target):
            nxt = Path(target)
        else:
            nxt = Path(os.path.normpath(str(current.parent / target)))
        current = nxt
    else:
        return Diagnostic(
            code=CODE_SOURCE_UNREADABLE,
            level="error",
            message=f"Symlink cycle: {rel}",
            path=rel,
            hint="Remove the symlink cycle and retry.",
        )
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except ValueError:
        return Diagnostic(
            code=CODE_SOURCE_UNREADABLE,
            level="error",
            message=f"Symlink escapes the source root: {rel}",
            path=rel,
            hint="Point the symlink at a path inside the source root.",
        )
    except OSError:
        return _unreadable(rel)
    return None


def _dir_digest(records: list[tuple[str, str, str]]) -> str:
    payload = bytearray(b"dir\0")
    for name, kind, digest in sorted(records):
        payload += name.encode("utf-8")
        payload += b"\0"
        payload += kind.encode("ascii")
        payload += b"\0"
        payload += digest.encode("ascii")
        payload += b"\n"
    return _sha256(bytes(payload))


def _fingerprint(entries: tuple[InventoryEntry, ...]) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"exclusions\0")
    hasher.update(FIXED_EXCLUSIONS[0].encode("utf-8"))
    hasher.update(b"\n")
    for entry in entries:
        hasher.update(
            f"{entry.path}\0{entry.kind}\0{entry.size}\0{entry.digest}\n".encode(
                "utf-8"
            )
        )
    return hasher.hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_utf8_text(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_utf8_bytes(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return path != parent


def _unreadable(path: str) -> Diagnostic:
    shown = path or "."
    return Diagnostic(
        code=CODE_SOURCE_UNREADABLE,
        level="error",
        message=f"Path is unreadable: {shown}",
        path=shown,
        hint="Restore read permission and keep the path inside the source root.",
    )


def _invalid_utf8(path: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_INVALID_UTF8,
        level="error",
        message=f"Path is not valid UTF-8: {path}",
        path=path,
        hint="Rename the path so every byte is valid UTF-8.",
    )


def _overlap() -> Diagnostic:
    return Diagnostic(
        code=CODE_WORKSPACE_OVERLAP,
        level="error",
        message="Output directory overlaps the input tree.",
        path=".",
        hint="Choose an output directory outside the input tree that does not contain it.",
    )
