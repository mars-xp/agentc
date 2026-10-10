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


def _write_host_zip(zip_path: Path, host: str, plugin_name: str = "demo-plugin") -> None:
    rel = {
        "codex": "payload/.codex-plugin/plugin.json",
        "claude-code": "payload/.claude-plugin/plugin.json",
    }[host]
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(rel, json.dumps({"name": plugin_name}) + "\n")
        zf.writestr("payload/skills/hello/SKILL.md", "# hello\n")


def test_codex_install_without_root_is_0006():
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "p.zip"
        _write_host_zip(zip_path, "codex")
        home = Path.home()
        before_codex = (home / ".codex").exists()
        before_claude = (home / ".claude").exists()
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "codex", "--from", str(zip_path), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0006" in result.stdout + result.stderr
        assert not (Path(tmp) / "demo-plugin").exists()
        assert not list(Path(tmp).glob(".agentc/installs/**/*.json"))
        assert (home / ".codex").exists() == before_codex
        assert (home / ".claude").exists() == before_claude


def test_codex_install_with_root_writes_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_host_zip(zip_path, "codex")
        root = base / "isolated-root"
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "codex",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "demo-plugin" / ".codex-plugin" / "plugin.json").is_file()
        assert (root / ".agentc" / "installs" / "codex" / "demo-plugin.json").is_file()


def test_claude_code_install_without_root_is_0006():
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "p.zip"
        _write_host_zip(zip_path, "claude-code")
        home = Path.home()
        before_codex = (home / ".codex").exists()
        before_claude = (home / ".claude").exists()
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "claude-code",
             "--from", str(zip_path), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode != 0
        assert "AGC-P5-0006" in result.stdout + result.stderr
        assert not (Path(tmp) / "demo-plugin").exists()
        assert not list(Path(tmp).glob(".agentc/installs/**/*.json"))
        assert (home / ".codex").exists() == before_codex
        assert (home / ".claude").exists() == before_claude


def test_claude_code_install_with_root_writes_ledger():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        zip_path = base / "p.zip"
        _write_host_zip(zip_path, "claude-code")
        root = base / "isolated-root"
        result = subprocess.run(
            [sys.executable, "-m", "agentc", "install", "claude-code",
             "--from", str(zip_path), "--root", str(root), "--yes"],
            capture_output=True, text=True, env=_env_src_scripts(), cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "demo-plugin" / ".claude-plugin" / "plugin.json").is_file()
        assert (root / ".agentc" / "installs" / "claude-code" / "demo-plugin.json").is_file()


def main() -> int:
    tests = [
        test_codex_install_without_root_is_0006,
        test_codex_install_with_root_writes_ledger,
        test_claude_code_install_without_root_is_0006,
        test_claude_code_install_with_root_writes_ledger,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
