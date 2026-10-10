#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plugin compiler resource-closure path-array collection (P1-5)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.contracts import path_array_dotted_paths as _path_array_dotted_paths


def test_official_plugin_relative_accepts_plugin_root():
    from plugin_targets.internal_refs import official_plugin_relative

    assert official_plugin_relative("${PLUGIN_ROOT}/scripts/server.py") == "scripts/server.py"
    assert official_plugin_relative("${CLAUDE_PLUGIN_ROOT}/scripts/server.py") == "scripts/server.py"
    assert official_plugin_relative("${CURSOR_PLUGIN_ROOT}/scripts/server.py") == "scripts/server.py"
    assert official_plugin_relative("./scripts/server.py") == "scripts/server.py"
    assert official_plugin_relative("${CODEX_PLUGIN_ROOT}/scripts/server.py") is None


def test_relpaths_from_fields_screenshots_array():
    from plugin_targets.internal_refs import candidate_internal_relpaths

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        (plugin / "assets").mkdir()
        (plugin / "assets" / "a.png").write_bytes(b"a")
        (plugin / "assets" / "b.png").write_bytes(b"b")
        fields = {
            "interface": {
                "screenshots": ["./assets/a.png", "./assets/b.png"],
            }
        }
        rels = candidate_internal_relpaths(plugin, fields, source_dialect="cursor")
        assert "assets/a.png" in rels
        assert "assets/b.png" in rels


def test_relpaths_from_overlay_screenshots_not_filtered_by_source_dialect():
    from plugin_targets.internal_refs import candidate_internal_relpaths

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        (plugin / "assets").mkdir()
        (plugin / "assets" / "a.png").write_bytes(b"a")
        (plugin / "assets" / "b.png").write_bytes(b"b")
        overlay = {
            "interface": {
                "screenshots": ["./assets/a.png", "./assets/b.png"],
            }
        }
        rels = candidate_internal_relpaths(
            plugin,
            {"name": "demo"},
            source_dialect="cursor",
            extra_field_maps=(overlay,),
        )
        assert "assets/a.png" in rels
        assert "assets/b.png" in rels


def test_missing_screenshot_is_missing_internal_ref():
    from plugin_targets.internal_refs import internal_ref_issues

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        fields = {"interface": {"screenshots": ["./assets/missing.png"]}}
        issues = internal_ref_issues(plugin, fields, source_dialect="cursor")
        assert any(
            item.code == "missing-internal-ref" and item.severity == "block"
            for item in issues
        )


def test_declared_internal_resources_directory():
    from plugin_compiler.mcp import McpDocument
    from plugin_targets.internal_refs import declared_internal_resources

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        (plugin / "img").mkdir()
        (plugin / "img" / "a.png").write_bytes(b"a")
        (plugin / "img" / "b.png").write_bytes(b"b")
        mcp = McpDocument(
            capability_id="mcp:.mcp.json",
            source_path=".mcp.json",
            servers={"s": {"command": "node", "cwd": "./img"}},
        )
        resources = declared_internal_resources(
            plugin, {}, mcp_documents=(mcp,)
        )
        kinds = {(item.source_path, item.kind) for item in resources}
        assert ("img", "directory") in kinds or any(
            item.kind == "directory" and item.source_path.startswith("img")
            for item in resources
        )


def test_declared_directory_symlink_skipped():
    from plugin_compiler.mcp import McpDocument
    from plugin_targets.internal_refs import declared_internal_resources

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        real = plugin / "real-img"
        real.mkdir()
        (real / "a.png").write_bytes(b"a")
        (plugin / "img").symlink_to(real)
        mcp = McpDocument(
            capability_id="mcp:.mcp.json",
            source_path=".mcp.json",
            servers={"s": {"command": "node", "cwd": "./img"}},
        )
        resources = declared_internal_resources(
            plugin, {}, mcp_documents=(mcp,)
        )
        assert not any(item.source_path == "img" for item in resources)


def test_mcp_cwd_directory_declared():
    from plugin_compiler.mcp import McpDocument
    from plugin_targets.internal_refs import declared_internal_resources

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        (plugin / "tools").mkdir()
        (plugin / "tools" / "data.txt").write_text("x\n", encoding="utf-8")
        mcp = McpDocument(
            capability_id="mcp:.mcp.json",
            source_path=".mcp.json",
            servers={"s": {"command": "node", "cwd": "./tools"}},
        )
        resources = declared_internal_resources(
            plugin, {}, mcp_documents=(mcp,)
        )
        assert any(
            item.source_path == "tools" and item.kind == "directory"
            for item in resources
        )


def test_hook_script_parent_dir_closure():
    from plugin_compiler.hooks import HookDocument, HookHandler
    from plugin_targets.internal_refs import declared_internal_resources

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        script_dir = plugin / "skills" / "notify" / "scripts"
        script_dir.mkdir(parents=True)
        (script_dir / "run.py").write_text("print(1)\n", encoding="utf-8")
        (script_dir / "helper.py").write_text("print(2)\n", encoding="utf-8")
        hook = HookDocument(
            capability_id="hook:hooks/hooks.json",
            source_path="hooks/hooks.json",
            handlers=(
                HookHandler(
                    event_name="stop",
                    command="python3 skills/notify/scripts/run.py",
                    raw={},
                ),
            ),
            script_relpaths=("skills/notify/scripts/run.py",),
        )
        resources = declared_internal_resources(plugin, {}, hooks=(hook,))
        assert any(
            item.source_path == "skills/notify/scripts" and item.kind == "directory"
            for item in resources
        )


def test_hook_script_one_segment_parent_not_added():
    from plugin_compiler.hooks import HookDocument, HookHandler
    from plugin_targets.internal_refs import declared_internal_resources

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        (plugin / "hooks").mkdir()
        (plugin / "hooks" / "check.py").write_text("print(1)\n", encoding="utf-8")
        hook = HookDocument(
            capability_id="hook:hooks/hooks.json",
            source_path="hooks/hooks.json",
            handlers=(
                HookHandler(
                    event_name="stop",
                    command="python3 hooks/check.py",
                    raw={},
                ),
            ),
            script_relpaths=("hooks/check.py",),
        )
        resources = declared_internal_resources(plugin, {}, hooks=(hook,))
        assert not any(item.source_path in {".", ""} for item in resources)
        assert not any(
            item.source_path == "hooks" and item.kind == "directory"
            for item in resources
        )


def test_staging_overlay_screenshots_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.codex.stage import stage_codex
    from plugin_targets.cursor.source_parse import parse_cursor_plugin

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"name": "demo", "description": "d", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (plugin / "assets").mkdir()
        (plugin / "assets" / "a.png").write_bytes(b"a")
        (plugin / "assets" / "b.png").write_bytes(b"b")
        overlay = plugin / "packaging" / "codex-plugin.json"
        overlay.parent.mkdir(parents=True)
        overlay.write_text(
            json.dumps({
                "description": "codex",
                "interface": {
                    "displayName": "Demo",
                    "screenshots": ["./assets/a.png", "./assets/b.png"],
                },
            }),
            encoding="utf-8",
        )
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_codex(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "assets" / "a.png").is_file()
        assert (staged / "assets" / "b.png").is_file()


def test_staging_hook_script_siblings_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"name": "demo", "description": "d"}),
            encoding="utf-8",
        )
        script_dir = plugin / "skills" / "notify" / "scripts"
        script_dir.mkdir(parents=True)
        (script_dir / "run.py").write_text("print(1)\n", encoding="utf-8")
        (script_dir / "helper.py").write_text("print(2)\n", encoding="utf-8")
        (plugin / "hooks").mkdir()
        (plugin / "hooks" / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "stop": [{
                        "command": "python3 ./skills/notify/scripts/run.py",
                    }]
                }
            }),
            encoding="utf-8",
        )
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert (staged / "skills" / "notify" / "scripts" / "helper.py").is_file()


def test_staging_hook_orphan_not_copied_without_script_relpath():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
            }),
            encoding="utf-8",
        )
        (plugin / "hooks").mkdir()
        (plugin / "hooks" / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "stop": [{"command": "python3 hooks/notify.py"}],
                }
            }),
            encoding="utf-8",
        )
        (plugin / "hooks" / "notify.py").write_text("print(1)\n", encoding="utf-8")
        (plugin / "hooks" / "orphan.py").write_text("print(0)\n", encoding="utf-8")
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert (staged / "hooks" / "notify.py").is_file()
        assert not (staged / "hooks" / "orphan.py").exists()


def test_staging_mcp_cwd_files_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.claude_code.source_parse import parse_claude_plugin
    from plugin_targets.claude_code.stage import stage_claude

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"name": "demo", "description": "d"}),
            encoding="utf-8",
        )
        (plugin / "tools").mkdir()
        (plugin / "tools" / "data.txt").write_text("x\n", encoding="utf-8")
        (plugin / ".mcp.json").write_text(
            json.dumps({
                "mcpServers": {
                    "s": {"command": "node", "args": ["index.js"], "cwd": "./tools"}
                }
            }),
            encoding="utf-8",
        )
        (plugin / "index.js").write_text("console.log(1)\n", encoding="utf-8")
        parsed = parse_claude_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_claude(parsed, layout, issues)
        assert (staged / "tools" / "data.txt").is_file()


def test_staging_mcp_cwd_directory_images_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.claude_code.source_parse import parse_claude_plugin
    from plugin_targets.claude_code.stage import stage_claude

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"name": "demo", "description": "d"}),
            encoding="utf-8",
        )
        (plugin / "img").mkdir()
        (plugin / "img" / "a.png").write_bytes(b"a")
        (plugin / "img" / "b.png").write_bytes(b"b")
        (plugin / ".mcp.json").write_text(
            json.dumps({
                "mcpServers": {
                    "s": {"command": "node", "args": ["index.js"], "cwd": "./img"}
                }
            }),
            encoding="utf-8",
        )
        (plugin / "index.js").write_text("console.log(1)\n", encoding="utf-8")
        parsed = parse_claude_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_claude(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "img" / "a.png").is_file()
        assert (staged / "img" / "b.png").is_file()


def test_staging_agent_sidecar_is_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "agents": "./agents/",
            }),
            encoding="utf-8",
        )
        (plugin / "agents").mkdir()
        (plugin / "agents" / "reviewer.md").write_text(
            "---\nname: reviewer\ndescription: Review\n---\n# Reviewer\n",
            encoding="utf-8",
        )
        (plugin / "agents" / "notes.txt").write_text("n\n", encoding="utf-8")
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "agents" / "reviewer.md").is_file()
        assert (staged / "agents" / "notes.txt").is_file()


_CONVERTIBLE_AGENT = """---
name: review-helper
description: Review a diff
---
# Review helper

Read the diff and comment.
"""


def test_codex_agent_sidecar_excluded_while_cursor_keeps_it():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor
    from plugin_targets.codex.stage import stage_codex

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "agents": "./agents/",
            }),
            encoding="utf-8",
        )
        (plugin / "agents").mkdir()
        (plugin / "agents" / "review-helper.md").write_text(
            _CONVERTIBLE_AGENT, encoding="utf-8"
        )
        (plugin / "agents" / "notes.txt").write_text("n\n", encoding="utf-8")
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        cursor_staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (cursor_staged / "agents" / "notes.txt").is_file()

        codex_staged = stage_codex(parsed, layout)
        skill = codex_staged / "skills" / "review-helper" / "SKILL.md"
        assert skill.is_file()
        assert "Read the diff" in skill.read_text(encoding="utf-8")
        assert not (codex_staged / "agents").exists()
        assert not any(
            path.name == "notes.txt" and "agents" in path.parts
            for path in codex_staged.rglob("notes.txt")
        )


def test_staging_custom_command_root_sidecar_remaps():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "commands": "./custom-cmds/",
            }),
            encoding="utf-8",
        )
        (plugin / "custom-cmds").mkdir()
        (plugin / "custom-cmds" / "foo.md").write_text(
            "# Foo\n\nDo foo.\n", encoding="utf-8"
        )
        (plugin / "custom-cmds" / "helper.py").write_text(
            "print(2)\n", encoding="utf-8"
        )
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "commands" / "foo.md").is_file()
        assert (staged / "commands" / "helper.py").is_file()
        assert not (staged / "custom-cmds" / "helper.py").exists()


def test_staging_undeclared_scripts_foo_not_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "commands": "./commands/",
            }),
            encoding="utf-8",
        )
        skill_dir = plugin / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill\n---\n"
            "# Demo\n\n"
            "Run the helper:\n\n"
            "```bash\n"
            "python3 scripts/foo.py\n"
            "```\n",
            encoding="utf-8",
        )
        (plugin / "scripts").mkdir()
        (plugin / "scripts" / "foo.py").write_text(
            "print('undeclared')\n", encoding="utf-8"
        )
        (plugin / "commands").mkdir()
        (plugin / "commands" / "deploy.md").write_text(
            "# Deploy\n\nRun it.\n", encoding="utf-8"
        )
        (plugin / "commands" / "helper.py").write_text(
            "print(1)\n", encoding="utf-8"
        )
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "skills" / "demo" / "SKILL.md").is_file()
        assert "python3 scripts/foo.py" in (
            staged / "skills" / "demo" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert not (staged / "scripts" / "foo.py").exists()
        assert (staged / "commands" / "deploy.md").is_file()
        assert (staged / "commands" / "helper.py").is_file()


def test_staging_command_sidecar_helper_is_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "commands": "./commands/",
            }),
            encoding="utf-8",
        )
        (plugin / "commands").mkdir()
        (plugin / "commands" / "deploy.md").write_text(
            "# Deploy\n\nRun it.\n", encoding="utf-8"
        )
        (plugin / "commands" / "helper.py").write_text(
            "print(1)\n", encoding="utf-8"
        )
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "commands" / "deploy.md").is_file()
        assert (staged / "commands" / "helper.py").is_file()
        helper = (staged / "commands" / "helper.py").read_text(encoding="utf-8")
        assert helper == "print(1)\n"


def _stage_target(plugin_dir, target, layout, collected_issues=None):
    from dataclasses import replace

    from plugin_compiler.model import Target
    from plugin_compiler.overrides import load_override_config
    from plugin_targets.claude_code.stage import stage_claude
    from plugin_targets.codex.stage import stage_codex
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    plugin_dir = Path(plugin_dir).resolve()
    config = load_override_config(plugin_dir)
    parsed = replace(parse_cursor_plugin(plugin_dir), override_config=config)
    if target is Target.CURSOR:
        return stage_cursor(parsed, layout, collected_issues)
    if target is Target.CODEX:
        return stage_codex(parsed, layout, collected_issues)
    if target is Target.CLAUDE_CODE:
        return stage_claude(parsed, layout, collected_issues)
    raise ValueError("unsupported target: {0!r}".format(target))


def test_plugin_json_resources_file_is_copied_and_stripped_from_manifest():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        (plugin / "data").mkdir()
        (plugin / "data" / "a.txt").write_text("a\n", encoding="utf-8")
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "resources": ["data/a.txt"],
            }),
            encoding="utf-8",
        )
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "data" / "a.txt").is_file()
        written = json.loads(
            (staged / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert "resources" not in written


def test_plugin_json_resources_not_dropped_manifest_field():
    from plugin_compiler.model import Target
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.manifest import dropped_manifest_field_issues
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        (plugin / "data").mkdir()
        (plugin / "data" / "a.txt").write_text("a\n", encoding="utf-8")
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "resources": ["data/a.txt"],
            }),
            encoding="utf-8",
        )
        parsed = parse_cursor_plugin(plugin)
        dropped = dropped_manifest_field_issues(parsed)
        assert not any(
            item.code == "dropped-manifest-field" and "resources" in item.message
            for item in dropped
        ), dropped
        assert any(
            item.code == "translated-manifest-declaration" and "resources" in item.message
            for item in dropped
        ), dropped
        layout = BuildLayout(root / "out")
        for target in (Target.CURSOR, Target.CLAUDE_CODE, Target.CODEX):
            issues: list = []
            staged = _stage_target(plugin, target, layout, collected_issues=issues)
            assert not any(
                item.code == "dropped-manifest-field" and "resources" in item.message
                for item in issues
            ), (target, issues)
            assert (staged / "data" / "a.txt").is_file()


def test_plugin_json_resources_copied_on_claude_and_codex_and_stripped():
    from plugin_compiler.model import Target
    from plugin_compiler.paths import BuildLayout

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        (plugin / "data").mkdir()
        (plugin / "data" / "a.txt").write_text("a\n", encoding="utf-8")
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "resources": ["data/a.txt"],
            }),
            encoding="utf-8",
        )
        layout = BuildLayout(root / "out")
        for target, rel in (
            (Target.CLAUDE_CODE, ".claude-plugin/plugin.json"),
            (Target.CODEX, ".codex-plugin/plugin.json"),
        ):
            issues: list = []
            staged = _stage_target(plugin, target, layout, collected_issues=issues)
            assert not any(item.severity == "block" for item in issues), issues
            assert (staged / "data" / "a.txt").is_file()
            written = json.loads((staged / rel).read_text(encoding="utf-8"))
            assert "resources" not in written


def test_existing_node_modules_and_package_json_are_copied():
    from plugin_compiler.paths import BuildLayout
    from plugin_compiler.model import Target
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
            }),
            encoding="utf-8",
        )
        (plugin / "package.json").write_text("{\"name\":\"demo\"}\n", encoding="utf-8")
        (plugin / "package-lock.json").write_text("{}\n", encoding="utf-8")
        pkg = plugin / "node_modules" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "package.json").is_file()
        assert (staged / "package-lock.json").is_file()
        assert (staged / "node_modules" / "pkg" / "index.js").is_file()
        for target in (Target.CODEX, Target.CLAUDE_CODE):
            more: list = []
            other = _stage_target(plugin, target, layout, collected_issues=more)
            assert not any(item.severity == "block" for item in more), more
            assert (other / "package.json").is_file()
            assert (other / "package-lock.json").is_file()
            assert (other / "node_modules" / "pkg" / "index.js").is_file()


def test_package_json_without_node_modules_does_not_invent_tree():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
            }),
            encoding="utf-8",
        )
        (plugin / "package.json").write_text("{\"name\":\"demo\"}\n", encoding="utf-8")
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        staged = stage_cursor(parsed, layout, issues)
        assert not any(item.severity == "block" for item in issues), issues
        assert (staged / "package.json").is_file()
        assert not (staged / "node_modules").exists()


def test_plugin_json_resources_missing_file_is_block():
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.cursor.source_parse import parse_cursor_plugin
    from plugin_targets.cursor.stage import stage_cursor

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "src" / "demo"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({
                "name": "demo",
                "description": "d",
                "version": "1.0.0",
                "resources": ["data/missing.txt"],
            }),
            encoding="utf-8",
        )
        parsed = parse_cursor_plugin(plugin)
        layout = BuildLayout(root / "out")
        issues: list = []
        stage_cursor(parsed, layout, issues)
        assert any(
            item.code == "missing-internal-ref" and item.severity == "block"
            for item in issues
        ), issues


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
