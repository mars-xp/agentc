# -*- coding: utf-8 -*-
"""in-repo adapter 注册表。运行：python3 scripts/tests/test_plugin_targets_registry.py"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_targets.registry import (
    AdapterSpec,
    enabled_ids,
    load_adapter,
    registered_specs,
    replace_registry_for_tests,
    spec_for,
)

SCRIPTS = Path(__file__).resolve().parents[1]
CORE_SCAN_DIRS = (
    SCRIPTS / "plugin_compiler",
    SCRIPTS / "plugin_targets" / "registry.py",
)
FORBIDDEN_STATIC = (
    "plugin_targets.cursor",
    "plugin_targets.codex",
    "plugin_targets.claude_code",
)


def test_default_registry_lists_three_in_repo_ids():
    ids = tuple(spec.adapter_id for spec in registered_specs())
    assert ids == ("cursor", "codex", "claude-code"), ids
    assert enabled_ids() == ("cursor", "codex", "claude-code")
    assert spec_for("claude-code").module_path == "plugin_targets.claude_code.adapter"


def test_disable_codex_drops_it_from_enabled_ids():
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
        assert enabled_ids() == ("cursor", "claude-code")
        assert spec_for("codex").enabled is False
    finally:
        replace_registry_for_tests(original)


def test_load_missing_adapter_raises():
    original = registered_specs()
    try:
        replace_registry_for_tests(
            (
                AdapterSpec(
                    "cursor",
                    "plugin_targets._missing_mod.adapter",
                    enabled=True,
                ),
            )
        )
        raised = False
        try:
            load_adapter("cursor")
        except RuntimeError:
            raised = True
        assert raised, "load_adapter must fail loudly when the module is missing"
    finally:
        replace_registry_for_tests(original)


def test_core_does_not_statically_import_adapter_packages():
    hits: list[str] = []
    files: list[Path] = []
    compiler = SCRIPTS / "plugin_compiler"
    files.extend(sorted(compiler.glob("*.py")))
    files.append(SCRIPTS / "plugin_targets" / "registry.py")
    files.append(SCRIPTS / "release.py")
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                if any(module == item or module.startswith(item + ".") for item in FORBIDDEN_STATIC):
                    hits.append("{0}:{1}:{2}".format(path.name, node.lineno, module))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if any(name == item or name.startswith(item + ".") for item in FORBIDDEN_STATIC):
                        hits.append("{0}:import {1}".format(path.name, name))
    assert hits == [], hits


def test_load_adapter_exposes_four_methods():
    adapter = load_adapter("cursor")
    assert adapter.id == "cursor"
    for name in ("compile", "validate", "publish_local", "install"):
        assert callable(getattr(adapter, name)), name


def test_staging_has_no_cursor_target_branch():
    text = (SCRIPTS / "plugin_compiler" / "staging.py").read_text(encoding="utf-8")
    assert "if target is Target.CURSOR" not in text
    assert "if target in (Target.CURSOR" not in text


def test_staging_has_no_codex_target_branch():
    text = (SCRIPTS / "plugin_compiler" / "staging.py").read_text(encoding="utf-8")
    assert "Target.CODEX" not in text


def test_staging_has_no_claude_target_branch():
    text = (SCRIPTS / "plugin_compiler" / "staging.py").read_text(encoding="utf-8")
    assert "Target.CLAUDE_CODE" not in text


def test_packaging_has_no_codex_host_branch():
    text = (SCRIPTS / "plugin_packaging.py").read_text(encoding="utf-8")
    assert "if host == \"codex\"" not in text
    assert "if host != \"codex\"" not in text


def test_packaging_legal_hosts_follow_live_enabled_registry():
    from plugin_packaging import iter_pack_files, write_runtime_host

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
        assert enabled_ids() == ("cursor", "claude-code")
        for action in (
            lambda: iter_pack_files(Path("/tmp"), "codex"),
            lambda: write_runtime_host(Path("/tmp") / "unused", "codex"),
        ):
            raised = False
            try:
                action()
            except RuntimeError as exc:
                raised = "illegal" in str(exc) and "codex" in str(exc)
            assert raised, "disabled Codex must not stay in packaging host allowlist"
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
