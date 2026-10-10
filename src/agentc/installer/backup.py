"""Install backup slots beside the ledger, outside the plugin tree."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from agentc.installer.ledger import hash_file
from agentc.model.diagnostics import CODE_INSTALL_BACKUP_INVALID, Diagnostic


def backup_dir(install_root: Path, host: str, name: str) -> Path:
    return install_root / ".agentc" / "backups" / host / name


def should_snapshot(
    *,
    dest: Path,
    ledger_files: dict[str, str],
    extracted: Path | None,
    update: tuple[str, ...],
    delete: tuple[str, ...],
) -> bool:
    if delete:
        return True
    if not update or extracted is None:
        return False
    for rel in update:
        dest_path = dest / rel
        if not dest_path.is_file():
            continue
        extracted_path = extracted / rel
        if not extracted_path.is_file():
            return True
        if hash_file(dest_path) != hash_file(extracted_path):
            return True
    return False


def write_backup(
    *,
    install_root: Path,
    host: str,
    name: str,
    dest: Path,
    files: dict[str, str],
) -> Path:
    from plugin_compiler.paths import require_inside

    slot = backup_dir(install_root, host, name)
    host_dir = install_root / ".agentc" / "backups" / host
    tmp_dir = host_dir / f".tmp-{name}"
    prev_dir = host_dir / f".prev-{name}"

    require_inside(install_root.resolve(), slot.resolve())

    host_dir.mkdir(parents=True, exist_ok=True)
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    try:
        files_dir = tmp_dir / "files"
        files_dir.mkdir(parents=True)
        for rel in files:
            src = dest / rel
            require_inside(dest.resolve(), src.resolve())
            dst = files_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        manifest = {
            "host": host,
            "name": name,
            "dest": dest.as_posix(),
            "files": files,
        }
        (tmp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        had_prev = False
        if slot.exists():
            os.replace(slot, prev_dir)
            had_prev = True
        os.replace(tmp_dir, slot)
        if had_prev and prev_dir.exists():
            shutil.rmtree(prev_dir)
    except OSError:
        if prev_dir.exists():
            if slot.exists():
                try:
                    shutil.rmtree(slot)
                except OSError:
                    pass
            try:
                os.replace(prev_dir, slot)
            except OSError:
                pass
        if tmp_dir.exists():
            try:
                shutil.rmtree(tmp_dir)
            except OSError:
                pass
        raise

    return slot


def read_backup(install_root: Path, host: str, name: str) -> dict | None:
    manifest = backup_dir(install_root, host, name) / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _backup_invalid(message: str, path: str | None = None) -> Diagnostic:
    return Diagnostic(
        code=CODE_INSTALL_BACKUP_INVALID,
        level="error",
        message=message,
        path=path,
        hint="",
    )


def verify_backup(
    install_root: Path, host: str, name: str, *, root: Path
) -> Diagnostic | None:
    from plugin_compiler.paths import require_inside

    manifest = backup_dir(install_root, host, name) / "manifest.json"
    if not manifest.is_file():
        return _backup_invalid(
            "backup manifest missing",
            backup_dir(install_root, host, name).as_posix(),
        )

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _backup_invalid("backup manifest unreadable", manifest.as_posix())

    if not isinstance(payload, dict):
        return _backup_invalid("backup manifest invalid", manifest.as_posix())

    dest_text = payload.get("dest")
    if not isinstance(dest_text, str):
        return _backup_invalid("backup manifest invalid dest")

    dest_path = Path(dest_text)
    try:
        require_inside(root.resolve(), dest_path.resolve())
    except ValueError:
        return _backup_invalid("backup dest escapes install root", dest_text)

    files = payload.get("files")
    if not isinstance(files, dict):
        return _backup_invalid("backup manifest invalid files")

    slot = backup_dir(install_root, host, name)
    files_root = slot / "files"
    resolved_dest = dest_path.resolve()
    resolved_files_root = files_root.resolve()
    for rel, expected in files.items():
        if not isinstance(rel, str) or not isinstance(expected, str):
            return _backup_invalid("backup manifest invalid file entry")
        backup_file = files_root / rel
        dest_file = dest_path / rel
        try:
            require_inside(resolved_files_root, backup_file.resolve())
            require_inside(resolved_dest, dest_file.resolve())
        except ValueError:
            return _backup_invalid("backup file path escapes root", rel)
        if not backup_file.is_file():
            return _backup_invalid("backup file missing", backup_file.as_posix())
        if hash_file(backup_file) != expected:
            return _backup_invalid("backup file hash mismatch", backup_file.as_posix())

    return None


def apply_backup_tree(
    *,
    dest: Path,
    source_files: Path,
    files: dict[str, str],
    extra_delete: tuple[str, ...],
) -> None:
    from plugin_compiler.paths import require_inside

    resolved_dest = dest.resolve()
    for rel in files:
        src = source_files / rel
        dst = dest / rel
        require_inside(resolved_dest, dst.resolve())
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for rel in extra_delete:
        target = dest / rel
        require_inside(resolved_dest, target.resolve())
        if target.is_file():
            target.unlink()
