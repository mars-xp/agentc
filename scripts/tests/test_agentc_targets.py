#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_HOSTS = ("cursor", "codex", "claude-code")
_LEFTOVER_BODY = '{"name":"leftover-cursor","version":"9.9.9"}\n'
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


def _posix(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def _hello_skill_md(outputs: Path, host: str) -> None:
    matches = [
        path
        for path in (outputs / host).rglob("*")
        if path.is_file()
        and "hello" in _posix(path)
        and _posix(path).endswith("/SKILL.md")
    ]
    assert matches, "missing hello SKILL.md under {0}".format(host)


def _has_leftover_cursor_identity(text: str) -> bool:
    return "leftover-cursor" in text.replace("\\", "/")


def _assert_no_leftover_identity(root: Path, host: str) -> None:
    assert root.is_dir(), "missing tree for {0}".format(host)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        posix = _posix(path)
        body = path.read_bytes().decode("utf-8", "replace")
        if _has_leftover_cursor_identity(posix) or _has_leftover_cursor_identity(body):
            raise AssertionError(
                "unexpected leftover-cursor under {0}: {1}".format(host, posix)
            )


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


def test_leftover_cursor_plugin_json_does_not_override_yaml_identity():
    from agentc.build.package import package_project
    from agentc.build.run import build_project
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        leftover = dest / ".cursor-plugin" / "plugin.json"
        leftover.parent.mkdir(parents=True, exist_ok=True)
        leftover.write_text(_LEFTOVER_BODY, encoding="utf-8")
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(d.level == "error" for d in diagnostics)
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in _HOSTS:
            _hello_skill_md(outputs, host)
            data = _manifest(outputs, host)
            assert data.get("name") == "Acme"
            assert data.get("version") == "0.1.0"
            _assert_no_leftover_identity(outputs / host, host)
        pkg, _pkg_failed, _pkg_policy = package_project(dest)
        assert not any(d.level == "error" for d in pkg)
        dist = dest / "build" / "dist"
        for host in _HOSTS:
            zips = [
                path
                for path in (dist / host).rglob("*.zip")
                if path.is_file()
            ]
            assert zips, "missing zip under {0}".format(host)
            with zipfile.ZipFile(zips[0]) as archive:
                names = [item.replace("\\", "/") for item in archive.namelist()]
                bodies = [
                    archive.read(item).decode("utf-8", "replace")
                    for item in archive.namelist()
                ]
            assert any(
                "hello" in name and name.endswith("/SKILL.md") for name in names
            ), "missing hello SKILL.md in {0} zip".format(host)
            for name in names:
                assert not _has_leftover_cursor_identity(name), (
                    "leftover-cursor in {0} zip name: {1}".format(host, name)
                )
            for body in bodies:
                assert not _has_leftover_cursor_identity(body), (
                    "leftover-cursor in {0} zip body".format(host)
                )


def main() -> int:
    tests = (test_leftover_cursor_plugin_json_does_not_override_yaml_identity,)
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
