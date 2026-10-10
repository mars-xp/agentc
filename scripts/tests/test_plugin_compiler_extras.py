#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.extras import (
    extra_conversion_issue,
    load_connector_document,
    load_lsp_document,
    load_monitor_document,
    load_output_style_document,
    load_theme_document,
    native_targets,
)
from plugin_compiler.mcp import payload_secret_issues
from plugin_compiler.model import CapabilityKind, Target


CONNECTOR_JSON = {"name": "demo-app", "command": "npx"}
SECRET_CONNECTOR_JSON = {
    "name": "demo-app",
    "env": {"API_KEY": "sk-live-secret"},
}
LSP_JSON = {"demo": {"command": "demo-lsp"}}
SECRET_LSP_JSON = {"demo": {"env": {"API_KEY": "sk-live-secret"}}}
THEME_JSON = {"name": "dark", "colors": {"bg": "#000"}}
MONITOR_JSON = {"name": "cpu", "interval": 5}
OUTPUT_STYLE_MD = """---
name: concise
description: Keep answers short
---
# Concise

Be brief.
"""


def test_connector_only_codex():
    assert native_targets(CapabilityKind.CONNECTOR) == frozenset({Target.CODEX})
    issue = extra_conversion_issue(CapabilityKind.CONNECTOR, Target.CURSOR)
    assert issue is not None and issue.code == "undeclared-target-unsupported"


def test_theme_only_claude_code():
    assert native_targets(CapabilityKind.THEME) == frozenset({Target.CLAUDE_CODE})
    assert extra_conversion_issue(CapabilityKind.THEME, Target.CLAUDE_CODE) is None
    issue = extra_conversion_issue(CapabilityKind.THEME, Target.CURSOR)
    assert issue is not None and issue.code == "undeclared-target-unsupported"
    issue = extra_conversion_issue(CapabilityKind.THEME, Target.CODEX)
    assert issue is not None and issue.code == "undeclared-target-unsupported"


def test_lsp_only_claude_code():
    assert native_targets(CapabilityKind.LSP) == frozenset({Target.CLAUDE_CODE})
    assert extra_conversion_issue(CapabilityKind.LSP, Target.CLAUDE_CODE) is None
    cursor_issue = extra_conversion_issue(CapabilityKind.LSP, Target.CURSOR)
    assert cursor_issue is not None
    assert cursor_issue.code == "undeclared-target-unsupported"
    codex_issue = extra_conversion_issue(CapabilityKind.LSP, Target.CODEX)
    assert codex_issue is not None
    assert codex_issue.code == "undeclared-target-unsupported"


def test_output_style_only_claude_code():
    assert native_targets(CapabilityKind.OUTPUT_STYLE) == frozenset(
        {Target.CLAUDE_CODE}
    )
    assert extra_conversion_issue(CapabilityKind.OUTPUT_STYLE, Target.CLAUDE_CODE) is None
    issue = extra_conversion_issue(CapabilityKind.OUTPUT_STYLE, Target.CURSOR)
    assert issue is not None and issue.code == "undeclared-target-unsupported"
    issue = extra_conversion_issue(CapabilityKind.OUTPUT_STYLE, Target.CODEX)
    assert issue is not None and issue.code == "undeclared-target-unsupported"


def test_monitor_only_claude_code():
    assert native_targets(CapabilityKind.MONITOR) == frozenset({Target.CLAUDE_CODE})
    assert extra_conversion_issue(CapabilityKind.MONITOR, Target.CLAUDE_CODE) is None
    for target in (Target.CURSOR, Target.CODEX):
        issue = extra_conversion_issue(CapabilityKind.MONITOR, target)
        assert issue is not None, target
        assert issue.code == "undeclared-target-unsupported", target
        assert issue.severity == "block", target


def test_theme_fails_cursor_and_codex():
    for target in (Target.CURSOR, Target.CODEX):
        issue = extra_conversion_issue(CapabilityKind.THEME, target)
        assert issue is not None, target
        assert issue.code == "undeclared-target-unsupported", target
        assert issue.severity == "block", target


def test_connector_codex_has_no_issue():
    assert extra_conversion_issue(CapabilityKind.CONNECTOR, Target.CODEX) is None
    issue = extra_conversion_issue(CapabilityKind.CONNECTOR, Target.CLAUDE_CODE)
    assert issue is not None and issue.code == "undeclared-target-unsupported"
    assert "packaging/platform-overrides.json" in issue.message


def test_load_connector_document():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".app.json"
        path.write_text(
            json.dumps(CONNECTOR_JSON), encoding="utf-8", newline="\n"
        )
        document = load_connector_document(path, plugin)
        assert document.capability_id == "connector:.app.json"
        assert document.source_path == ".app.json"
        assert document.payload == CONNECTOR_JSON


def test_load_lsp_document():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".lsp.json"
        path.write_text(json.dumps(LSP_JSON), encoding="utf-8", newline="\n")
        document = load_lsp_document(path, plugin)
        assert document.capability_id == "lsp:.lsp.json"
        assert document.source_path == ".lsp.json"
        assert document.payload == {"demo": {"command": "demo-lsp"}}
        assert "lspServers" not in document.payload


def test_load_lsp_document_rejects_wrapped_lsp_servers_root():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".lsp.json"
        path.write_text(
            json.dumps({"lspServers": {"demo": {"command": "demo-lsp"}}}),
            encoding="utf-8",
            newline="\n",
        )
        try:
            load_lsp_document(path, plugin)
        except RuntimeError as exc:
            assert ".lsp.json" in str(exc)
            assert "lspServers" in str(exc)
        else:
            raise AssertionError("wrapped .lsp.json root must not load")


def test_connector_secret_payload_emits_mcp_secret_issue():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".app.json"
        path.write_text(
            json.dumps(SECRET_CONNECTOR_JSON), encoding="utf-8", newline="\n"
        )
        document = load_connector_document(path, plugin)
        issues = payload_secret_issues(document.payload, document.source_path)
        assert any(item.code == "mcp-secret-in-source" for item in issues)
        assert issues[0].path == ".app.json"


def test_lsp_secret_payload_emits_mcp_secret_issue():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".lsp.json"
        path.write_text(
            json.dumps(SECRET_LSP_JSON), encoding="utf-8", newline="\n"
        )
        document = load_lsp_document(path, plugin)
        issues = payload_secret_issues(document.payload, document.source_path)
        assert any(item.code == "mcp-secret-in-source" for item in issues)
        assert issues[0].path == ".lsp.json"


def test_clean_connector_has_no_secret_issue():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".app.json"
        path.write_text(
            json.dumps(CONNECTOR_JSON), encoding="utf-8", newline="\n"
        )
        document = load_connector_document(path, plugin)
        assert payload_secret_issues(document.payload, document.source_path) == ()


def test_load_theme_document():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / "themes" / "dark.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(THEME_JSON), encoding="utf-8", newline="\n")
        document = load_theme_document(path, plugin)
        assert document.capability_id == "theme:themes/dark.json"
        assert document.source_path == "themes/dark.json"
        assert document.payload == THEME_JSON


def test_load_monitor_document():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / "monitors" / "monitors.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(MONITOR_JSON), encoding="utf-8", newline="\n")
        document = load_monitor_document(path, plugin)
        assert document.capability_id == "monitor:monitors/monitors.json"
        assert document.source_path == "monitors/monitors.json"
        assert document.payload == MONITOR_JSON


def test_load_output_style_document():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / "output-styles" / "concise.md"
        path.parent.mkdir(parents=True)
        path.write_text(OUTPUT_STYLE_MD, encoding="utf-8", newline="\n")
        document = load_output_style_document(path, plugin)
        assert document.capability_id == "output-style:output-styles/concise.md"
        assert document.source_path == "output-styles/concise.md"
        assert document.name == "concise"
        assert "Be brief." in document.body
        assert ("description", "Keep answers short") in document.extra_fields


def test_load_json_rejects_invalid():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".app.json"
        path.write_text("{", encoding="utf-8")
        try:
            load_connector_document(path, plugin)
        except RuntimeError as exc:
            assert ".app.json" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_extras_module_has_no_skill_conversion():
    import plugin_compiler.extras as extras

    names = [name for name in dir(extras) if "skill" in name.lower()]
    assert names == []
    render_names = [
        name for name in dir(extras) if name.startswith("render_")
    ]
    assert render_names == []


def test_load_lsp_document_plugin_json_payload_is_server_mapping():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / ".claude-plugin" / "plugin.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "name": "demo",
                    "lspServers": {"python": {"command": "demo-lsp"}},
                }
            ),
            encoding="utf-8",
            newline="\n",
        )
        document = load_lsp_document(path, plugin)
        assert document.payload == {"python": {"command": "demo-lsp"}}
        assert "lspServers" not in document.payload


def test_load_lsp_document_keeps_server_named_lsp_servers():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".lsp.json"
        payload = {"lspServers": {"command": "named-lsp"}}
        path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
        document = load_lsp_document(path, plugin)
        assert document.payload == payload
        assert document.payload["lspServers"]["command"] == "named-lsp"


def test_merge_lsp_documents_merges_disjoint_maps():
    from plugin_compiler.extras import LspDocument, merge_lsp_documents

    left = LspDocument(
        capability_id="lsp:a.json",
        source_path="a.json",
        payload={"python": {"command": "a"}},
    )
    right = LspDocument(
        capability_id="lsp:b.json",
        source_path="b.json",
        payload={"go": {"command": "b"}},
    )
    merged = merge_lsp_documents((left, right))
    assert len(merged) == 1
    assert merged[0].payload == {
        "python": {"command": "a"},
        "go": {"command": "b"},
    }


def test_merge_lsp_documents_duplicate_server_name_raises():
    from plugin_compiler.extras import LspDocument, merge_lsp_documents

    left = LspDocument(
        capability_id="lsp:a.json",
        source_path="a.json",
        payload={"python": {"command": "a"}},
    )
    right = LspDocument(
        capability_id="lsp:b.json",
        source_path="b.json",
        payload={"python": {"command": "b"}},
    )
    try:
        merged = merge_lsp_documents((left, right))
    except RuntimeError:
        return
    raise AssertionError(
        "duplicate LSP names must not silently merge: {0!r}".format(
            merged[0].payload if merged else merged
        )
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
