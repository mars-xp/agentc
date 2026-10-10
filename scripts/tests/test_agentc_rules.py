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

_ALWAYS = (
    "---\n"
    "description: Always on helper\n"
    "alwaysApply: true\n"
    "---\n"
    "# Body\n"
    "\n"
    "Keep this.\n"
)

_SCOPED = (
    "---\n"
    "description: Python files only\n"
    "alwaysApply: false\n"
    'globs: "**/*.py"\n'
    "---\n"
    "# Scoped\n"
)

_SOFT = (
    "---\n"
    "description: Only when relevant\n"
    "alwaysApply: false\n"
    "---\n"
    "# Soft\n"
)


def _seed(dest: Path, name: str) -> None:
    from agentc.project.create_extension import create_rule
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)
    rule_issues = create_rule(dest, name)
    assert not any(i.level == "error" for i in rule_issues)


def _write_rule(dest: Path, name: str, body: str) -> None:
    path = dest / "extensions" / "rules" / "{0}.md".format(name)
    path.write_text(body, encoding="utf-8", newline="\n")


def _rule_files(outputs: Path, host: str, name: str) -> list[Path]:
    matches = [
        path
        for path in (outputs / host).rglob("*")
        if path.is_file() and name in path.as_posix()
    ]
    assert matches, "missing {0} rule artifact under {1}".format(name, host)
    return matches


def _rule_blob(outputs: Path, host: str, name: str) -> str:
    parts = []
    for path in _rule_files(outputs, host, name):
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    blob = "\n".join(parts)
    assert blob, "no text artifacts for {0} under {1}".format(name, host)
    return blob


def test_always_on_renders_on_all_hosts():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.validate.engine import validate_project

    name = "always-on"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest, name)
        _write_rule(dest, name, _ALWAYS)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(
            d.code == CODE_ADAPTER_BLOCK and d.level == "error"
            for d in diagnostics
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        cursor = _rule_blob(outputs, "cursor", name)
        assert "alwaysApply: true" in cursor
        assert "# Body" in cursor
        assert "Keep this." in cursor
        for host in ("codex", "claude-code"):
            blob = _rule_blob(outputs, host, name)
            assert "Keep this." in blob


def test_scoped_globs_visible_on_cursor():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.validate.engine import validate_project

    name = "py-scope"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest, name)
        _write_rule(dest, name, _SCOPED)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(
            d.code == CODE_ADAPTER_BLOCK and d.level == "error"
            for d in diagnostics
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        cursor = _rule_blob(outputs, "cursor", name)
        assert "alwaysApply: false" in cursor
        assert "**/*.py" in cursor
        assert "# Scoped" in cursor
        for host in ("codex", "claude-code"):
            blob = _rule_blob(outputs, host, name)
            assert "# Scoped" in blob


def test_soft_rule_blocks_codex_and_claude():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.validate.engine import validate_project

    name = "soft-rule"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest, name)
        _write_rule(dest, name, _SOFT)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        outputs = dest / "build" / "outputs"
        cursor = _rule_blob(outputs, "cursor", name)
        assert "# Soft" in cursor
        blocked = [
            d
            for d in diagnostics
            if d.code == CODE_ADAPTER_BLOCK and d.level == "error"
        ]
        paths = {d.path for d in blocked}
        assert "/targets/codex" in paths, diagnostics
        assert "/targets/claude-code" in paths, diagnostics
        assert "/targets/cursor" not in paths


def main() -> int:
    tests = (
        test_always_on_renders_on_all_hosts,
        test_scoped_globs_visible_on_cursor,
        test_soft_rule_blocks_codex_and_claude,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
