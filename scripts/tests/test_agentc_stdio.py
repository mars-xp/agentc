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
_ADAPTER_BODY = "print(1)\n"


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


def _is_leftover_stdio_adapter(text: str) -> bool:
    posix = text.replace("\\", "/")
    return posix.endswith("/stdio_decision_v1.py") or posix.endswith(
        "stdio_decision_v1.py"
    ) or "/hooks/adapters/stdio_decision_v1.py" in posix


def _assert_no_stdio_adapter(root: Path, host: str) -> None:
    assert root.is_dir(), "missing tree for {0}".format(host)
    for path in root.rglob("*"):
        if path.is_file() and _is_leftover_stdio_adapter(_posix(path)):
            raise AssertionError(
                "unexpected stdio_decision_v1.py under {0}: {1}".format(
                    host, _posix(path)
                )
            )


def test_leftover_stdio_adapter_is_omitted_from_build_and_zip():
    from agentc.build.package import package_project
    from agentc.build.run import build_project
    from agentc.project.create_skill import create_skill
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init(dest)
        skill_issues = create_skill(dest, "hello")
        assert not any(i.level == "error" for i in skill_issues)
        adapter_dir = dest / "hooks" / "adapters"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        (adapter_dir / "stdio_decision_v1.py").write_text(
            _ADAPTER_BODY, encoding="utf-8"
        )
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(d.level == "error" for d in diagnostics)
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        for host in _HOSTS:
            _hello_skill_md(outputs, host)
            _assert_no_stdio_adapter(outputs / host, host)
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
            for name in names:
                assert not _is_leftover_stdio_adapter(name), (
                    "stdio_decision_v1.py in {0} zip: {1}".format(host, name)
                )


def main() -> int:
    tests = (test_leftover_stdio_adapter_is_omitted_from_build_and_zip,)
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
