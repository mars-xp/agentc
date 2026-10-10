#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def _env_src_scripts() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def _write_cursor_zip(
    zip_path: Path,
    *,
    plugin_name: str = "demo-plugin",
    extra_files: dict[str, str] | None = None,
) -> None:
    payload = {
        "payload/.cursor-plugin/plugin.json": json.dumps({"name": plugin_name, "version": "0.0.1"}) + "\n",
        "payload/skills/hello/SKILL.md": "# hello\n",
    }
    if extra_files:
        payload.update(extra_files)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, text in payload.items():
            zf.writestr(name, text)


def _install_cursor(root: Path) -> Path:
    zip_path = root.parent / "p.zip"
    _write_cursor_zip(zip_path)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "agentc",
            "install",
            "cursor",
            "--from",
            str(zip_path),
            "--root",
            str(root),
            "--yes",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_env_src_scripts(),
        cwd=ROOT,
    )
    return root / "demo-plugin"


def test_p5_backup_codes_exist():
    from agentc.model.diagnostics import (
        CODE_AUDIT_FLAG,
        CODE_INSTALL_BACKUP_INVALID,
        CODE_INSTALL_NO_BACKUP,
    )

    assert CODE_INSTALL_NO_BACKUP == "AGC-P5-0014"
    assert CODE_INSTALL_BACKUP_INVALID == "AGC-P5-0015"
    assert CODE_AUDIT_FLAG == "AGC-P5-0009"


def test_backup_dir_outside_plugin_tree():
    from agentc.installer.backup import backup_dir

    root = Path("/tmp/root")
    dest = root / "demo"
    path = backup_dir(root, "cursor", "demo")
    assert path == root / ".agentc" / "backups" / "cursor" / "demo"
    assert dest not in path.parents or path != dest / "backup"
    assert ".agentc" in path.as_posix()


def test_should_snapshot_only_when_replace_or_delete():
    from agentc.installer.backup import should_snapshot
    from agentc.installer.ledger import hash_file

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "dest"
        extracted = Path(tmp) / "zip"
        dest.mkdir()
        extracted.mkdir()
        (dest / "a.txt").write_text("old\n", encoding="utf-8")
        (extracted / "a.txt").write_text("old\n", encoding="utf-8")
        (extracted / "b.txt").write_text("new\n", encoding="utf-8")
        files = {"a.txt": hash_file(dest / "a.txt")}
        assert should_snapshot(
            dest=dest, ledger_files=files, extracted=extracted,
            update=("a.txt",), delete=(),
        ) is False
        (extracted / "a.txt").write_text("changed\n", encoding="utf-8")
        assert should_snapshot(
            dest=dest, ledger_files=files, extracted=extracted,
            update=("a.txt",), delete=(),
        ) is True
        assert should_snapshot(
            dest=dest, ledger_files=files, extracted=None,
            update=(), delete=("a.txt",),
        ) is True


def test_write_and_read_backup_roundtrip_skips_untracked():
    from agentc.installer.backup import backup_dir, read_backup, write_backup
    from agentc.installer.ledger import hash_file

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dest = root / "demo"
        dest.mkdir()
        (dest / "a.txt").write_text("keep\n", encoding="utf-8")
        (dest / "user.txt").write_text("mine\n", encoding="utf-8")
        files = {"a.txt": hash_file(dest / "a.txt")}
        path = write_backup(
            install_root=root, host="cursor", name="demo", dest=dest, files=files,
        )
        assert path == backup_dir(root, "cursor", "demo")
        payload = read_backup(root, "cursor", "demo")
        assert payload is not None
        assert payload["host"] == "cursor"
        assert payload["name"] == "demo"
        assert payload["files"]["a.txt"] == hashlib.sha256(b"keep\n").hexdigest()
        assert "user.txt" not in payload["files"]
        assert (path / "files" / "a.txt").read_text(encoding="utf-8") == "keep\n"
        assert not (path / "files" / "user.txt").exists()
        assert not (dest / ".agentc").exists()


def test_verify_backup_detects_tamper():
    from agentc.installer.backup import verify_backup, write_backup
    from agentc.installer.ledger import hash_file
    from agentc.model.diagnostics import CODE_INSTALL_BACKUP_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dest = root / "demo"
        dest.mkdir()
        (dest / "a.txt").write_text("keep\n", encoding="utf-8")
        files = {"a.txt": hash_file(dest / "a.txt")}
        write_backup(install_root=root, host="cursor", name="demo", dest=dest, files=files)
        slot = root / ".agentc" / "backups" / "cursor" / "demo" / "files" / "a.txt"
        slot.write_text("tamper\n", encoding="utf-8")
        diag = verify_backup(root, "cursor", "demo", root=root)
        assert diag is not None
        assert diag.code == CODE_INSTALL_BACKUP_INVALID


def test_verify_backup_rejects_dest_outside_root():
    from agentc.installer.backup import backup_dir, verify_backup
    from agentc.model.diagnostics import CODE_INSTALL_BACKUP_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        slot = backup_dir(root, "cursor", "demo")
        slot.mkdir(parents=True)
        manifest = {
            "host": "cursor",
            "name": "demo",
            "dest": "/tmp/escaped/demo",
            "files": {},
        }
        (slot / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        diag = verify_backup(root, "cursor", "demo", root=root)
        assert diag is not None
        assert diag.code == CODE_INSTALL_BACKUP_INVALID


def test_apply_backup_tree_copies_and_deletes_extra():
    from agentc.installer.backup import apply_backup_tree
    from agentc.installer.ledger import hash_file

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "dest"
        source = Path(tmp) / "source"
        dest.mkdir()
        source.mkdir()
        (source / "a.txt").write_text("restored\n", encoding="utf-8")
        (dest / "extra.txt").write_text("gone\n", encoding="utf-8")
        files = {"a.txt": hash_file(source / "a.txt")}
        apply_backup_tree(
            dest=dest,
            source_files=source,
            files=files,
            extra_delete=("extra.txt",),
        )
        assert (dest / "a.txt").read_text(encoding="utf-8") == "restored\n"
        assert not (dest / "extra.txt").exists()


def test_sync_without_yes_does_not_write_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        _install_cursor(root)
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0004" in result.stdout + result.stderr
        out = result.stdout + result.stderr
        assert "backup: yes" in out
        assert not (root / ".agentc" / "backups" / "cursor" / "demo-plugin").exists()
        skill = root / "demo-plugin" / "skills" / "hello" / "SKILL.md"
        assert skill.read_text(encoding="utf-8") == "# hello\n"


def test_sync_replace_writes_backup_then_updates():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        extra = dest / "user.txt"
        extra.write_text("mine\n", encoding="utf-8")
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        backed = (
            root
            / ".agentc"
            / "backups"
            / "cursor"
            / "demo-plugin"
            / "files"
            / "skills"
            / "hello"
            / "SKILL.md"
        )
        assert backed.read_text(encoding="utf-8") == "# hello\n"
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# hello v2\n"
        assert extra.read_text(encoding="utf-8") == "mine\n"
        assert "user.txt" not in json.loads(
            (root / ".agentc" / "backups" / "cursor" / "demo-plugin" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )["files"]


def test_sync_create_only_does_not_refresh_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        _install_cursor(root)
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/other/SKILL.md": "# other\n"})
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "demo-plugin" / "skills" / "other" / "SKILL.md").is_file()
        assert not (root / ".agentc" / "backups" / "cursor" / "demo-plugin").exists()


def test_uninstall_yes_keeps_backup_deletes_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "uninstall",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").exists()
        backed = (
            root
            / ".agentc"
            / "backups"
            / "cursor"
            / "demo-plugin"
            / "files"
            / "skills"
            / "hello"
            / "SKILL.md"
        )
        assert backed.read_text(encoding="utf-8") == "# hello\n"
        assert not (dest / "skills" / "hello" / "SKILL.md").exists()


def test_sync_io_failure_restores_dest_from_backup():
    from agentc.installer.run import sync_zip
    from agentc.installer.ledger import ledger_path, read_ledger

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        old = (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8")
        payload = read_ledger(ledger_path(root, "cursor", "demo-plugin"))
        assert payload is not None
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        real_write = Path.write_bytes

        def write_bytes(self, data):  # type: ignore[no-untyped-def]
            posix = self.as_posix()
            if dest.as_posix() in posix and posix.endswith("SKILL.md"):
                raise OSError("injected")
            return real_write(self, data)

        Path.write_bytes = write_bytes  # type: ignore[method-assign]
        try:
            diagnostics, _text, _out = sync_zip(zip_path, root, host="cursor", yes=True)
        finally:
            Path.write_bytes = real_write  # type: ignore[method-assign]
        assert any(d.code == "AGC-P5-0005" for d in diagnostics)
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == old
        assert read_ledger(ledger_path(root, "cursor", "demo-plugin"))["files"] == payload["files"]


def test_restore_without_backup_is_0014():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        _install_cursor(root)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "restore",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0014" in result.stdout + result.stderr


def test_restore_without_yes_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "restore",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0004" in result.stdout + result.stderr
        assert "backup:" in result.stdout + result.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# hello v2\n"


def test_restore_undoes_sync_and_swaps():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(
            zip_path,
            extra_files={
                "payload/skills/hello/SKILL.md": "# hello v2\n",
                "payload/skills/other/SKILL.md": "# other\n",
            },
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert (dest / "skills" / "other" / "SKILL.md").is_file()
        first = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "restore",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# hello\n"
        assert not (dest / "skills" / "other" / "SKILL.md").exists()
        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "restore",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# hello v2\n"
        assert (dest / "skills" / "other" / "SKILL.md").is_file()


def test_restore_after_uninstall_rebuilds_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        extra = dest / "user.txt"
        extra.write_text("mine\n", encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "uninstall",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "restore",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# hello\n"
        assert extra.is_file()
        assert (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").is_file()
        assert (
            root / ".agentc" / "backups" / "cursor" / "demo-plugin" / "manifest.json"
        ).is_file()


def test_restore_modified_is_0008():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        (dest / "skills" / "hello" / "SKILL.md").write_text("# edited\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "restore",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0008" in result.stdout + result.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# edited\n"


def test_restore_codex_requires_root():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentc",
            "restore",
            "codex",
            "--name",
            "demo-plugin",
            "--yes",
        ],
        capture_output=True,
        text=True,
        env=_env_src_scripts(),
        cwd=ROOT,
    )
    assert result.returncode != 0
    assert "AGC-P5-0006" in result.stdout + result.stderr


def test_status_backup_only_is_not_installed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        _install_cursor(root)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "uninstall",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "status",
                "--kind",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        out = result.stdout + result.stderr
        assert "unregistered" in out
        assert "restore" in out.lower() or "backup: yes" in out
        assert "AGC-P5-0007" not in out


def test_sync_write_backup_oserror_leaves_dest_unchanged():
    from agentc.installer.run import sync_zip
    from agentc.installer.ledger import ledger_path, read_ledger
    import agentc.installer.backup as backup_mod

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        old = (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8")
        payload = read_ledger(ledger_path(root, "cursor", "demo-plugin"))
        assert payload is not None
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        real_write = backup_mod.write_backup

        def failing_write_backup(**kwargs):  # type: ignore[no-untyped-def]
            raise OSError("injected write_backup")

        backup_mod.write_backup = failing_write_backup  # type: ignore[assignment]
        try:
            diagnostics, _text, _out = sync_zip(zip_path, root, host="cursor", yes=True)
        finally:
            backup_mod.write_backup = real_write  # type: ignore[assignment]
        assert any(d.code == "AGC-P5-0005" for d in diagnostics)
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == old
        assert read_ledger(ledger_path(root, "cursor", "demo-plugin"))["files"] == payload["files"]
        assert not (root / ".agentc" / "backups" / "cursor" / ".tmp-demo-plugin").exists()


def test_restore_promote_oserror_keeps_dest_ledger_and_backup():
    from agentc.installer.run import restore_plugin, _promote_outgoing_to_slot
    from agentc.installer.ledger import ledger_path, read_ledger
    import agentc.installer.run as run_mod

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        synced = (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8")
        payload = read_ledger(ledger_path(root, "cursor", "demo-plugin"))
        assert payload is not None
        backed = (
            root
            / ".agentc"
            / "backups"
            / "cursor"
            / "demo-plugin"
            / "files"
            / "skills"
            / "hello"
            / "SKILL.md"
        )
        assert backed.read_text(encoding="utf-8") == "# hello\n"

        def failing_promote(**kwargs):  # type: ignore[no-untyped-def]
            raise OSError("injected promote")

        run_mod._promote_outgoing_to_slot = failing_promote  # type: ignore[assignment]
        try:
            diagnostics, _text, _out = restore_plugin(
                host="cursor", name="demo-plugin", install_root=root, yes=True
            )
        finally:
            run_mod._promote_outgoing_to_slot = _promote_outgoing_to_slot  # type: ignore[assignment]
        assert any(d.code == "AGC-P5-0005" for d in diagnostics)
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == synced
        assert read_ledger(ledger_path(root, "cursor", "demo-plugin"))["files"] == payload["files"]
        assert backed.read_text(encoding="utf-8") == "# hello\n"


def test_restore_apply_backup_tree_oserror_keeps_dest_and_ledger():
    from agentc.installer.run import restore_plugin
    from agentc.installer.ledger import ledger_path, read_ledger
    import agentc.installer.backup as backup_mod

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        synced = (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8")
        payload = read_ledger(ledger_path(root, "cursor", "demo-plugin"))
        assert payload is not None
        real_apply = backup_mod.apply_backup_tree
        apply_calls = 0

        def failing_apply(**kwargs):  # type: ignore[no-untyped-def]
            nonlocal apply_calls
            apply_calls += 1
            if apply_calls == 1:
                raise OSError("injected apply_backup_tree")
            return real_apply(**kwargs)

        backup_mod.apply_backup_tree = failing_apply  # type: ignore[assignment]
        try:
            diagnostics, _text, _out = restore_plugin(
                host="cursor", name="demo-plugin", install_root=root, yes=True
            )
        finally:
            backup_mod.apply_backup_tree = real_apply  # type: ignore[assignment]
        assert any(d.code == "AGC-P5-0005" for d in diagnostics)
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == synced
        assert read_ledger(ledger_path(root, "cursor", "demo-plugin"))["files"] == payload["files"]


def test_verify_backup_rejects_file_escape():
    from agentc.installer.backup import backup_dir, verify_backup
    from agentc.model.diagnostics import CODE_INSTALL_BACKUP_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        slot = backup_dir(root, "cursor", "demo")
        files_dir = slot / "files"
        files_dir.mkdir(parents=True)
        dest = root / "demo"
        dest.mkdir()
        manifest = {
            "host": "cursor",
            "name": "demo",
            "dest": dest.as_posix(),
            "files": {"../escape.txt": "deadbeef"},
        }
        (slot / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        diag = verify_backup(root, "cursor", "demo", root=root)
        assert diag is not None
        assert diag.code == CODE_INSTALL_BACKUP_INVALID


def test_restore_corrupt_manifest_is_0015_dest_unchanged():
    from agentc.installer.backup import backup_dir

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        old = (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8")
        zip_path = root.parent / "p2.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "sync",
                "cursor",
                "--from",
                str(zip_path),
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        slot = backup_dir(root, "cursor", "demo-plugin")
        (slot / "manifest.json").write_text("{not-json", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "restore",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0015" in result.stdout + result.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") != old


def test_status_corrupt_backup_exits_zero():
    from agentc.installer.backup import backup_dir

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        _install_cursor(root)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "uninstall",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        slot = backup_dir(root, "cursor", "demo-plugin")
        (slot / "manifest.json").write_text("{bad", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "status",
                "--kind",
                "cursor",
                "--name",
                "demo-plugin",
                "--root",
                str(root),
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        out = result.stdout + result.stderr
        assert "backup: no" in out or "unregistered" in out


def test_help_lists_restore():
    proc = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "restore" in proc.stdout


def main() -> int:
    tests = [
        test_p5_backup_codes_exist,
        test_backup_dir_outside_plugin_tree,
        test_should_snapshot_only_when_replace_or_delete,
        test_write_and_read_backup_roundtrip_skips_untracked,
        test_verify_backup_detects_tamper,
        test_verify_backup_rejects_dest_outside_root,
        test_apply_backup_tree_copies_and_deletes_extra,
        test_sync_without_yes_does_not_write_backup,
        test_sync_replace_writes_backup_then_updates,
        test_sync_create_only_does_not_refresh_backup,
        test_uninstall_yes_keeps_backup_deletes_ledger,
        test_sync_io_failure_restores_dest_from_backup,
        test_restore_without_backup_is_0014,
        test_restore_without_yes_does_not_write,
        test_restore_undoes_sync_and_swaps,
        test_restore_after_uninstall_rebuilds_ledger,
        test_restore_modified_is_0008,
        test_restore_codex_requires_root,
        test_status_backup_only_is_not_installed,
        test_sync_write_backup_oserror_leaves_dest_unchanged,
        test_restore_promote_oserror_keeps_dest_ledger_and_backup,
        test_restore_apply_backup_tree_oserror_keeps_dest_and_ledger,
        test_verify_backup_rejects_file_escape,
        test_restore_corrupt_manifest_is_0015_dest_unchanged,
        test_status_corrupt_backup_exits_zero,
        test_help_lists_restore,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
