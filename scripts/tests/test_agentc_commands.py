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

_STANDALONE = (
    "---\n"
    "name: ship-it\n"
    "---\n"
    "# Ship it\n"
    "\n"
    "Deploy the plugin independently. No skill directory here.\n"
)


def _init(dest: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)


def _write_command(dest: Path, name: str, body: str) -> None:
    path = dest / "extensions" / "commands" / "{0}.md".format(name)
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


def test_standalone_command_renders_on_all_hosts():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.project.create_extension import create_command
    from agentc.validate.engine import validate_project

    name = "ship-it"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        command_issues = create_command(dest, name)
        assert not any(i.level == "error" for i in command_issues)
        _write_command(dest, name, _STANDALONE)
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
            assert "# Ship it" in blob
            assert "Deploy the plugin independently. No skill directory here." in blob
        codex = _named_blob(outputs, "codex", name)
        assert "name:" in codex
        assert "ship-it" in codex
        assert "Deploy the plugin independently. No skill directory here." in codex


def test_command_skill_name_conflict_blocks_codex_only():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK
    from agentc.project.create_extension import create_command
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    name = "ship-it"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, name)
        assert not any(i.level == "error" for i in skill_issues)
        command_issues = create_command(dest, name)
        assert not any(i.level == "error" for i in command_issues)
        _write_command(dest, name, _STANDALONE)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        outputs = dest / "build" / "outputs"
        for host in ("cursor", "claude-code"):
            blob = _named_blob(outputs, host, name)
            assert "# Ship it" in blob
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
        test_standalone_command_renders_on_all_hosts,
        test_command_skill_name_conflict_blocks_codex_only,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
