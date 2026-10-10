#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""来源×目标最小交叉合同。运行：python3 scripts/tests/test_plugin_targets_source_matrix.py"""
from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.validation import CommandResult

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "origin_plugins"
_CODEX_ORIGIN = _FIXTURES / "codex-origin-plugin"
_CLAUDE_ORIGIN = _FIXTURES / "claude-origin-plugin"
_NATIVE_MANIFEST = {
    "cursor": ".cursor-plugin/plugin.json",
    "codex": ".codex-plugin/plugin.json",
    "claude-code": ".claude-plugin/plugin.json",
}


def _load_origin_interface() -> dict:
    raw = json.loads(
        (_CODEX_ORIGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    return raw["interface"]


_FIXTURE_REQUIRED = (
    ".codex-plugin/plugin.json",
    "custom-skills/codex-origin/SKILL.md",
    "skills/default-skill/SKILL.md",
    "hooks/hooks.json",
    "hooks/notify.py",
    ".mcp.json",
    "scripts/server.py",
)


def _copy_codex_origin(repo_root: Path) -> Path:
    destination = repo_root / "plugins" / "codex-origin-plugin"
    shutil.copytree(_CODEX_ORIGIN, destination)
    return destination


def test_codex_origin_fixture_is_not_markdown_only():
    missing = [rel for rel in _FIXTURE_REQUIRED if not (_CODEX_ORIGIN / rel).is_file()]
    assert not missing, missing
    manifest = json.loads(
        (_CODEX_ORIGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert "interface" in manifest
    assert "custom-skills" in str(manifest.get("skills", ""))
    hooks = (_CODEX_ORIGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert "SessionStart" in hooks
    assert "${PLUGIN_ROOT}/hooks/notify.py" in hooks
    mcp = (_CODEX_ORIGIN / ".mcp.json").read_text(encoding="utf-8")
    assert "${PLUGIN_ROOT}/scripts/server.py" in mcp


def _copy_claude_origin(repo_root: Path) -> Path:
    destination = repo_root / "plugins" / "claude-origin-plugin"
    shutil.copytree(_CLAUDE_ORIGIN, destination)
    return destination


@contextmanager
def _codex_validator_env(repo_root: Path):
    validator = repo_root / "validate_plugin.py"
    validator.write_text("# matrix synthetic validator\n", encoding="utf-8")
    previous = os.environ.get("CODEX_PLUGIN_VALIDATOR")
    os.environ["CODEX_PLUGIN_VALIDATOR"] = str(validator)
    try:
        yield validator
    finally:
        if previous is None:
            os.environ.pop("CODEX_PLUGIN_VALIDATOR", None)
        else:
            os.environ["CODEX_PLUGIN_VALIDATOR"] = previous


class _OfficialPathRunner:
    """Validator invocation contract only: same command path as validate_*_staged, fake success stdout."""

    structure_only = True

    def __init__(self, plugin_name: str) -> None:
        self.plugin_name = plugin_name
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command) -> CommandResult:
        cmd = tuple(str(item) for item in command)
        self.commands.append(cmd)
        if cmd[:3] == ("claude", "plugin", "validate"):
            return CommandResult(0, "Validation passed\n", "")
        return CommandResult(
            0, "Plugin validation passed: {0}\n".format(self.plugin_name), ""
        )


def _assert_cross_link_ok(result, target: str, runner: _OfficialPathRunner) -> Path:
    assert result.ok, {
        adapter_id: [(issue.code, issue.message) for issue in report.issues]
        for adapter_id, report in result.validation.items()
    }
    report = result.validation[target]
    assert report.ok, [(issue.code, issue.message) for issue in report.issues]
    published = Path(result.published_roots[target])
    manifest_rel = _NATIVE_MANIFEST[target]
    assert (published / manifest_rel).is_file(), manifest_rel
    if target == "claude-code":
        assert any(
            cmd[:3] == ("claude", "plugin", "validate") for cmd in runner.commands
        ), runner.commands
    elif target == "codex":
        assert any(
            "validate_plugin.py" in " ".join(cmd) for cmd in runner.commands
        ), runner.commands
    return published


def test_codex_source_round_trip_interface():
    from plugin_targets.codex.adapter import get_adapter
    from plugin_targets.codex.manifest import build_codex_manifest

    parsed = get_adapter().parse(_CODEX_ORIGIN)
    result = build_codex_manifest(parsed)
    assert result["interface"] == _load_origin_interface()
    assert result["name"] == "codex-origin-plugin"
    assert result["version"] == "0.0.1"


def test_codex_source_to_codex_preserves_published_interface():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_codex_origin(repo_root)
        runner = _OfficialPathRunner("codex-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("codex",),
                runner=runner,
            )
        published = _assert_cross_link_ok(result, "codex", runner)
        manifest = json.loads(
            (published / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert manifest["interface"] == _load_origin_interface()
        assert (published / "scripts" / "server.py").is_file()
        assert (published / "hooks" / "notify.py").is_file()
        assert (published / "hooks" / "hooks.json").is_file()
        assert (published / ".mcp.json").is_file()
        mcp = json.loads((published / ".mcp.json").read_text(encoding="utf-8"))
        assert "${PLUGIN_ROOT}/scripts/server.py" in json.dumps(mcp)
        assert "CODEX_PLUGIN_ROOT" not in json.dumps(mcp)
        hooks = (published / "hooks" / "hooks.json").read_text(encoding="utf-8")
        assert "CODEX_PLUGIN_ROOT" not in hooks
        assert "${PLUGIN_ROOT}/hooks/notify.py" in hooks


def test_codex_source_to_cursor_drops_interface_with_warn():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_codex_origin(repo_root)
        runner = _OfficialPathRunner("codex-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("cursor",),
                runner=runner,
            )
        published = _assert_cross_link_ok(result, "cursor", runner)
        manifest = json.loads(
            (published / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert "interface" not in manifest
        assert any(
            issue.code == "dropped-manifest-field" and "interface" in issue.message
            for issue in result.validation["cursor"].issues
        )
        assert (published / "skills" / "codex-origin" / "SKILL.md").is_file()
        assert (published / "skills" / "default-skill" / "SKILL.md").is_file()
        assert (published / "hooks" / "hooks.json").is_file()
        assert (published / "hooks" / "notify.py").is_file()
        assert (published / "mcp.json").is_file()
        assert (published / "scripts" / "server.py").is_file()
        mcp = json.loads((published / "mcp.json").read_text(encoding="utf-8"))
        assert "${CURSOR_PLUGIN_ROOT}/scripts/server.py" in json.dumps(mcp)
        hooks = (published / "hooks" / "hooks.json").read_text(encoding="utf-8")
        assert "sessionStart" in hooks
        assert "${CURSOR_PLUGIN_ROOT}/hooks/notify.py" in hooks
        assert "CODEX_PLUGIN_ROOT" not in hooks
        assert "CODEX_PLUGIN_ROOT" not in json.dumps(mcp)


def test_codex_source_to_claude_code_builds():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_codex_origin(repo_root)
        runner = _OfficialPathRunner("codex-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("claude-code",),
                runner=runner,
            )
        published = _assert_cross_link_ok(result, "claude-code", runner)
        manifest = json.loads(
            (published / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert manifest["name"] == "codex-origin-plugin"
        assert manifest["version"] == "0.0.1"
        assert any(
            issue.code == "dropped-manifest-field" and "interface" in issue.message
            for issue in result.validation["claude-code"].issues
        ), [
            (issue.code, issue.message)
            for issue in result.validation["claude-code"].issues
        ]


def test_codex_origin_does_not_copy_custom_skills_root():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_codex_origin(repo_root)
        runner = _OfficialPathRunner("codex-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("cursor", "codex", "claude-code"),
                runner=runner,
            )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        for adapter_id, root in result.published_roots.items():
            published = Path(root)
            assert not (published / "custom-skills").exists(), adapter_id
            assert (published / "skills" / "codex-origin" / "SKILL.md").is_file(), (
                adapter_id
            )
            assert (published / "skills" / "default-skill" / "SKILL.md").is_file(), (
                adapter_id
            )
            assert (published / "hooks" / "hooks.json").is_file(), adapter_id
            assert (published / "hooks" / "notify.py").is_file(), adapter_id
            assert (published / "scripts" / "server.py").is_file(), adapter_id
            if adapter_id == "codex":
                assert (published / ".mcp.json").is_file(), adapter_id
            elif adapter_id == "cursor":
                assert (published / "mcp.json").is_file(), adapter_id


def test_claude_source_keeps_allowed_tools_when_targeting_claude():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_claude_origin(repo_root)
        runner = _OfficialPathRunner("claude-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("claude-code",),
                runner=runner,
            )
        assert result.ok
        assert result.validation["claude-code"].ok
        published = Path(result.published_roots["claude-code"])
        skill = (
            published / "skills" / "claude-origin" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "allowed-tools: Read" in skill
        manifest = json.loads(
            (published / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert manifest["displayName"] == "Claude Origin"
        assert manifest["name"] == "claude-origin-plugin"
        assert manifest["version"] == "0.0.1"


def test_claude_source_to_cursor_drops_allowed_tools_with_warn():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_claude_origin(repo_root)
        runner = _OfficialPathRunner("claude-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("cursor",),
                runner=runner,
            )
        published = _assert_cross_link_ok(result, "cursor", runner)
        skill = (
            published / "skills" / "claude-origin" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "allowed-tools" not in skill
        assert any(
            issue.code == "dropped-frontmatter-field" and "allowed-tools" in issue.message
            for issue in result.validation["cursor"].issues
        )


def test_claude_source_to_codex_builds():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_claude_origin(repo_root)
        runner = _OfficialPathRunner("claude-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("codex",),
                runner=runner,
            )
        published = _assert_cross_link_ok(result, "codex", runner)
        manifest = json.loads(
            (published / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert manifest["name"] == "claude-origin-plugin"
        assert manifest["version"] == "0.0.1"


def test_codex_compile_does_not_need_source_tree_rglob():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.codex.adapter import get_adapter
    from plugin_targets.codex.manifest import build_codex_manifest

    class Runner:
        structure_only = True

        def __call__(self, command):
            cmd = tuple(command)
            if cmd[:3] == ("claude", "plugin", "validate"):
                return CommandResult(0, "Validation passed\n", "")
            return CommandResult(
                0, "Plugin validation passed: codex-origin-plugin\n", ""
            )

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_codex_origin(repo_root)
        (plugin / "junk.bin").write_bytes(b"undeclared-junk")
        result = compile_plugin(
            plugin,
            repo_root,
            ("codex", "cursor", "claude-code"),
            runner=Runner(),
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        for adapter_id, root in result.published_roots.items():
            published = Path(root)
            assert not (published / "junk.bin").exists(), adapter_id
            assert not any(
                path.name == "junk.bin" for path in published.rglob("*")
            ), adapter_id

        parsed = get_adapter().parse(plugin)
        (plugin / ".codex-plugin" / "plugin.json").unlink()
        rebuilt = build_codex_manifest(parsed)
        assert rebuilt["name"] == "codex-origin-plugin"
        assert rebuilt["interface"] == _load_origin_interface()


def _stage_iter_function_body_has_plugin_rglob(stage_path: Path, func_name: str) -> bool:
    import ast

    source = stage_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            body = ast.get_source_segment(source, node) or ""
            return '.rglob("*")' in body or ".rglob('*')" in body
    raise AssertionError("missing function {0} in {1}".format(func_name, stage_path))


def test_stage_iter_functions_do_not_rglob_plugin_dir():
    scripts = Path(__file__).resolve().parents[1]
    checks = (
        ("plugin_targets/codex/stage.py", "iter_codex_stage_files"),
        ("plugin_targets/claude_code/stage.py", "iter_claude_stage_files"),
        ("plugin_targets/cursor/stage.py", "iter_cursor_stage_files"),
    )
    for relative, func_name in checks:
        path = scripts / relative
        assert path.is_file(), path
        assert not _stage_iter_function_body_has_plugin_rglob(
            path, func_name
        ), "{0}.{1} still uses .rglob(\"*\")".format(relative, func_name)


def test_cursor_parse_fills_declared_resources_for_scanned_skills():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult
    from plugin_targets.cursor.source_parse import parse_cursor_plugin

    class Runner:
        structure_only = True

        def __call__(self, command):
            cmd = tuple(command)
            if cmd[:3] == ("claude", "plugin", "validate"):
                return CommandResult(0, "Validation passed\n", "")
            return CommandResult(
                0, "Plugin validation passed: cursor-demo\n", ""
            )

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = repo_root / "plugins" / "cursor-demo"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "cursor-demo", "version": "0.0.1"}),
            encoding="utf-8",
        )
        skill_dir = plugin / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo\n---\n# Demo\n",
            encoding="utf-8",
        )
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "helper.py").write_text("x = 1\n", encoding="utf-8")
        (plugin / "junk.bin").write_bytes(b"nope")
        parsed = parse_cursor_plugin(plugin)
        declared_paths = {item.source_path for item in parsed.declared_resources}
        assert "skills/demo" in declared_paths
        assert any(
            item.source_path == "skills/demo" and item.kind == "skill"
            for item in parsed.declared_resources
        )
        result = compile_plugin(
            plugin,
            repo_root,
            ("cursor", "codex", "claude-code"),
            runner=Runner(),
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        for adapter_id, root in result.published_roots.items():
            published = Path(root)
            assert (
                published / "skills" / "demo" / "scripts" / "helper.py"
            ).is_file(), adapter_id
            assert not (published / "junk.bin").exists(), adapter_id


def test_cursor_source_still_builds_all_three_targets():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = repo_root / "plugins" / "cursor-mini"
        (plugin / ".cursor-plugin").mkdir(parents=True)
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "cursor-mini",
                    "version": "0.0.1",
                    "description": "cursor source mini",
                    "skills": "./skills/",
                }
            ),
            encoding="utf-8",
        )
        skill_dir = plugin / "skills" / "mini"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: mini\ndescription: Mini skill\n---\n# Mini\n",
            encoding="utf-8",
        )
        packaging = plugin / "packaging"
        packaging.mkdir()
        (packaging / "codex-plugin.json").write_text(
            json.dumps({"description": "Codex from cursor mini"}) + "\n",
            encoding="utf-8",
        )
        (packaging / "claude-plugin.json").write_text(
            json.dumps({"description": "Claude from cursor mini"}) + "\n",
            encoding="utf-8",
        )
        runner = _OfficialPathRunner("cursor-mini")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("cursor", "codex", "claude-code"),
                runner=runner,
            )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        for target in ("cursor", "codex", "claude-code"):
            report = result.validation[target]
            assert report.ok, (
                target,
                [(issue.code, issue.message) for issue in report.issues],
            )
            published = Path(result.published_roots[target])
            assert (published / _NATIVE_MANIFEST[target]).is_file(), target
        assert any(
            cmd[:3] == ("claude", "plugin", "validate") for cmd in runner.commands
        ), runner.commands
        assert any(
            "validate_plugin.py" in " ".join(cmd) for cmd in runner.commands
        ), runner.commands


def test_claude_origin_builds_all_three_targets():
    from plugin_compiler.orchestrator import compile_plugin

    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_claude_origin(repo_root)
        runner = _OfficialPathRunner("claude-origin-plugin")
        with _codex_validator_env(repo_root):
            result = compile_plugin(
                plugin,
                repo_root,
                ("cursor", "codex", "claude-code"),
                runner=runner,
            )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        for target in ("cursor", "codex", "claude-code"):
            report = result.validation[target]
            assert report.ok, (
                target,
                [(issue.code, issue.message) for issue in report.issues],
            )
            published = Path(result.published_roots[target])
            assert (published / _NATIVE_MANIFEST[target]).is_file(), target
        assert any(
            cmd[:3] == ("claude", "plugin", "validate") for cmd in runner.commands
        ), runner.commands
        assert any(
            "validate_plugin.py" in " ".join(cmd) for cmd in runner.commands
        ), runner.commands


def _real_codex_validator_available() -> bool:
    from plugin_targets.codex.validate import _codex_yaml_available, resolve_codex_validator

    validator = resolve_codex_validator()
    return bool(validator and validator.is_file() and _codex_yaml_available())


def test_codex_origin_published_passes_real_official_validator():
    from plugin_compiler.orchestrator import compile_plugin

    if not _real_codex_validator_available():
        print("SKIP test_codex_origin_published_passes_real_official_validator")
        return
    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_codex_origin(repo_root)
        result = compile_plugin(plugin, repo_root, ("codex",), source_format="codex")
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        assert result.validation["codex"].ok
        assert not any(
            issue.code == "validator-failed"
            for issue in result.validation["codex"].issues
        )


def test_claude_origin_to_codex_published_passes_real_official_validator():
    from plugin_compiler.orchestrator import compile_plugin

    if not _real_codex_validator_available():
        print("SKIP test_claude_origin_to_codex_published_passes_real_official_validator")
        return
    with TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        plugin = _copy_claude_origin(repo_root)
        result = compile_plugin(
            plugin, repo_root, ("codex",), source_format="claude-code"
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        assert result.validation["codex"].ok
        assert not any(
            issue.code == "validator-failed"
            for issue in result.validation["codex"].issues
        )


def test_disabled_codex_adapter_cannot_detect_codex_source():
    from plugin_targets.registry import (
        AdapterSpec,
        registered_specs,
        replace_registry_for_tests,
    )
    from plugin_targets.source import SourceResolutionError, resolve_source_adapter

    original = registered_specs()
    try:
        replace_registry_for_tests(
            tuple(
                AdapterSpec(spec.adapter_id, spec.module_path, enabled=False)
                if spec.adapter_id == "codex"
                else spec
                for spec in original
            )
        )
        raised = False
        try:
            resolve_source_adapter(_CODEX_ORIGIN, "auto")
        except SourceResolutionError:
            raised = True
        assert raised, "disabled Codex adapter must not auto-detect Codex source"
    finally:
        replace_registry_for_tests(original)


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
