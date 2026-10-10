#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.model import CapabilityKind, CapabilityRef, Target
from plugin_compiler.scanner import _ref, scan_plugin


def _write_plugin(root: Path) -> None:
    files = {
        ".cursor-plugin/plugin.json": "{}",
        ".codex-plugin/plugin.json": "{}",
        ".claude-plugin/plugin.json": "{}",
        "skills/demo/SKILL.md": "# Demo",
        "rules/always.mdc": "",
        "commands/deploy.md": "",
        "agents/reviewer.md": "",
        "workflows/release.py": "",
        "hooks/hooks.json": "{}",
        "mcp.json": "{}",
        ".mcp.json": "{}",
        ".app.json": "{}",
        ".lsp.json": "{}",
        "AGENTS.md": "",
        "CLAUDE.md": "",
        "output-styles/concise.md": "",
        "themes/dark.json": "{}",
        "monitors/monitors.json": "{}",
        "build/outputs/junk.txt": "junk",
        "packaging/private.json": "{}",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _expected_refs() -> tuple[CapabilityRef, ...]:
    return (
        CapabilityRef(
            capability_id="agent:agents/reviewer.md",
            kind=CapabilityKind.AGENT,
            source_path="agents/reviewer.md",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="command:commands/deploy.md",
            kind=CapabilityKind.COMMAND,
            source_path="commands/deploy.md",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="connector:.app.json",
            kind=CapabilityKind.CONNECTOR,
            source_path=".app.json",
            source_dialect="codex",
        ),
        CapabilityRef(
            capability_id="hook:hooks/hooks.json",
            kind=CapabilityKind.HOOK,
            source_path="hooks/hooks.json",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="lsp:.lsp.json",
            kind=CapabilityKind.LSP,
            source_path=".lsp.json",
            source_dialect="claude-code",
        ),
        CapabilityRef(
            capability_id="mcp:.mcp.json",
            kind=CapabilityKind.MCP,
            source_path=".mcp.json",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="mcp:mcp.json",
            kind=CapabilityKind.MCP,
            source_path="mcp.json",
            source_dialect="cursor",
        ),
        CapabilityRef(
            capability_id="monitor:monitors/monitors.json",
            kind=CapabilityKind.MONITOR,
            source_path="monitors/monitors.json",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="output-style:output-styles/concise.md",
            kind=CapabilityKind.OUTPUT_STYLE,
            source_path="output-styles/concise.md",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="plugin-metadata:.claude-plugin/plugin.json",
            kind=CapabilityKind.PLUGIN_METADATA,
            source_path=".claude-plugin/plugin.json",
            source_dialect="claude-code",
        ),
        CapabilityRef(
            capability_id="plugin-metadata:.codex-plugin/plugin.json",
            kind=CapabilityKind.PLUGIN_METADATA,
            source_path=".codex-plugin/plugin.json",
            source_dialect="codex",
        ),
        CapabilityRef(
            capability_id="plugin-metadata:.cursor-plugin/plugin.json",
            kind=CapabilityKind.PLUGIN_METADATA,
            source_path=".cursor-plugin/plugin.json",
            source_dialect="cursor",
        ),
        CapabilityRef(
            capability_id="rule:AGENTS.md",
            kind=CapabilityKind.RULE,
            source_path="AGENTS.md",
            source_dialect="codex",
        ),
        CapabilityRef(
            capability_id="rule:CLAUDE.md",
            kind=CapabilityKind.RULE,
            source_path="CLAUDE.md",
            source_dialect="claude-code",
        ),
        CapabilityRef(
            capability_id="rule:rules/always.mdc",
            kind=CapabilityKind.RULE,
            source_path="rules/always.mdc",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="skill:skills/demo",
            kind=CapabilityKind.SKILL,
            source_path="skills/demo",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="theme:themes/dark.json",
            kind=CapabilityKind.THEME,
            source_path="themes/dark.json",
            source_dialect=None,
        ),
        CapabilityRef(
            capability_id="workflow:workflows/release.py",
            kind=CapabilityKind.WORKFLOW,
            source_path="workflows/release.py",
            source_dialect=None,
        ),
    )


def test_old_build_subdirectory_assertion_is_blind_to_root_relative_build_paths():
    junk_path = "build/outputs/junk.txt"
    assert "/build/" not in junk_path
    assert junk_path.startswith("build/")


def test_ref_normalizes_windows_path_representation_to_posix():
    windows_relative = PureWindowsPath("rules\\always.mdc")
    ref = _ref(CapabilityKind.RULE, windows_relative, None)
    assert ref.source_path == "rules/always.mdc"
    assert ref.capability_id == "rule:rules/always.mdc"
    assert "\\" not in ref.source_path


def test_scan_plugin_discovers_exact_official_component_inventory():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        _write_plugin(plugin)
        refs = scan_plugin(plugin, "cursor")
        expected = _expected_refs()

        assert refs == expected
        assert tuple(ref.capability_id for ref in refs) == tuple(
            sorted(ref.capability_id for ref in expected)
        )
        assert all("\\" not in ref.source_path for ref in refs)

        source_paths = {ref.source_path for ref in refs}
        assert "build/outputs/junk.txt" not in source_paths
        assert "packaging/private.json" not in source_paths
        assert all(not path.startswith("build/") for path in source_paths)
        assert all(not path.startswith("packaging/") for path in source_paths)


def test_scan_plugin_cursor_ignores_claude_leftover_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        files = {
            ".cursor-plugin/plugin.json": "{}",
            "SKILL.md": "# Root skill\n",
            "package.json": "{}",
            "settings.json": "{}",
            "bin/leftover": "#!/bin/sh\n",
        }
        for relative, content in files.items():
            path = plugin / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        refs = scan_plugin(plugin, "cursor")
        source_paths = {ref.source_path for ref in refs}
        assert "SKILL.md" in source_paths
        assert "package.json" not in source_paths
        assert "settings.json" not in source_paths
        assert "bin/leftover" not in source_paths
        by_path = {ref.source_path: ref for ref in refs}
        assert by_path["SKILL.md"].kind is CapabilityKind.SKILL


def test_scan_plugin_claude_still_discovers_bin_settings_and_package_json():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        files = {
            "bin/my-tool": "#!/bin/sh\n",
            "settings.json": "{}",
            "SKILL.md": "# Root skill\n",
            "package.json": "{}",
            "package-lock.json": "{}",
        }
        for relative, content in files.items():
            path = plugin / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        refs = scan_plugin(plugin, "claude-code")
        source_paths = {ref.source_path for ref in refs}
        for relative in files:
            assert relative in source_paths, relative
        by_path = {ref.source_path: ref for ref in refs}
        assert by_path["bin/my-tool"].kind is CapabilityKind.COMMAND
        assert by_path["settings.json"].kind is CapabilityKind.PLUGIN_METADATA
        assert by_path["package.json"].kind is CapabilityKind.PLUGIN_METADATA
        assert by_path["package-lock.json"].kind is CapabilityKind.PLUGIN_METADATA
        assert by_path["SKILL.md"].kind is CapabilityKind.SKILL


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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
