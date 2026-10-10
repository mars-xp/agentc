"""Extract Cursor plugin zips, compare with install dest, build install plans."""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from plugin_compiler.paths import require_inside, require_safe_plugin_name
from plugin_compiler.zip_tree import extract_zip_filtered

from agentc.model.diagnostics import (
    CODE_INSTALL_ROOT_REQUIRED,
    CODE_INSTALL_UNREADABLE,
    CODE_NOT_CURSOR_ZIP,
    Diagnostic,
)

DEFAULT_INSTALL_ROOT = Path.home() / ".cursor" / "plugins" / "local"
HOST_MANIFEST = {
    "cursor": Path(".cursor-plugin") / "plugin.json",
    "codex": Path(".codex-plugin") / "plugin.json",
    "claude-code": Path(".claude-plugin") / "plugin.json",
}


@dataclass(frozen=True)
class InstallPlan:
    plugin_name: str
    zip_path: Path
    install_root: Path
    dest: Path
    extracted_plugin: Path
    create: tuple[str, ...]
    identical: tuple[str, ...]
    conflicts: tuple[str, ...]
    host: str


def _not_cursor_zip(zip_path: Path, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_NOT_CURSOR_ZIP,
        level="error",
        message=message,
        path=zip_path.as_posix(),
        hint="",
    )


def _unreadable_zip(zip_path: Path, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_INSTALL_UNREADABLE,
        level="error",
        message=message,
        path=zip_path.as_posix(),
        hint="",
    )


def _list_rel_files(tree: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in tree.rglob("*"):
        if path.is_file() and not path.is_dir():
            files[path.relative_to(tree).as_posix()] = path
    return files


def _check_dest_symlinks(dest: Path) -> None:
    resolved_dest = dest.resolve()
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        current = Path(dirpath)
        for name in dirnames + filenames:
            path = current / name
            if path.is_symlink():
                require_inside(resolved_dest, path.resolve())


def _compare_trees(source: Path, dest: Path) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    src_files = _list_rel_files(source)
    if not dest.exists():
        return (tuple(sorted(src_files)), (), ())

    dest_files = _list_rel_files(dest)
    create: list[str] = []
    identical: list[str] = []
    conflicts: list[str] = []

    for rel in sorted(set(src_files) | set(dest_files)):
        in_src = rel in src_files
        in_dest = rel in dest_files
        if in_src and not in_dest:
            conflicts.append(rel)
        elif in_src and in_dest:
            if src_files[rel].read_bytes() == dest_files[rel].read_bytes():
                identical.append(rel)
            else:
                conflicts.append(rel)
        else:
            conflicts.append(rel)

    return (tuple(create), tuple(identical), tuple(conflicts))


def build_install_plan(
    zip_path: Path,
    install_root: Path | None,
    *,
    staging: Path,
    host: str = "cursor",
) -> tuple[InstallPlan | None, list[Diagnostic]]:
    if host in ("codex", "claude-code") and install_root is None:
        return None, [
            Diagnostic(
                code=CODE_INSTALL_ROOT_REQUIRED,
                level="error",
                message=f"{host} install requires --root",
                path=zip_path.as_posix(),
                hint="pass --root DIR",
            )
        ]

    root = (install_root or DEFAULT_INSTALL_ROOT).expanduser()
    manifest_rel = HOST_MANIFEST[host]

    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                extract_zip_filtered(zf, staging)
            except ValueError as exc:
                return None, [_unreadable_zip(zip_path, str(exc))]
    except (OSError, zipfile.BadZipFile) as exc:
        return None, [_unreadable_zip(zip_path, str(exc))]

    entries = list(staging.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        return None, [
            _not_cursor_zip(
                zip_path,
                "ZIP must contain exactly one top-level plugin directory.",
            )
        ]

    extracted = entries[0]
    manifest = extracted / manifest_rel
    if not manifest.is_file():
        return None, [
            _not_cursor_zip(
                zip_path,
                f"Missing {manifest_rel.as_posix()} in extracted {host} plugin tree.",
            )
        ]

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, [
            _not_cursor_zip(
                zip_path,
                f"Invalid {manifest_rel.as_posix()} in extracted {host} plugin tree.",
            )
        ]

    plugin_name = str(payload.get("name", "")).strip()
    if not plugin_name:
        return None, [
            _not_cursor_zip(
                zip_path,
                "plugin.json is missing a non-empty name field.",
            )
        ]

    try:
        require_safe_plugin_name(plugin_name)
    except ValueError as exc:
        return None, [_not_cursor_zip(zip_path, str(exc))]

    dest = root / plugin_name
    try:
        require_inside(root.resolve(), dest.resolve())
    except ValueError as exc:
        return None, [_not_cursor_zip(zip_path, str(exc))]

    if dest.exists():
        try:
            _check_dest_symlinks(dest)
        except ValueError as exc:
            return None, [_unreadable_zip(zip_path, str(exc))]

    create, identical, conflicts = _compare_trees(extracted, dest)

    plan = InstallPlan(
        plugin_name=plugin_name,
        zip_path=zip_path,
        install_root=root,
        dest=dest,
        extracted_plugin=extracted,
        create=create,
        identical=identical,
        conflicts=conflicts,
        host=host,
    )
    return plan, []
