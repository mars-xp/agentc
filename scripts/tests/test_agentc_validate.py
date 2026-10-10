#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def _seed_project(root: Path) -> None:
    from agentc.project.init_project import init_project

    assert init_project(root, project_id="acme", name="Acme", targets=("cursor",)) == []


def _patch_yaml(root: Path, old: str, new: str) -> None:
    yaml_path = root / "agent-project.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    assert old in text, old
    yaml_path.write_text(text.replace(old, new), encoding="utf-8")


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_validate_blocks_outside_path():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        from agentc.project.init_project import init_project

        init_project(root, project_id="acme", name="Acme", targets=("cursor",))
        yaml_path = root / "agent-project.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        text = text.replace("skills: []", "skills:\n  - ../outside")
        yaml_path.write_text(text, encoding="utf-8")

        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        assert any(i.code == "AGC-P1-0003" for i in issues)


def test_validate_rejects_non_utf8():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        root.mkdir()
        yaml_path = root / "agent-project.yaml"
        yaml_path.write_bytes(b"schemaVersion: agent-project/v1\n\xc3\x28")

        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        assert any(i.code == "AGC-P1-0006" for i in issues)


def test_validate_uses_to_ir_without_mutating_source():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        from agentc.project.init_project import init_project

        init_project(root, project_id="acme", name="Acme", targets=("cursor",))
        yaml_path = root / "agent-project.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        text = text.replace(
            "forbidAbsolutePaths: true",
            "forbidAbsolutePaths: false",
        )
        text = text.replace("skills: []", "skills:\n  - skills/demo")
        text = text + "\n# /Users/not-scanned-when-disabled\n"
        yaml_path.write_text(text, encoding="utf-8")

        skill_dir = root / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# demo\n\nRef: /Users/ignored\n",
            encoding="utf-8",
        )

        snapshot = yaml_path.read_text(encoding="utf-8")
        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        assert yaml_path.read_text(encoding="utf-8") == snapshot
        assert not any(i.code == "AGC-P1-0003" for i in issues)
        assert not any(i.level == "error" for i in issues)


def test_init_success_has_no_validate_errors():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        from agentc.project.init_project import init_project
        from agentc.validate.engine import validate_project

        issues = init_project(
            dest, project_id="acme", name="Acme", targets=("all",)
        )
        assert not any(i.level == "error" for i in issues)
        followup = validate_project(dest)
        assert not any(i.level == "error" for i in followup)


def test_validate_accepts_relative_project_root_with_skill_md():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        _patch_yaml(root, "skills: []", "skills:\n  - skills/demo")
        _write_skill(root, "demo", "# demo\n\nUse project-relative paths.\n")

        from agentc.validate.engine import validate_project

        cwd = Path.cwd()
        os.chdir(tmp)
        try:
            issues = validate_project(Path("p"))
        finally:
            os.chdir(cwd)
        assert issues == [], [i.message for i in issues]


def test_validate_flags_home_dir_literal_in_skill_md():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        _patch_yaml(root, "skills: []", "skills:\n  - skills/demo")
        _write_skill(root, "demo", "# demo\n\nRef: /Users/someone/x\n")

        from agentc.validate.engine import validate_project

        cwd = Path.cwd()
        os.chdir(tmp)
        try:
            issues = validate_project(Path("p"))
        finally:
            os.chdir(cwd)
        hits = [i for i in issues if i.code == "AGC-P1-0003"]
        assert len(hits) == 1, [i.message for i in issues]
        assert hits[0].path == "skills/demo/SKILL.md"


def test_validate_reports_missing_paths_for_every_contents_kind():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        _patch_yaml(root, "skills: []", "skills:\n  - skills/ghost")
        _patch_yaml(root, "rules: []", "rules:\n  - extensions/rules/ghost.mdc")

        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        missing = {i.path for i in issues if i.code == "AGC-P1-0005"}
        assert missing == {"skills/ghost", "extensions/rules/ghost.mdc"}


def test_validate_blocks_escape_in_non_skill_contents():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        _patch_yaml(root, "rules: []", 'rules:\n  - "../../etc/passwd"')

        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        hits = [i for i in issues if i.code == "AGC-P1-0003"]
        assert len(hits) == 1, [i.message for i in issues]
        assert hits[0].path == "../../etc/passwd"
        assert any(i.level == "error" for i in issues)


def test_validate_reports_yaml_syntax_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        root.mkdir()
        (root / "agent-project.yaml").write_text(
            "project: [unclosed\n", encoding="utf-8"
        )

        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        assert [i.code for i in issues] == ["AGC-P1-0007"]
        assert "YAML" in issues[0].message


def test_validate_reports_non_mapping_document():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        root.mkdir()
        (root / "agent-project.yaml").write_text("- a\n- b\n", encoding="utf-8")

        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        assert [i.code for i in issues] == ["AGC-P1-0007"]
        assert "mapping" in issues[0].message


def test_validate_diagnostic_paths_stay_project_relative():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        root.mkdir()
        (root / "agent-project.yaml").write_bytes(
            b"schemaVersion: agent-project/v1\n\xc3\x28"
        )

        from agentc.validate.engine import validate_project

        issues = validate_project(root)
        assert [i.path for i in issues] == ["agent-project.yaml"]


def test_validate_does_not_import_plugin_compiler():
    import sys

    sys.modules.pop("plugin_compiler", None)
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "p"
        _seed_project(root)
        _patch_yaml(root, "skills: []", "skills:\n  - skills/demo")
        _write_skill(root, "demo", "# demo\n")
        assert validate_project(root) == []

    assert "plugin_compiler" not in sys.modules


def main() -> int:
    tests = (
        test_validate_blocks_outside_path,
        test_validate_rejects_non_utf8,
        test_validate_uses_to_ir_without_mutating_source,
        test_init_success_has_no_validate_errors,
        test_validate_accepts_relative_project_root_with_skill_md,
        test_validate_flags_home_dir_literal_in_skill_md,
        test_validate_reports_missing_paths_for_every_contents_kind,
        test_validate_blocks_escape_in_non_skill_contents,
        test_validate_reports_yaml_syntax_error,
        test_validate_reports_non_mapping_document,
        test_validate_diagnostic_paths_stay_project_relative,
        test_validate_does_not_import_plugin_compiler,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
