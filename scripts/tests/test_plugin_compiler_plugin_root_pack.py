#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP scripts/server.py closure + official PLUGIN_ROOT args (Task 3)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

INTERFACE = {
    "displayName": "Demo",
    "shortDescription": "short",
    "longDescription": "long",
    "developerName": "demo",
    "category": "Productivity",
    "capabilities": ["Read"],
    "defaultPrompt": ["hello"],
}

_SERVER_PY = """\
import os
import sys
from pathlib import Path

root = os.environ.get("PLUGIN_ROOT") or os.environ.get("CURSOR_PLUGIN_ROOT")
if not root:
    sys.exit(1)
path = Path(root) / "scripts" / "server.py"
if not path.is_file():
    sys.exit(2)
sys.exit(0)
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _codex_manifest(plugin: Path, payload: dict) -> None:
    body = {
        "name": payload.get("name", "demo"),
        "version": "0.0.1",
        "description": "demo",
        "author": {"name": "demo"},
        "interface": INTERFACE,
    }
    body.update(payload)
    _write(plugin / ".codex-plugin" / "plugin.json", json.dumps(body, indent=2) + "\n")


def _build_codex_mcp_plugin(repo: Path) -> Path:
    plugin = repo / "plugins" / "mcp-server-pack"
    _codex_manifest(plugin, {"name": "mcp-server-pack"})
    _write(
        plugin / "skills" / "demo" / "SKILL.md",
        "---\nname: demo\ndescription: demo skill\n---\n",
    )
    _write(plugin / "scripts" / "server.py", _SERVER_PY)
    _write(
        plugin / ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "command": "python3",
                        "args": ["${PLUGIN_ROOT}/scripts/server.py"],
                    }
                }
            }
        )
        + "\n",
    )
    return plugin


class _StructureRunner:
    structure_only = True

    def __call__(self, command):
        from plugin_compiler.validation import CommandResult

        return CommandResult(0, "Plugin validation passed: mcp-server-pack\n", "")


def _copy_staged(staged: Path) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="staged-copy-"))
    shutil.copytree(staged, dest, dirs_exist_ok=True)
    return dest


def _launch_from_outside(server: Path, env: dict[str, str]) -> int:
    merged = {"PATH": os.environ.get("PATH", "")}
    merged.update(env)
    result = subprocess.run(
        ["python3", str(server)],
        cwd=str(Path(tempfile.gettempdir())),
        env=merged,
        check=False,
    )
    return result.returncode


def test_mcp_server_script_packed_and_codex_args_keep_plugin_root():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = _build_codex_mcp_plugin(repo)
        result = compile_plugin(
            plugin,
            repo,
            ("codex", "cursor"),
            runner=_StructureRunner(),
            source_format="codex",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }

        codex_staged = Path(result.staged_roots["codex"])
        cursor_staged = Path(result.staged_roots["cursor"])

        assert (codex_staged / "scripts" / "server.py").is_file()
        assert (cursor_staged / "scripts" / "server.py").is_file()

        codex_mcp_text = (codex_staged / ".mcp.json").read_text(encoding="utf-8")
        assert "CODEX_PLUGIN_ROOT" not in codex_mcp_text
        codex_mcp = json.loads(codex_mcp_text)
        assert codex_mcp["mcpServers"]["demo"]["args"] == [
            "${PLUGIN_ROOT}/scripts/server.py"
        ]

        cursor_mcp_text = (cursor_staged / "mcp.json").read_text(encoding="utf-8")
        assert "CODEX_PLUGIN_ROOT" not in cursor_mcp_text
        cursor_mcp = json.loads(cursor_mcp_text)
        assert cursor_mcp["mcpServers"]["demo"]["args"] == [
            "${CURSOR_PLUGIN_ROOT}/scripts/server.py"
        ]

        codex_copy = _copy_staged(codex_staged)
        assert (
            _launch_from_outside(
                codex_copy / "scripts" / "server.py",
                {"PLUGIN_ROOT": str(codex_copy)},
            )
            == 0
        )

        cursor_copy = _copy_staged(cursor_staged)
        assert (
            _launch_from_outside(
                cursor_copy / "scripts" / "server.py",
                {"CURSOR_PLUGIN_ROOT": str(cursor_copy)},
            )
            == 0
        )


def main() -> int:
    failures = []
    for key, value in sorted(globals().items()):
        if key.startswith("test_") and callable(value):
            try:
                value()
                print("PASS", key)
            except Exception as exc:
                failures.append((key, exc))
                print("FAIL", key, type(exc).__name__, exc)
    print("failures:", len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
