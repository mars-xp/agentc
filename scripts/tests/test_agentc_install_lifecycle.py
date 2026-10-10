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


def _write_cursor_zip(
    zip_path: Path,
    *,
    plugin_name: str = "demo-plugin",
    extra_files: dict[str, str] | None = None,
) -> None:
    payload = {
        "payload/.cursor-plugin/plugin.json": json.dumps(
            {"name": plugin_name, "version": "0.0.1"}
        )
        + "\n",
        "payload/skills/hello/SKILL.md": "# hello\n",
    }
    if extra_files:
        payload.update(extra_files)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, text in payload.items():
            zf.writestr(name, text)


def _env_src_only() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _env_src_scripts() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


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


def test_p5_lifecycle_codes_exist():
    from agentc.model.diagnostics import (
        CODE_INSTALL_MODIFIED,
        CODE_INSTALL_NO_LEDGER,
        CODE_INSTALL_ROOT_REQUIRED,
    )

    assert CODE_INSTALL_ROOT_REQUIRED == "AGC-P5-0006"
    assert CODE_INSTALL_NO_LEDGER == "AGC-P5-0007"
    assert CODE_INSTALL_MODIFIED == "AGC-P5-0008"


def test_ledger_path_outside_dest():
    from agentc.installer.ledger import ledger_path

    root = Path("/tmp/root")
    dest = root / "demo"
    path = ledger_path(root, "cursor", "demo")
    assert path == root / ".agentc" / "installs" / "cursor" / "demo.json"
    assert dest not in path.parents or path != dest / "demo.json"
    assert ".agentc" in path.as_posix()


def test_write_and_read_ledger_roundtrip():
    from agentc.installer.ledger import hash_tree, read_ledger, write_ledger

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dest = root / "demo"
        dest.mkdir()
        target = dest / "a.txt"
        target.write_text("hi\n", encoding="utf-8")
        files = hash_tree(dest)
        expected = hashlib.sha256(b"hi\n").hexdigest()
        assert files["a.txt"] == expected
        path = write_ledger(
            install_root=root,
            host="cursor",
            name="demo",
            dest=dest,
            files=files,
        )
        assert path.is_file()
        payload = read_ledger(path)
        assert payload is not None
        assert payload["host"] == "cursor"
        assert payload["name"] == "demo"
        assert payload["files"]["a.txt"] == expected
        assert not (dest / ".agentc").exists()


def test_uninstall_without_yes_does_not_delete():
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
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0004" in result.stdout + result.stderr
        assert dest.is_dir()


def test_uninstall_yes_removes_registered_tree_and_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        extra = dest / "user.txt"
        extra.write_text("mine\n", encoding="utf-8")
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
        assert extra.is_file()
        assert not (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").exists()
        assert not (dest / "skills" / "hello" / "SKILL.md").exists()


def test_uninstall_without_ledger_is_0007():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = root / "demo-plugin"
        dest.mkdir(parents=True)
        (dest / "keep.txt").write_text("x\n", encoding="utf-8")
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
        assert result.returncode != 0
        assert "AGC-P5-0007" in result.stdout + result.stderr
        assert (dest / "keep.txt").is_file()


def test_uninstall_modified_is_0008():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        skill = dest / "skills" / "hello" / "SKILL.md"
        skill.write_text("# changed\n", encoding="utf-8")
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
        assert result.returncode != 0
        assert "AGC-P5-0008" in result.stdout + result.stderr
        assert skill.read_text(encoding="utf-8") == "# changed\n"


def test_help_without_scripts():
    result = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        capture_output=True,
        text=True,
        env=_env_src_only(),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "uninstall" in result.stdout
    assert "status" in result.stdout
    assert "sync" in result.stdout


def test_status_reports_drift():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = _install_cursor(root)
        (dest / "skills" / "hello" / "SKILL.md").write_text("# changed\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "status", "--kind", "cursor",
             "--name", "demo-plugin", "--root", str(root), "--format", "json"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert any("drift" in d.get("message", "").lower() or d.get("level") == "warning" for d in payload)


def test_sync_updates_unmodified_and_fails_when_modified():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "isolated-root"
        dest = _install_cursor(root)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        ok = subprocess.run(
            [sys.executable, "-m", "agentc", "sync", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert ok.returncode == 0, ok.stdout + ok.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# hello v2\n"
        (dest / "skills" / "hello" / "SKILL.md").write_text("# user\n", encoding="utf-8")
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v3\n"})
        bad = subprocess.run(
            [sys.executable, "-m", "agentc", "sync", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert bad.returncode != 0
        assert "AGC-P5-0008" in bad.stdout + bad.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# user\n"


def test_sync_without_ledger_is_0007():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "sync", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0007" in result.stdout + result.stderr


def test_status_without_ledger_does_not_guess_dest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "isolated-root"
        dest = root / "demo-plugin"
        dest.mkdir(parents=True)
        (dest / ".cursor-plugin").mkdir()
        (dest / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin"}) + "\n", encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "status", "--kind", "cursor",
             "--name", "demo-plugin", "--root", str(root), "--format", "json"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        combined = result.stdout + result.stderr
        assert "AGC-P5-0007" not in combined
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert not any("install ok" in d.get("message", "").lower() for d in payload)


def test_sync_without_yes_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "isolated-root"
        dest = _install_cursor(root)
        before = (dest / "skills" / "hello" / "SKILL.md").read_bytes()
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path, extra_files={"payload/skills/hello/SKILL.md": "# hello v2\n"})
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "sync", "cursor",
             "--from", str(zip_path), "--root", str(root)],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0004" in result.stdout + result.stderr
        assert (dest / "skills" / "hello" / "SKILL.md").read_bytes() == before


def test_sync_creates_new_zip_file_and_deletes_removed_registered():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "isolated-root"
        dest = _install_cursor(root)
        extra = dest / "user.txt"
        extra.write_text("mine\n", encoding="utf-8")
        zip_path = base / "p2.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "payload/.cursor-plugin/plugin.json",
                json.dumps({"name": "demo-plugin", "version": "0.0.1"}) + "\n",
            )
            zf.writestr("payload/new.md", "# added\n")
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "sync", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (dest / "new.md").is_file()
        assert not (dest / "skills" / "hello" / "SKILL.md").exists()
        assert extra.is_file()
        ledger = json.loads(
            (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").read_text(
                encoding="utf-8"
            )
        )
        assert "new.md" in ledger["files"]
        assert "skills/hello/SKILL.md" not in ledger["files"]


def test_sync_skips_untracked_zip_new_and_omits_from_ledger():
    from agentc.installer.ledger import hash_file

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "isolated-root"
        dest = _install_cursor(root)
        extra = dest / "new.md"
        extra_bytes = b"user extra\n"
        extra.write_bytes(extra_bytes)
        zip_path = base / "p2.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "payload/.cursor-plugin/plugin.json",
                json.dumps({"name": "demo-plugin", "version": "0.0.1"}) + "\n",
            )
            zf.writestr("payload/new.md", "# added\n")
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "sync", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert extra.read_bytes() == extra_bytes
        ledger = json.loads(
            (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").read_text(
                encoding="utf-8"
            )
        )
        assert "new.md" not in ledger["files"]
        assert ".cursor-plugin/plugin.json" in ledger["files"]
        for rel, digest in ledger["files"].items():
            assert hash_file(dest / rel) == digest


def test_apply_install_ledger_io_failure_removes_dest():
    from agentc.installer.pipeline import build_install_plan
    from agentc.installer.write import apply_install
    from agentc.model.diagnostics import CODE_INSTALL_IO

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        staging = base / "staging"
        staging.mkdir()
        root = base / "isolated-root"
        blocker = root / ".agentc" / "installs" / "cursor"
        blocker.parent.mkdir(parents=True)
        blocker.write_text("not-a-dir\n", encoding="utf-8")
        plan, diags = build_install_plan(
            zip_path, root, staging=staging, host="cursor"
        )
        assert plan is not None
        diagnostics, _text, wrote = apply_install(plan, diags, "")
        assert wrote is False
        assert any(d.code == CODE_INSTALL_IO for d in diagnostics)
        assert not plan.dest.exists()
        assert not (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").exists()


def test_uninstall_yes_removes_empty_dest():
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
        assert not dest.exists()
        assert not (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").exists()


def main() -> int:
    tests = [
        test_p5_lifecycle_codes_exist,
        test_ledger_path_outside_dest,
        test_write_and_read_ledger_roundtrip,
        test_uninstall_without_yes_does_not_delete,
        test_uninstall_yes_removes_registered_tree_and_ledger,
        test_uninstall_without_ledger_is_0007,
        test_uninstall_modified_is_0008,
        test_uninstall_yes_removes_empty_dest,
        test_help_without_scripts,
        test_status_reports_drift,
        test_sync_updates_unmodified_and_fails_when_modified,
        test_sync_without_ledger_is_0007,
        test_status_without_ledger_does_not_guess_dest,
        test_sync_without_yes_does_not_write,
        test_sync_creates_new_zip_file_and_deletes_removed_registered,
        test_sync_skips_untracked_zip_new_and_omits_from_ledger,
        test_apply_install_ledger_io_failure_removes_dest,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
