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

_SOURCE = (
    "---\n"
    "name: demo-skill\n"
    "description: Shared helper\n"
    "allowed-tools: Read\n"
    "cursor-only-key: nope\n"
    "---\n"
    "# Demo\n"
    "\n"
    "Do not rewrite this body.\n"
)


def _seed(dest: Path) -> None:
    from agentc.project.create_skill import create_skill
    from agentc.project.init_project import init_project

    issues = init_project(
        dest, project_id="acme", name="Acme", targets=_HOSTS
    )
    assert not any(i.level == "error" for i in issues)
    skill_issues = create_skill(dest, "demo-skill")
    assert not any(i.level == "error" for i in skill_issues)


def _write_skill(dest: Path, body: str) -> None:
    path = dest / "skills" / "demo-skill" / "SKILL.md"
    path.write_text(body, encoding="utf-8", newline="\n")


def _skill_artifact(outputs: Path, host: str) -> Path:
    matches = [
        path
        for path in (outputs / host).rglob("SKILL.md")
        if path.is_file() and "demo-skill" in path.as_posix()
    ]
    assert matches, "missing skills/demo-skill/SKILL.md under {0}".format(host)
    return matches[0]


def test_validate_and_build_render_skill_per_host():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_TRANSLATE_FAILED
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest)
        _write_skill(dest, _SOURCE)
        validate = validate_project(dest)
        assert not any(d.level == "error" for d in validate)
        diagnostics, _source_failed, _policy = build_project(dest)
        assert not any(
            d.code == CODE_TRANSLATE_FAILED and d.level == "error"
            for d in diagnostics
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        claude = _skill_artifact(outputs, "claude-code").read_text(
            encoding="utf-8"
        )
        assert "allowed-tools: Read" in claude
        assert "cursor-only-key" not in claude
        assert "# Demo" in claude
        assert "Do not rewrite this body." in claude
        for host in ("cursor", "codex"):
            text = _skill_artifact(outputs, host).read_text(encoding="utf-8")
            assert "name:" in text
            assert "demo-skill" in text
            assert "allowed-tools" not in text
            assert "cursor-only-key" not in text
            assert "Do not rewrite this body." in text


def _assert_build_translate_failed(dest: Path, needle: str) -> None:
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_TRANSLATE_FAILED
    from agentc.validate.engine import validate_project

    validate = validate_project(dest)
    assert not any(d.level == "error" for d in validate)
    diagnostics, _source_failed, _policy = build_project(dest)
    matched = [
        d
        for d in diagnostics
        if d.code == CODE_TRANSLATE_FAILED and d.level == "error"
    ]
    assert matched, diagnostics
    blob = " ".join((d.message or "") + " " + (d.hint or "") for d in matched)
    assert needle in blob, blob


def test_build_missing_frontmatter():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest)
        _write_skill(dest, "# No header\n")
        _assert_build_translate_failed(dest, "missing YAML frontmatter")


def test_build_incomplete_frontmatter():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest)
        _write_skill(dest, "---\nname: demo-skill\n")
        _assert_build_translate_failed(dest, "incomplete YAML frontmatter")


def test_build_empty_name():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest)
        _write_skill(
            dest,
            "---\nname:\ndescription: Shared helper\n---\nbody\n",
        )
        _assert_build_translate_failed(dest, "skills/demo-skill/SKILL.md")


def test_build_empty_description():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _seed(dest)
        _write_skill(
            dest,
            "---\nname: demo-skill\ndescription:\n---\nbody\n",
        )
        _assert_build_translate_failed(dest, "skills/demo-skill/SKILL.md")


def main() -> int:
    tests = (
        test_validate_and_build_render_skill_per_host,
        test_build_missing_frontmatter,
        test_build_incomplete_frontmatter,
        test_build_empty_name,
        test_build_empty_description,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
