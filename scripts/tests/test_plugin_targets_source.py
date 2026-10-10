# -*- coding: utf-8 -*-
"""来源 detect 仲裁。运行：python3 scripts/tests/test_plugin_targets_source.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _plugin(root: Path, *manifest_relatives: str) -> Path:
    plugin = root / "p"
    plugin.mkdir()
    for rel in manifest_relatives:
        path = plugin / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"name": "demo", "version": "1.0.0"}\n', encoding="utf-8")
    return plugin


def test_auto_detects_cursor_only():
    from plugin_targets.source import resolve_source_adapter

    with TemporaryDirectory() as tmp:
        plugin = _plugin(Path(tmp), ".cursor-plugin/plugin.json")
        adapter = resolve_source_adapter(plugin, "auto")
        assert adapter.id == "cursor"


def test_auto_detects_codex_only():
    from plugin_targets.source import resolve_source_adapter

    with TemporaryDirectory() as tmp:
        plugin = _plugin(Path(tmp), ".codex-plugin/plugin.json")
        adapter = resolve_source_adapter(plugin, "auto")
        assert adapter.id == "codex"


def test_auto_zero_match_raises():
    from plugin_targets.source import SourceResolutionError, resolve_source_adapter

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "empty"
        plugin.mkdir()
        raised = False
        try:
            resolve_source_adapter(plugin, "auto")
        except SourceResolutionError:
            raised = True
        assert raised


def test_auto_ambiguous_two_manifests_raises():
    from plugin_targets.source import SourceResolutionError, resolve_source_adapter

    with TemporaryDirectory() as tmp:
        plugin = _plugin(
            Path(tmp),
            ".cursor-plugin/plugin.json",
            ".codex-plugin/plugin.json",
        )
        raised = False
        try:
            resolve_source_adapter(plugin, "auto")
        except SourceResolutionError:
            raised = True
        assert raised


def test_explicit_format_mismatch_raises():
    from plugin_targets.source import SourceResolutionError, resolve_source_adapter

    with TemporaryDirectory() as tmp:
        plugin = _plugin(Path(tmp), ".codex-plugin/plugin.json")
        raised = False
        try:
            resolve_source_adapter(plugin, "cursor")
        except SourceResolutionError:
            raised = True
        assert raised


def test_cursor_parse_sets_source_dialect():
    from plugin_targets.cursor.adapter import get_adapter

    with TemporaryDirectory() as tmp:
        plugin = _plugin(Path(tmp), ".cursor-plugin/plugin.json")
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n# S\n",
            encoding="utf-8",
        )
        parsed = get_adapter().parse(plugin)
        assert parsed.source_dialect == "cursor"
        assert parsed.plugin_name == "demo"
        assert parsed.skills


def test_cursor_parse_loads_codex_overlay_into_model():
    import json

    from plugin_targets.cursor.adapter import get_adapter

    with TemporaryDirectory() as tmp:
        plugin = _plugin(Path(tmp), ".cursor-plugin/plugin.json")
        overlay_path = plugin / "packaging" / "codex-plugin.json"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text(
            json.dumps(
                {
                    "description": "Codex overlay",
                    "interface": {"displayName": "FromOverlay"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        parsed = get_adapter().parse(plugin)
        codex_overlays = [
            item for item in parsed.target_overlays if item.target_id == "codex"
        ]
        assert len(codex_overlays) == 1
        assert codex_overlays[0].fields["interface"]["displayName"] == "FromOverlay"


def test_codex_manifest_from_parsed_uses_identity_not_cursor_file():
    from plugin_compiler.manifests import PluginIdentity
    from plugin_targets.codex.manifest import build_codex_manifest
    from plugin_targets.spi import (
        ParsedPlugin,
        SourceExtension,
        TargetOverlay,
    )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin_dir = root / "no-cursor-manifest"
        plugin_dir.mkdir()
        (plugin_dir / "skills").mkdir()
        parsed = ParsedPlugin(
            plugin_dir=plugin_dir,
            plugin_name="demo",
            source_dialect="cursor",
            identity=PluginIdentity(
                name="demo",
                version="1.0.0",
                description="from-model",
                author=None,
            ),
            source_extension=SourceExtension(dialect="cursor", fields={}),
            capabilities=(),
            skills=(),
            commands=(),
            rules=(),
            hooks=(),
            agents=(),
            workflows=(),
            mcp_documents=(),
            connectors=(),
            lsp_documents=(),
            output_styles=(),
            themes=(),
            monitors=(),
            override_config=None,
            target_overlays=(
                TargetOverlay(
                    target_id="codex",
                    fields={
                        "description": "overlay-desc",
                        "interface": {"displayName": "FromOverlay"},
                    },
                ),
            ),
        )
        assert not (plugin_dir / ".cursor-plugin" / "plugin.json").exists()
        result = build_codex_manifest(parsed, tree=plugin_dir)
        assert result["name"] == "demo"
        assert result["interface"]["displayName"] == "FromOverlay"
        assert result["description"] == "overlay-desc"
        assert result["skills"] == "./skills/"


def test_source_overlay_conflict_emits_block_code_not_stage_failed():
    import json

    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = repo_root / "plugins" / "overlay-conflict"
        plugin.mkdir(parents=True)
        (plugin / ".cursor-plugin").mkdir()
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "overlay-conflict",
                    "version": "1.0.0",
                    "logo": "./from-bag.png",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (plugin / "from-bag.png").write_text("bag\n", encoding="utf-8")
        (plugin / "from-overlay.png").write_text("overlay\n", encoding="utf-8")
        packaging = plugin / "packaging"
        packaging.mkdir()
        (packaging / "cursor-plugin.json").write_text(
            json.dumps({"logo": "./from-overlay.png"}) + "\n",
            encoding="utf-8",
        )
        result = compile_plugin(
            plugin,
            repo_root,
            ("cursor",),
            runner=Runner(),
        )
        report = result.validation["cursor"]
        assert report.ok is False
        assert any(
            issue.code == "source-overlay-conflict" for issue in report.issues
        ), [(issue.code, issue.message) for issue in report.issues]
        assert not any(
            issue.code == "stage-failed" for issue in report.issues
        ), [(issue.code, issue.message) for issue in report.issues]


def test_source_overlay_same_value_still_succeeds():
    import json

    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = repo_root / "plugins" / "overlay-same"
        plugin.mkdir(parents=True)
        (plugin / ".cursor-plugin").mkdir()
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "overlay-same",
                    "version": "1.0.0",
                    "logo": "./same.png",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (plugin / "same.png").write_text("same\n", encoding="utf-8")
        packaging = plugin / "packaging"
        packaging.mkdir()
        (packaging / "cursor-plugin.json").write_text(
            json.dumps({"logo": "./same.png"}) + "\n",
            encoding="utf-8",
        )
        result = compile_plugin(
            plugin,
            repo_root,
            ("cursor",),
            runner=Runner(),
        )
        assert result.validation["cursor"].ok
        assert not any(
            issue.code == "source-overlay-conflict"
            for issue in result.validation["cursor"].issues
        )


def test_own_target_overlay_fills_missing_logo():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.cursor.source_parse import parse_cursor_plugin

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "overlay-fill"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "overlay-fill", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n", encoding="utf-8"
        )
        (plugin / "assets").mkdir()
        (plugin / "assets" / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
        (plugin / "packaging").mkdir()
        (plugin / "packaging" / "cursor-plugin.json").write_text(
            json.dumps({"logo": "./assets/logo.svg"}) + "\n", encoding="utf-8"
        )
        parsed = parse_cursor_plugin(plugin)
        assert any(item.target_id == "cursor" for item in parsed.target_overlays)
        result = compile_plugin(plugin, repo, ("cursor",), runner=Runner())
        assert result.ok
        manifest = json.loads(
            (
                Path(result.published_roots["cursor"])
                / ".cursor-plugin"
                / "plugin.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest.get("logo") == "./assets/logo.svg"


def test_own_target_overlay_logo_file_is_published():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "overlay-fill"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "overlay-fill", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n", encoding="utf-8"
        )
        (plugin / "assets").mkdir()
        (plugin / "assets" / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
        (plugin / "packaging").mkdir()
        (plugin / "packaging" / "cursor-plugin.json").write_text(
            json.dumps({"logo": "./assets/logo.svg"}) + "\n", encoding="utf-8"
        )
        result = compile_plugin(plugin, repo, ("cursor",), runner=Runner())
        assert result.ok
        published = Path(result.published_roots["cursor"])
        manifest = json.loads(
            (published / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert manifest.get("logo") == "./assets/logo.svg"
        assert (published / "assets" / "logo.svg").is_file()


def test_overlay_missing_logo_blocks_compile():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "overlay-missing"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "overlay-missing", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n", encoding="utf-8"
        )
        (plugin / "packaging").mkdir()
        (plugin / "packaging" / "cursor-plugin.json").write_text(
            json.dumps({"logo": "./assets/logo.svg"}) + "\n", encoding="utf-8"
        )
        result = compile_plugin(plugin, repo, ("cursor",), runner=Runner())
        assert result.ok is False
        assert not result.published_roots.get("cursor")
        codes = {issue.code for issue in result.validation["cursor"].issues}
        assert "missing-internal-ref" in codes or "path-escape" in codes


def test_overlay_escape_logo_blocks():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "overlay-escape"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "overlay-escape", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n", encoding="utf-8"
        )
        (plugin / "packaging").mkdir()
        (plugin / "packaging" / "cursor-plugin.json").write_text(
            json.dumps({"logo": "../outside.svg"}) + "\n", encoding="utf-8"
        )
        result = compile_plugin(plugin, repo, ("cursor",), runner=Runner())
        assert result.ok is False
        assert not result.published_roots.get("cursor")
        assert any(
            issue.code in {"path-escape", "missing-internal-ref"}
            for issue in result.validation["cursor"].issues
        )


def test_overlay_symlink_logo_blocks():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "overlay-link"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "overlay-link", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n", encoding="utf-8"
        )
        (plugin / "assets").mkdir()
        target = plugin / "assets" / "real.svg"
        target.write_text("<svg/>\n", encoding="utf-8")
        (plugin / "assets" / "logo.svg").symlink_to(target)
        (plugin / "packaging").mkdir()
        (plugin / "packaging" / "cursor-plugin.json").write_text(
            json.dumps({"logo": "./assets/logo.svg"}) + "\n", encoding="utf-8"
        )
        result = compile_plugin(plugin, repo, ("cursor",), runner=Runner())
        assert result.ok is False
        assert not result.published_roots.get("cursor")
        assert any(
            issue.code == "path-escape"
            for issue in result.validation["cursor"].issues
        )


def test_codex_overlay_interface_logo_is_published():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(
                0, "Plugin validation passed: codex-overlay-logo\n", ""
            )

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "codex-overlay-logo"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "codex-overlay-logo", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n", encoding="utf-8"
        )
        (plugin / "assets").mkdir()
        (plugin / "assets" / "logo.png").write_text("png\n", encoding="utf-8")
        (plugin / "packaging").mkdir()
        (plugin / "packaging" / "codex-plugin.json").write_text(
            json.dumps(
                {
                    "description": "codex",
                    "interface": {
                        "displayName": "Demo",
                        "shortDescription": "s",
                        "longDescription": "l",
                        "developerName": "d",
                        "category": "Productivity",
                        "capabilities": ["Read"],
                        "defaultPrompt": ["hello"],
                        "logo": "./assets/logo.png",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = compile_plugin(plugin, repo, ("codex",), runner=Runner())
        assert result.ok, [
            (i.code, i.message) for i in result.validation["codex"].issues
        ]
        published = Path(result.published_roots["codex"])
        assert (published / "assets" / "logo.png").is_file()


def test_claude_overlay_asset_is_published():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "claude-overlay-logo"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "claude-overlay-logo", "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "skills" / "s").mkdir(parents=True)
        (plugin / "skills" / "s" / "SKILL.md").write_text(
            "---\nname: s\ndescription: d\n---\n", encoding="utf-8"
        )
        (plugin / "assets").mkdir()
        (plugin / "assets" / "mark.svg").write_text("<svg/>\n", encoding="utf-8")
        (plugin / "packaging").mkdir()
        # Claude manifest rejects top-level logo; option 1 unions other-host
        # overlay file refs (cursor packaging logo) into the closure, while
        # Claude overlay writes a real whitelist field (displayName).
        (plugin / "packaging" / "cursor-plugin.json").write_text(
            json.dumps({"logo": "./assets/mark.svg"}) + "\n",
            encoding="utf-8",
        )
        (plugin / "packaging" / "claude-plugin.json").write_text(
            json.dumps({"displayName": "Claude Overlay Mark"}) + "\n",
            encoding="utf-8",
        )
        result = compile_plugin(plugin, repo, ("claude-code",), runner=Runner())
        assert result.ok, [
            (i.code, i.message) for i in result.validation["claude-code"].issues
        ]
        published = Path(result.published_roots["claude-code"])
        assert (published / "assets" / "mark.svg").is_file()
        manifest = json.loads(
            (published / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert manifest.get("displayName") == "Claude Overlay Mark"


def test_codex_parse_expands_commands_directory():
    import json

    from plugin_targets.codex.adapter import get_adapter

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "codex-commands"
        plugin.mkdir()
        (plugin / ".codex-plugin").mkdir()
        (plugin / "custom-commands").mkdir()
        (plugin / "custom-commands" / "hello.md").write_text(
            "---\nname: hello\ndescription: hi\n---\n# Hello\n",
            encoding="utf-8",
        )
        (plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "codex-commands",
                    "version": "0.0.1",
                    "commands": "./custom-commands/",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        parsed = get_adapter().parse(plugin)
        assert parsed.commands, "Codex parse must expand commands directory"


def test_codex_parse_loads_claude_packaging_overlay():
    import json

    from plugin_targets.codex.adapter import get_adapter

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "codex-with-claude-overlay"
        plugin.mkdir()
        (plugin / ".codex-plugin").mkdir()
        (plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "codex-with-claude-overlay", "version": "0.0.1"})
            + "\n",
            encoding="utf-8",
        )
        packaging = plugin / "packaging"
        packaging.mkdir()
        (packaging / "claude-plugin.json").write_text(
            json.dumps({"displayName": "From Packaging"}) + "\n",
            encoding="utf-8",
        )
        parsed = get_adapter().parse(plugin)
        claude_overlays = [
            item
            for item in parsed.target_overlays
            if item.target_id == "claude-code"
        ]
        assert len(claude_overlays) == 1
        assert claude_overlays[0].fields["displayName"] == "From Packaging"


def test_codex_source_claude_overlay_reaches_claude_manifest():
    import json

    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            cmd = tuple(str(item) for item in command)
            if cmd[:3] == ("claude", "plugin", "validate"):
                return CommandResult(0, "Validation passed\n", "")
            return CommandResult(0, "ok\n", "")

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = repo_root / "plugins" / "codex-claude-overlay"
        plugin.mkdir(parents=True)
        (plugin / ".codex-plugin").mkdir()
        (plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "codex-claude-overlay",
                    "version": "0.0.1",
                    "skills": "./custom-skills/",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        skill = plugin / "custom-skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: d\n---\n# Demo\n",
            encoding="utf-8",
        )
        packaging = plugin / "packaging"
        packaging.mkdir()
        (packaging / "claude-plugin.json").write_text(
            json.dumps({"displayName": "From Packaging"}) + "\n",
            encoding="utf-8",
        )
        result = compile_plugin(
            plugin,
            repo_root,
            ("claude-code",),
            runner=Runner(),
        )
        assert result.validation["claude-code"].ok, [
            (i.code, i.message) for i in result.validation["claude-code"].issues
        ]
        manifest = json.loads(
            (
                Path(result.published_roots["claude-code"])
                / ".claude-plugin"
                / "plugin.json"
            ).read_text(encoding="utf-8")
        )
        assert manifest["displayName"] == "From Packaging"


def test_claude_default_discovers_official_paths_including_themes_and_monitors():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.claude_code.source_parse import parse_claude_plugin

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "claude-defaults"
        plugin.mkdir(parents=True)
        _write(
            plugin / ".claude-plugin" / "plugin.json",
            json.dumps({"name": "claude-defaults", "version": "1.0.0"}) + "\n",
        )
        _write(plugin / "commands" / "hello.md", "# hello\n")
        _write(plugin / "agents" / "bot.md", "# bot\n")
        _write(plugin / "hooks" / "hooks.json", json.dumps({"hooks": {}}) + "\n")
        _write(
            plugin / ".mcp.json",
            json.dumps({"mcpServers": {"s": {"command": "x"}}}) + "\n",
        )
        _write(
            plugin / ".lsp.json",
            json.dumps({"python": {"command": "demo-lsp"}}) + "\n",
        )
        _write(plugin / "output-styles" / "plain.md", "# plain\n")
        _write(
            plugin / "themes" / "dark.json",
            json.dumps({"name": "dark", "colors": {"bg": "#000"}}) + "\n",
        )
        _write(
            plugin / "monitors" / "monitors.json",
            json.dumps({"name": "cpu", "interval": 5}) + "\n",
        )
        parsed = parse_claude_plugin(plugin)
        kinds = {cap.kind.value for cap in parsed.capabilities}
        assert parsed.commands, parsed.commands
        assert parsed.agents, parsed.agents
        assert parsed.hooks, parsed.hooks
        assert parsed.mcp_documents, parsed.mcp_documents
        assert parsed.lsp_documents, parsed.lsp_documents
        assert parsed.output_styles, parsed.output_styles
        assert "theme" in kinds, kinds
        assert "monitor" in kinds, kinds
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
        assert (published / "commands" / "hello.md").is_file(), list(
            published.rglob("*")
        )
        assert (published / "agents" / "bot.md").is_file()
        assert (published / "hooks" / "hooks.json").is_file()
        assert (published / ".mcp.json").is_file()
        assert (published / ".lsp.json").is_file()
        assert (published / "output-styles" / "plain.md").is_file()
        assert (published / "themes" / "dark.json").is_file()
        assert (published / "monitors" / "monitors.json").is_file()


def test_claude_inline_hook_plugin_root_script_is_published():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.claude_code.source_parse import parse_claude_plugin

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "claude-hook-script"
        plugin.mkdir(parents=True)
        _write(
            plugin / ".claude-plugin" / "plugin.json",
            json.dumps(
                {
                    "name": "claude-hook-script",
                    "version": "1.0.0",
                    "hooks": {
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
                    },
                }
            )
            + "\n",
        )
        _write(plugin / "scripts" / "check.py", "print('ok')\n")
        parsed = parse_claude_plugin(plugin)
        assert any(
            "scripts/check.py" in document.script_relpaths for document in parsed.hooks
        ), [document.script_relpaths for document in parsed.hooks]
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
        assert (published / "scripts" / "check.py").is_file()


def test_claude_default_theme_notes_md_does_not_scan_fail():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.claude_code.source_parse import parse_claude_plugin

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "claude-theme-notes"
        plugin.mkdir(parents=True)
        _write(
            plugin / ".claude-plugin" / "plugin.json",
            json.dumps({"name": "claude-theme-notes", "version": "1.0.0"}) + "\n",
        )
        _write(
            plugin / "themes" / "dark.json",
            json.dumps({"name": "dark", "colors": {"bg": "#000"}}) + "\n",
        )
        _write(plugin / "themes" / "NOTES.md", "# notes, not a theme\n")
        parsed = parse_claude_plugin(plugin)
        theme_paths = [document.source_path for document in parsed.themes]
        assert "themes/dark.json" in theme_paths, theme_paths
        assert "themes/NOTES.md" not in theme_paths, theme_paths
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        scan_failed = [
            (issue.code, issue.message)
            for report in result.validation.values()
            for issue in report.issues
            if issue.code == "scan-failed"
        ]
        assert not scan_failed, scan_failed
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }


def test_claude_default_command_icon_png_does_not_scan_fail():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.claude_code.source_parse import parse_claude_plugin

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "claude-command-icon"
        plugin.mkdir(parents=True)
        _write(
            plugin / ".claude-plugin" / "plugin.json",
            json.dumps({"name": "claude-command-icon", "version": "1.0.0"}) + "\n",
        )
        _write(plugin / "commands" / "hello.md", "# hello\n")
        icon = plugin / "commands" / "icon.png"
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_bytes(b"\xff\xfe\x00\x01not-utf8")
        parsed = parse_claude_plugin(plugin)
        command_paths = [document.source_path for document in parsed.commands]
        assert "commands/hello.md" in command_paths, command_paths
        assert "commands/icon.png" not in command_paths, command_paths
        result = compile_plugin(
            plugin,
            repo,
            ("claude-code",),
            runner=Runner(),
            source_format="claude-code",
        )
        scan_failed = [
            (issue.code, issue.message)
            for report in result.validation.values()
            for issue in report.issues
            if issue.code == "scan-failed"
        ]
        assert not scan_failed, scan_failed
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }


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
