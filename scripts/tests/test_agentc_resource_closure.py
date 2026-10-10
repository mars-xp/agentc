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
_EXTRAS = ("notes.md", "helper.py")


def _init(dest: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)


def _posix(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def _skill_files(outputs: Path, host: str) -> list[Path]:
    matches = [
        path
        for path in (outputs / host).rglob("*")
        if path.is_file() and "hello" in _posix(path)
    ]
    assert matches, "missing hello files under {0}".format(host)
    return matches


def _assert_named_files(paths: list[Path], host: str) -> None:
    names = {path.name for path in paths}
    assert "SKILL.md" in names, "missing hello SKILL.md under {0}".format(host)
    for extra in _EXTRAS:
        assert extra in names, "missing {0} under {1}".format(extra, host)


def test_skill_sidecar_extras_are_copied_into_build_and_zip():
    from agentc.build.package import package_project
    from agentc.build.run import build_project
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        skill_dir = dest / "skills" / "hello"
        (skill_dir / "notes.md").write_text("# notes\n", encoding="utf-8")
        (skill_dir / "helper.py").write_text("print(1)\n", encoding="utf-8")
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(d.level == "error" for d in diagnostics)
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in _HOSTS:
            _assert_named_files(_skill_files(outputs, host), host)
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
            assert any(
                "hello" in name and name.endswith("/SKILL.md") for name in names
            ), "missing hello SKILL.md in {0} zip".format(host)
            for extra in _EXTRAS:
                assert any(
                    "hello" in name and name.endswith("/" + extra) for name in names
                ), "missing {0} in {1} zip".format(extra, host)


def main() -> int:
    tests = (test_skill_sidecar_extras_are_copied_into_build_and_zip,)
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
