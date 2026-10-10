#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_HOSTS = ("cursor", "codex", "claude-code")
_BUNDLE_HOSTS = ("codex", "claude-code")
_ALWAYS = (
    "---\n"
    "description: Always on helper\n"
    "alwaysApply: true\n"
    "---\n"
    "# Body\n"
    "\n"
    "Keep this.\n"
)


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


def _assert_no_compiled_rules_dir(outputs: Path, host: str) -> None:
    root = outputs / host
    assert root.is_dir(), "missing outputs for {0}".format(host)
    for path in root.rglob("*"):
        if path.is_dir() and path.name == "compiled-rules":
            raise AssertionError(
                "unexpected compiled-rules directory: {0}".format(_posix(path))
            )


def _assert_compiled_rules_index(outputs: Path, host: str) -> None:
    root = outputs / host
    assert root.is_dir(), "missing outputs for {0}".format(host)
    matches = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and "compiled-rules" in _posix(path)
        and _posix(path).endswith("/index.json")
    ]
    assert matches, "missing compiled-rules/index.json under {0}".format(host)


def test_skill_only_does_not_emit_compiled_rules():
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
            _assert_no_compiled_rules_dir(outputs, host)


def test_always_on_rule_emits_compiled_rules_on_codex_and_claude():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.project.create_extension import create_rule
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        rule_issues = create_rule(dest, "always-on")
        assert not any(i.level == "error" for i in rule_issues)
        rule_path = dest / "extensions" / "rules" / "always-on.md"
        rule_path.write_text(_ALWAYS, encoding="utf-8", newline="\n")
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
        _assert_no_compiled_rules_dir(outputs, "cursor")
        for host in _BUNDLE_HOSTS:
            _assert_compiled_rules_index(outputs, host)


def main() -> int:
    tests = (
        test_skill_only_does_not_emit_compiled_rules,
        test_always_on_rule_emits_compiled_rules_on_codex_and_claude,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
