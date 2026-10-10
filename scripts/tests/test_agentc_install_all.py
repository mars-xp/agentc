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


def _env_src_scripts() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def _write_cursor_zip(zip_path: Path, *, plugin_name: str = "demo-plugin") -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "payload/.cursor-plugin/plugin.json",
            json.dumps({"name": plugin_name, "version": "0.0.1"}) + "\n",
        )
        zf.writestr("payload/skills/hello/SKILL.md", "# hello\n")


def _write_host_zip(
    zip_path: Path,
    host: str,
    plugin_name: str = "demo-plugin",
    *,
    skill_body: str = "# hello\n",
) -> None:
    rel = {
        "codex": "payload/.codex-plugin/plugin.json",
        "claude-code": "payload/.claude-plugin/plugin.json",
    }[host]
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(rel, json.dumps({"name": plugin_name}) + "\n")
        zf.writestr("payload/skills/hello/SKILL.md", skill_body)


def _write_three(base: Path) -> tuple[Path, Path, Path]:
    cursor_zip = base / "cursor.zip"
    codex_zip = base / "codex.zip"
    claude_zip = base / "claude.zip"
    _write_cursor_zip(cursor_zip)
    _write_host_zip(codex_zip, "codex")
    _write_host_zip(claude_zip, "claude-code")
    return cursor_zip, codex_zip, claude_zip


def test_p5_install_all_codes_exist():
    from agentc.model.diagnostics import (
        CODE_AUDIT_FLAG,
        CODE_INSTALL_NO_BACKUP,
        CODE_INSTALL_TXN_INCOMPLETE,
        CODE_INSTALL_TXN_ROLLED_BACK,
    )

    assert CODE_INSTALL_TXN_ROLLED_BACK == "AGC-P5-0016"
    assert CODE_INSTALL_TXN_INCOMPLETE == "AGC-P5-0017"
    assert CODE_AUDIT_FLAG == "AGC-P5-0009"
    assert CODE_INSTALL_NO_BACKUP == "AGC-P5-0014"


def test_install_all_missing_codex_root_is_0006():
    from agentc.installer.run import install_all
    from agentc.model.diagnostics import CODE_INSTALL_ROOT_REQUIRED

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        claude_root = base / "claude-root"
        diags, _text, dests = install_all(
            cursor_zip=cursor_zip,
            codex_zip=codex_zip,
            claude_zip=claude_zip,
            cursor_root=cursor_root,
            codex_root=None,
            claude_root=claude_root,
            yes=True,
            partial_ok=False,
        )
        assert any(d.code == CODE_INSTALL_ROOT_REQUIRED for d in diags)
        assert dests == ()
        assert not (cursor_root / "demo-plugin").exists()
        assert not (claude_root / "demo-plugin").exists()


def test_install_all_without_yes_does_not_write():
    from agentc.installer.run import install_all
    from agentc.model.diagnostics import CODE_INSTALL_UNCONFIRMED

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        diags, text, dests = install_all(
            cursor_zip=cursor_zip,
            codex_zip=codex_zip,
            claude_zip=claude_zip,
            cursor_root=cursor_root,
            codex_root=codex_root,
            claude_root=claude_root,
            yes=False,
            partial_ok=False,
        )
        unconfirmed = [d for d in diags if d.code == CODE_INSTALL_UNCONFIRMED]
        assert len(unconfirmed) == 3
        assert dests == ()
        assert not (cursor_root / "demo-plugin").exists()
        assert not (codex_root / "demo-plugin").exists()
        assert not (claude_root / "demo-plugin").exists()
        assert "agentc install" in text


def test_install_all_yes_writes_three_ledgers():
    from agentc.installer.run import install_all

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        diags, _text, dests = install_all(
            cursor_zip=cursor_zip,
            codex_zip=codex_zip,
            claude_zip=claude_zip,
            cursor_root=cursor_root,
            codex_root=codex_root,
            claude_root=claude_root,
            yes=True,
            partial_ok=False,
        )
        assert not any(d.level == "error" for d in diags), diags
        assert (cursor_root / "demo-plugin").is_dir()
        assert (codex_root / "demo-plugin").is_dir()
        assert (claude_root / "demo-plugin").is_dir()
        assert (cursor_root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").is_file()
        assert (codex_root / ".agentc" / "installs" / "codex" / "demo-plugin.json").is_file()
        assert (claude_root / ".agentc" / "installs" / "claude-code" / "demo-plugin.json").is_file()
        assert len(dests) == 3


def test_install_all_second_conflict_rolls_back_first():
    from agentc.installer.run import install_all, install_zip
    from agentc.model.diagnostics import CODE_INSTALL_CONFLICT, CODE_INSTALL_TXN_ROLLED_BACK

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        other = base / "other-codex.zip"
        _write_host_zip(other, "codex", plugin_name="demo-plugin", skill_body="# other\n")
        install_zip(other, codex_root, host="codex", yes=True)
        diags, _text, dests = install_all(
            cursor_zip=cursor_zip,
            codex_zip=codex_zip,
            claude_zip=claude_zip,
            cursor_root=cursor_root,
            codex_root=codex_root,
            claude_root=claude_root,
            yes=True,
            partial_ok=False,
        )
        assert any(d.code == CODE_INSTALL_CONFLICT for d in diags)
        assert any(d.code == CODE_INSTALL_TXN_ROLLED_BACK for d in diags)
        assert not (cursor_root / "demo-plugin").exists()
        assert not (cursor_root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").exists()
        assert (codex_root / "demo-plugin").is_dir()
        assert not (claude_root / "demo-plugin").exists()
        assert dests == ()


def test_install_all_partial_ok_keeps_first():
    from agentc.installer.run import install_all, install_zip
    from agentc.model.diagnostics import CODE_INSTALL_CONFLICT, CODE_INSTALL_TXN_ROLLED_BACK

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        other = base / "other-codex.zip"
        _write_host_zip(other, "codex", plugin_name="demo-plugin", skill_body="# other\n")
        install_zip(other, codex_root, host="codex", yes=True)
        diags, _text, dests = install_all(
            cursor_zip=cursor_zip,
            codex_zip=codex_zip,
            claude_zip=claude_zip,
            cursor_root=cursor_root,
            codex_root=codex_root,
            claude_root=claude_root,
            yes=True,
            partial_ok=True,
        )
        assert any(d.code == CODE_INSTALL_CONFLICT for d in diags)
        assert not any(d.code == CODE_INSTALL_TXN_ROLLED_BACK for d in diags)
        assert not any(d.code == "AGC-P5-0017" for d in diags)
        assert (cursor_root / "demo-plugin").is_dir()
        assert (cursor_root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").is_file()
        assert not (claude_root / "demo-plugin").exists()


def test_install_all_identical_first_is_not_rolled_back():
    from agentc.installer.run import install_all, install_zip
    from agentc.model.diagnostics import CODE_INSTALL_CONFLICT, CODE_INSTALL_TXN_ROLLED_BACK

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        first, _text, dest, wrote = install_zip(
            cursor_zip, cursor_root, host="cursor", yes=True
        )
        assert wrote is True
        assert dest is not None
        other = base / "other-codex.zip"
        _write_host_zip(other, "codex", plugin_name="demo-plugin", skill_body="# other\n")
        install_zip(other, codex_root, host="codex", yes=True)
        diags, _text, dests = install_all(
            cursor_zip=cursor_zip,
            codex_zip=codex_zip,
            claude_zip=claude_zip,
            cursor_root=cursor_root,
            codex_root=codex_root,
            claude_root=claude_root,
            yes=True,
            partial_ok=False,
        )
        assert any(d.code == CODE_INSTALL_CONFLICT for d in diags)
        assert not any(d.code == CODE_INSTALL_TXN_ROLLED_BACK for d in diags)
        assert dest.is_dir()
        assert (cursor_root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").is_file()
        assert dests == ()


def test_install_all_partial_ok_does_not_continue_after_failure():
    from agentc.installer.run import install_all, install_zip
    from agentc.model.diagnostics import CODE_INSTALL_CONFLICT, CODE_INSTALL_UNREADABLE

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        claude_zip.write_bytes(b"not-a-zip")
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        other = base / "other-codex.zip"
        _write_host_zip(other, "codex", plugin_name="demo-plugin", skill_body="# other\n")
        install_zip(other, codex_root, host="codex", yes=True)
        diags, _text, _dests = install_all(
            cursor_zip=cursor_zip,
            codex_zip=codex_zip,
            claude_zip=claude_zip,
            cursor_root=cursor_root,
            codex_root=codex_root,
            claude_root=claude_root,
            yes=True,
            partial_ok=True,
        )
        assert any(d.code == CODE_INSTALL_CONFLICT for d in diags)
        assert not any(d.code == CODE_INSTALL_UNREADABLE for d in diags)
        assert (cursor_root / "demo-plugin").is_dir()
        assert not (claude_root / "demo-plugin").exists()


def test_cli_install_all_without_yes_is_0004():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        result = subprocess.run(
            [
                sys.executable, "-m", "agentc", "install", "all",
                "--cursor", str(cursor_zip),
                "--codex", str(codex_zip),
                "--claude-code", str(claude_zip),
                "--cursor-root", str(cursor_root),
                "--codex-root", str(codex_root),
                "--claude-root", str(claude_root),
                "--format", "json",
            ],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert {row["code"] for row in payload} >= {"AGC-P5-0004"}
        for row in payload:
            assert set(row) == {"code", "level", "message", "path", "hint"}
        assert not (cursor_root / "demo-plugin").exists()


def test_cli_install_all_missing_zip_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, _codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        result = subprocess.run(
            [
                sys.executable, "-m", "agentc", "install", "all",
                "--cursor", str(cursor_zip),
                "--claude-code", str(claude_zip),
                "--cursor-root", str(cursor_root),
                "--codex-root", str(codex_root),
                "--claude-root", str(claude_root),
                "--yes",
            ],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        assert not (cursor_root / "demo-plugin").exists()
        assert not (codex_root / "demo-plugin").exists()
        assert not (claude_root / "demo-plugin").exists()


def test_cli_help_lists_install_all():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    top = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert top.returncode == 0, top.stderr
    nested = subprocess.run(
        [sys.executable, "-m", "agentc", "install", "--help"],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert nested.returncode == 0, nested.stderr
    assert "all" in nested.stdout


def test_cli_install_all_protocol_unconfirmed_summary():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        result = subprocess.run(
            [
                sys.executable, "-m", "agentc", "install", "all",
                "--cursor", str(cursor_zip),
                "--codex", str(codex_zip),
                "--claude-code", str(claude_zip),
                "--cursor-root", str(cursor_root),
                "--codex-root", str(codex_root),
                "--claude-root", str(claude_root),
                "--format", "protocol",
            ],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        obj = json.loads(result.stdout)
        assert set(obj) == {
            "protocolVersion",
            "status",
            "summary",
            "diagnostics",
            "artifacts",
            "confirmation",
        }
        assert obj["protocolVersion"] == "agentc-protocol/v1"
        assert obj["status"] == "unconfirmed"
        assert obj["summary"] == "install failed"
        assert obj["confirmation"]["required"] is True
        assert obj["confirmation"]["flag"] == "--yes"
        assert isinstance(obj["diagnostics"], list)
        for row in obj["diagnostics"]:
            assert set(row) == {"code", "level", "message", "path", "hint"}
        assert not (cursor_root / "demo-plugin").exists()


def test_cli_install_all_protocol_success_summary():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip, codex_zip, claude_zip = _write_three(base)
        cursor_root = base / "cursor-root"
        codex_root = base / "codex-root"
        claude_root = base / "claude-root"
        result = subprocess.run(
            [
                sys.executable, "-m", "agentc", "install", "all",
                "--cursor", str(cursor_zip),
                "--codex", str(codex_zip),
                "--claude-code", str(claude_zip),
                "--cursor-root", str(cursor_root),
                "--codex-root", str(codex_root),
                "--claude-root", str(claude_root),
                "--yes",
                "--format", "protocol",
            ],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        obj = json.loads(result.stdout)
        assert set(obj) == {
            "protocolVersion",
            "status",
            "summary",
            "diagnostics",
            "artifacts",
            "confirmation",
        }
        assert obj["summary"] == "install ok"
        assert obj["status"] == "ok"
        assert obj["confirmation"]["required"] is False
        assert (cursor_root / "demo-plugin").is_dir()
        assert (codex_root / "demo-plugin").is_dir()
        assert (claude_root / "demo-plugin").is_dir()


def test_cli_single_host_install_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "cursor.zip"
        _write_cursor_zip(zip_path)
        root = base / "isolated-root"
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "cursor",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "demo-plugin").is_dir()
        assert (root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").is_file()


def test_readme_documents_install_all_codes():
    text = (ROOT / "packaging" / "agentc" / "README.md").read_text(encoding="utf-8")
    assert "install all" in text
    assert "AGC-P5-0016" in text
    assert "AGC-P5-0017" in text
    assert "--partial-ok" in text


def test_adr_0013_copies_match():
    packaged = (
        ROOT
        / "packaging"
        / "agentc"
        / "docs"
        / "adr"
        / "0013-install-all-composes-facade.md"
    )
    packaged_text = packaged.read_text(encoding="utf-8")
    spec = (
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-09-09-open-source-cross-platform-agent-compiler"
        / "adr"
        / "0013-install-all-composes-facade.md"
    )
    if spec.is_file():
        assert spec.read_text(encoding="utf-8") == packaged_text
    else:
        assert "install all" in packaged_text.lower() or "InstallAll" in packaged_text


def test_rollback_modified_is_0017():
    from agentc.installer.run import _rollback_written, install_zip
    from agentc.model.diagnostics import CODE_INSTALL_MODIFIED, CODE_INSTALL_TXN_INCOMPLETE

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cursor_zip = base / "cursor.zip"
        _write_cursor_zip(cursor_zip)
        cursor_root = base / "cursor-root"
        diags, _text, dest, wrote = install_zip(cursor_zip, cursor_root, host="cursor", yes=True)
        assert wrote is True
        assert dest is not None
        (dest / "skills" / "hello" / "SKILL.md").write_text("tamper\n", encoding="utf-8")
        out = _rollback_written([("cursor", dest.name, cursor_root)], list(diags))
        assert any(d.code == CODE_INSTALL_MODIFIED for d in out)
        assert any(d.code == CODE_INSTALL_TXN_INCOMPLETE and "cursor" in d.message for d in out)
        assert dest.is_dir()
        assert (cursor_root / ".agentc" / "installs" / "cursor" / "demo-plugin.json").is_file()


def main() -> int:
    tests = [
        test_p5_install_all_codes_exist,
        test_install_all_missing_codex_root_is_0006,
        test_install_all_without_yes_does_not_write,
        test_install_all_yes_writes_three_ledgers,
        test_install_all_second_conflict_rolls_back_first,
        test_install_all_partial_ok_keeps_first,
        test_install_all_identical_first_is_not_rolled_back,
        test_install_all_partial_ok_does_not_continue_after_failure,
        test_readme_documents_install_all_codes,
        test_adr_0013_copies_match,
        test_rollback_modified_is_0017,
        test_cli_install_all_without_yes_is_0004,
        test_cli_install_all_missing_zip_does_not_write,
        test_cli_help_lists_install_all,
        test_cli_install_all_protocol_unconfirmed_summary,
        test_cli_install_all_protocol_success_summary,
        test_cli_single_host_install_unchanged,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
