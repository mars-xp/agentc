#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_HOSTS = ("cursor", "codex", "claude-code")
_OTHER_HOSTS = ("codex", "claude-code")
_LEFTOVER = "leftover.txt"
_KEEP = "keep.txt"
_LEFTOVER_BODY = "stale-cursor\n"
_KEEP_BODIES = {
    "codex": "keep-codex\n",
    "claude-code": "keep-claude\n",
}


def _init(dest: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)


def _plugin_dir(outputs: Path, host: str) -> Path:
    host_root = outputs / host
    assert host_root.is_dir(), "missing outputs for {0}".format(host)
    children = [path for path in host_root.iterdir() if path.is_dir()]
    assert len(children) == 1, "expected one plugin dir under {0}".format(host)
    return children[0]


def _skill_files(outputs: Path, host: str) -> list[Path]:
    matches = [
        path
        for path in (outputs / host).rglob("*")
        if path.is_file() and "hello" in path.as_posix()
    ]
    assert matches, "missing hello artifact under {0}".format(host)
    return matches


def _disable_hosts(dest: Path, hosts: tuple[str, ...]) -> None:
    yaml_path = dest / "agent-project.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    targets = data.setdefault("targets", {})
    for host in hosts:
        spec = targets.setdefault(host, {})
        if not isinstance(spec, dict):
            spec = {}
            targets[host] = spec
        spec["enabled"] = False
    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def test_cursor_only_rebuild_clears_own_stale_and_keeps_other_hosts():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_NO_ENABLED_HOSTS
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

        leftover = _plugin_dir(outputs, "cursor") / _LEFTOVER
        leftover.write_text(_LEFTOVER_BODY, encoding="utf-8", newline="\n")
        keep_paths = {}
        for host in _OTHER_HOSTS:
            path = _plugin_dir(outputs, host) / _KEEP
            path.write_text(_KEEP_BODIES[host], encoding="utf-8", newline="\n")
            keep_paths[host] = path

        _disable_hosts(dest, _OTHER_HOSTS)
        diagnostics2, _source_failed2, _policy2 = build_project(dest)
        assert not any(
            d.code == CODE_NO_ENABLED_HOSTS and d.level == "error"
            for d in diagnostics2
        )
        assert not leftover.exists()
        _skill_files(outputs, "cursor")
        for host in _OTHER_HOSTS:
            path = keep_paths[host]
            assert path.is_file()
            assert path.read_text(encoding="utf-8") == _KEEP_BODIES[host]


def main() -> int:
    tests = (test_cursor_only_rebuild_clears_own_stale_and_keeps_other_hosts,)
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
