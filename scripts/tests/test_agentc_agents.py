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

_CONVERTIBLE = (
    "---\n"
    "name: review-helper\n"
    "description: Review a diff\n"
    "---\n"
    "# Review helper\n"
    "\n"
    "Read the diff and comment.\n"
)

_ISOLATED = (
    "---\n"
    "name: locked-reviewer\n"
    "description: Isolated reviewer\n"
    "tools: Read, Grep\n"
    "---\n"
    "# Locked\n"
    "\n"
    "Must not become a skill.\n"
)


def _seed(dest: Path, name: str) -> None:
    from agentc.project.create_extension import create_agent
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)
    agent_issues = create_agent(dest, name)
    assert not any(i.level == "error" for i in agent_issues)


def _write_agent(dest: Path, name: str, body: str) -> None:
    path = dest / "extensions" / "agents" / "{0}.md".format(name)
    path.write_text(body, encoding="utf-8", newline="\n")


def _named_files(outputs: Path, host: str, name: str) -> list[Path]:
    matches = [
        path
        for path in (outputs / host).rglob("*")
        if path.is_file() and name in path.as_posix()
    ]
    assert matches, "missing {0} artifact under {1}".format(name, host)
    return matches


def _named_blob(outputs: Path, host: str, name: str) -> str:
    parts = []
    for path in _named_files(outputs, host, name):
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    blob = "\n".join(parts)
    assert blob, "no text artifacts for {0} under {1}".format(name, host)
    return blob


def test_convertible_agent_renders_on_all_hosts():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.validate.engine import validate_project

    name = "review-helper"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest, name)
        _write_agent(dest, name, _CONVERTIBLE)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(
            d.code == CODE_ADAPTER_BLOCK and d.level == "error"
            for d in diagnostics
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in ("cursor", "claude-code"):
            blob = _named_blob(outputs, host, name)
            assert "# Review helper" in blob
            assert "Read the diff and comment." in blob
        codex = _named_blob(outputs, "codex", name)
        assert "name:" in codex
        assert "review-helper" in codex
        assert "Review a diff" in codex
        assert "Read the diff and comment." in codex
        assert "tools:" not in codex


def test_isolated_agent_blocks_codex_only():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.validate.engine import validate_project

    name = "locked-reviewer"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest, name)
        _write_agent(dest, name, _ISOLATED)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        outputs = dest / "build" / "outputs"
        for host in ("cursor", "claude-code"):
            blob = _named_blob(outputs, host, name)
            assert "# Locked" in blob
        blocked = [
            d
            for d in diagnostics
            if d.code == CODE_ADAPTER_BLOCK and d.level == "error"
        ]
        paths = {d.path for d in blocked}
        assert "/targets/codex" in paths, diagnostics
        assert "/targets/cursor" not in paths
        assert "/targets/claude-code" not in paths


def main() -> int:
    tests = (
        test_convertible_agent_renders_on_all_hosts,
        test_isolated_agent_blocks_codex_only,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
