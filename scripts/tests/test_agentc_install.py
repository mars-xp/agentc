#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

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


def test_p5_codes_exist():
    from agentc.model.diagnostics import (
        CODE_INSTALL_CONFLICT,
        CODE_INSTALL_IO,
        CODE_INSTALL_UNCONFIRMED,
        CODE_INSTALL_UNREADABLE,
        CODE_NOT_CURSOR_ZIP,
    )

    assert CODE_NOT_CURSOR_ZIP == "AGC-P5-0001"
    assert CODE_INSTALL_UNREADABLE == "AGC-P5-0002"
    assert CODE_INSTALL_CONFLICT == "AGC-P5-0003"
    assert CODE_INSTALL_UNCONFIRMED == "AGC-P5-0004"
    assert CODE_INSTALL_IO == "AGC-P5-0005"


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


def test_plan_missing_dest_lists_create():
    from agentc.installer.pipeline import build_install_plan
    from agentc.installer.render import render_install_text

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        staging = base / "staging"
        staging.mkdir()
        root = base / "root"
        plan, diags = build_install_plan(zip_path, root, staging=staging)
        assert plan is not None
        assert not any(d.level == "error" for d in diags)
        assert plan.plugin_name == "demo-plugin"
        assert plan.dest == root / "demo-plugin"
        assert "skills/hello/SKILL.md" in plan.create
        assert plan.conflicts == ()
        text = render_install_text(plan)
        assert text.startswith("# agentc install\n")
        assert "create:" in text


def test_plan_rejects_zip_slip():
    from agentc.installer.pipeline import build_install_plan
    from agentc.model.diagnostics import CODE_INSTALL_UNREADABLE

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../escape.txt", "nope")
        staging = base / "staging"
        staging.mkdir()
        plan, diags = build_install_plan(zip_path, base / "root", staging=staging)
        assert plan is None
        assert any(d.code == CODE_INSTALL_UNREADABLE and d.level == "error" for d in diags)


def _env_src_only() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _env_src_scripts() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def test_help_without_scripts_mentions_install():
    result = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        capture_output=True,
        text=True,
        env=_env_src_only(),
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "install" in result.stdout


def test_yes_writes_when_dest_missing():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        dest = root / "demo-plugin"
        result = subprocess.run(
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
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (dest / ".cursor-plugin" / "plugin.json").is_file()
        assert (dest / "skills" / "hello" / "SKILL.md").is_file()
        assert result.stdout.startswith("# agentc install")


def test_yes_idempotent_when_identical():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        first = subprocess.run(
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
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert first.returncode == 0
        dest = root / "demo-plugin"
        skill = dest / "skills" / "hello" / "SKILL.md"
        before = skill.read_bytes()
        mtime = skill.stat().st_mtime_ns
        second = subprocess.run(
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
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert skill.read_bytes() == before
        assert skill.stat().st_mtime_ns == mtime


def test_yes_conflict_does_not_touch_dest():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        dest = root / "demo-plugin"
        dest.mkdir(parents=True)
        (dest / "local.txt").write_text("user\n", encoding="utf-8")
        result = subprocess.run(
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
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P5-0003" for d in payload)
        assert (dest / "local.txt").read_text(encoding="utf-8") == "user\n"
        assert not (dest / "skills").exists()


def test_install_without_yes_does_not_create_dest():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        dest = root / "demo-plugin"
        result = subprocess.run(
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
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert any(d["code"] == "AGC-P5-0004" for d in payload)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}
        assert not dest.exists()
        assert not root.exists() or not dest.exists()


def test_yes_content_mismatch_is_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        dest = root / "demo-plugin"
        (dest / ".cursor-plugin").mkdir(parents=True)
        (dest / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "0.0.1"}) + "\n",
            encoding="utf-8",
        )
        (dest / "skills" / "hello").mkdir(parents=True)
        (dest / "skills" / "hello" / "SKILL.md").write_text("# changed\n", encoding="utf-8")
        result = subprocess.run(
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
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P5-0003" for d in payload)
        assert (dest / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8") == "# changed\n"


def test_not_zip_is_0001_or_0002():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        bogus = base / "not.zip"
        bogus.write_text("hello\n", encoding="utf-8")
        root = base / "isolated-root"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "install",
                "cursor",
                "--from",
                str(bogus),
                "--root",
                str(root),
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        codes = {d["code"] for d in payload}
        assert codes & {"AGC-P5-0001", "AGC-P5-0002"}
        assert not (root / "demo-plugin").exists()


def test_plan_dest_exists_lists_zip_only_as_conflicts():
    from agentc.installer.pipeline import build_install_plan

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        staging = base / "staging"
        staging.mkdir()
        root = base / "root"
        dest = root / "demo-plugin"
        (dest / ".cursor-plugin").mkdir(parents=True)
        (dest / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "0.0.1"}) + "\n",
            encoding="utf-8",
        )
        plan, diags = build_install_plan(zip_path, root, staging=staging)
        assert plan is not None
        assert not any(d.level == "error" for d in diags)
        assert "skills/hello/SKILL.md" in plan.conflicts
        assert "skills/hello/SKILL.md" not in plan.create
        assert plan.create == ()


def test_plan_rejects_absolute_plugin_name():
    from agentc.installer.pipeline import build_install_plan
    from agentc.model.diagnostics import CODE_NOT_CURSOR_ZIP

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        escaped_name = f"/tmp/agentc-pwned-{os.getpid()}"
        _write_cursor_zip(zip_path, plugin_name=escaped_name)
        staging = base / "staging"
        staging.mkdir()
        root = base / "isolated-root"
        plan, diags = build_install_plan(zip_path, root, staging=staging)
        assert plan is None
        assert any(d.code == CODE_NOT_CURSOR_ZIP and d.level == "error" for d in diags)
        assert not (Path(escaped_name)).exists()


def test_plan_rejects_parent_traversal_plugin_name():
    from agentc.installer.pipeline import build_install_plan
    from agentc.model.diagnostics import CODE_NOT_CURSOR_ZIP

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path, plugin_name="../evil")
        staging = base / "staging"
        staging.mkdir()
        root = base / "isolated-root"
        plan, diags = build_install_plan(zip_path, root, staging=staging)
        assert plan is None
        assert any(d.code == CODE_NOT_CURSOR_ZIP and d.level == "error" for d in diags)


def test_yes_rejects_absolute_plugin_name_without_writes():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        escaped_name = f"/tmp/agentc-pwned-cli-{os.getpid()}"
        _write_cursor_zip(zip_path, plugin_name=escaped_name)
        root = base / "isolated-root"
        escaped_dest = Path(escaped_name)
        result = subprocess.run(
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
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P5-0001" for d in payload)
        assert not escaped_dest.exists()
        assert not root.exists() or not any(root.iterdir())


def test_yes_rejects_parent_traversal_plugin_name_without_writes():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path, plugin_name="../evil")
        root = base / "isolated-root"
        parent = root.parent
        evil_dest = parent / "evil"
        result = subprocess.run(
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
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P5-0001" for d in payload)
        assert not evil_dest.exists()
        assert not root.exists() or not any(root.iterdir())


def test_yes_missing_files_in_existing_dest_is_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        dest = root / "demo-plugin"
        (dest / ".cursor-plugin").mkdir(parents=True)
        manifest = dest / ".cursor-plugin" / "plugin.json"
        manifest.write_text(
            json.dumps({"name": "demo-plugin", "version": "0.0.1"}) + "\n",
            encoding="utf-8",
        )
        before_manifest = manifest.read_bytes()
        skill = dest / "skills" / "hello" / "SKILL.md"
        result = subprocess.run(
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
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P5-0003" for d in payload)
        assert manifest.read_bytes() == before_manifest
        assert not skill.exists()


def test_yes_rejects_dest_symlink_escape():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        dest = root / "demo-plugin"
        dest.mkdir(parents=True)
        outside = base / "outside-secret.txt"
        outside.write_text("secret\n", encoding="utf-8")
        (dest / "escape-link").symlink_to(outside)
        result = subprocess.run(
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
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=_env_src_scripts(),
            cwd=ROOT,
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P5-0002" for d in payload)
        assert outside.read_text(encoding="utf-8") == "secret\n"
        assert (dest / "escape-link").is_symlink()
        assert not (dest / "skills").exists()


def test_plan_not_cursor_zip():
    from agentc.installer.pipeline import build_install_plan
    from agentc.model.diagnostics import CODE_NOT_CURSOR_ZIP

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a.txt", "x")
            zf.writestr("b.txt", "y")
        staging = base / "staging"
        staging.mkdir()
        plan, diags = build_install_plan(zip_path, base / "root", staging=staging)
        assert plan is None
        assert any(d.code == CODE_NOT_CURSOR_ZIP for d in diags)


def test_yes_new_install_writes_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        ledger = root / ".agentc" / "installs" / "cursor" / "demo-plugin.json"
        assert ledger.is_file()
        payload = json.loads(ledger.read_text(encoding="utf-8"))
        assert payload["name"] == "demo-plugin"
        assert "skills/hello/SKILL.md" in payload["files"]


def test_identical_without_ledger_does_not_backfill():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        dest = root / "demo-plugin"
        dest.mkdir(parents=True)
        (dest / ".cursor-plugin").mkdir()
        (dest / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "0.0.1"}) + "\n",
            encoding="utf-8",
        )
        (dest / "skills" / "hello").mkdir(parents=True)
        (dest / "skills" / "hello" / "SKILL.md").write_text("# hello\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").exists()


def test_no_yes_writes_neither_dest_nor_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "cursor",
             "--from", str(zip_path), "--root", str(root)],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0004" in result.stdout + result.stderr
        assert not (root / "demo-plugin").exists()
        assert not (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").exists()


def main() -> int:
    tests = [
        test_p5_codes_exist,
        test_plan_missing_dest_lists_create,
        test_plan_rejects_zip_slip,
        test_plan_dest_exists_lists_zip_only_as_conflicts,
        test_plan_rejects_absolute_plugin_name,
        test_plan_rejects_parent_traversal_plugin_name,
        test_plan_not_cursor_zip,
        test_help_without_scripts_mentions_install,
        test_install_without_yes_does_not_create_dest,
        test_yes_writes_when_dest_missing,
        test_yes_idempotent_when_identical,
        test_yes_conflict_does_not_touch_dest,
        test_yes_content_mismatch_is_conflict,
        test_yes_rejects_absolute_plugin_name_without_writes,
        test_yes_rejects_parent_traversal_plugin_name_without_writes,
        test_yes_missing_files_in_existing_dest_is_conflict,
        test_yes_rejects_dest_symlink_escape,
        test_not_zip_is_0001_or_0002,
        test_yes_new_install_writes_ledger,
        test_identical_without_ledger_does_not_backfill,
        test_no_yes_writes_neither_dest_nor_ledger,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
