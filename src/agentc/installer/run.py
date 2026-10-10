"""Install host plugin zips onto the local filesystem."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from agentc.installer.pipeline import DEFAULT_INSTALL_ROOT, build_install_plan
from agentc.installer.render import render_install_text
from agentc.model.diagnostics import (
    CODE_INSTALL_IO,
    CODE_INSTALL_MODIFIED,
    CODE_INSTALL_NO_BACKUP,
    CODE_INSTALL_NO_LEDGER,
    CODE_INSTALL_ROOT_REQUIRED,
    CODE_INSTALL_TXN_INCOMPLETE,
    CODE_INSTALL_TXN_ROLLED_BACK,
    CODE_INSTALL_UNCONFIRMED,
    CODE_INSTALL_UNREADABLE,
    Diagnostic,
)


def install_zip(
    zip_path: Path, install_root: Path | None, *, host: str, yes: bool
) -> tuple[list[Diagnostic], str, Path | None, bool]:
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        plan, diagnostics = build_install_plan(
            zip_path, install_root, staging=staging, host=host
        )
        if plan is None:
            return diagnostics, "", None, False
        text = render_install_text(plan)
        dest = plan.dest
        if not yes:
            diagnostics = list(diagnostics) + [
                Diagnostic(
                    code=CODE_INSTALL_UNCONFIRMED,
                    level="error",
                    message="install not confirmed",
                    path=dest.as_posix(),
                    hint="re-run with --yes",
                )
            ]
            return diagnostics, text, dest, False
        from agentc.installer.write import apply_install

        diagnostics, text, wrote = apply_install(plan, diagnostics, text)
        return diagnostics, text, dest, wrote


def install_cursor_zip(
    zip_path: Path, install_root: Path | None, *, yes: bool
) -> tuple[list[Diagnostic], str, Path | None, bool]:
    return install_zip(zip_path, install_root, host="cursor", yes=yes)


def _diag_has_error(diagnostics: list[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)


def _rollback_written(
    written: list[tuple[str, str, Path | None]], diagnostics: list[Diagnostic]
) -> list[Diagnostic]:
    diagnostics = list(diagnostics)
    rollback_failed_hosts: list[str] = []
    for host, name, install_root in reversed(written):
        uninstall_diags, _text, _dest = uninstall_plugin(
            host=host, name=name, install_root=install_root, yes=True
        )
        diagnostics.extend(uninstall_diags)
        if _diag_has_error(uninstall_diags):
            rollback_failed_hosts.append(host)
    if rollback_failed_hosts:
        diagnostics.append(
            Diagnostic(
                code=CODE_INSTALL_TXN_INCOMPLETE,
                level="error",
                message=f"rollback incomplete for {rollback_failed_hosts[-1]}",
                path=None,
                hint="",
            )
        )
    else:
        diagnostics.append(
            Diagnostic(
                code=CODE_INSTALL_TXN_ROLLED_BACK,
                level="error",
                message="three-host install rolled back",
                path=None,
                hint="",
            )
        )
    return diagnostics


def install_all(
    *,
    cursor_zip: Path,
    codex_zip: Path,
    claude_zip: Path,
    cursor_root: Path | None,
    codex_root: Path | None,
    claude_root: Path | None,
    yes: bool,
    partial_ok: bool,
) -> tuple[list[Diagnostic], str, tuple[Path, ...]]:
    if codex_root is None or claude_root is None:
        missing: list[str] = []
        if codex_root is None:
            missing.append("--codex-root")
        if claude_root is None:
            missing.append("--claude-root")
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_ROOT_REQUIRED,
                    level="error",
                    message=f"install requires {' and '.join(missing)}",
                    path=None,
                    hint="pass --root DIR",
                )
            ],
            "",
            (),
        )

    hosts: tuple[tuple[str, Path, Path | None], ...] = (
        ("cursor", cursor_zip, cursor_root),
        ("codex", codex_zip, codex_root),
        ("claude-code", claude_zip, claude_root),
    )

    diagnostics: list[Diagnostic] = []
    text_parts: list[str] = []
    written: list[tuple[str, str, Path | None]] = []
    dests: list[Path] = []

    for host, zip_path, root in hosts:
        host_diags, host_text, dest, wrote = install_zip(
            zip_path, root, host=host, yes=yes
        )
        diagnostics.extend(host_diags)
        if host_text:
            text_parts.append(host_text)
        if yes and wrote and dest is not None:
            written.append((host, dest.name, root))
            dests.append(dest)
        if yes and _diag_has_error(host_diags):
            if not partial_ok and written:
                diagnostics = _rollback_written(written, diagnostics)
            return diagnostics, "".join(text_parts), ()

    return diagnostics, "".join(text_parts), tuple(dests)


def _render_uninstall_text(
    *,
    host: str,
    name: str,
    root: Path,
    dest: Path,
    delete: tuple[str, ...],
    backup: str,
) -> str:
    lines = [
        "# agentc uninstall",
        f"host: {host}",
        f"plugin: {name}",
        f"root: {root.as_posix()}",
        f"dest: {dest.as_posix()}",
        f"backup: {backup}",
        "delete:",
    ]
    if delete:
        lines.extend(f"- {rel}" for rel in delete)
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def uninstall_plugin(
    *, host: str, name: str, install_root: Path | None, yes: bool
) -> tuple[list[Diagnostic], str, Path | None]:
    from agentc.installer.ledger import drifted_files, hash_tree, ledger_path, read_ledger

    if host in ("codex", "claude-code") and install_root is None:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_ROOT_REQUIRED,
                    level="error",
                    message=f"{host} uninstall requires --root",
                    path=None,
                    hint="pass --root DIR",
                )
            ],
            "",
            None,
        )

    from plugin_compiler.paths import require_inside

    root = (install_root or DEFAULT_INSTALL_ROOT).expanduser()
    path = ledger_path(root, host, name)
    payload = read_ledger(path)
    dest = Path(payload["dest"]) if payload is not None else root / name
    if payload is None:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_NO_LEDGER,
                    level="error",
                    message="no install ledger",
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            "",
            dest,
        )

    files = payload.get("files") or {}
    try:
        require_inside(root.resolve(), dest.resolve())
        require_inside(root.resolve(), path.resolve())
    except ValueError as exc:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_UNREADABLE,
                    level="error",
                    message=str(exc),
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            "",
            dest,
        )

    drifted = drifted_files(dest, files)
    if drifted:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_MODIFIED,
                    level="error",
                    message="installed files have been modified",
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            "",
            dest,
        )

    delete = tuple(sorted(files))
    text = _render_uninstall_text(
        host=host, name=name, root=root, dest=dest, delete=delete, backup="yes"
    )
    if not yes:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_UNCONFIRMED,
                    level="error",
                    message="uninstall not confirmed",
                    path=dest.as_posix(),
                    hint="re-run with --yes",
                )
            ],
            text,
            dest,
        )

    from agentc.installer.backup import apply_backup_tree, backup_dir, write_backup

    try:
        write_backup(
            install_root=root, host=host, name=name, dest=dest, files=files
        )
    except OSError as exc:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_IO,
                    level="error",
                    message=str(exc),
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            text,
            dest,
        )

    try:
        for rel in delete:
            target = dest / rel
            require_inside(dest.resolve(), target.resolve())
            if target.is_file():
                target.unlink()
        if dest.exists() and not hash_tree(dest):
            shutil.rmtree(dest)
        path.unlink()
    except ValueError as exc:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_UNREADABLE,
                    level="error",
                    message=str(exc),
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            text,
            dest,
        )
    except OSError as exc:
        apply_backup_tree(
            dest=dest,
            source_files=backup_dir(root, host, name) / "files",
            files=files,
            extra_delete=(),
        )
        shutil.rmtree(
            root / ".agentc" / "backups" / host / (".tmp-" + name),
            ignore_errors=True,
        )
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_IO,
                    level="error",
                    message=str(exc),
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            text,
            dest,
        )

    return [], text, dest if dest.exists() else None


def _render_restore_text(
    *,
    host: str,
    name: str,
    root: Path,
    dest: Path,
    backup: Path,
    write: tuple[str, ...],
    delete: tuple[str, ...],
) -> str:
    lines = [
        "# agentc restore",
        f"host: {host}",
        f"plugin: {name}",
        f"root: {root.as_posix()}",
        f"dest: {dest.as_posix()}",
        f"backup: {backup.as_posix()}",
        "write:",
    ]
    if write:
        lines.extend(f"- {rel}" for rel in write)
    else:
        lines.append("(none)")
    lines.append("delete:")
    if delete:
        lines.extend(f"- {rel}" for rel in delete)
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _write_outgoing_snapshot(
    *,
    install_root: Path,
    host: str,
    name: str,
    dest: Path,
    files: dict[str, str],
) -> Path:
    from plugin_compiler.paths import require_inside

    host_dir = install_root / ".agentc" / "backups" / host
    outgoing = host_dir / f".outgoing-{name}"
    require_inside(install_root.resolve(), outgoing.resolve())

    host_dir.mkdir(parents=True, exist_ok=True)
    if outgoing.exists():
        shutil.rmtree(outgoing)

    files_dir = outgoing / "files"
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
    (outgoing / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return outgoing


def _cleanup_restore_temps(install_root: Path, host: str, name: str) -> None:
    host_dir = install_root / ".agentc" / "backups" / host
    shutil.rmtree(host_dir / f".outgoing-{name}", ignore_errors=True)
    shutil.rmtree(host_dir / f".tmp-{name}", ignore_errors=True)
    shutil.rmtree(host_dir / f".prev-{name}", ignore_errors=True)


def _rollback_dest_from_outgoing(
    *,
    dest: Path,
    outgoing: Path,
    files: dict[str, str],
) -> None:
    from agentc.installer.backup import apply_backup_tree

    apply_backup_tree(
        dest=dest,
        source_files=outgoing / "files",
        files=files,
        extra_delete=(),
    )


def _restore_backup_slot_from_prev(install_root: Path, host: str, name: str) -> None:
    host_dir = install_root / ".agentc" / "backups" / host
    slot = host_dir / name
    prev_dir = host_dir / f".prev-{name}"
    if not prev_dir.exists():
        return
    if slot.exists():
        shutil.rmtree(slot)
    os.replace(prev_dir, slot)


def _rollback_restore_pre_state(
    *,
    install_root: Path,
    host: str,
    name: str,
    dest: Path,
    outgoing: Path,
    ledger_files: dict[str, str],
) -> None:
    from agentc.installer.ledger import write_ledger

    _rollback_dest_from_outgoing(dest=dest, outgoing=outgoing, files=ledger_files)
    write_ledger(
        install_root=install_root,
        host=host,
        name=name,
        dest=dest,
        files=ledger_files,
    )
    _restore_backup_slot_from_prev(install_root, host, name)


def _remove_restored_files(dest: Path, files: dict[str, str]) -> None:
    from plugin_compiler.paths import require_inside

    resolved_dest = dest.resolve()
    for rel in files:
        target = dest / rel
        try:
            require_inside(resolved_dest, target.resolve())
        except ValueError:
            continue
        if target.is_file():
            target.unlink()


def _promote_outgoing_to_slot(
    *,
    install_root: Path,
    host: str,
    name: str,
    outgoing: Path,
) -> None:
    host_dir = install_root / ".agentc" / "backups" / host
    slot = host_dir / name
    prev_dir = host_dir / f".prev-{name}"

    had_prev = False
    try:
        if slot.exists():
            os.replace(slot, prev_dir)
            had_prev = True
        os.replace(outgoing, slot)
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
        raise


def restore_plugin(
    *, host: str, name: str, install_root: Path | None, yes: bool
) -> tuple[list[Diagnostic], str, Path | None]:
    from agentc.installer.backup import apply_backup_tree, backup_dir, read_backup, verify_backup
    from agentc.installer.ledger import drifted_files, ledger_path, read_ledger, write_ledger
    from plugin_compiler.paths import require_inside

    if host in ("codex", "claude-code") and install_root is None:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_ROOT_REQUIRED,
                    level="error",
                    message=f"{host} restore requires --root",
                    path=None,
                    hint="pass --root DIR",
                )
            ],
            "",
            None,
        )

    root = (install_root or DEFAULT_INSTALL_ROOT).expanduser()
    slot = backup_dir(root, host, name)
    manifest_path = slot / "manifest.json"
    if not manifest_path.is_file():
        dest = root / name
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_NO_BACKUP,
                    level="error",
                    message="no install backup",
                    path=slot.as_posix(),
                    hint="",
                )
            ],
            "",
            dest,
        )

    invalid = verify_backup(root, host, name, root=root)
    if invalid is not None:
        dest = root / name
        return ([invalid], "", dest)

    backup = read_backup(root, host, name)
    assert backup is not None
    backup_files = backup.get("files") or {}
    backup_dest = Path(str(backup.get("dest") or root / name))

    path = ledger_path(root, host, name)
    payload = read_ledger(path)
    ledger_files: dict[str, str] | None = None
    dest = backup_dest
    if payload is not None:
        dest = Path(payload["dest"])
        ledger_files = payload.get("files") or {}
        try:
            require_inside(root.resolve(), dest.resolve())
            require_inside(root.resolve(), path.resolve())
        except ValueError as exc:
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_UNREADABLE,
                        level="error",
                        message=str(exc),
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                "",
                dest,
            )
        drifted = drifted_files(dest, ledger_files)
        if drifted:
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_MODIFIED,
                        level="error",
                        message="installed files have been modified",
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                "",
                dest,
            )

    write = tuple(sorted(backup_files))
    if ledger_files is not None:
        delete = tuple(sorted(set(ledger_files) - set(backup_files)))
    else:
        delete = ()

    text = _render_restore_text(
        host=host,
        name=name,
        root=root,
        dest=dest,
        backup=slot,
        write=write,
        delete=delete,
    )
    if not yes:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_UNCONFIRMED,
                    level="error",
                    message="restore not confirmed",
                    path=dest.as_posix(),
                    hint="re-run with --yes",
                )
            ],
            text,
            dest,
        )

    source_files = slot / "files"
    host_dir = root / ".agentc" / "backups" / host

    if ledger_files is None:
        dest_changed = False
        try:
            dest.mkdir(parents=True, exist_ok=True)
            apply_backup_tree(
                dest=dest,
                source_files=source_files,
                files=backup_files,
                extra_delete=(),
            )
            dest_changed = True
            write_ledger(
                install_root=root,
                host=host,
                name=name,
                dest=dest,
                files=backup_files,
            )
        except ValueError as exc:
            if dest_changed:
                _remove_restored_files(dest, backup_files)
            _cleanup_restore_temps(root, host, name)
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_UNREADABLE,
                        level="error",
                        message=str(exc),
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                text,
                dest,
            )
        except OSError as exc:
            if dest_changed:
                _remove_restored_files(dest, backup_files)
            _cleanup_restore_temps(root, host, name)
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_IO,
                        level="error",
                        message=str(exc),
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                text,
                dest,
            )
        return [], text, dest

    outgoing: Path | None = None
    dest_changed = False
    try:
        outgoing = _write_outgoing_snapshot(
            install_root=root,
            host=host,
            name=name,
            dest=dest,
            files=ledger_files,
        )
        dest_changed = True
        apply_backup_tree(
            dest=dest,
            source_files=source_files,
            files=backup_files,
            extra_delete=delete,
        )
        write_ledger(
            install_root=root,
            host=host,
            name=name,
            dest=dest,
            files=backup_files,
        )
    except ValueError as exc:
        if dest_changed and outgoing is not None:
            _rollback_restore_pre_state(
                install_root=root,
                host=host,
                name=name,
                dest=dest,
                outgoing=outgoing,
                ledger_files=ledger_files,
            )
        _cleanup_restore_temps(root, host, name)
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_UNREADABLE,
                    level="error",
                    message=str(exc),
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            text,
            dest,
        )
    except OSError as exc:
        if dest_changed and outgoing is not None:
            _rollback_restore_pre_state(
                install_root=root,
                host=host,
                name=name,
                dest=dest,
                outgoing=outgoing,
                ledger_files=ledger_files,
            )
        _cleanup_restore_temps(root, host, name)
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_IO,
                    level="error",
                    message=str(exc),
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            text,
            dest,
        )

    assert outgoing is not None
    try:
        _promote_outgoing_to_slot(
            install_root=root, host=host, name=name, outgoing=outgoing
        )
    except OSError as exc:
        _rollback_restore_pre_state(
            install_root=root,
            host=host,
            name=name,
            dest=dest,
            outgoing=outgoing,
            ledger_files=ledger_files,
        )
        _cleanup_restore_temps(root, host, name)
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_IO,
                    level="error",
                    message=str(exc),
                    path=dest.as_posix(),
                    hint="",
                )
            ],
            text,
            dest,
        )

    return [], text, dest


def _render_status_text(
    *,
    host: str,
    name: str,
    root: Path,
    dest: Path | None,
    state: str,
    drift: tuple[str, ...],
    backup: str,
) -> str:
    lines = [
        "# agentc status",
        f"host: {host}",
        f"plugin: {name}",
        f"root: {root.as_posix()}",
    ]
    if dest is not None:
        lines.append(f"dest: {dest.as_posix()}")
    lines.append(f"state: {state}")
    lines.append(f"backup: {backup}")
    if state == "unregistered" and backup == "yes":
        lines.append("restore: available (re-run with agentc restore --yes)")
    lines.append("drift:")
    if drift:
        lines.extend(f"- {rel}" for rel in drift)
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _render_sync_text(
    *,
    host: str,
    name: str,
    zip_path: Path,
    root: Path,
    dest: Path,
    backup: str,
    update: tuple[str, ...],
    create: tuple[str, ...],
    delete: tuple[str, ...],
) -> str:
    lines = [
        "# agentc sync",
        f"host: {host}",
        f"plugin: {name}",
        f"zip: {zip_path.as_posix()}",
        f"root: {root.as_posix()}",
        f"dest: {dest.as_posix()}",
        f"backup: {backup}",
        "update:",
    ]
    if update:
        lines.extend(f"- {rel}" for rel in update)
    else:
        lines.append("(none)")
    lines.append("create:")
    if create:
        lines.extend(f"- {rel}" for rel in create)
    else:
        lines.append("(none)")
    lines.append("delete:")
    if delete:
        lines.extend(f"- {rel}" for rel in delete)
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


def _status_entry(
    *, host: str, name: str, root: Path, payload: dict
) -> tuple[list[Diagnostic], str]:
    from agentc.installer.backup import read_backup
    from agentc.installer.ledger import drifted_files

    dest = Path(payload["dest"])
    files = payload.get("files") or {}
    drift = drifted_files(dest, files)
    diagnostics: list[Diagnostic] = []
    if drift:
        diagnostics.append(
            Diagnostic(
                code=CODE_INSTALL_MODIFIED,
                level="warning",
                message="installed files have drifted",
                path=dest.as_posix(),
                hint="",
            )
        )
    backup_label = "yes" if read_backup(root, host, name) is not None else "no"
    text = _render_status_text(
        host=host,
        name=name,
        root=root,
        dest=dest,
        state="registered",
        drift=drift,
        backup=backup_label,
    )
    return diagnostics, text


def _status_backup_only_entry(
    *, host: str, name: str, root: Path, payload: dict
) -> tuple[list[Diagnostic], str]:
    dest = Path(payload["dest"])
    diagnostics = [
        Diagnostic(
            code="",
            level="notice",
            message="unregistered",
            path=None,
            hint="",
        )
    ]
    text = _render_status_text(
        host=host,
        name=name,
        root=root,
        dest=dest,
        state="unregistered",
        drift=(),
        backup="yes",
    )
    return diagnostics, text


def status_installs(
    *, host: str | None, name: str | None, install_root: Path | None
) -> tuple[list[Diagnostic], str]:
    from agentc.installer.backup import read_backup
    from agentc.installer.ledger import INSTALL_HOSTS, ledger_path, read_ledger

    if host in ("codex", "claude-code") and install_root is None:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_ROOT_REQUIRED,
                    level="error",
                    message=f"{host} status requires --root",
                    path=None,
                    hint="pass --root DIR",
                )
            ],
            "",
        )

    root = (install_root or DEFAULT_INSTALL_ROOT).expanduser()
    hosts = (host,) if host else INSTALL_HOSTS
    diagnostics: list[Diagnostic] = []
    sections: list[str] = []

    if name is not None:
        for item_host in hosts:
            payload = read_ledger(ledger_path(root, item_host, name))
            if payload is None:
                continue
            entry_diags, text = _status_entry(
                host=item_host, name=name, root=root, payload=payload
            )
            diagnostics.extend(entry_diags)
            sections.append(text)
        if sections:
            return diagnostics, "".join(sections)
        for item_host in hosts:
            backup_payload = read_backup(root, item_host, name)
            if backup_payload is None:
                continue
            entry_diags, text = _status_backup_only_entry(
                host=item_host, name=name, root=root, payload=backup_payload
            )
            diagnostics.extend(entry_diags)
            sections.append(text)
        if sections:
            return diagnostics, "".join(sections)
        return (
            [
                Diagnostic(
                    code="",
                    level="notice",
                    message="unregistered",
                    path=None,
                    hint="",
                )
            ],
            _render_status_text(
                host=host or "any",
                name=name,
                root=root,
                dest=None,
                state="unregistered",
                drift=(),
                backup="no",
            ),
        )

    seen: set[tuple[str, str]] = set()
    installs_root = root / ".agentc" / "installs"
    for item_host in hosts:
        host_dir = installs_root / item_host
        if not host_dir.is_dir():
            continue
        for path in sorted(host_dir.glob("*.json")):
            payload = read_ledger(path)
            if payload is None:
                continue
            plugin_name = str(payload.get("name") or path.stem)
            seen.add((item_host, plugin_name))
            entry_diags, text = _status_entry(
                host=item_host, name=plugin_name, root=root, payload=payload
            )
            diagnostics.extend(entry_diags)
            sections.append(text)

    backups_root = root / ".agentc" / "backups"
    for item_host in hosts:
        host_dir = backups_root / item_host
        if not host_dir.is_dir():
            continue
        for manifest_path in sorted(host_dir.glob("*/manifest.json")):
            plugin_name = manifest_path.parent.name
            if plugin_name.startswith(".tmp-") or plugin_name.startswith(".prev-"):
                continue
            if (item_host, plugin_name) in seen:
                continue
            backup_payload = read_backup(root, item_host, plugin_name)
            if backup_payload is None:
                continue
            seen.add((item_host, plugin_name))
            entry_diags, text = _status_backup_only_entry(
                host=item_host,
                name=plugin_name,
                root=root,
                payload=backup_payload,
            )
            diagnostics.extend(entry_diags)
            sections.append(text)
    return diagnostics, "".join(sections)


def sync_zip(
    zip_path: Path, install_root: Path | None, *, host: str, yes: bool
) -> tuple[list[Diagnostic], str, Path | None]:
    if host in ("codex", "claude-code") and install_root is None:
        return (
            [
                Diagnostic(
                    code=CODE_INSTALL_ROOT_REQUIRED,
                    level="error",
                    message=f"{host} sync requires --root",
                    path=zip_path.as_posix(),
                    hint="pass --root DIR",
                )
            ],
            "",
            None,
        )

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        plan, diagnostics = build_install_plan(
            zip_path, install_root, staging=staging, host=host
        )
        if plan is None:
            return diagnostics, "", None

        from agentc.installer.ledger import (
            drifted_files,
            hash_file,
            hash_tree,
            ledger_path,
            read_ledger,
            write_ledger,
        )
        from plugin_compiler.paths import require_inside

        path = ledger_path(plan.install_root, host, plan.plugin_name)
        payload = read_ledger(path)
        dest = Path(payload["dest"]) if payload is not None else plan.dest
        if payload is None:
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_NO_LEDGER,
                        level="error",
                        message="no install ledger",
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                "",
                dest,
            )

        files = payload.get("files") or {}
        try:
            require_inside(plan.install_root.resolve(), dest.resolve())
            require_inside(plan.install_root.resolve(), path.resolve())
        except ValueError as exc:
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_UNREADABLE,
                        level="error",
                        message=str(exc),
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                "",
                dest,
            )

        drifted = drifted_files(dest, files)
        if drifted:
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_MODIFIED,
                        level="error",
                        message="installed files have been modified",
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                "",
                dest,
            )

        zip_files = hash_tree(plan.extracted_plugin)
        ledger_rels = set(files)
        zip_rels = set(zip_files)
        update = tuple(sorted(rel for rel in zip_rels if rel in ledger_rels))
        create = tuple(
            sorted(
                rel
                for rel in zip_rels
                if rel not in ledger_rels and not (dest / rel).exists()
            )
        )
        delete = tuple(sorted(rel for rel in ledger_rels if rel not in zip_rels))
        from agentc.installer.backup import (
            apply_backup_tree,
            backup_dir,
            should_snapshot,
            write_backup,
        )

        extracted = plan.extracted_plugin
        snapshot = should_snapshot(
            dest=dest,
            ledger_files=files,
            extracted=extracted,
            update=update,
            delete=delete,
        )
        backup_label = "yes" if snapshot else "no"
        text = _render_sync_text(
            host=host,
            name=plan.plugin_name,
            zip_path=zip_path,
            root=plan.install_root,
            dest=dest,
            backup=backup_label,
            update=update,
            create=create,
            delete=delete,
        )
        if not yes:
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_UNCONFIRMED,
                        level="error",
                        message="sync not confirmed",
                        path=dest.as_posix(),
                        hint="re-run with --yes",
                    )
                ],
                text,
                dest,
            )

        if snapshot:
            try:
                write_backup(
                    install_root=plan.install_root,
                    host=host,
                    name=plan.plugin_name,
                    dest=dest,
                    files=files,
                )
            except OSError as exc:
                shutil.rmtree(
                    plan.install_root
                    / ".agentc"
                    / "backups"
                    / host
                    / (".tmp-" + plan.plugin_name),
                    ignore_errors=True,
                )
                return (
                    [
                        Diagnostic(
                            code=CODE_INSTALL_IO,
                            level="error",
                            message=str(exc),
                            path=dest.as_posix(),
                            hint="",
                        )
                    ],
                    text,
                    dest,
                )

        try:
            for rel in update + create:
                target = dest / rel
                require_inside(dest.resolve(), target.resolve())
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((extracted / rel).read_bytes())
            for rel in delete:
                target = dest / rel
                require_inside(dest.resolve(), target.resolve())
                if target.is_file():
                    target.unlink()
            kept = (set(files) - set(delete)) | set(create)
            write_ledger(
                install_root=plan.install_root,
                host=host,
                name=plan.plugin_name,
                dest=dest,
                files={rel: hash_file(dest / rel) for rel in sorted(kept)},
            )
        except ValueError as exc:
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_UNREADABLE,
                        level="error",
                        message=str(exc),
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                text,
                dest,
            )
        except OSError as exc:
            if snapshot:
                apply_backup_tree(
                    dest=dest,
                    source_files=backup_dir(plan.install_root, host, plan.plugin_name)
                    / "files",
                    files=files,
                    extra_delete=create,
                )
            shutil.rmtree(
                plan.install_root
                / ".agentc"
                / "backups"
                / host
                / (".tmp-" + plan.plugin_name),
                ignore_errors=True,
            )
            return (
                [
                    Diagnostic(
                        code=CODE_INSTALL_IO,
                        level="error",
                        message=str(exc),
                        path=dest.as_posix(),
                        hint="",
                    )
                ],
                text,
                dest,
            )

        return [], text, dest
