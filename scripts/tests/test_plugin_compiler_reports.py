#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.agents import AgentDocument
from plugin_compiler.commands import CommandDocument
from plugin_compiler.mcp import McpDocument
from plugin_compiler.model import CapabilityKind, CapabilityRef, Target
from plugin_compiler.orchestrator import CompileResult, compile_plugin
from plugin_compiler.overrides import CommandBinding, OverrideConfig, OverrideEntry
from plugin_compiler.paths import BuildLayout
from plugin_compiler.reports import (
    _compatibility_payload,
    apply_compatibility_gate,
    write_reports,
)
from plugin_targets.spi import DeclaredResource
from plugin_compiler.validation import CommandResult, ValidationIssue, ValidationReport
from plugin_compiler.workflows import WorkflowDocument


def _capability() -> CapabilityRef:
    return CapabilityRef(
        capability_id="skill:skills/demo",
        kind=CapabilityKind.SKILL,
        source_path="skills/demo",
        source_dialect=None,
    )


def _result(root: Path) -> CompileResult:
    staged = root / "intermediates" / "cursor" / "demo-plugin"
    staged_skill = staged / "skills" / "demo" / "SKILL.md"
    staged_skill.parent.mkdir(parents=True)
    staged_skill.write_text("# Demo\n", encoding="utf-8")
    return CompileResult(
        plugin_name="demo-plugin",
        targets=(Target.CURSOR, Target.CODEX),
        capabilities=(_capability(),),
        staged_roots={Target.CURSOR: str(staged)},
        validation={
            Target.CURSOR: ValidationReport(
                target=Target.CURSOR,
                plugin_root=str(staged),
                issues=(
                    ValidationIssue(
                        severity="hint",
                        code="manual-check",
                        path="skills/demo/SKILL.md",
                        message="保留提示",
                    ),
                    ValidationIssue(
                        severity="warn",
                        code="validator-warning",
                        path="validator",
                        message="保留警告",
                    ),
                    ValidationIssue(
                        severity="block",
                        code="missing-target-artifact",
                        path="skills/demo",
                        message="缺少目标产物",
                    ),
                ),
            )
        },
        published_roots={},
    )


def test_reports_are_complete_deterministic_serializable_and_lossless():
    with TemporaryDirectory() as tmp:
        report_dir = Path(tmp) / "reports"
        result = _result(Path(tmp))

        paths = write_reports(result, report_dir)
        first = {path.name: path.read_bytes() for path in paths}
        second_paths = write_reports(result, report_dir)
        second = {path.name: path.read_bytes() for path in second_paths}

        assert [path.name for path in paths] == [
            "capabilities.json",
            "compatibility.json",
            "artifacts.json",
            "validation.json",
            "report.md",
        ]
        assert first == second
        assert all(b"\r" not in content for content in first.values())
        for name in (
            "capabilities.json",
            "compatibility.json",
            "artifacts.json",
            "validation.json",
        ):
            json.loads(first[name].decode("utf-8"))

        validation = json.loads(first["validation.json"].decode("utf-8"))
        assert validation["ok"] is False
        assert validation["targets"] == ["cursor", "codex"]
        assert [item["status"] for item in validation["validation"]] == [
            "blocked",
            "missing",
        ]
        assert [item["published"] for item in validation["validation"]] == [
            False,
            False,
        ]
        assert [item["severity"] for item in validation["issues"]] == [
            "hint",
            "warn",
            "block",
        ]
        assert [item["message"] for item in validation["issues"]] == [
            "保留提示",
            "保留警告",
            "缺少目标产物",
        ]

        markdown = first["report.md"].decode("utf-8")
        assert "本轮没有新的正式包" in markdown
        assert "没有生成可发布包" not in markdown
        assert "保留提示" in markdown
        assert "保留警告" in markdown
        assert "行为等价" not in markdown
        assert "真实宿主集成有效" not in markdown
        assert "三端行为等价" not in markdown
        assert "真实宿主有效" not in markdown
        assert "需宿主信任" not in markdown


def test_reports_distinguish_sources_staged_artifacts_compatibility_and_publication():
    with TemporaryDirectory() as tmp:
        report_dir = Path(tmp) / "reports"
        result = _result(Path(tmp))
        write_reports(result, report_dir)

        capabilities = json.loads(
            (report_dir / "capabilities.json").read_text(encoding="utf-8")
        )
        assert capabilities["capabilities"] == [
            {
                "id": "skill:skills/demo",
                "kind": "skill",
                "source_path": "skills/demo",
                "source_dialect": None,
            }
        ]

        artifacts = json.loads(
            (report_dir / "artifacts.json").read_text(encoding="utf-8")
        )
        assert artifacts["staged_targets"][0]["target"] == "cursor"
        assert artifacts["staged_targets"][0]["artifacts"] == [
            "skills/demo/SKILL.md"
        ]
        assert artifacts["staged_targets"][1] == {
            "target": "codex",
            "staged_root": None,
            "artifacts": [],
        }
        assert artifacts["published_roots"] == []

        compatibility = json.loads(
            (report_dir / "compatibility.json").read_text(encoding="utf-8")
        )
        entries = compatibility["capabilities"][0]["targets"]
        assert entries[0]["status"] == "blocked_missing_artifact"
        assert entries[0]["artifacts"] == ["skills/demo/SKILL.md"]
        assert entries[1] == {
            "target": "codex",
            "status": "blocked_missing_artifact",
            "artifacts": [],
        }


def test_compatibility_marks_cursor_only_skill_declared_unsupported_on_codex():
    # 构造 CompileResult：capabilities 含 skill:skills/cursor-only 与 skill:skills/shared
    # override 仅 cursor-only → cursor
    # staged_roots[CODEX] 只有 shared 的 SKILL.md
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_staged = root / "intermediates" / "cursor" / "demo-plugin"
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        for name in ("cursor-only", "shared"):
            path = cursor_staged / "skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                "---\nname: {0}\ndescription: d\n---\n# {0}\n".format(name),
                encoding="utf-8",
            )
        shared_path = codex_staged / "skills" / "shared" / "SKILL.md"
        shared_path.parent.mkdir(parents=True)
        shared_path.write_text(
            "---\nname: shared\ndescription: d\n---\n# shared\n",
            encoding="utf-8",
        )
        cursor_only = CapabilityRef(
            capability_id="skill:skills/cursor-only",
            kind=CapabilityKind.SKILL,
            source_path="skills/cursor-only",
            source_dialect=None,
        )
        shared = CapabilityRef(
            capability_id="skill:skills/shared",
            kind=CapabilityKind.SKILL,
            source_path="skills/shared",
            source_dialect=None,
        )
        config = OverrideConfig(
            schema_version=1,
            default_targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
            overrides={
                "skill:skills/cursor-only": OverrideEntry(
                    capability_id="skill:skills/cursor-only",
                    targets=(Target.CURSOR,),
                    reason="Cursor integration",
                )
            },
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(cursor_only, shared),
            staged_roots={
                Target.CURSOR: str(cursor_staged),
                Target.CODEX: str(codex_staged),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
            override_config=config,
        )
        inventories = {
            Target.CURSOR: (
                "skills/cursor-only/SKILL.md",
                "skills/shared/SKILL.md",
            ),
            Target.CODEX: ("skills/shared/SKILL.md",),
        }
        payload = _compatibility_payload(result, inventories)
        by_id = {row["id"]: row for row in payload["capabilities"]}
        cursor_only = by_id["skill:skills/cursor-only"]
        statuses = {item["target"]: item["status"] for item in cursor_only["targets"]}
        assert statuses["codex"] == "declared_unsupported"
        assert statuses["cursor"] == "staged_artifact_present"

        shared_row = by_id["skill:skills/shared"]
        shared_statuses = {
            item["target"]: item["status"] for item in shared_row["targets"]
        }
        assert shared_statuses["codex"] == "staged_artifact_present"
        assert shared_statuses["cursor"] == "staged_artifact_present"

        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "三端行为等价" not in markdown
        assert "真实宿主有效" not in markdown
        assert "已声明不进入该平台" in markdown
        assert "不阻止其它点名平台发布" in markdown


def _rule_capability(name: str) -> CapabilityRef:
    source = "rules/{0}.mdc".format(name)
    return CapabilityRef(
        capability_id="rule:{0}".format(source),
        kind=CapabilityKind.RULE,
        source_path=source,
        source_dialect=None,
    )


def _command_capability(filename: str) -> CapabilityRef:
    source = "commands/{0}".format(filename)
    return CapabilityRef(
        capability_id="command:{0}".format(source),
        kind=CapabilityKind.COMMAND,
        source_path=source,
        source_dialect=None,
    )


def test_compatibility_marks_cursor_only_rule_declared_unsupported_on_codex():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_staged = root / "intermediates" / "cursor" / "demo-plugin"
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        for name in ("cursor-only", "shared"):
            path = cursor_staged / "rules" / "{0}.mdc".format(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "---\ndescription: {0}\nalwaysApply: true\n---\n# {0}\n".format(
                    name
                ),
                encoding="utf-8",
            )
        compiled_body = (
            codex_staged / "compiled-rules" / "bodies" / "rules" / "shared.mdc.txt"
        )
        compiled_body.parent.mkdir(parents=True)
        compiled_body.write_text("# shared\n", encoding="utf-8")
        (codex_staged / "compiled-rules" / "index.json").write_text(
            '{"schema_version": 1, "rules": []}\n', encoding="utf-8"
        )
        cursor_only = _rule_capability("cursor-only")
        shared = _rule_capability("shared")
        config = OverrideConfig(
            schema_version=1,
            default_targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
            overrides={
                "rule:rules/cursor-only.mdc": OverrideEntry(
                    capability_id="rule:rules/cursor-only.mdc",
                    targets=(Target.CURSOR,),
                    reason="Cursor editor rule",
                )
            },
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(cursor_only, shared),
            staged_roots={
                Target.CURSOR: str(cursor_staged),
                Target.CODEX: str(codex_staged),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
            override_config=config,
        )
        inventories = {
            Target.CURSOR: (
                "rules/cursor-only.mdc",
                "rules/shared.mdc",
            ),
            Target.CODEX: (
                "compiled-rules/bodies/rules/shared.mdc.txt",
                "compiled-rules/index.json",
            ),
        }
        payload = _compatibility_payload(result, inventories)
        by_id = {row["id"]: row for row in payload["capabilities"]}
        cursor_only_row = by_id["rule:rules/cursor-only.mdc"]
        statuses = {
            item["target"]: item["status"] for item in cursor_only_row["targets"]
        }
        assert statuses["codex"] == "declared_unsupported"
        assert statuses["cursor"] == "staged_artifact_present"

        shared_row = by_id["rule:rules/shared.mdc"]
        shared_statuses = {
            item["target"]: item["status"] for item in shared_row["targets"]
        }
        assert shared_statuses["codex"] == "staged_artifact_present"
        assert shared_statuses["cursor"] == "staged_artifact_present"

        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "三端行为等价" not in markdown
        assert "已声明不进入该平台" in markdown


def test_compatibility_marks_thin_command_carried_by_skill_on_codex():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_staged = root / "intermediates" / "cursor" / "demo-plugin"
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        command_path = cursor_staged / "commands" / "tune.md"
        command_path.parent.mkdir(parents=True)
        command_path.write_text("# Tune\n\nRun skills/demo.\n", encoding="utf-8")
        skill_path = cursor_staged / "skills" / "demo" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\nname: demo\ndescription: d\n---\n# demo\n", encoding="utf-8"
        )
        codex_skill = codex_staged / "skills" / "demo" / "SKILL.md"
        codex_skill.parent.mkdir(parents=True)
        codex_skill.write_text(
            "---\nname: demo\ndescription: d\n---\n# demo\n", encoding="utf-8"
        )
        command = _command_capability("tune.md")
        skill = CapabilityRef(
            capability_id="skill:skills/demo",
            kind=CapabilityKind.SKILL,
            source_path="skills/demo",
            source_dialect=None,
        )
        document = CommandDocument(
            capability_id="command:commands/tune.md",
            source_path="commands/tune.md",
            name="tune",
            body="# Tune\n\nRun skills/demo.\n",
            referenced_skill_dir="demo",
        )
        config = OverrideConfig(
            schema_version=1,
            default_targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
            overrides={},
            command_bindings={
                "command:commands/tune.md": CommandBinding(
                    capability_id="command:commands/tune.md",
                    codex_skill_dir="demo",
                    reason="Codex uses the existing demo skill as the command entry",
                )
            },
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(command, skill),
            staged_roots={
                Target.CURSOR: str(cursor_staged),
                Target.CODEX: str(codex_staged),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
            override_config=config,
            commands=(document,),
        )
        inventories = {
            Target.CURSOR: (
                "commands/tune.md",
                "skills/demo/SKILL.md",
            ),
            Target.CODEX: ("skills/demo/SKILL.md",),
        }
        payload = _compatibility_payload(result, inventories)
        by_id = {row["id"]: row for row in payload["capabilities"]}
        command_row = by_id["command:commands/tune.md"]
        statuses = {
            item["target"]: item["status"] for item in command_row["targets"]
        }
        assert statuses["codex"] == "carried_by_skill"
        assert statuses["cursor"] == "staged_artifact_present"
        assert statuses["codex"] != "declared_unsupported"
        assert statuses["codex"] != "blocked_missing_artifact"

        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "carried_by_skill" in markdown
        assert "由技能承载" in markdown
        assert "三端行为等价" not in markdown


def test_skill_mention_without_binding_is_not_carried_by_skill():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_staged = root / "intermediates" / "cursor" / "demo-plugin"
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        command_path = cursor_staged / "commands" / "tune.md"
        command_path.parent.mkdir(parents=True)
        command_path.write_text("# Tune\n\nRun skills/demo.\n", encoding="utf-8")
        skill_path = cursor_staged / "skills" / "demo" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\nname: demo\ndescription: d\n---\n# demo\n", encoding="utf-8"
        )
        generated = codex_staged / "skills" / "tune" / "SKILL.md"
        generated.parent.mkdir(parents=True)
        generated.write_text(
            "---\nname: tune\ndescription: Tune\n---\n# Tune\n", encoding="utf-8"
        )
        command = _command_capability("tune.md")
        skill = CapabilityRef(
            capability_id="skill:skills/demo",
            kind=CapabilityKind.SKILL,
            source_path="skills/demo",
            source_dialect=None,
        )
        document = CommandDocument(
            capability_id="command:commands/tune.md",
            source_path="commands/tune.md",
            name="tune",
            body="# Tune\n\nRun skills/demo.\n",
            referenced_skill_dir="demo",
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(command, skill),
            staged_roots={
                Target.CURSOR: str(cursor_staged),
                Target.CODEX: str(codex_staged),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
            commands=(document,),
        )
        inventories = {
            Target.CURSOR: ("commands/tune.md", "skills/demo/SKILL.md"),
            Target.CODEX: ("skills/tune/SKILL.md", "skills/demo/SKILL.md"),
        }
        payload = _compatibility_payload(result, inventories)
        by_id = {row["id"]: row for row in payload["capabilities"]}
        statuses = {
            item["target"]: item["status"]
            for item in by_id["command:commands/tune.md"]["targets"]
        }
        assert statuses["codex"] != "carried_by_skill"
        assert statuses["codex"] == "staged_artifact_present"


def test_binding_without_body_mention_is_carried_by_skill():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_staged = root / "intermediates" / "cursor" / "demo-plugin"
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        command_path = cursor_staged / "commands" / "tune.md"
        command_path.parent.mkdir(parents=True)
        command_path.write_text("# Tune\n\nIndependent flow text.\n", encoding="utf-8")
        skill_path = cursor_staged / "skills" / "demo" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\nname: demo\ndescription: d\n---\n# demo\n", encoding="utf-8"
        )
        codex_skill = codex_staged / "skills" / "demo" / "SKILL.md"
        codex_skill.parent.mkdir(parents=True)
        codex_skill.write_text(
            "---\nname: demo\ndescription: d\n---\n# demo\n", encoding="utf-8"
        )
        command = _command_capability("tune.md")
        skill = CapabilityRef(
            capability_id="skill:skills/demo",
            kind=CapabilityKind.SKILL,
            source_path="skills/demo",
            source_dialect=None,
        )
        document = CommandDocument(
            capability_id="command:commands/tune.md",
            source_path="commands/tune.md",
            name="tune",
            body="# Tune\n\nIndependent flow text.\n",
            referenced_skill_dir=None,
        )
        config = OverrideConfig(
            schema_version=1,
            default_targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
            overrides={},
            command_bindings={
                "command:commands/tune.md": CommandBinding(
                    capability_id="command:commands/tune.md",
                    codex_skill_dir="demo",
                    reason="Codex uses the existing demo skill as the command entry",
                )
            },
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(command, skill),
            staged_roots={
                Target.CURSOR: str(cursor_staged),
                Target.CODEX: str(codex_staged),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
            override_config=config,
            commands=(document,),
        )
        inventories = {
            Target.CURSOR: ("commands/tune.md", "skills/demo/SKILL.md"),
            Target.CODEX: ("skills/demo/SKILL.md",),
        }
        payload = _compatibility_payload(result, inventories)
        by_id = {row["id"]: row for row in payload["capabilities"]}
        statuses = {
            item["target"]: item["status"]
            for item in by_id["command:commands/tune.md"]["targets"]
        }
        assert statuses["codex"] == "carried_by_skill"
        assert statuses["cursor"] == "staged_artifact_present"
        assert statuses["codex"] != "declared_unsupported"
        assert statuses["codex"] != "blocked_missing_artifact"


def _write(path: Path, content: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _PassingRunner:
    """Structure-only command stub. Does not perform official validation."""

    structure_only = True

    def __call__(self, command) -> CommandResult:
        command = tuple(command)
        if command[:3] == ("claude", "plugin", "validate"):
            return CommandResult(0, "Validation passed\n", "")
        return CommandResult(0, "Plugin validation passed: demo-plugin\n", "")


@contextmanager
def _codex_validator(root: Path):
    validator = root / "validate_plugin.py"
    _write(validator, "# synthetic validator path\n")
    previous = os.environ.get("CODEX_PLUGIN_VALIDATOR")
    os.environ["CODEX_PLUGIN_VALIDATOR"] = str(validator)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CODEX_PLUGIN_VALIDATOR", None)
        else:
            os.environ["CODEX_PLUGIN_VALIDATOR"] = previous


def test_compatibility_marks_cursor_only_theme_declared_unsupported_on_codex():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_staged = root / "intermediates" / "cursor" / "demo-plugin"
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        theme_path = cursor_staged / "themes" / "dark.json"
        theme_path.parent.mkdir(parents=True)
        theme_path.write_text('{"name": "dark"}\n', encoding="utf-8")
        theme = CapabilityRef(
            capability_id="theme:themes/dark.json",
            kind=CapabilityKind.THEME,
            source_path="themes/dark.json",
            source_dialect=None,
        )
        config = OverrideConfig(
            schema_version=1,
            default_targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
            overrides={
                "theme:themes/dark.json": OverrideEntry(
                    capability_id="theme:themes/dark.json",
                    targets=(Target.CURSOR,),
                    reason="Cursor-only theme exception",
                )
            },
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
            capabilities=(theme,),
            staged_roots={
                Target.CURSOR: str(cursor_staged),
                Target.CODEX: str(codex_staged),
                Target.CLAUDE_CODE: str(codex_staged),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
                Target.CLAUDE_CODE: ValidationReport(
                    target=Target.CLAUDE_CODE,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
            override_config=config,
        )
        inventories = {
            Target.CURSOR: ("themes/dark.json",),
            Target.CODEX: (),
            Target.CLAUDE_CODE: (),
        }
        payload = _compatibility_payload(result, inventories)
        by_id = {row["id"]: row for row in payload["capabilities"]}
        theme_row = by_id["theme:themes/dark.json"]
        statuses = {
            item["target"]: item["status"] for item in theme_row["targets"]
        }
        assert statuses["cursor"] == "staged_artifact_present"
        assert statuses["codex"] == "declared_unsupported"
        assert statuses["claude-code"] == "declared_unsupported"

        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "已声明不进入该平台" in markdown
        assert "三端行为等价" not in markdown


def test_compatibility_marks_convertible_agent_carried_by_skill_on_codex():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_staged = root / "intermediates" / "cursor" / "demo-plugin"
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        agent_path = cursor_staged / "agents" / "researcher.md"
        agent_path.parent.mkdir(parents=True)
        agent_path.write_text(
            "---\nname: researcher\ndescription: Research helper\n---\n"
            "# Researcher\n",
            encoding="utf-8",
        )
        codex_skill = codex_staged / "skills" / "researcher" / "SKILL.md"
        codex_skill.parent.mkdir(parents=True)
        codex_skill.write_text(
            "---\nname: researcher\ndescription: Research helper\n---\n"
            "# Researcher\n",
            encoding="utf-8",
        )
        agent = CapabilityRef(
            capability_id="agent:agents/researcher.md",
            kind=CapabilityKind.AGENT,
            source_path="agents/researcher.md",
            source_dialect=None,
        )
        document = AgentDocument(
            capability_id="agent:agents/researcher.md",
            source_path="agents/researcher.md",
            name="researcher",
            body="# Researcher\n",
            frontmatter_fields=(
                ("name", "researcher"),
                ("description", "Research helper"),
            ),
            convertible_to_skill=True,
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(agent,),
            staged_roots={
                Target.CURSOR: str(cursor_staged),
                Target.CODEX: str(codex_staged),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
            agents=(document,),
        )
        inventories = {
            Target.CURSOR: ("agents/researcher.md",),
            Target.CODEX: ("skills/researcher/SKILL.md",),
        }
        payload = _compatibility_payload(result, inventories)
        by_id = {row["id"]: row for row in payload["capabilities"]}
        agent_row = by_id["agent:agents/researcher.md"]
        statuses = {
            item["target"]: item["status"] for item in agent_row["targets"]
        }
        assert statuses["codex"] == "carried_by_skill"
        assert statuses["cursor"] == "staged_artifact_present"
        assert statuses["codex"] != "declared_unsupported"
        assert statuses["codex"] != "blocked_missing_artifact"

        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "carried_by_skill" in markdown
        assert "由技能承载" in markdown


def test_isolated_agent_is_not_carried_by_skill_on_codex():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        agent = CapabilityRef(
            capability_id="agent:agents/isolated.md",
            kind=CapabilityKind.AGENT,
            source_path="agents/isolated.md",
            source_dialect=None,
        )
        document = AgentDocument(
            capability_id="agent:agents/isolated.md",
            source_path="agents/isolated.md",
            name="isolated",
            body="# Isolated\n",
            frontmatter_fields=(
                ("name", "isolated"),
                ("description", "Isolated agent"),
                ("tools", "read"),
            ),
            convertible_to_skill=False,
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CODEX,),
            capabilities=(agent,),
            staged_roots={Target.CODEX: str(root / "codex")},
            validation={
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(root / "codex"),
                    issues=(),
                ),
            },
            published_roots={},
            agents=(document,),
        )
        inventories = {Target.CODEX: ()}
        payload = _compatibility_payload(result, inventories)
        statuses = {
            item["target"]: item["status"]
            for item in payload["capabilities"][0]["targets"]
        }
        assert statuses["codex"] != "carried_by_skill"


def test_convertible_workflow_carried_by_skill_on_codex():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        workflow = CapabilityRef(
            capability_id="workflow:workflows/review.md",
            kind=CapabilityKind.WORKFLOW,
            source_path="workflows/review.md",
            source_dialect=None,
        )
        document = WorkflowDocument(
            capability_id="workflow:workflows/review.md",
            source_path="workflows/review.md",
            name="review",
            body="# Review\n",
            frontmatter_fields=(
                ("name", "review"),
                ("description", "Review flow"),
            ),
            convertible_to_skill=True,
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CODEX,),
            capabilities=(workflow,),
            staged_roots={Target.CODEX: str(root / "codex")},
            validation={
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(root / "codex"),
                    issues=(),
                ),
            },
            published_roots={},
            workflows=(document,),
        )
        inventories = {Target.CODEX: ("skills/review/SKILL.md",)}
        payload = _compatibility_payload(result, inventories)
        statuses = {
            item["target"]: item["status"]
            for item in payload["capabilities"][0]["targets"]
        }
        assert statuses["codex"] == "carried_by_skill"


def test_convertible_workflow_carried_by_skill_on_cursor():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        workflow = CapabilityRef(
            capability_id="workflow:workflows/review.md",
            kind=CapabilityKind.WORKFLOW,
            source_path="workflows/review.md",
            source_dialect=None,
        )
        document = WorkflowDocument(
            capability_id="workflow:workflows/review.md",
            source_path="workflows/review.md",
            name="review",
            body="# Review\n",
            frontmatter_fields=(
                ("name", "review"),
                ("description", "Review flow"),
            ),
            convertible_to_skill=True,
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE),
            capabilities=(workflow,),
            staged_roots={
                Target.CURSOR: str(root / "cursor"),
                Target.CODEX: str(root / "codex"),
                Target.CLAUDE_CODE: str(root / "claude"),
            },
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(root / "cursor"),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(root / "codex"),
                    issues=(),
                ),
                Target.CLAUDE_CODE: ValidationReport(
                    target=Target.CLAUDE_CODE,
                    plugin_root=str(root / "claude"),
                    issues=(),
                ),
            },
            published_roots={},
            workflows=(document,),
        )
        inventories = {
            Target.CURSOR: ("skills/review/SKILL.md",),
            Target.CODEX: ("skills/review/SKILL.md",),
            Target.CLAUDE_CODE: ("workflows/review.md",),
        }
        payload = _compatibility_payload(result, inventories)
        statuses = {
            item["target"]: item["status"]
            for item in payload["capabilities"][0]["targets"]
        }
        assert statuses["cursor"] == "carried_by_skill"
        assert statuses["codex"] == "carried_by_skill"
        assert statuses["cursor"] != "blocked_missing_artifact"
        by_target = {
            item["target"]: item
            for item in payload["capabilities"][0]["targets"]
        }
        assert "skills/review/SKILL.md" in by_target["cursor"]["artifacts"]
        assert statuses["claude-code"] != "carried_by_skill"


def test_user_hooks_artifacts_add_hook_trust_reminder():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        codex_staged = root / "intermediates" / "codex" / "demo-plugin"
        hooks_json = codex_staged / "hooks" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text('{"hooks": {}}\n', encoding="utf-8")
        script = codex_staged / "hooks" / "notify.py"
        script.write_text("print('ok')\n", encoding="utf-8")
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CODEX, Target.CLAUDE_CODE),
            capabilities=(),
            staged_roots={
                Target.CODEX: str(codex_staged),
                Target.CLAUDE_CODE: str(codex_staged),
            },
            validation={
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
                Target.CLAUDE_CODE: ValidationReport(
                    target=Target.CLAUDE_CODE,
                    plugin_root=str(codex_staged),
                    issues=(),
                ),
            },
            published_roots={},
        )
        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert (
            "Codex / Claude Code 插件钩子需宿主信任后才会真正注入。"
            in markdown
        )


def test_compile_with_three_platform_rule_adds_hook_trust_reminder_and_stays_ok():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = root / "source" / "demo"
        _write(
            plugin / ".cursor-plugin" / "plugin.json",
            json.dumps(
                {
                    "name": "demo-plugin",
                    "version": "1.0.0",
                    "description": "Synthetic plugin",
                    "skills": "./skills/",
                }
            )
            + "\n",
        )
        _write(
            plugin / "skills" / "demo" / "SKILL.md",
            "---\nname: demo\ndescription: Synthetic\n---\n# Demo\n",
        )
        _write(
            plugin / "packaging" / "platform-overrides.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "default_targets": ["cursor", "codex", "claude-code"],
                    "overrides": {},
                }
            )
            + "\n",
        )
        _write(
            plugin / "rules" / "always.mdc",
            "---\ndescription: Always\nalwaysApply: true\n---\n# Always\n",
        )
        layout = BuildLayout.from_repo(root)
        with _codex_validator(root):
            result = compile_plugin(
                plugin,
                root,
                tuple(Target),
                validate_only=True,
                runner=_PassingRunner(),
            )
        assert result.ok
        markdown = (layout.report_dir("demo-plugin") / "report.md").read_text(
            encoding="utf-8"
        )
        assert "Codex / Claude Code 插件钩子需宿主信任后才会真正注入" in markdown
        assert "三端行为等价" not in markdown
        compatibility = json.loads(
            (layout.report_dir("demo-plugin") / "compatibility.json").read_text(
                encoding="utf-8"
            )
        )
        by_id = {row["id"]: row for row in compatibility["capabilities"]}
        rule_row = by_id["rule:rules/always.mdc"]
        rule_statuses = {
            item["target"]: item["status"] for item in rule_row["targets"]
        }
        assert rule_statuses["cursor"] == "staged_artifact_present"
        assert rule_statuses["codex"] == "staged_artifact_present"
        assert rule_statuses["claude-code"] == "staged_artifact_present"


def test_compile_result_is_not_ok_when_any_requested_validation_is_missing():
    with TemporaryDirectory() as tmp:
        result = _result(Path(tmp))
        assert not result.ok

        only_missing = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR,),
            capabilities=(),
            staged_roots={},
            validation={},
            published_roots={},
        )
        assert not only_missing.ok


def test_markdown_does_not_treat_existing_directory_as_this_round_publish():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor_published = root / "outputs" / "cursor" / "demo-plugin"
        cursor_published.mkdir(parents=True)
        leftover_codex = root / "outputs" / "codex" / "demo-plugin"
        leftover_codex.mkdir(parents=True)
        (leftover_codex / "old.txt").write_text(
            "stale leftover\n", encoding="utf-8"
        )
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(_capability(),),
            staged_roots={},
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(cursor_published),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(leftover_codex),
                    issues=(
                        ValidationIssue(
                            severity="block",
                            code="missing-target-artifact",
                            path="skills/demo",
                            message="Codex blocked",
                        ),
                    ),
                ),
            },
            published_roots={Target.CURSOR: str(cursor_published)},
        )
        assert not result.ok

        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert str(cursor_published) in markdown
        assert "`codex`: 本轮未发布" in markdown
        assert str(leftover_codex) not in markdown
        assert "全部请求目标已通过" not in markdown
        assert "本轮没有新的正式包" not in markdown

        validation = json.loads(
            (report_dir / "validation.json").read_text(encoding="utf-8")
        )
        by_target = {item["target"]: item for item in validation["validation"]}
        assert by_target["cursor"]["published"] is True
        assert by_target["codex"]["published"] is False

        artifacts = json.loads(
            (report_dir / "artifacts.json").read_text(encoding="utf-8")
        )
        assert [item["target"] for item in artifacts["published_roots"]] == ["cursor"]
        assert artifacts["published_roots"][0]["published_root"] == str(
            cursor_published
        )


def _dual_skill_plugin(root: Path) -> Path:
    plugin = root / "source" / "demo"
    _write(
        plugin / ".cursor-plugin" / "plugin.json",
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "description": "Synthetic plugin",
                "skills": "./skills/",
            }
        )
        + "\n",
    )
    _write(
        plugin / "skills" / "a" / "SKILL.md",
        "---\nname: a\ndescription: Synthetic\n---\nDo the work\nThen stop\n",
    )
    _write(
        plugin / "skills" / "b" / "SKILL.md",
        "---\nname: b\ndescription: Synthetic\n---\ndo the work\nTHEN STOP\n",
    )
    _write(
        plugin / "packaging" / "platform-overrides.json",
        json.dumps(
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
            }
        )
        + "\n",
    )
    return plugin


def test_overlap_section_lists_content_duplicate_and_skill_paths():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = _dual_skill_plugin(root)
        layout = BuildLayout.from_repo(root)
        compile_plugin(
            plugin,
            root,
            (Target.CURSOR,),
            validate_only=True,
            runner=_PassingRunner(),
        )
        report = layout.report_dir("demo-plugin") / "report.md"
        text = report.read_text(encoding="utf-8")
        assert "## Overlap" in text or "## 重叠" in text
        overlap_at = text.find("## Overlap")
        if overlap_at < 0:
            overlap_at = text.find("## 重叠")
        overlap = text[overlap_at:]
        assert "content-duplicate" in overlap
        assert "skills/a/SKILL.md" in overlap
        assert "skills/b/SKILL.md" in overlap


def test_allow_overlap_overlap_section_says_gate_b_demoted_to_warning():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = _dual_skill_plugin(root)
        layout = BuildLayout.from_repo(root)
        compile_plugin(
            plugin,
            root,
            (Target.CURSOR,),
            validate_only=True,
            runner=_PassingRunner(),
            allow_overlap=True,
        )
        report = layout.report_dir("demo-plugin") / "report.md"
        text = report.read_text(encoding="utf-8")
        assert "门 B 已按本次命令改为警告" in text


def _mcp_capability() -> CapabilityRef:
    return CapabilityRef(
        capability_id="mcp:.mcp.json",
        kind=CapabilityKind.MCP,
        source_path=".mcp.json",
        source_dialect=None,
    )


def _mcp_document() -> McpDocument:
    return McpDocument(
        capability_id="mcp:.mcp.json",
        source_path=".mcp.json",
        servers={
            "demo": {
                "command": "python3",
                "args": ["${PLUGIN_ROOT}/scripts/server.py"],
            }
        },
    )


def test_mcp_missing_script_compatibility_is_blocked_missing_artifact():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        staged = root / "intermediates" / "codex" / "demo-plugin"
        staged.mkdir(parents=True)
        (staged / ".mcp.json").write_text("{}\n", encoding="utf-8")
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CODEX,),
            capabilities=(_mcp_capability(),),
            staged_roots={Target.CODEX: str(staged)},
            validation={
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(staged),
                    issues=(),
                )
            },
            published_roots={},
            mcp_documents=(_mcp_document(),),
        )
        inventories = {Target.CODEX: (".mcp.json",)}
        payload = _compatibility_payload(result, inventories)
        statuses = {
            item["target"]: item["status"]
            for item in payload["capabilities"][0]["targets"]
        }
        assert statuses["codex"] == "blocked_missing_artifact"


def test_unexpected_bin_compatibility_is_unexpected_artifact_present():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        staged = root / "intermediates" / "codex" / "demo-plugin"
        skill = staged / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# Demo\n", encoding="utf-8")
        (staged / "unexpected.bin").write_bytes(b"\x00")
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CODEX,),
            capabilities=(_capability(),),
            staged_roots={Target.CODEX: str(staged)},
            validation={
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(staged),
                    issues=(),
                )
            },
            published_roots={},
        )
        inventories = {
            Target.CODEX: ("skills/demo/SKILL.md", "unexpected.bin"),
        }
        payload = _compatibility_payload(result, inventories)
        statuses = [
            item["status"]
            for row in payload["capabilities"]
            for item in row["targets"]
            if item["target"] == "codex"
        ]
        assert "unexpected_artifact_present" in statuses


def test_declared_file_resource_is_not_unexpected_artifact():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        staged = root / "intermediates" / "cursor" / "demo-plugin"
        skill = staged / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# Demo\n", encoding="utf-8")
        declared = staged / "scripts" / "helper_paths.py"
        declared.parent.mkdir(parents=True)
        declared.write_text("PLUGIN_ROOT = '.'\n", encoding="utf-8")
        (staged / "unexpected.bin").write_bytes(b"\x00")
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR,),
            capabilities=(_capability(),),
            staged_roots={Target.CURSOR: str(staged)},
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(staged),
                    issues=(),
                )
            },
            published_roots={},
            declared_resources=(
                DeclaredResource(
                    source_path="scripts/helper_paths.py",
                    kind="file",
                ),
            ),
        )
        inventories = {
            Target.CURSOR: (
                "scripts/helper_paths.py",
                "skills/demo/SKILL.md",
                "unexpected.bin",
            ),
        }
        payload = _compatibility_payload(result, inventories)
        unexpected_paths = [
            row["source_path"]
            for row in payload["capabilities"]
            if row.get("kind") == "unexpected-artifact"
        ]
        assert "scripts/helper_paths.py" not in unexpected_paths
        assert "unexpected.bin" in unexpected_paths
        gated = apply_compatibility_gate(result)
        assert gated.ok is False
        unexpected_issues = [
            issue
            for issue in gated.validation[Target.CURSOR].issues
            if issue.code == "unexpected-artifact-present"
        ]
        assert unexpected_issues
        assert all(
            issue.path != "scripts/helper_paths.py" for issue in unexpected_issues
        )
        assert any(issue.path == "unexpected.bin" for issue in unexpected_issues)


def test_custom_skill_root_remap_is_not_missing_or_unexpected():
    cap = CapabilityRef(
        capability_id="skill:custom-skills/codex-origin",
        kind=CapabilityKind.SKILL,
        source_path="custom-skills/codex-origin",
        source_dialect="codex",
    )
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        staged = root / "intermediates" / "codex" / "demo-plugin"
        skill = staged / "skills" / "codex-origin" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# Codex Origin\n", encoding="utf-8")
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CODEX,),
            capabilities=(cap,),
            staged_roots={Target.CODEX: str(staged)},
            validation={
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(staged),
                    issues=(),
                )
            },
            published_roots={},
        )
        inventories = {Target.CODEX: ("skills/codex-origin/SKILL.md",)}
        payload = _compatibility_payload(result, inventories)
        skill_row = next(
            row for row in payload["capabilities"] if row["id"] == cap.capability_id
        )
        assert skill_row["targets"][0]["status"] == "staged_artifact_present"
        assert "skills/codex-origin/SKILL.md" in skill_row["targets"][0]["artifacts"]
        unexpected = [
            row["source_path"]
            for row in payload["capabilities"]
            if row.get("kind") == "unexpected-artifact"
        ]
        assert unexpected == []


def test_compatibility_gate_makes_validation_json_ok_false():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        staged = root / "intermediates" / "codex" / "demo-plugin"
        staged.mkdir(parents=True)
        (staged / ".mcp.json").write_text("{}\n", encoding="utf-8")
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CODEX,),
            capabilities=(_mcp_capability(),),
            staged_roots={Target.CODEX: str(staged)},
            validation={
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(staged),
                    issues=(),
                )
            },
            published_roots={},
            mcp_documents=(_mcp_document(),),
        )
        assert result.ok
        gated = apply_compatibility_gate(result)
        assert gated.ok is False
        report_dir = root / "reports"
        write_reports(gated, report_dir)
        validation = json.loads(
            (report_dir / "validation.json").read_text(encoding="utf-8")
        )
        assert validation["ok"] is False
        assert any(
            issue["severity"] == "block" for issue in validation["issues"]
        )


def test_validate_only_markdown_says_outputs_unchanged():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        staged = root / "intermediates" / "cursor" / "demo-plugin"
        staged.mkdir(parents=True)
        result = CompileResult(
            plugin_name="demo-plugin",
            targets=(Target.CURSOR, Target.CODEX),
            capabilities=(_capability(),),
            staged_roots={Target.CURSOR: str(staged)},
            validation={
                Target.CURSOR: ValidationReport(
                    target=Target.CURSOR,
                    plugin_root=str(staged),
                    issues=(),
                ),
                Target.CODEX: ValidationReport(
                    target=Target.CODEX,
                    plugin_root=str(staged),
                    issues=(),
                ),
            },
            published_roots={},
            validate_only=True,
        )
        assert result.ok

        report_dir = root / "reports"
        write_reports(result, report_dir)
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        assert "未改正式输出" in markdown
        assert "全部请求目标已通过结构校验并完成发布" not in markdown
        assert "本轮没有新的正式包" not in markdown

        validation = json.loads(
            (report_dir / "validation.json").read_text(encoding="utf-8")
        )
        assert [item["published"] for item in validation["validation"]] == [
            False,
            False,
        ]


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
