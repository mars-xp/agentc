#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex 来源合同：路径边界、默认发现、内联 MCP。运行：python3 scripts/tests/test_plugin_targets_codex_source_contract.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SENTINEL = "OUTSIDE-SKILL-SENTINEL-9f3a"
INTERFACE = {
    "displayName": "Demo",
    "shortDescription": "short",
    "longDescription": "long",
    "developerName": "demo",
    "category": "Productivity",
    "capabilities": ["Read"],
    "defaultPrompt": ["hello"],
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill_md(name: str, extra: str = "") -> str:
    return "---\nname: {0}\ndescription: demo skill\n---\n{1}\n".format(name, extra)


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


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_resolve_declared_path_rejects_parent_escape():
    from plugin_targets.source_paths import DeclaredPathError, resolve_declared_path

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plugin"
        plugin.mkdir()
        raised = False
        try:
            resolve_declared_path(plugin, "../outside-skill")
        except DeclaredPathError as exc:
            raised = True
            assert exc.code == "path-escape"
        assert raised


def test_resolve_declared_path_rejects_absolute():
    from plugin_targets.source_paths import DeclaredPathError, resolve_declared_path

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plugin"
        plugin.mkdir()
        raised = False
        try:
            resolve_declared_path(plugin, str(Path(tmp) / "other"))
        except DeclaredPathError as exc:
            raised = True
            assert exc.code == "path-escape"
        assert raised


def test_codex_source_blocks_skill_path_outside_plugin_root():
    from plugin_targets.codex.source_parse import validate_codex_source

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "path-escape"
        outside = root / "outside-skill"
        _write(outside / "SKILL.md", _skill_md("outside-skill", SENTINEL))
        _codex_manifest(plugin, {"name": "path-escape", "skills": "../outside-skill"})
        issues = validate_codex_source(plugin)
        assert "path-escape" in _codes(issues), issues


def test_codex_compile_does_not_package_outside_skill():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Plugin validation passed: path-escape\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "path-escape"
        outside = repo / "plugins" / "outside-skill"
        _write(outside / "SKILL.md", _skill_md("outside-skill", SENTINEL))
        _codex_manifest(plugin, {"name": "path-escape", "skills": "../outside-skill"})
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="codex"
        )
        assert result.ok is False
        assert any(
            issue.code == "path-escape"
            for report in result.validation.values()
            for issue in report.issues
        )
        assert "codex" not in result.published_roots


def test_codex_default_discovers_root_hooks_json_and_mcp():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.codex.source_parse import parse_codex_plugin

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Plugin validation passed: codex-defaults\n", "")

    hook_payload = {
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "echo ROOT-HOOK-SENTINEL",
                        }
                    ]
                }
            ]
        }
    }
    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "codex-defaults"
        _codex_manifest(plugin, {"name": "codex-defaults"})
        _write(plugin / "skills" / "only" / "SKILL.md", _skill_md("only"))
        _write(plugin / "hooks.json", json.dumps(hook_payload) + "\n")
        _write(
            plugin / ".mcp.json",
            json.dumps({"mcpServers": {"s": {"command": "x"}}}) + "\n",
        )
        parsed = parse_codex_plugin(plugin)
        kinds = {cap.kind.value for cap in parsed.capabilities}
        assert "hook" in kinds, kinds
        assert "mcp" in kinds
        assert parsed.hooks, parsed.hooks
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="codex"
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        published = Path(result.published_roots["codex"])
        sidecar = published / "hooks" / "hooks.json"
        assert sidecar.is_file(), list(published.rglob("*"))
        assert "ROOT-HOOK-SENTINEL" in sidecar.read_text(encoding="utf-8")


def test_codex_default_discovers_hooks_dir_hooks_json_without_root_file():
    from plugin_targets.codex.source_parse import parse_codex_plugin

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "codex-hooks-dir"
        _codex_manifest(plugin, {"name": "codex-hooks-dir"})
        _write(plugin / "hooks" / "hooks.json", json.dumps({"hooks": {}}) + "\n")
        parsed = parse_codex_plugin(plugin)
        kinds = {cap.kind.value for cap in parsed.capabilities}
        assert "hook" in kinds, kinds
        assert any(
            cap.source_path.replace("\\", "/") == "hooks/hooks.json"
            for cap in parsed.capabilities
            if cap.kind.value == "hook"
        ), parsed.capabilities
        assert parsed.hooks


def test_codex_default_skills_dir_is_discovered_without_manifest_field():
    from plugin_targets.codex.source_parse import parse_codex_plugin, validate_codex_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "default-discovery"
        _codex_manifest(plugin, {"name": "default-discovery"})
        _write(
            plugin / "skills" / "default-skill" / "SKILL.md",
            _skill_md("default-skill", "DEFAULT-SKILL-BODY"),
        )
        assert validate_codex_source(plugin) == ()
        parsed = parse_codex_plugin(plugin)
        names = [item.name for item in parsed.skills]
        assert "default-skill" in names, parsed.skills
        assert any(
            item.kind.value == "skill" for item in parsed.capabilities
        ), parsed.capabilities


def test_codex_default_discovery_survives_compile_to_codex():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Plugin validation passed: default-discovery\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "default-discovery"
        _codex_manifest(plugin, {"name": "default-discovery"})
        _write(
            plugin / "skills" / "default-skill" / "SKILL.md",
            _skill_md("default-skill", "DEFAULT-SKILL-BODY"),
        )
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="codex"
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        published = Path(result.published_roots["codex"])
        skill = published / "skills" / "default-skill" / "SKILL.md"
        assert skill.is_file(), list(published.rglob("*"))
        assert "DEFAULT-SKILL-BODY" in skill.read_text(encoding="utf-8")


def test_codex_inline_mcp_servers_are_not_invalid_paths():
    from plugin_targets.codex.source_parse import parse_codex_plugin, validate_codex_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "inline-mcp"
        _codex_manifest(
            plugin,
            {
                "name": "inline-mcp",
                "mcpServers": {"demo": {"command": "demo-server"}},
            },
        )
        issues = validate_codex_source(plugin)
        assert "invalid-component-path" not in _codes(issues), issues
        parsed = parse_codex_plugin(plugin)
        assert parsed.mcp_documents
        assert parsed.mcp_documents[0].servers["demo"]["command"] == "demo-server"


def test_codex_inline_mcp_round_trips_as_object_on_codex_target():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Plugin validation passed: inline-mcp\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "inline-mcp"
        _codex_manifest(
            plugin,
            {
                "name": "inline-mcp",
                "mcpServers": {"demo": {"command": "demo-server"}},
            },
        )
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="codex"
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        manifest = json.loads(
            (
                Path(result.published_roots["codex"])
                / ".codex-plugin"
                / "plugin.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest["mcpServers"] == {"demo": {"command": "demo-server"}}


def _claude_manifest(plugin: Path, payload: dict) -> None:
    body = {
        "name": payload.get("name", "demo"),
        "version": "0.0.1",
        "description": "demo",
        "author": {"name": "demo"},
    }
    body.update(payload)
    _write(plugin / ".claude-plugin" / "plugin.json", json.dumps(body, indent=2) + "\n")


def _claude_overrides(plugin: Path, overrides: dict) -> None:
    payload = {
        "schema_version": 1,
        "default_targets": ["cursor", "codex", "claude-code"],
        "overrides": overrides,
    }
    _write(
        plugin / "packaging" / "platform-overrides.json",
        json.dumps(payload, indent=2) + "\n",
    )


def test_codex_source_blocks_mixed_type_skills_array():
    from plugin_targets.codex.source_parse import validate_codex_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "mixed-declaration"
        _codex_manifest(
            plugin,
            {"name": "mixed-declaration", "skills": ["./skills/valid", 42]},
        )
        _write(plugin / "skills" / "valid" / "SKILL.md", _skill_md("valid"))
        issues = validate_codex_source(plugin)
        assert "invalid-component-path" in _codes(issues), issues


def test_codex_source_blocks_empty_string_in_skills_array():
    from plugin_targets.codex.source_parse import validate_codex_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "empty-item"
        _codex_manifest(
            plugin,
            {"name": "empty-item", "skills": ["./skills/valid", ""]},
        )
        _write(plugin / "skills" / "valid" / "SKILL.md", _skill_md("valid"))
        issues = validate_codex_source(plugin)
        assert "invalid-component-path" in _codes(issues), issues


def test_codex_compile_mixed_skills_array_does_not_publish():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Plugin validation passed: mixed-declaration\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mixed-declaration"
        _codex_manifest(
            plugin,
            {"name": "mixed-declaration", "skills": ["./skills/valid", 42]},
        )
        _write(plugin / "skills" / "valid" / "SKILL.md", _skill_md("valid"))
        result = compile_plugin(
            plugin, repo, ("codex", "cursor"), runner=Runner(), source_format="codex"
        )
        assert result.ok is False
        assert result.published_roots == {}
        assert any(
            issue.code == "invalid-component-path"
            for report in result.validation.values()
            for issue in report.issues
        )


def test_claude_source_accepts_inline_hooks_and_lsp():
    from plugin_targets.claude_code.source_parse import (
        parse_claude_plugin,
        validate_claude_source,
    )

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "inline-claude"
        _claude_manifest(
            plugin,
            {
                "name": "inline-claude",
                "hooks": {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 hooks/notify.py",
                                    }
                                ]
                            }
                        ]
                    }
                },
                "lspServers": {"python": {"command": "demo-lsp"}},
            },
        )
        _write(plugin / "hooks" / "notify.py", "print(1)\n")
        issues = validate_claude_source(plugin)
        assert "invalid-component-path" not in _codes(issues), issues
        parsed = parse_claude_plugin(plugin)
        assert parsed.hooks, "inline hooks must become hook documents"
        assert parsed.lsp_documents, "inline lspServers must become LSP documents"


def test_claude_compile_inline_hooks_and_lsp():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "inline-claude"
        _claude_manifest(
            plugin,
            {
                "name": "inline-claude",
                "hooks": {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 hooks/notify.py",
                                    }
                                ]
                            }
                        ]
                    }
                },
                "lspServers": {"python": {"command": "demo-lsp"}},
            },
        )
        _write(plugin / "hooks" / "notify.py", "print(1)\n")
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        published = Path(result.published_roots["claude-code"])
        assert (published / "hooks" / "hooks.json").is_file()
        lsp = json.loads((published / ".lsp.json").read_text(encoding="utf-8"))
        assert lsp == {"python": {"command": "demo-lsp"}}, lsp
        assert "lspServers" not in lsp


def test_claude_wrapped_lsp_json_sidecar_is_intercepted_before_publish():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "wrapped-lsp"
        _claude_manifest(plugin, {"name": "wrapped-lsp"})
        _write(
            plugin / ".lsp.json",
            json.dumps({"lspServers": {"python": {"command": "demo-lsp"}}}) + "\n",
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok is False, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        messages = " ".join(
            issue.message
            for report in result.validation.values()
            for issue in report.issues
        )
        assert "lspServers" in messages or "scan-failed" in {
            issue.code
            for report in result.validation.values()
            for issue in report.issues
        }, messages
        published = result.published_roots.get("claude-code")
        if published:
            sidecar = Path(published) / ".lsp.json"
            assert not sidecar.is_file(), sidecar


def test_claude_official_lsp_json_sidecar_publishes_mapping():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "official-lsp"
        _claude_manifest(plugin, {"name": "official-lsp"})
        _write(
            plugin / ".lsp.json",
            json.dumps({"python": {"command": "demo-lsp"}}) + "\n",
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        lsp = json.loads(
            (Path(result.published_roots["claude-code"]) / ".lsp.json").read_text(
                encoding="utf-8"
            )
        )
        assert lsp == {"python": {"command": "demo-lsp"}}, lsp
        assert "lspServers" not in lsp


def _hook_payload(event_name: str, script: str, sentinel: str) -> dict:
    return {
        "hooks": {
            event_name: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 {0} {1}".format(script, sentinel),
                        }
                    ]
                }
            ]
        }
    }


def test_claude_experimental_nested_theme_monitor_paths_are_valid():
    from plugin_targets.claude_code.source_parse import (
        parse_claude_plugin,
        validate_claude_source,
    )

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "experimental-nested"
        _claude_manifest(
            plugin,
            {
                "name": "experimental-nested",
                "experimental": {
                    "themes": "./themes/",
                    "monitors": "./monitors/monitors.json",
                },
            },
        )
        _write(
            plugin / "themes" / "dark.json",
            json.dumps({"name": "dark", "colors": {"bg": "#000"}}) + "\n",
        )
        _write(
            plugin / "monitors" / "monitors.json",
            json.dumps({"name": "cpu", "interval": 5}) + "\n",
        )
        issues = validate_claude_source(plugin)
        assert "invalid-component-path" not in _codes(issues), issues
        parsed = parse_claude_plugin(plugin)
        kinds = {cap.kind.value for cap in parsed.capabilities}
        assert "theme" in kinds, parsed.capabilities
        assert "monitor" in kinds, parsed.capabilities
        assert parsed.themes, parsed.themes
        assert parsed.monitors, parsed.monitors


def test_claude_mcp_sidecar_list_does_not_publish_empty_success():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mcp-list-sidecar"
        _claude_manifest(plugin, {"name": "mcp-list-sidecar"})
        _write(
            plugin / ".mcp.json",
            json.dumps({"mcpServers": ["./other.json"]}) + "\n",
        )
        _write(
            plugin / "other.json",
            json.dumps({"mcpServers": {"other": {"command": "other"}}}) + "\n",
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok is False
        published = result.published_roots.get("claude-code")
        if published:
            sidecar = Path(published) / ".mcp.json"
            if sidecar.is_file():
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
                assert payload.get("mcpServers") != {}, payload


def test_claude_hooks_string_object_and_array_shapes():
    from plugin_compiler.hooks import load_hook_document
    from plugin_targets.claude_code.source_parse import (
        parse_claude_plugin,
        validate_claude_source,
    )

    custom_payload = _hook_payload("SessionStart", "hooks/custom.py", "CUSTOM-HOOK")
    cases = (
        ("hooks-string", {"hooks": "./hooks/custom-hooks.json"}),
        (
            "hooks-object",
            {"hooks": _hook_payload("SessionStart", "hooks/inline.py", "INLINE-HOOK")["hooks"]},
        ),
        (
            "hooks-array",
            {"hooks": ["./hooks/custom-hooks.json", "./hooks/second-hooks.json"]},
        ),
    )
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, fields in cases:
            plugin = root / name
            payload = {"name": name}
            payload.update(fields)
            _claude_manifest(plugin, payload)
            _write(
                plugin / "hooks" / "custom-hooks.json",
                json.dumps(custom_payload) + "\n",
            )
            _write(
                plugin / "hooks" / "second-hooks.json",
                json.dumps(_hook_payload("Stop", "hooks/second.py", "SECOND-HOOK")) + "\n",
            )
            _write(plugin / "hooks" / "custom.py", "print(1)\n")
            _write(plugin / "hooks" / "inline.py", "print(1)\n")
            _write(plugin / "hooks" / "second.py", "print(1)\n")
            issues = validate_claude_source(plugin)
            assert "invalid-component-path" not in _codes(issues), (name, issues)
            parsed = parse_claude_plugin(plugin)
            assert parsed.hooks, (name, parsed.hooks)
            if name == "hooks-string":
                custom = load_hook_document(
                    plugin / "hooks" / "custom-hooks.json", plugin
                )
                assert custom.handlers, custom
                assert any(
                    "CUSTOM-HOOK" in handler.command for handler in custom.handlers
                )


def test_claude_two_hook_documents_merge_into_sidecar():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "hooks-merge"
        _claude_manifest(
            plugin,
            {
                "name": "hooks-merge",
                "hooks": ["./hooks/first.json", "./hooks/second.json"],
            },
        )
        _write(
            plugin / "hooks" / "first.json",
            json.dumps(_hook_payload("SessionStart", "hooks/first.py", "HOOK-FIRST"))
            + "\n",
        )
        _write(
            plugin / "hooks" / "second.json",
            json.dumps(_hook_payload("Stop", "hooks/second.py", "HOOK-SECOND")) + "\n",
        )
        _write(plugin / "hooks" / "first.py", "print(1)\n")
        _write(plugin / "hooks" / "second.py", "print(2)\n")
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        published = Path(result.published_roots["claude-code"])
        text = (published / "hooks" / "hooks.json").read_text(encoding="utf-8")
        assert "HOOK-FIRST" in text, text
        assert "HOOK-SECOND" in text, text


def test_claude_mcp_disjoint_path_maps_merge_into_sidecar():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mcp-merge"
        _claude_manifest(
            plugin,
            {
                "name": "mcp-merge",
                "mcpServers": ["./mcp/alpha.json", "./mcp/beta.json"],
            },
        )
        _write(
            plugin / "mcp" / "alpha.json",
            json.dumps({"mcpServers": {"alpha": {"command": "alpha-cmd"}}}) + "\n",
        )
        _write(
            plugin / "mcp" / "beta.json",
            json.dumps({"mcpServers": {"beta": {"command": "beta-cmd"}}}) + "\n",
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        payload = json.loads(
            (Path(result.published_roots["claude-code"]) / ".mcp.json").read_text(
                encoding="utf-8"
            )
        )
        servers = payload["mcpServers"]
        assert servers["alpha"]["command"] == "alpha-cmd", payload
        assert servers["beta"]["command"] == "beta-cmd", payload


def test_claude_mcp_duplicate_server_name_blocks_before_publish():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mcp-collision"
        _claude_manifest(
            plugin,
            {
                "name": "mcp-collision",
                "mcpServers": {
                    "demo": {"command": "inline-cmd"},
                },
            },
        )
        _write(
            plugin / ".mcp.json",
            json.dumps({"mcpServers": {"demo": {"command": "file-cmd"}}}) + "\n",
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok is False
        assert result.published_roots == {}


def test_claude_mcp_excluded_document_is_not_published():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mcp-exclude"
        _claude_manifest(
            plugin,
            {
                "name": "mcp-exclude",
                "mcpServers": ["./mcp/keep.json", "./mcp/skip.json"],
            },
        )
        _write(
            plugin / "mcp" / "keep.json",
            json.dumps({"mcpServers": {"keep": {"command": "keep-cmd"}}}) + "\n",
        )
        _write(
            plugin / "mcp" / "skip.json",
            json.dumps({"mcpServers": {"skip": {"command": "skip-cmd"}}}) + "\n",
        )
        _claude_overrides(
            plugin,
            {
                "mcp:mcp/skip.json": {
                    "targets": ["cursor"],
                    "reason": "skip.json is cursor-only",
                }
            },
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        payload = json.loads(
            (Path(result.published_roots["claude-code"]) / ".mcp.json").read_text(
                encoding="utf-8"
            )
        )
        servers = payload["mcpServers"]
        assert servers.get("keep", {}).get("command") == "keep-cmd", payload
        assert "skip" not in servers, payload


def test_claude_mcp_excluded_duplicate_server_name_does_not_block():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mcp-exclude-dup"
        _claude_manifest(
            plugin,
            {
                "name": "mcp-exclude-dup",
                "mcpServers": ["./mcp/keep.json", "./mcp/skip.json"],
            },
        )
        _write(
            plugin / "mcp" / "keep.json",
            json.dumps({"mcpServers": {"demo": {"command": "keep-cmd"}}}) + "\n",
        )
        _write(
            plugin / "mcp" / "skip.json",
            json.dumps({"mcpServers": {"demo": {"command": "skip-cmd"}}}) + "\n",
        )
        _claude_overrides(
            plugin,
            {
                "mcp:mcp/skip.json": {
                    "targets": ["cursor"],
                    "reason": "skip.json is cursor-only",
                }
            },
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        payload = json.loads(
            (Path(result.published_roots["claude-code"]) / ".mcp.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload["mcpServers"]["demo"]["command"] == "keep-cmd", payload


def test_claude_compile_orphan_mcp_lsp_sidecars_block_when_no_documents():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.claude_code import stage as claude_stage

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    original = claude_stage.stage_claude

    def stage_and_leave_orphan_sidecars(parsed, layout, collected_issues=None):
        destination = original(parsed, layout, collected_issues)
        (destination / ".mcp.json").write_text(
            json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8"
        )
        (destination / ".lsp.json").write_text(
            json.dumps({"python": {"command": "demo-lsp"}}) + "\n",
            encoding="utf-8",
        )
        return destination

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "no-mcp-lsp"
        _claude_manifest(plugin, {"name": "no-mcp-lsp"})
        claude_stage.stage_claude = stage_and_leave_orphan_sidecars
        try:
            result = compile_plugin(
                plugin,
                repo,
                ("claude-code",),
                runner=Runner(),
                source_format="claude-code",
            )
        finally:
            claude_stage.stage_claude = original
        assert result.ok is False, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        codes = {
            issue.code
            for report in result.validation.values()
            for issue in report.issues
        }
        assert "unexpected-mcp-artifact" in codes, codes
        assert "unexpected-lsp-artifact" in codes, codes
        assert result.published_roots == {}


def test_claude_lsp_disjoint_path_maps_merge_into_sidecar():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "lsp-merge"
        _claude_manifest(
            plugin,
            {
                "name": "lsp-merge",
                "lspServers": ["./lsp/python.json", "./lsp/go.json"],
            },
        )
        _write(
            plugin / "lsp" / "python.json",
            json.dumps({"python": {"command": "py-lsp"}}) + "\n",
        )
        _write(
            plugin / "lsp" / "go.json",
            json.dumps({"go": {"command": "go-lsp"}}) + "\n",
        )
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        payload = json.loads(
            (Path(result.published_roots["claude-code"]) / ".lsp.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload == {
            "python": {"command": "py-lsp"},
            "go": {"command": "go-lsp"},
        }, payload
        assert "lspServers" not in payload


def test_claude_default_dir_escaping_file_symlink_is_path_escape():
    from plugin_targets.claude_code.source_parse import (
        parse_claude_plugin,
        validate_claude_source,
    )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "default-dir-symlink"
        outside = root / "outside.md"
        _write(outside, "# escaped\n")
        _claude_manifest(plugin, {"name": "default-dir-symlink"})
        commands = plugin / "commands"
        commands.mkdir()
        (commands / "escaped.md").symlink_to(outside)
        _write(commands / "ok.md", "# ok\n")
        issues = validate_claude_source(plugin)
        assert "path-escape" in _codes(issues), issues
        parsed = parse_claude_plugin(plugin)
        command_paths = {
            cap.source_path.replace("\\", "/")
            for cap in parsed.capabilities
            if cap.kind.value == "command"
        }
        assert "commands/ok.md" in command_paths, parsed.capabilities
        assert "commands/escaped.md" not in command_paths, parsed.capabilities


def test_claude_source_blocks_mixed_type_skills_array():
    from plugin_targets.claude_code.source_parse import validate_claude_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "mixed-declaration"
        _claude_manifest(
            plugin,
            {"name": "mixed-declaration", "skills": ["./skills/valid", 42]},
        )
        _write(plugin / "skills" / "valid" / "SKILL.md", _skill_md("valid"))
        issues = validate_claude_source(plugin)
        assert "invalid-component-path" in _codes(issues), issues


def test_claude_compile_mixed_skills_array_does_not_publish():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mixed-declaration"
        _claude_manifest(
            plugin,
            {"name": "mixed-declaration", "skills": ["./skills/valid", 42]},
        )
        _write(plugin / "skills" / "valid" / "SKILL.md", _skill_md("valid"))
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok is False
        assert result.published_roots == {}


def test_codex_default_discovery_blocks_escaped_symlink_skill():
    from plugin_targets.codex.source_parse import validate_codex_source

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "default-symlink-probe"
        outside = root / "outside-skill"
        _write(outside / "SKILL.md", _skill_md("escaped-default", SENTINEL))
        _codex_manifest(plugin, {"name": "default-symlink-probe"})
        skills_root = plugin / "skills"
        skills_root.mkdir()
        (skills_root / "escaped-default").symlink_to(outside)
        issues = validate_codex_source(plugin)
        assert "path-escape" in _codes(issues), issues


def test_codex_source_blocks_declared_custom_skills_file_symlink():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.codex.source_parse import validate_codex_source

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(
                0, "Plugin validation passed: custom-symlink-skill\n", ""
            )

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "custom-symlink-skill"
        real = plugin / "payloads" / "ok.md"
        _write(real, _skill_md("escaped", SENTINEL))
        _codex_manifest(
            plugin,
            {"name": "custom-symlink-skill", "skills": "./custom-skills/"},
        )
        skill_dir = plugin / "custom-skills" / "escaped"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").symlink_to(real)
        issues = validate_codex_source(plugin)
        assert "path-escape" in _codes(issues), issues
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="codex"
        )
        assert result.ok is False
        published = result.published_roots.get("codex")
        if published:
            packed = Path(published) / "skills" / "escaped" / "SKILL.md"
            if packed.is_file():
                assert SENTINEL not in packed.read_text(encoding="utf-8")


def test_codex_compile_does_not_skip_escaped_default_symlink():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Plugin validation passed: default-symlink-probe\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "default-symlink-probe"
        outside = repo / "plugins" / "outside-skill"
        _write(outside / "SKILL.md", _skill_md("escaped-default", SENTINEL))
        _codex_manifest(plugin, {"name": "default-symlink-probe"})
        skills_root = plugin / "skills"
        skills_root.mkdir()
        (skills_root / "escaped-default").symlink_to(outside)
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="codex"
        )
        assert result.ok is False
        assert "codex" not in result.published_roots
        assert any(
            issue.code == "path-escape"
            for report in result.validation.values()
            for issue in report.issues
        )


def test_cursor_to_codex_classifies_translated_manifest_fields():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Plugin validation passed: demo\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "cursor-translated"
        plugin.mkdir(parents=True)
        _write(
            plugin / ".cursor-plugin" / "plugin.json",
            json.dumps(
                {
                    "name": "cursor-translated",
                    "version": "0.0.1",
                    "description": "demo",
                    "author": {"name": "demo"},
                    "displayName": "Cursor Translated",
                    "commands": "./commands/",
                    "rules": "./rules/",
                }
            )
            + "\n",
        )
        _write(
            plugin / "packaging" / "codex-plugin.json",
            json.dumps({"interface": INTERFACE}) + "\n",
        )
        _write(plugin / "commands" / "hello.md", "# hello\n")
        _write(plugin / "rules" / "one.mdc", "---\ndescription: r\n---\n# r\n")
        _write(plugin / "skills" / "keep" / "SKILL.md", _skill_md("keep"))
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="cursor"
        )
        messages = [
            (issue.code, issue.message) for issue in result.validation["codex"].issues
        ]
        assert any(
            code == "translated-manifest-declaration" and "commands" in message
            for code, message in messages
        ), messages
        assert any(
            code == "translated-manifest-declaration" and "rules" in message
            for code, message in messages
        ), messages
        assert any(
            code == "replaced-by-target-overlay" and "displayName" in message
            for code, message in messages
        ), messages
        assert not any(
            code == "dropped-manifest-field"
            and any(name in message for name in ("commands", "rules", "displayName"))
            for code, message in messages
        ), messages


def test_claude_declared_custom_hook_missing_script_blocks():
    from plugin_targets.claude_code.source_parse import validate_claude_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "custom-hook-missing"
        _claude_manifest(
            plugin,
            {
                "name": "custom-hook-missing",
                "hooks": "./config/events.json",
            },
        )
        _write(
            plugin / "config" / "events.json",
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": (
                                            "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check.py"
                                        ),
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
            + "\n",
        )
        issues = validate_claude_source(plugin)
        assert "missing-internal-ref" in _codes(issues), issues


def test_claude_declared_hooks_on_default_mcp_sidecar_still_checks_scripts():
    from plugin_targets.claude_code.source_parse import validate_claude_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "hooks-on-mcp-sidecar"
        _claude_manifest(
            plugin,
            {
                "name": "hooks-on-mcp-sidecar",
                "hooks": "./.mcp.json",
            },
        )
        _write(
            plugin / ".mcp.json",
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python3 ./scripts/check.py",
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
            + "\n",
        )
        issues = validate_claude_source(plugin)
        assert "missing-internal-ref" in _codes(issues), issues


def _version_invalid_identity(issues) -> bool:
    return any(
        issue.code == "invalid-identity" and "version" in issue.message.lower()
        for issue in issues
    )


def test_codex_source_blocks_non_semver_and_empty_author():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.codex.source_parse import validate_codex_source

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "codex-invalid-version"
        _codex_manifest(
            plugin,
            {
                "name": "codex-invalid-version",
                "version": "not-semver",
                "author": {},
            },
        )
        _write(plugin / "skills" / "s" / "SKILL.md", _skill_md("s"))
        issues = validate_codex_source(plugin)
        assert issues, "expected identity schema blocks"
        assert "invalid-identity" in _codes(issues), issues
        result = compile_plugin(
            plugin, repo, ("codex",), runner=Runner(), source_format="codex"
        )
        assert result.ok is False


def test_claude_source_blocks_non_semver_version():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.claude_code.source_parse import validate_claude_source

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "claude-invalid-version"
        _claude_manifest(
            plugin,
            {
                "name": "claude-invalid-version",
                "version": "not-semver",
                "author": {"name": "demo"},
            },
        )
        _write(plugin / "skills" / "s" / "SKILL.md", _skill_md("s"))
        issues = validate_claude_source(plugin)
        assert _version_invalid_identity(issues), issues
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        assert result.ok is False


def test_claude_source_blocks_empty_author():
    from plugin_targets.claude_code.source_parse import validate_claude_source

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "claude-empty-author"
        _claude_manifest(
            plugin,
            {
                "name": "claude-empty-author",
                "version": "1.0.0",
                "author": {},
            },
        )
        _write(plugin / "skills" / "s" / "SKILL.md", _skill_md("s"))
        issues = validate_claude_source(plugin)
        assert "invalid-identity" in _codes(issues), issues
        assert any("author" in issue.message.lower() for issue in issues), issues


def test_hook_outdir_dot_slash_dir_is_not_missing_internal_ref():
    from plugin_targets.claude_code.source_parse import validate_claude_source
    from plugin_targets.internal_refs import candidate_internal_relpaths

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "hook-outdir-dir"
        _claude_manifest(plugin, {"name": "hook-outdir-dir"})
        _write(
            plugin / "hooks" / "hooks.json",
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": (
                                            "python3 hooks/run.py "
                                            "--outdir ./build/reports --flag"
                                        ),
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
            + "\n",
        )
        _write(plugin / "hooks" / "run.py", "print('ok')\n")
        (plugin / "build" / "reports").mkdir(parents=True)
        issues = validate_claude_source(plugin)
        assert not any(
            issue.code == "missing-internal-ref"
            and "build/reports" in "{0}\n{1}".format(issue.path, issue.message)
            for issue in issues
        ), issues
        rels = candidate_internal_relpaths(
            plugin, {}, source_dialect="claude-code"
        )
        assert "hooks/run.py" in rels, rels
        assert "build/reports" not in rels, rels


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
