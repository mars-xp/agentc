#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def _seed_project(root: Path) -> None:
    from agentc.project.init_project import init_project

    issues = init_project(
        root, project_id="acme", name="Acme", targets=("cursor",)
    )
    assert issues == []


def test_create_skill_rejects_invalid_name():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        from agentc.project.create_skill import create_skill

        issues = create_skill(root, "Bad_Name")
        assert any(i.code == "AGC-P1-0004" for i in issues)
        assert not (root / "skills" / "Bad_Name").exists()


def test_create_skill_rejects_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        from agentc.project.create_skill import create_skill

        assert create_skill(root, "dup-skill") == []
        marker = (root / "skills" / "dup-skill" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        issues = create_skill(root, "dup-skill")
        assert len(issues) == 1
        assert issues[0].code == "AGC-P1-0008"
        assert issues[0].level == "error"
        assert (root / "skills" / "dup-skill" / "SKILL.md").read_text(
            encoding="utf-8"
        ) == marker


def test_create_skill_success_and_validate_schema():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        from agentc.project.create_skill import create_skill
        from agentc.project.load import load_project_yaml, validate_schema

        yaml_before = (root / "agent-project.yaml").read_text(encoding="utf-8")
        issues = create_skill(root, "my-skill")
        assert issues == []

        skill_dir = root / "skills" / "my-skill"
        assert skill_dir.is_dir()
        assert (skill_dir / "scripts").is_dir()
        assert (skill_dir / "references").is_dir()
        assert (skill_dir / "assets").is_dir()

        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "/" + "Users" + "/" not in skill_md
        assert "name: my-skill" in skill_md
        assert 'description: "my-skill skill"' in skill_md

        data = load_project_yaml(root / "agent-project.yaml")
        assert data["contents"]["skills"] == ["skills/my-skill"]
        assert validate_schema(data) == []

        yaml_after = (root / "agent-project.yaml").read_text(encoding="utf-8")
        assert "project:" in yaml_after
        assert "targets:" in yaml_after
        assert "defaults:" in yaml_after
        assert yaml_before != yaml_after


def test_create_skill_rejects_empty_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        root.mkdir()
        (root / "agent-project.yaml").write_text("", encoding="utf-8")
        from agentc.project.create_skill import create_skill

        issues = create_skill(root, "my-skill")
        assert any(i.code == "AGC-P1-0007" for i in issues)
        assert not (root / "skills").exists()


def test_create_skill_rejects_malformed_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        root.mkdir()
        (root / "agent-project.yaml").write_text("schemaVersion: [\n", encoding="utf-8")
        from agentc.project.create_skill import create_skill

        issues = create_skill(root, "my-skill")
        assert any(i.code == "AGC-P1-0007" for i in issues)
        assert not (root / "skills").exists()


def test_create_skill_rejects_missing_schema_version():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        root.mkdir()
        (root / "agent-project.yaml").write_text(
            "project:\n  id: acme\n  name: Acme\n  version: 0.1.0\n",
            encoding="utf-8",
        )
        from agentc.project.create_skill import create_skill

        issues = create_skill(root, "my-skill")
        assert any(i.code == "AGC-P1-0007" for i in issues)
        assert not (root / "skills").exists()


def test_create_skill_requires_an_existing_project():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "not-a-project"
        from agentc.project.create_skill import create_skill

        issues = create_skill(root, "my-skill")
        assert [i.code for i in issues] == ["AGC-P1-0007"]
        assert issues[0].level == "error"
        assert not root.exists()


def test_create_skill_template_ships_with_the_package():
    from agentc.project import create_skill as module

    assert module._TEMPLATE_PATH.is_file()
    assert (module._TEMPLATE_PATH.parents[1] / "__init__.py").is_file()


def main() -> int:
    tests = (
        test_create_skill_rejects_invalid_name,
        test_create_skill_rejects_duplicate,
        test_create_skill_success_and_validate_schema,
        test_create_skill_rejects_empty_yaml,
        test_create_skill_rejects_malformed_yaml,
        test_create_skill_rejects_missing_schema_version,
        test_create_skill_requires_an_existing_project,
        test_create_skill_template_ships_with_the_package,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
