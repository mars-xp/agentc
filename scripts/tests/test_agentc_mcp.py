#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_HOSTS = ("cursor", "codex", "claude-code")
_MCP_REL = "extensions/mcp/mcp.json"
_MCP_PAYLOAD = {
    "mcpServers": {
        "demo": {
            "command": "npx",
            "args": ["-y", "demo-mcp"],
        }
    }
}


def _init(dest: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)


def _write_mcp_file(dest: Path) -> None:
    mcp_path = dest / "extensions" / "mcp" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps(_MCP_PAYLOAD, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _register_mcp(dest: Path) -> None:
    yaml_path = dest / "agent-project.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    contents = data.setdefault("contents", {})
    if not isinstance(contents, dict):
        contents = {}
        data["contents"] = contents
    contents["mcp"] = [_MCP_REL]
    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _skill_files(outputs: Path, host: str) -> list[Path]:
    matches = [
        path
        for path in (outputs / host).rglob("*")
        if path.is_file() and "hello" in path.as_posix()
    ]
    assert matches, "missing hello artifact under {0}".format(host)
    return matches


def _assert_no_mcp_tree(outputs: Path, host: str) -> None:
    root = outputs / host
    assert root.is_dir(), "missing outputs for {0}".format(host)
    for path in root.rglob("*"):
        posix = path.as_posix().replace("\\", "/")
        if posix.endswith("/mcp.json") or posix.endswith("/.mcp.json"):
            raise AssertionError("unexpected mcp artifact: {0}".format(posix))
        if path.is_dir() and path.name == "mcp":
            raise AssertionError("unexpected mcp directory: {0}".format(posix))


def test_registered_mcp_is_not_emitted():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import (
        CODE_ADAPTER_BLOCK,
        CODE_MATRIX_UNSUPPORTED,
    )
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        _write_mcp_file(dest)
        _register_mcp(dest)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(
            d.code in (CODE_ADAPTER_BLOCK, CODE_MATRIX_UNSUPPORTED)
            and d.level == "error"
            for d in diagnostics
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in _HOSTS:
            _skill_files(outputs, host)
            _assert_no_mcp_tree(outputs, host)


def main() -> int:
    tests = (test_registered_mcp_is_not_emitted,)
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
