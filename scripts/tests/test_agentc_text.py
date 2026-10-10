#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_HOSTS = ("cursor", "codex", "claude-code")


def _init(dest: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)


def _posix(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def _hello_skill_paths(root: Path) -> list[Path]:
    matches = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and "hello" in _posix(path)
        and _posix(path).endswith("/SKILL.md")
    ]
    assert matches, "missing hello SKILL.md under {0}".format(_posix(root))
    return matches


def test_create_skill_writes_utf8_lf_and_build_zip_keep_bytes():
    from agentc.build.package import package_project
    from agentc.build.run import build_project
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        source = dest / "skills" / "hello" / "SKILL.md"
        raw = source.read_bytes()
        assert b"\r" not in raw
        assert b"\n" in raw
        raw.decode("utf-8")
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(d.level == "error" for d in diagnostics)
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in _HOSTS:
            for path in _hello_skill_paths(outputs / host):
                assert path.read_bytes() == raw
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
                names = [
                    item
                    for item in archive.namelist()
                    if "hello" in item.replace("\\", "/")
                    and item.replace("\\", "/").endswith("/SKILL.md")
                ]
                assert names, "missing hello SKILL.md in {0} zip".format(host)
                assert archive.read(names[0]) == raw


def main() -> int:
    tests = (test_create_skill_writes_utf8_lf_and_build_zip_keep_bytes,)
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
