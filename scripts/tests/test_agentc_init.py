#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def test_init_refuses_nonempty():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        dest.mkdir()
        (dest / "x").write_text("1", encoding="utf-8")
        from agentc.project.init_project import init_project

        issues = init_project(dest, project_id="a", name="A", targets=("cursor",))
        assert any(i.code == "AGC-P1-0001" for i in issues)
        assert not (dest / ".github").exists()


def test_init_writes_schema_and_github_ci():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        from agentc.project.init_project import init_project

        issues = init_project(dest, project_id="acme", name="Acme", targets=("all",))
        assert issues == []
        text = (dest / "agent-project.yaml").read_text(encoding="utf-8")
        assert "schemaVersion: agent-project/v1" in text
        ci = dest / ".github" / "workflows" / "ci.yml"
        assert ci.is_file()
        body = ci.read_text(encoding="utf-8")
        assert "ubuntu-latest" in body
        assert "windows-latest" in body
        assert "macos-latest" in body
        assert 'python-version: "3.10"' in body
        assert "agentc validate ." in body
        assert "agentc build ." in body
        assert "fail-fast: false" in body
        assert "workflow_dispatch" in body
        assert "branches: [main, master]" in body
        assert "scripts/tests/test_" not in body
        assert "twine" not in body.lower()
        assert "pypi" not in body.lower()
        assert "pip install agentc" not in body
        assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in body
        assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in body
        assert "actions/checkout@v" not in body
        assert "actions/setup-python@v" not in body
        assert 'python -m pip install "git+https://github.com/mars-xp/agentc.git@main"' in body
        assert (dest / "overlays" / "cursor").is_dir()
        gitignore = (dest / ".gitignore").read_text(encoding="utf-8")
        assert "build/" in gitignore.splitlines()
        readme = (dest / "README.md").read_text(encoding="utf-8")
        assert "example GitHub Actions workflow" in readme
        assert "installs AgentC from the public GitHub source" in readme
        license_text = (dest / "LICENSE").read_text(encoding="utf-8")
        assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in license_text
        assert "9. Accepting Warranty or Additional Liability." in license_text


def test_init_refuses_existing_file_dest():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        dest.write_text("not a directory\n", encoding="utf-8")
        from agentc.project.init_project import init_project

        issues = init_project(dest, project_id="a", name="A", targets=("cursor",))
        assert [i.code for i in issues] == ["AGC-P1-0001"]
        assert dest.read_text(encoding="utf-8") == "not a directory\n"


def test_init_rejects_unknown_target():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        from agentc.project.init_project import init_project

        issues = init_project(
            dest, project_id="a", name="A", targets=("cursr", "codex")
        )
        assert [i.code for i in issues] == ["AGC-P1-0004"]
        assert "cursr" in issues[0].message
        assert not dest.exists()


def test_init_help_has_no_ci_flag():
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "agentc", "init", "--help"],
        cwd=str(ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "--with-ci" not in combined
    assert "--no-ci" not in combined


def main() -> int:
    tests = (
        test_init_refuses_nonempty,
        test_init_writes_schema_and_github_ci,
        test_init_refuses_existing_file_dest,
        test_init_rejects_unknown_target,
        test_init_help_has_no_ci_flag,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
