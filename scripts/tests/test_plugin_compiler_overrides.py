#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.model import CapabilityKind, CapabilityRef, Target
from plugin_compiler.orchestrator import compile_plugin
from plugin_compiler.overrides import (
    CommandBinding,
    OverrideConfig,
    OverrideEntry,
    command_binding_for,
    load_override_config,
    resolved_targets,
    skill_dir_allowed,
    validate_overrides,
)


def test_missing_file_defaults_to_three_targets_and_no_overrides():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        config = load_override_config(plugin)
        assert config.default_targets == (
            Target.CURSOR,
            Target.CODEX,
            Target.CLAUDE_CODE,
        )
        assert config.overrides == {}
        assert config.command_bindings == {}


def test_skills_json_without_overrides_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        (plugin / "packaging").mkdir(parents=True)
        (plugin / "packaging" / "skills.json").write_text(
            '{"schema_version": 1, "default_hosts": ["cursor"], "skills": {}}',
            encoding="utf-8",
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            assert "platform-overrides.json" in str(exc)
            assert "skills.json" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_both_files_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        (plugin / "packaging").mkdir(parents=True)
        (plugin / "packaging" / "skills.json").write_text(
            '{"schema_version": 1, "default_hosts": ["cursor"], "skills": {}}',
            encoding="utf-8",
        )
        (plugin / "packaging" / "platform-overrides.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_targets": ["cursor", "codex", "claude-code"],
                    "overrides": {},
                }
            ),
            encoding="utf-8",
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            assert "platform-overrides.json" in str(exc)
            assert "skills.json" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_kiwi_sp_override_resolves_cursor_only():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        (plugin / "packaging").mkdir(parents=True)
        (plugin / "packaging" / "platform-overrides.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_targets": ["cursor", "codex", "claude-code"],
                    "overrides": {
                        "skill:skills/kiwi-sp": {
                            "targets": ["cursor"],
                            "reason": "Cursor Superpowers adapter",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        config = load_override_config(plugin)
        skill = CapabilityRef(
            capability_id="skill:skills/kiwi-sp",
            kind=CapabilityKind.SKILL,
            source_path="skills/kiwi-sp",
            source_dialect=None,
        )
        shared = CapabilityRef(
            capability_id="skill:skills/shared",
            kind=CapabilityKind.SKILL,
            source_path="skills/shared",
            source_dialect=None,
        )
        assert resolved_targets(config, skill) == (Target.CURSOR,)
        assert resolved_targets(config, shared) == tuple(Target)
        assert skill_dir_allowed(config, "kiwi-sp", "cursor") is True
        assert skill_dir_allowed(config, "kiwi-sp", "codex") is False
        assert skill_dir_allowed(config, "kiwi-sp", "claude-code") is False


def test_override_unknown_id_and_missing_reason_and_short_default_targets_block():
    config = OverrideConfig(
        schema_version=1,
        default_targets=(Target.CURSOR, Target.CODEX),
        overrides={
            "skill:skills/missing": OverrideEntry(
                capability_id="skill:skills/missing",
                targets=(Target.CURSOR,),
                reason="",
            )
        },
    )
    present = (
        CapabilityRef(
            capability_id="skill:skills/demo",
            kind=CapabilityKind.SKILL,
            source_path="skills/demo",
            source_dialect=None,
        ),
    )
    codes = {issue.code for issue in validate_overrides(config, present)}
    assert "override-default-targets-must-be-all-three" in codes
    assert "override-unknown-capability" in codes
    assert "override-reason-required" in codes


def test_override_that_repeats_all_three_targets_is_warn_not_block():
    skill = CapabilityRef(
        capability_id="skill:skills/demo",
        kind=CapabilityKind.SKILL,
        source_path="skills/demo",
        source_dialect=None,
    )
    config = OverrideConfig(
        schema_version=1,
        default_targets=tuple(Target),
        overrides={
            "skill:skills/demo": OverrideEntry(
                capability_id="skill:skills/demo",
                targets=tuple(Target),
                reason="leftover",
            )
        },
    )
    issues = validate_overrides(config, (skill,))
    assert any(
        issue.code == "stale-override" and issue.severity == "warn" for issue in issues
    )
    assert not any(issue.severity == "block" for issue in issues)


def _write_overrides(plugin: Path, payload: dict) -> None:
    packaging = plugin / "packaging"
    packaging.mkdir(parents=True, exist_ok=True)
    (packaging / "platform-overrides.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_typo_overrides_key_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overides": {
                    "skill:skills/kiwi-sp": {
                        "targets": ["cursor"],
                        "reason": "Cursor only",
                    }
                },
            },
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            message = str(exc)
            assert "unknown fields" in message
            assert "overides" in message
        else:
            raise AssertionError("expected RuntimeError")


def test_unknown_override_entry_field_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {
                    "skill:skills/kiwi-sp": {
                        "targets": ["cursor"],
                        "reason": "Cursor only",
                        "hosts": ["cursor"],
                    }
                },
            },
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            message = str(exc)
            assert "unknown fields" in message
            assert "hosts" in message
        else:
            raise AssertionError("expected RuntimeError")


def test_unsupported_schema_version_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 2,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
            },
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            message = str(exc)
            assert "schema_version" in message
            assert "2" in message
        else:
            raise AssertionError("expected RuntimeError")


def test_schema_version_true_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": True,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
            },
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            message = str(exc)
            assert "schema_version" in message
            assert "integer" in message
        else:
            raise AssertionError("expected RuntimeError")


def test_command_bindings_round_trip():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
                "command_bindings": {
                    "command:commands/jxp_merge-dev.md": {
                        "codex_skill_dir": "jxp_merge-dev",
                        "reason": "Codex uses the existing merge skill as the command entry",
                    }
                },
            },
        )
        config = load_override_config(plugin)
        binding = config.command_bindings["command:commands/jxp_merge-dev.md"]
        assert binding.codex_skill_dir == "jxp_merge-dev"
        assert binding.reason
        assert command_binding_for(config, "command:commands/jxp_merge-dev.md") is binding
        assert command_binding_for(config, "command:commands/missing.md") is None


def test_missing_command_bindings_field_defaults_to_empty():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
            },
        )
        config = load_override_config(plugin)
        assert config.command_bindings == {}
        assert command_binding_for(config, "command:commands/jxp_merge-dev.md") is None


def test_command_bindings_missing_reason_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
                "command_bindings": {
                    "command:commands/jxp_merge-dev.md": {
                        "codex_skill_dir": "jxp_merge-dev",
                    }
                },
            },
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            message = str(exc)
            assert "reason" in message
            assert "jxp_merge-dev.md" in message
        else:
            raise AssertionError("expected RuntimeError")


def test_command_bindings_missing_codex_skill_dir_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
                "command_bindings": {
                    "command:commands/jxp_merge-dev.md": {
                        "reason": "Codex uses the existing merge skill as the command entry",
                    }
                },
            },
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            message = str(exc)
            assert "codex_skill_dir" in message
            assert "jxp_merge-dev.md" in message
        else:
            raise AssertionError("expected RuntimeError")


def _command_cap(filename: str) -> CapabilityRef:
    source = "commands/{0}".format(filename)
    return CapabilityRef(
        capability_id="command:{0}".format(source),
        kind=CapabilityKind.COMMAND,
        source_path=source,
        source_dialect=None,
    )


def _skill_cap(dirname: str) -> CapabilityRef:
    source = "skills/{0}".format(dirname)
    return CapabilityRef(
        capability_id="skill:{0}".format(source),
        kind=CapabilityKind.SKILL,
        source_path=source,
        source_dialect=None,
    )


def test_command_binding_unknown_command_blocks():
    command = _command_cap("present.md")
    skill = _skill_cap("demo")
    config = OverrideConfig(
        schema_version=1,
        default_targets=tuple(Target),
        overrides={},
        command_bindings={
            "command:commands/missing.md": CommandBinding(
                capability_id="command:commands/missing.md",
                codex_skill_dir="demo",
                reason="bound to a command that is not scanned",
            )
        },
    )
    codes = {issue.code for issue in validate_overrides(config, (command, skill))}
    assert "command-binding-unknown-command" in codes
    assert all(
        issue.severity == "block"
        for issue in validate_overrides(config, (command, skill))
        if issue.code == "command-binding-unknown-command"
    )


def test_command_binding_unknown_skill_blocks():
    command = _command_cap("tune.md")
    config = OverrideConfig(
        schema_version=1,
        default_targets=tuple(Target),
        overrides={},
        command_bindings={
            "command:commands/tune.md": CommandBinding(
                capability_id="command:commands/tune.md",
                codex_skill_dir="missing-skill",
                reason="bound to a skill directory that does not exist",
            )
        },
    )
    issues = validate_overrides(config, (command,))
    assert any(
        issue.code == "command-binding-unknown-skill" and issue.severity == "block"
        for issue in issues
    )


def test_command_binding_skill_not_on_codex_blocks():
    command = _command_cap("tune.md")
    skill = _skill_cap("demo")
    config = OverrideConfig(
        schema_version=1,
        default_targets=tuple(Target),
        overrides={
            "skill:skills/demo": OverrideEntry(
                capability_id="skill:skills/demo",
                targets=(Target.CURSOR,),
                reason="Cursor only skill",
            )
        },
        command_bindings={
            "command:commands/tune.md": CommandBinding(
                capability_id="command:commands/tune.md",
                codex_skill_dir="demo",
                reason="bound to a skill that does not enter Codex",
            )
        },
    )
    issues = validate_overrides(config, (command, skill))
    assert any(
        issue.code == "command-binding-skill-not-on-codex"
        and issue.severity == "block"
        for issue in issues
    )


def test_command_bindings_unknown_entry_field_is_a_hard_error():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        _write_overrides(
            plugin,
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
                "command_bindings": {
                    "command:commands/jxp_merge-dev.md": {
                        "codex_skill_dir": "jxp_merge-dev",
                        "reason": "Codex uses the existing merge skill as the command entry",
                        "extra": "nope",
                    }
                },
            },
        )
        try:
            load_override_config(plugin)
        except RuntimeError as exc:
            message = str(exc)
            assert "unknown fields" in message
            assert "extra" in message
        else:
            raise AssertionError("expected RuntimeError")


def test_reason_with_cursor_install_paths_still_honors_targets():
    capability = CapabilityRef(
        capability_id="skill:skills/portable",
        kind=CapabilityKind.SKILL,
        source_path="skills/portable",
        source_dialect=None,
    )
    config = OverrideConfig(
        schema_version=1,
        default_targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
        overrides={
            capability.capability_id: OverrideEntry(
                capability_id=capability.capability_id,
                targets=(Target.CURSOR, Target.CLAUDE_CODE),
                reason="mentions .cursor-plugin only as documentation",
            )
        },
    )
    assert Target.CODEX not in resolved_targets(config, capability)
    assert Target.CURSOR in resolved_targets(config, capability)
    issues = validate_overrides(config, (capability,))
    assert not any(
        issue.code == "override-reason-not-no-equivalent" for issue in issues
    )


def test_legal_cursor_only_override_has_no_layout_issue():
    capability = CapabilityRef(
        capability_id="skill:skills/cursor-settings",
        kind=CapabilityKind.SKILL,
        source_path="skills/cursor-settings",
        source_dialect=None,
    )
    config = OverrideConfig(
        schema_version=1,
        default_targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
        overrides={
            capability.capability_id: OverrideEntry(
                capability_id=capability.capability_id,
                targets=(Target.CURSOR,),
                reason="Updates Cursor settings",
            )
        },
    )
    assert Target.CODEX not in resolved_targets(config, capability)
    issues = validate_overrides(config, (capability,))
    assert not any(
        issue.code == "override-reason-not-no-equivalent" for issue in issues
    )


def _layout_escape_plugin(root: Path) -> Path:
    plugin = root / "source" / "demo"
    plugin.mkdir(parents=True)
    (plugin / ".cursor-plugin").mkdir()
    (plugin / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "description": "Synthetic plugin",
                "skills": "./skills/",
            }
        ),
        encoding="utf-8",
    )
    skill_dir = plugin / "skills" / "portable"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: portable\ndescription: d\n---\n# Portable\n",
        encoding="utf-8",
    )
    _write_overrides(
        plugin,
        {
            "schema_version": 1,
            "default_targets": ["cursor", "codex", "claude-code"],
            "overrides": {
                "skill:skills/portable": {
                    "targets": ["cursor", "claude-code"],
                    "reason": (
                        "Codex-visible files still contain Cursor layout strings "
                        "(.cursor-plugin / .cursor/skills / ~/.cursor/plugins / "
                        "JXP_CURSOR_PLUGIN_ROOT)"
                    ),
                }
            },
        },
    )
    return plugin


def test_compile_plugin_layout_escape_override_blocks_only_dropped_target():
    import shutil
    from unittest.mock import patch

    from plugin_compiler.validation import CommandResult
    from plugin_targets.spi import AdapterCompileResult, ValidationResult

    class _Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "ok\n", "")

    class _FakeAdapter:
        def __init__(self, adapter_id: str) -> None:
            self.id = adapter_id

        def compile(self, request):
            staged = request.layout.intermediate(self.id, request.parsed.plugin_name)
            staged.mkdir(parents=True, exist_ok=True)
            plugin_dir = Path(request.parsed.plugin_dir)
            skills = plugin_dir / "skills"
            if skills.is_dir():
                shutil.copytree(skills, staged / "skills", dirs_exist_ok=True)
            for manifest_dir in (".cursor-plugin", ".codex-plugin", ".claude-plugin"):
                src = plugin_dir / manifest_dir / "plugin.json"
                if src.is_file():
                    dest = staged / manifest_dir / "plugin.json"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            return AdapterCompileResult(
                ok=True,
                adapter_id=self.id,
                staged_root=str(staged),
                issues=(),
            )

        def validate(self, request):
            return ValidationResult(
                ok=True,
                adapter_id=self.id,
                staged_root=str(request.staged_root),
                issues=(),
            )

        def publish_local(self, request):
            raise AssertionError("validate_only compile must not publish")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = _layout_escape_plugin(root)
        adapters = {
            "cursor": _FakeAdapter("cursor"),
            "codex": _FakeAdapter("codex"),
        }
        with patch(
            "plugin_compiler.orchestrator.load_adapter",
            side_effect=lambda adapter_id: adapters[adapter_id],
        ):
            result = compile_plugin(
                plugin,
                root,
                ("cursor", "codex"),
                validate_only=True,
                runner=_Runner(),
            )

        cursor_report = result.validation["cursor"]
        codex_report = result.validation["codex"]
        assert cursor_report.ok
        portable = next(
            item
            for item in result.capabilities
            if item.capability_id == "skill:skills/portable"
        )
        assert result.override_config is not None
        assert Target.CODEX not in resolved_targets(result.override_config, portable)
        assert Target.CURSOR in resolved_targets(result.override_config, portable)
        assert not any(
            issue.code == "override-reason-not-no-equivalent"
            for issue in cursor_report.issues
        )
        assert not any(
            issue.code == "override-reason-not-no-equivalent"
            for issue in codex_report.issues
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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
