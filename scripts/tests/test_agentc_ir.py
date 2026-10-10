#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def _full_project() -> dict:
    return {
        "schemaVersion": "agent-project/v1",
        "project": {"id": "demo", "name": "Demo", "version": "0.1.0"},
        "targets": {
            "cursor": {"enabled": True},
            "codex": {"enabled": False},
            "claude-code": {"enabled": True},
        },
        "contents": {"skills": ["skills/hello", "skills/world"]},
        "defaults": {
            "scopes": ["workspace"],
            "conflictPolicy": "fail",
            "portabilityPolicy": "compatible",
        },
        "requirements": {
            "executables": ["git", "python3"],
            "environment": {
                "optional": ["GITHUB_TOKEN"],
                "required": ["HOME"],
            },
        },
        "security": {"forbidAbsolutePaths": False},
    }


def test_to_ir_returns_frozen_object():
    from agentc.project.load import to_ir

    ir = to_ir(_full_project())
    try:
        ir.project_id = "other"
    except FrozenInstanceError:
        return
    raise AssertionError("CanonicalProject must be frozen")


def test_skill_paths_from_contents_is_tuple():
    from agentc.project.load import to_ir

    ir = to_ir(_full_project())
    assert ir.skill_paths == ("skills/hello", "skills/world")
    assert isinstance(ir.skill_paths, tuple)


def test_content_paths_cover_every_contents_key():
    from agentc.project.load import to_ir

    data = _full_project()
    data["contents"] = {
        "skills": ["skills/hello"],
        "rules": ["extensions/rules/a.mdc"],
        "commands": ["extensions/commands/c.md"],
        "agents": ["extensions/agents/g.md"],
        "hooks": ["extensions/hooks/h.json"],
        "mcp": ["extensions/mcp/m.json"],
    }
    ir = to_ir(data)
    assert ir.skill_paths == ("skills/hello",)
    assert ir.content_paths == (
        "skills/hello",
        "extensions/rules/a.mdc",
        "extensions/commands/c.md",
        "extensions/agents/g.md",
        "extensions/hooks/h.json",
        "extensions/mcp/m.json",
    )


def test_requirement_names_are_names_only():
    from agentc.project.load import to_ir

    ir = to_ir(_full_project())
    assert ir.requirement_names == ("git", "python3", "GITHUB_TOKEN", "HOME")
    assert all("=" not in name and "://" not in name for name in ir.requirement_names)


def test_requirement_names_skip_credential_shaped_values():
    from agentc.project.load import to_ir

    data = {
        "schemaVersion": "agent-project/v1",
        "project": {"id": "demo", "name": "Demo", "version": "0.1.0"},
        "requirements": {
            "executables": ["git"],
            "environment": {
                "optional": ["GITHUB_TOKEN=ghp_not_a_real_token"],
                "required": ["https://example.invalid/token"],
            },
        },
    }
    ir = to_ir(data)
    assert ir.requirement_names == ("git",)


def test_forbid_absolute_paths_from_security():
    from agentc.project.load import to_ir

    ir = to_ir(_full_project())
    assert ir.forbid_absolute_paths is False


def test_forbid_absolute_paths_defaults_true():
    from agentc.project.load import to_ir

    data = {
        "schemaVersion": "agent-project/v1",
        "project": {"id": "demo", "name": "Demo", "version": "0.1.0"},
    }
    ir = to_ir(data)
    assert ir.forbid_absolute_paths is True


def test_portability_policy_and_scopes():
    from agentc.project.load import to_ir

    ir = to_ir(_full_project())
    assert ir.portability_policy == "compatible"
    assert ir.scopes == ("workspace",)
    assert isinstance(ir.scopes, tuple)


def test_portability_policy_defaults_strict():
    from agentc.project.load import to_ir

    data = {
        "schemaVersion": "agent-project/v1",
        "project": {"id": "demo", "name": "Demo", "version": "0.1.0"},
    }
    ir = to_ir(data)
    assert ir.portability_policy == "strict"
    assert ir.skill_paths == ()
    assert ir.content_paths == ()
    assert ir.requirement_names == ()
    assert ir.targets == {}
    assert ir.scopes == ()


def test_targets_enabled_mapping_is_immutable():
    from agentc.project.load import to_ir

    ir = to_ir(_full_project())
    assert dict(ir.targets) == {
        "cursor": True,
        "codex": False,
        "claude-code": True,
    }
    try:
        ir.targets["cursor"] = False  # type: ignore[index]
    except TypeError:
        return
    raise AssertionError("targets mapping must be immutable")


def test_identity_fields():
    from agentc.project.load import to_ir

    ir = to_ir(_full_project())
    assert ir.project_id == "demo"
    assert ir.name == "Demo"
    assert ir.version == "0.1.0"


def test_to_ir_does_not_mutate_input_dict():
    from agentc.project.load import to_ir

    data = _full_project()
    snapshot = copy.deepcopy(data)
    before_id = id(data)
    to_ir(data)
    assert id(data) == before_id
    assert data == snapshot


def test_to_ir_rejects_invalid_portability_policy():
    from agentc.project.load import to_ir

    data = {
        "schemaVersion": "agent-project/v1",
        "project": {"id": "demo", "name": "Demo", "version": "0.1.0"},
        "defaults": {"portabilityPolicy": "host-only"},
    }
    try:
        to_ir(data)
    except ValueError:
        return
    raise AssertionError("invalid portabilityPolicy must be rejected")


def main() -> int:
    tests = (
        test_to_ir_returns_frozen_object,
        test_skill_paths_from_contents_is_tuple,
        test_content_paths_cover_every_contents_key,
        test_requirement_names_are_names_only,
        test_requirement_names_skip_credential_shaped_values,
        test_forbid_absolute_paths_from_security,
        test_forbid_absolute_paths_defaults_true,
        test_portability_policy_and_scopes,
        test_portability_policy_defaults_strict,
        test_targets_enabled_mapping_is_immutable,
        test_identity_fields,
        test_to_ir_does_not_mutate_input_dict,
        test_to_ir_rejects_invalid_portability_policy,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
