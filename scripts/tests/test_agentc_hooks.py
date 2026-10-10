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
_HOOK_REL = "extensions/hooks/hooks.json"
_HOOKS_PAYLOAD = {
    "hooks": {
        "sessionStart": [
            {"command": "python3 ./hooks/notify.py"}
        ]
    }
}


def _init(dest: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)


def _write_hook_files(dest: Path) -> None:
    hook_path = dest / "extensions" / "hooks" / "hooks.json"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(
        json.dumps(_HOOKS_PAYLOAD, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (hook_path.parent / "notify.py").write_text(
        "print(1)\n", encoding="utf-8", newline="\n"
    )


def _register_hook(dest: Path) -> None:
    yaml_path = dest / "agent-project.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    contents = data.setdefault("contents", {})
    if not isinstance(contents, dict):
        contents = {}
        data["contents"] = contents
    contents["hooks"] = [_HOOK_REL]
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


def _assert_no_hooks_tree(outputs: Path, host: str) -> None:
    root = outputs / host
    assert root.is_dir(), "missing outputs for {0}".format(host)
    for path in root.rglob("*"):
        posix = path.as_posix()
        if posix.endswith("/hooks/hooks.json") or posix.endswith("\\hooks\\hooks.json"):
            raise AssertionError("unexpected hook artifact: {0}".format(posix))
        if path.is_dir() and path.name == "hooks":
            raise AssertionError("unexpected hooks directory: {0}".format(posix))


def test_registered_hook_is_not_emitted():
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
        _write_hook_files(dest)
        _register_hook(dest)
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
            _assert_no_hooks_tree(outputs, host)


def main() -> int:
    tests = (test_registered_hook_is_not_emitted,)
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
