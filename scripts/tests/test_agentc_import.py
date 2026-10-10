#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def _write_min_plugin(
    root: Path,
    *,
    with_hook: bool = True,
    with_unknown: bool = True,
    with_rule: bool = False,
    with_command: bool = False,
    with_agent: bool = False,
) -> None:
    (root / ".cursor-plugin").mkdir(parents=True)
    (root / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "0.0.1",
                "license": "Apache-2.0",
            }
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    skill = root / "skills" / "hello"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n",
        encoding="utf-8",
        newline="\n",
    )
    if with_hook:
        (root / "hooks").mkdir(parents=True)
        (root / "hooks" / "hooks.json").write_text(
            '{"version": 1}\n', encoding="utf-8", newline="\n"
        )
    if with_unknown:
        (root / "mystery.txt").write_text("unknown\n", encoding="utf-8")
    if with_rule:
        (root / "rules").mkdir(parents=True)
        (root / "rules" / "house.md").write_text(
            "---\ndescription: house\nalwaysApply: false\n---\n\n# house\n",
            encoding="utf-8",
            newline="\n",
        )
    if with_command:
        (root / "commands").mkdir(parents=True)
        (root / "commands" / "ping.md").write_text(
            "---\nname: ping\n---\n\n# ping\n",
            encoding="utf-8",
            newline="\n",
        )
    if with_agent:
        (root / "agents").mkdir(parents=True)
        (root / "agents" / "helper.md").write_text(
            "---\nname: helper\ndescription: helper agent\n---\n\n# helper\n",
            encoding="utf-8",
            newline="\n",
        )


def _env(src_only: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    if src_only:
        env["PYTHONPATH"] = str(ROOT / "src")
    else:
        env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def _run_agentc(*args: str, src_only: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(src_only=src_only),
        cwd=ROOT,
    )


def test_import_without_yes_does_not_create_out():
    from agentc.model.diagnostics import CODE_UNCONFIRMED

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_min_plugin(src)
        before = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out),
            "--format", "json",
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_UNCONFIRMED for d in payload)
        assert any(
            d["code"] == "AGC-P4-0006"
            and d["level"] == "warning"
            and d["path"] == "hooks/hooks.json"
            for d in payload
        )
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}
        after = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        assert after == before

        text_result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out),
        )
        assert text_result.returncode == 1, text_result.stdout + text_result.stderr
        assert "# agentc inspect" in text_result.stdout
        assert "# agentc import" not in text_result.stdout.split("error:")[0]


def test_import_yes_json_includes_unresolved_and_redaction_notes():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_min_plugin(src)
        skill = src / "skills" / "hello" / "SKILL.md"
        skill.write_text(
            "---\nname: hello\ndescription: hello skill\n---\n\n"
            "See /Users/demo/.cursor/plugins\n",
            encoding="utf-8",
            newline="\n",
        )
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out),
            "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}
        assert any(
            d["code"] == "AGC-P4-0006"
            and d["level"] == "warning"
            and d["path"] == "hooks/hooks.json"
            for d in payload
        )
        assert any(
            d["code"] == "AGC-P4-0006" and d["level"] == "notice" for d in payload
        )


def test_import_yes_writes_skill_dir_and_unresolved_report():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_min_plugin(src)
        before = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out), "--yes",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (out / "agent-project.yaml").is_file()
        assert (out / "skills" / "hello").is_dir()
        assert (out / "skills" / "hello" / "SKILL.md").is_file()
        yaml_text = (out / "agent-project.yaml").read_text(encoding="utf-8")
        assert "skills/hello" in yaml_text
        assert "cursor:" in yaml_text
        assert "hooks/hooks.json" not in yaml_text
        report = (out / "import-report.md").read_text(encoding="utf-8")
        assert "hooks/hooks.json" in report
        assert "mystery.txt" in report
        assert "skills/hello" in report
        after = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        assert after == before


def test_import_yes_writes_rule_command_agent_files():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_min_plugin(
            src, with_hook=False, with_unknown=False,
            with_rule=True, with_command=True, with_agent=True,
        )
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out), "--yes",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (out / "extensions" / "rules" / "house.md").is_file()
        assert (out / "extensions" / "commands" / "ping.md").is_file()
        assert (out / "extensions" / "agents" / "helper.md").is_file()
        text = (out / "agent-project.yaml").read_text(encoding="utf-8")
        assert "extensions/rules/house.md" in text
        assert "extensions/commands/ping.md" in text
        assert "extensions/agents/helper.md" in text


def test_import_yes_refuses_nonempty_out():
    from agentc.model.diagnostics import CODE_OUTPUT_NONEMPTY

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        out.mkdir()
        (out / "keep.txt").write_text("x\n", encoding="utf-8")
        _write_min_plugin(src)
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out),
            "--yes", "--format", "json",
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_OUTPUT_NONEMPTY for d in payload)
        assert (out / "keep.txt").read_text(encoding="utf-8") == "x\n"
        assert not (out / "agent-project.yaml").exists()


def test_import_yes_outbound_symlink_does_not_create_out():
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        outside = Path(tmp) / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        src.mkdir()
        _write_min_plugin(src, with_hook=False, with_unknown=False)
        (src / "escape.txt").symlink_to(outside)
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out),
            "--yes", "--format", "json",
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_SOURCE_UNREADABLE for d in payload)
        assert not out.exists()


def test_import_yes_redacts_home_path_in_skill():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_min_plugin(src, with_hook=False, with_unknown=False)
        skill = src / "skills" / "hello" / "SKILL.md"
        skill.write_text(
            "---\nname: hello\ndescription: hello skill\n---\n\n"
            "See /Users/demo/.cursor/plugins\n",
            encoding="utf-8",
            newline="\n",
        )
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out), "--yes",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        body = (out / "skills" / "hello" / "SKILL.md").read_text(encoding="utf-8")
        assert "/" + "Users" + "/" not in body
        assert "<redacted-home-path>" in body
        report = (out / "import-report.md").read_text(encoding="utf-8")
        assert "redacted:" in report
        assert "SKILL.md" in report


def test_import_only_unresolved_still_writes_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        (src / ".cursor-plugin").mkdir(parents=True)
        (src / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "hooks-only", "version": "0.0.1"}) + "\n",
            encoding="utf-8",
        )
        (src / "hooks").mkdir()
        (src / "hooks" / "hooks.json").write_text("{}\n", encoding="utf-8")
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out), "--yes",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (out / "agent-project.yaml").is_file()
        report = (out / "import-report.md").read_text(encoding="utf-8")
        assert "converted:" in report
        assert "(none)" in report
        assert "hooks/hooks.json" in report
        yaml_text = (out / "agent-project.yaml").read_text(encoding="utf-8")
        assert "skills/hello" not in yaml_text


def test_import_yes_invalid_utf8_skill_keeps_out_without_report():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_min_plugin(src, with_hook=False, with_unknown=False)
        (src / "skills" / "hello" / "SKILL.md").write_bytes(b"\xff\xfe not utf8")
        result = _run_agentc(
            "import", "cursor", "--from", str(src), "--output", str(out),
            "--yes", "--format", "json",
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert out.is_dir()
        assert (out / "agent-project.yaml").is_file()
        assert not (out / "import-report.md").exists()
        payload = json.loads(result.stdout)
        assert any(d["level"] == "error" for d in payload)


def test_help_lists_import_without_scripts():
    result = _run_agentc("--help", src_only=True)
    assert result.returncode == 0
    assert "import" in result.stdout


def test_readme_documents_p4_codes_and_adr_0005():
    readme = (ROOT / "packaging" / "agentc" / "README.md").read_text(encoding="utf-8")
    assert "AGC-P4-0001" in readme
    assert "AGC-P4-0004" in readme
    assert "AGC-P4-0005" in readme
    assert "AGC-P4-0006" in readme
    packaged = (
        ROOT
        / "packaging"
        / "agentc"
        / "docs"
        / "adr"
        / "0005-import-facade-not-importer-spi.md"
    )
    adr = packaged.read_text(encoding="utf-8")
    assert "Importer" in adr
    assert "scan_plugin" in adr or "scan → classify → plan" in adr or "scan" in adr
    spec = (
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-09-09-open-source-cross-platform-agent-compiler"
        / "adr"
        / "0005-import-facade-not-importer-spi.md"
    )
    if spec.is_file():
        assert spec.read_text(encoding="utf-8") == adr


def main() -> int:
    tests = (
        test_import_without_yes_does_not_create_out,
        test_import_yes_json_includes_unresolved_and_redaction_notes,
        test_import_yes_writes_skill_dir_and_unresolved_report,
        test_import_yes_writes_rule_command_agent_files,
        test_import_yes_refuses_nonempty_out,
        test_import_yes_outbound_symlink_does_not_create_out,
        test_import_yes_redacts_home_path_in_skill,
        test_import_only_unresolved_still_writes_yaml,
        test_import_yes_invalid_utf8_skill_keeps_out_without_report,
        test_help_lists_import_without_scripts,
        test_readme_documents_p4_codes_and_adr_0005,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
