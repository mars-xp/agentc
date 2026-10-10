#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

MIN_PROJECT = {"id": "demo", "name": "Demo", "version": "0.1.0"}


def _base(**extra):
    data = {
        "schemaVersion": "agent-project/v1",
        "project": dict(MIN_PROJECT),
    }
    data.update(extra)
    return data


def test_unknown_top_level_field_fails():
    from agentc.project.load import validate_schema

    issues = validate_schema(_base(surprise=1))
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_unknown_nested_project_field_fails():
    from agentc.project.load import validate_schema

    data = {
        "schemaVersion": "agent-project/v1",
        "project": {**MIN_PROJECT, "secretToken": "x"},
    }
    issues = validate_schema(data)
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_unknown_nested_targets_field_fails():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(targets={"cursor": {"enabled": True, "apiKey": "x"}})
    )
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_unknown_nested_contents_field_fails():
    from agentc.project.load import validate_schema

    issues = validate_schema(_base(contents={"skills": [], "cacheDir": "tmp"}))
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_unknown_nested_defaults_field_fails():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(defaults={"scopes": ["workspace"], "hostHome": "/tmp"})
    )
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_unknown_nested_requirements_field_fails():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(requirements={"executables": ["git"], "token": "x"})
    )
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_unknown_nested_environment_field_fails():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(
            requirements={
                "environment": {"optional": ["GITHUB_TOKEN"], "secretValue": "x"}
            }
        )
    )
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_unknown_nested_security_field_fails():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(security={"forbidAbsolutePaths": True, "privateKey": "x"})
    )
    assert any(i.code == "AGC-P1-0002" for i in issues)


def test_x_namespace_allowed():
    from agentc.project.load import validate_schema

    issues = validate_schema(_base(**{"x-experimental": {"flag": True}}))
    assert not any(i.code == "AGC-P1-0002" for i in issues)


def test_x_namespace_nested_allowed():
    from agentc.project.load import validate_schema

    data = {
        "schemaVersion": "agent-project/v1",
        "project": {**MIN_PROJECT, "x-internal": {"flag": True}},
        "targets": {"cursor": {"enabled": True, "x-host": {}}},
        "contents": {"skills": [], "x-extra": []},
        "defaults": {"scopes": ["workspace"], "x-policy": "off"},
        "requirements": {
            "executables": ["git"],
            "environment": {"optional": ["GITHUB_TOKEN"], "x-meta": []},
            "x-notes": {},
        },
        "security": {"forbidAbsolutePaths": True, "x-scan": True},
    }
    issues = validate_schema(data)
    assert not any(i.code == "AGC-P1-0002" for i in issues)


def test_requirements_reject_credential_shaped_values():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(
            requirements={
                "environment": {"optional": ["GITHUB_TOKEN=ghp_not_a_real_token"]}
            }
        )
    )
    assert issues


def test_requirements_reject_url_shaped_values():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(
            requirements={
                "environment": {
                    "required": ["https://example.invalid/token?k=1"],
                }
            }
        )
    )
    assert issues


def test_requirements_executables_must_be_name_list():
    from agentc.project.load import validate_schema

    issues = validate_schema(
        _base(requirements={"executables": ["/usr/bin/git"]})
    )
    assert issues


def test_valid_minimal_has_no_unknown_field():
    from agentc.project.load import validate_schema

    issues = validate_schema(_base())
    assert not any(i.code == "AGC-P1-0002" for i in issues)


def test_load_project_yaml_reads_mapping():
    from agentc.project.load import load_project_yaml

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "agent-project.yaml"
        path.write_text(
            "schemaVersion: agent-project/v1\n"
            "project:\n"
            "  id: demo\n"
            "  name: Demo\n"
            "  version: 0.1.0\n",
            encoding="utf-8",
            newline="\n",
        )
        data = load_project_yaml(path)
    assert data["schemaVersion"] == "agent-project/v1"
    assert data["project"]["id"] == "demo"


def main() -> int:
    tests = (
        test_unknown_top_level_field_fails,
        test_unknown_nested_project_field_fails,
        test_unknown_nested_targets_field_fails,
        test_unknown_nested_contents_field_fails,
        test_unknown_nested_defaults_field_fails,
        test_unknown_nested_requirements_field_fails,
        test_unknown_nested_environment_field_fails,
        test_unknown_nested_security_field_fails,
        test_x_namespace_allowed,
        test_x_namespace_nested_allowed,
        test_requirements_reject_credential_shaped_values,
        test_requirements_reject_url_shaped_values,
        test_requirements_executables_must_be_name_list,
        test_valid_minimal_has_no_unknown_field,
        test_load_project_yaml_reads_mapping,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
