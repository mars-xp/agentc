#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_HOSTS = ("cursor", "codex", "claude-code")
_COMMAND_HOSTS = ("cursor", "claude-code")
_NATIVE = {
    "cursor": ".cursor-plugin/plugin.json",
    "codex": ".codex-plugin/plugin.json",
    "claude-code": ".claude-plugin/plugin.json",
}


def _init(dest: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)


def _skill_files(outputs: Path, host: str) -> list[Path]:
    matches = [
        path
        for path in (outputs / host).rglob("*")
        if path.is_file() and "hello" in path.as_posix()
    ]
    assert matches, "missing hello artifact under {0}".format(host)
    return matches


def _posix(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def _manifest(outputs: Path, host: str) -> dict:
    root = outputs / host
    assert root.is_dir(), "missing outputs for {0}".format(host)
    native = _NATIVE[host]
    matches = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and native in _posix(path)
        and _posix(path).endswith("/plugin.json")
    ]
    assert matches, "missing {0} under {1}".format(native, host)
    return json.loads(matches[0].read_text(encoding="utf-8"))


def test_skill_only_manifests_have_skills_not_commands():
    from agentc.build.run import build_project
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(d.level == "error" for d in diagnostics)
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in _HOSTS:
            _skill_files(outputs, host)
            data = _manifest(outputs, host)
            assert data.get("name") == "Acme"
            assert "skills" in data
            assert "rules" not in data
            assert "commands" not in data


def test_command_manifests_omit_commands_on_codex():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.project.create_extension import create_command
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        command_issues = create_command(dest, "ship")
        assert not any(i.level == "error" for i in command_issues)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(
            d.code == CODE_ADAPTER_BLOCK and d.level == "error"
            for d in diagnostics
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in _HOSTS:
            _skill_files(outputs, host)
            data = _manifest(outputs, host)
            assert data.get("name") == "Acme"
            assert "skills" in data
        for host in _COMMAND_HOSTS:
            data = _manifest(outputs, host)
            assert data.get("commands") == "./commands/"
        codex = _manifest(outputs, "codex")
        assert "commands" not in codex


def main() -> int:
    tests = (
        test_skill_only_manifests_have_skills_not_commands,
        test_command_manifests_omit_commands_on_codex,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
