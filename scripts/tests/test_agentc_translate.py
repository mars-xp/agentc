#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def _project_with_skill(dest: Path) -> None:
    from agentc.project.create_skill import create_skill
    from agentc.project.init_project import init_project

    issues = init_project(dest, project_id="acme", name="Acme", targets=("cursor",))
    assert not any(i.level == "error" for i in issues)
    skill_issues = create_skill(dest, "hello")
    assert not any(i.level == "error" for i in skill_issues)


def _project_with_skill_and_rule(dest: Path) -> None:
    from agentc.project.create_extension import create_rule

    _project_with_skill(dest)
    rule_issues = create_rule(dest, "house-style")
    assert not any(i.level == "error" for i in rule_issues)


def _project_with_skill_and_command(dest: Path) -> None:
    from agentc.project.create_extension import create_command

    _project_with_skill(dest)
    command_issues = create_command(dest, "do-thing")
    assert not any(i.level == "error" for i in command_issues)


def _project_with_skill_and_agent(dest: Path) -> None:
    from agentc.project.create_extension import create_agent

    _project_with_skill(dest)
    agent_issues = create_agent(dest, "reviewer")
    assert not any(i.level == "error" for i in agent_issues)


def test_translate_empty_rules_commands_agents():
    from agentc.build.translate import translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir
    from plugin_compiler.model import CapabilityKind

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest)
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        parsed = translate_to_parsed(dest, ir)
        assert parsed.rules == ()
        assert parsed.commands == ()
        assert parsed.agents == ()
        assert parsed.hooks == ()
        assert len(parsed.skills) == 1
        assert parsed.skills[0].name == "hello"
        assert parsed.source_dialect == "agent-project"
        assert parsed.plugin_dir.resolve() == dest.resolve()
        assert {cap.kind for cap in parsed.capabilities} == {CapabilityKind.SKILL}


def test_translate_request_uses_project_build_layout():
    from agentc.build.translate import translate_to_request
    from agentc.project.load import load_project_yaml, to_ir
    from plugin_compiler.paths import BuildLayout

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest)
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        request = translate_to_request(dest, ir)
        assert request.layout.root == BuildLayout.from_repo(dest).root
        assert request.runner is None
        assert request.parsed.plugin_name == "acme"


def test_translate_rule_source_path_is_file():
    from agentc.build.translate import translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir
    from plugin_compiler.model import CapabilityKind

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_rule(dest)
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        parsed = translate_to_parsed(dest, ir)
        assert len(parsed.rules) == 1
        assert parsed.rules[0].source_path == "extensions/rules/house-style.md"
        rule_caps = [c for c in parsed.capabilities if c.kind == CapabilityKind.RULE]
        assert len(rule_caps) == 1
        assert rule_caps[0].source_path == "extensions/rules/house-style.md"
        assert rule_caps[0].capability_id == "rule:extensions/rules/house-style.md"
        assert (dest / rule_caps[0].source_path).is_file()
        skill_caps = [c for c in parsed.capabilities if c.kind == CapabilityKind.SKILL]
        assert skill_caps[0].source_path == "skills/hello"
        assert not skill_caps[0].source_path.endswith("SKILL.md")


def test_translate_command_source_path_is_file():
    from agentc.build.translate import translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir
    from plugin_compiler.model import CapabilityKind

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_command(dest)
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        parsed = translate_to_parsed(dest, ir)
        assert len(parsed.commands) == 1
        assert parsed.commands[0].source_path == "extensions/commands/do-thing.md"
        command_caps = [c for c in parsed.capabilities if c.kind == CapabilityKind.COMMAND]
        assert len(command_caps) == 1
        assert command_caps[0].source_path == "extensions/commands/do-thing.md"
        assert command_caps[0].capability_id == "command:extensions/commands/do-thing.md"
        assert (dest / command_caps[0].source_path).is_file()
        skill_caps = [c for c in parsed.capabilities if c.kind == CapabilityKind.SKILL]
        assert skill_caps[0].source_path == "skills/hello"
        assert not skill_caps[0].source_path.endswith("SKILL.md")


def test_translate_agent_source_path_is_file():
    from agentc.build.translate import translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir
    from plugin_compiler.model import CapabilityKind

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_agent(dest)
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        parsed = translate_to_parsed(dest, ir)
        assert len(parsed.agents) == 1
        assert parsed.agents[0].source_path == "extensions/agents/reviewer.md"
        agent_caps = [c for c in parsed.capabilities if c.kind == CapabilityKind.AGENT]
        assert len(agent_caps) == 1
        assert agent_caps[0].source_path == "extensions/agents/reviewer.md"
        assert agent_caps[0].capability_id == "agent:extensions/agents/reviewer.md"
        assert (dest / agent_caps[0].source_path).is_file()
        skill_caps = [c for c in parsed.capabilities if c.kind == CapabilityKind.SKILL]
        assert skill_caps[0].source_path == "skills/hello"
        assert not skill_caps[0].source_path.endswith("SKILL.md")


def test_translate_missing_rule_file_raises():
    from agentc.build.translate import TranslateError, translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_rule(dest)
        (dest / "extensions" / "rules" / "house-style.md").unlink()
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        try:
            translate_to_parsed(dest, ir)
        except TranslateError:
            return
        raise AssertionError("expected TranslateError")


def test_translate_overlay_identity_lift():
    import json

    from agentc.build.translate import translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest)
        overlay_dir = dest / "overlays" / "codex"
        (overlay_dir / "plugin.json").write_text(
            json.dumps(
                [
                    {"op": "set", "field": "name", "value": "overlay-name"},
                    {"op": "set", "field": "version", "value": "9.9.9"},
                    {"op": "set", "field": "author", "value": {"name": "Overlay Author"}},
                    {"op": "set", "field": "interface", "value": {"displayName": "Test"}},
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        parsed = translate_to_parsed(dest, ir)
        assert parsed.identity.name == "overlay-name"
        assert parsed.identity.version == "9.9.9"
        assert parsed.identity.author == {"name": "Overlay Author"}
        assert len(parsed.target_overlays) == 1
        overlay = parsed.target_overlays[0]
        assert overlay.target_id == "codex"
        assert "name" not in overlay.fields
        assert "author" not in overlay.fields
        assert "version" not in overlay.fields
        assert overlay.fields["interface"] == {"displayName": "Test"}


def test_translate_invalid_overlay_json_raises():
    from agentc.build.translate import TranslateError, translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest)
        (dest / "overlays" / "cursor" / "plugin.json").write_text(
            "{not json", encoding="utf-8"
        )
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        try:
            translate_to_parsed(dest, ir)
        except TranslateError:
            return
        raise AssertionError("expected TranslateError")


def test_translate_missing_skill_md_raises():
    from agentc.build.translate import TranslateError, translate_to_parsed
    from agentc.project.load import load_project_yaml, to_ir

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest)
        (dest / "skills" / "hello" / "SKILL.md").unlink()
        ir = to_ir(load_project_yaml(dest / "agent-project.yaml"))
        try:
            translate_to_parsed(dest, ir)
        except TranslateError:
            return
        raise AssertionError("expected TranslateError")


def main() -> int:
    tests = (
        test_translate_empty_rules_commands_agents,
        test_translate_request_uses_project_build_layout,
        test_translate_rule_source_path_is_file,
        test_translate_command_source_path_is_file,
        test_translate_agent_source_path_is_file,
        test_translate_missing_rule_file_raises,
        test_translate_overlay_identity_lift,
        test_translate_invalid_overlay_json_raises,
        test_translate_missing_skill_md_raises,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
