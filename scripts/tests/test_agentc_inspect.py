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


def test_p4_codes_exist():
    from agentc.model.diagnostics import (
        CODE_IMPORT_IO,
        CODE_IMPORT_NOTE,
        CODE_NOT_PLUGIN,
        CODE_OUTPUT_NONEMPTY,
        CODE_SOURCE_UNREADABLE,
        CODE_UNCONFIRMED,
    )

    assert CODE_NOT_PLUGIN == "AGC-P4-0001"
    assert CODE_SOURCE_UNREADABLE == "AGC-P4-0002"
    assert CODE_OUTPUT_NONEMPTY == "AGC-P4-0003"
    assert CODE_UNCONFIRMED == "AGC-P4-0004"
    assert CODE_IMPORT_IO == "AGC-P4-0005"
    assert CODE_IMPORT_NOTE == "AGC-P4-0006"


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


def test_build_plan_classifies_skill_and_unresolved():
    from agentc.importers.pipeline import build_plan

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        src.mkdir()
        _write_min_plugin(src)
        plan, diags = build_plan(src)
        assert plan is not None, diags
        assert not any(d.level == "error" for d in diags)
        kinds = {item.kind: item.dest_rel for item in plan.convertibles}
        assert kinds["skill"] == "skills/hello"
        rels = {item.source_rel for item in plan.unresolved}
        assert any(r.endswith("hooks/hooks.json") or r == "hooks/hooks.json" for r in rels)
        assert "mystery.txt" in rels
        reasons = {item.source_rel.replace("\\", "/"): item.reason for item in plan.unresolved}
        assert reasons.get("hooks/hooks.json") == "hook"
        assert reasons.get("mystery.txt") == "unknown"
        assert any(
            d.code == "AGC-P4-0006" and d.level == "warning" and d.path == "hooks/hooks.json"
            for d in diags
        )


def test_build_plan_rejects_non_cursor_plugin():
    from agentc.importers.pipeline import build_plan
    from agentc.model.diagnostics import CODE_NOT_PLUGIN

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "not-plugin"
        src.mkdir()
        (src / "README.md").write_text("no\n", encoding="utf-8")
        plan, diags = build_plan(src)
        assert plan is None
        assert any(d.code == CODE_NOT_PLUGIN and d.level == "error" for d in diags)


def test_build_plan_outbound_symlink_is_0002():
    from agentc.importers.pipeline import build_plan
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        outside = Path(tmp) / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        src.mkdir()
        _write_min_plugin(src, with_hook=False, with_unknown=False)
        (src / "escape.txt").symlink_to(outside)
        plan, diags = build_plan(src)
        assert plan is None
        assert any(
            d.code == CODE_SOURCE_UNREADABLE and d.level == "error" for d in diags
        )


def test_build_plan_packaging_file_is_unresolved_unknown():
    from agentc.importers.pipeline import build_plan

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        src.mkdir()
        _write_min_plugin(src, with_hook=False)
        packaging = src / "packaging"
        packaging.mkdir()
        (packaging / "foo.txt").write_text("packaging artifact\n", encoding="utf-8")
        plan, diags = build_plan(src)
        assert plan is not None, diags
        rels = {item.source_rel for item in plan.unresolved}
        assert "packaging/foo.txt" in rels
        reasons = {item.source_rel: item.reason for item in plan.unresolved}
        assert reasons["packaging/foo.txt"] == "unknown"
        assert any(
            d.code == "AGC-P4-0006"
            and d.level == "warning"
            and d.path == "packaging/foo.txt"
            for d in diags
        )


def test_build_plan_skill_tree_redactions_include_non_skill_md():
    from agentc.importers.pipeline import build_plan

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        src.mkdir()
        _write_min_plugin(src, with_hook=False, with_unknown=False)
        notes_dir = src / "skills" / "hello" / "scripts"
        notes_dir.mkdir(parents=True)
        (notes_dir / "notes.md").write_text(
            "Install from /Users/demo/.cursor/plugins\n",
            encoding="utf-8",
        )
        plan, diags = build_plan(src)
        assert plan is not None, diags
        redacted_paths = {path for path, _reason in plan.redactions}
        assert "skills/hello/scripts/notes.md" in redacted_paths
        assert any(
            d.code == "AGC-P4-0006"
            and d.level == "notice"
            and d.path == "skills/hello/scripts/notes.md"
            for d in diags
        )


def test_build_plan_rule_command_agent_dest_files():
    from agentc.importers.pipeline import build_plan

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        src.mkdir()
        _write_min_plugin(
            src, with_hook=False, with_unknown=False,
            with_rule=True, with_command=True, with_agent=True,
        )
        plan, diags = build_plan(src)
        assert plan is not None, diags
        dests = {item.kind: item.dest_rel for item in plan.convertibles}
        assert dests["rule"] == "extensions/rules/house.md"
        assert dests["command"] == "extensions/commands/ping.md"
        assert dests["agent"] == "extensions/agents/helper.md"


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


def test_help_lists_inspect_without_scripts():
    result = _run_agentc("--help", src_only=True)
    assert result.returncode == 0, result.stderr
    assert "inspect" in result.stdout


def test_cli_inspect_json_is_five_key_array():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        src.mkdir()
        _write_min_plugin(src)
        result = _run_agentc("inspect", str(src), "--format", "json")
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert not any(d["level"] == "error" for d in payload)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}
        assert any(
            d["code"] == "AGC-P4-0006"
            and d["level"] == "warning"
            and d["path"] == "hooks/hooks.json"
            for d in payload
        )


def test_cli_inspect_text_lists_unresolved_and_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        src.mkdir()
        _write_min_plugin(src)
        before = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        result = _run_agentc("inspect", str(src), "--format", "text")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hooks/hooks.json" in result.stdout
        assert "mystery.txt" in result.stdout
        assert "skills/hello" in result.stdout
        assert "converted:" in result.stdout
        assert "unresolved:" in result.stdout
        after = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        assert after == before


def test_cli_inspect_unknown_is_0008():
    from agentc.model.diagnostics import CODE_KIND_UNKNOWN

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "nope"
        src.mkdir()
        result = _run_agentc("inspect", str(src), "--format", "json")
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_KIND_UNKNOWN for d in payload)


def test_cli_inspect_kind_cursor_on_workspace_is_0007():
    from agentc.model.diagnostics import CODE_KIND_MISMATCH

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        skill = proj / ".cursor" / "skills" / "hello"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n",
            encoding="utf-8",
            newline="\n",
        )
        result = _run_agentc(
            "inspect", str(proj), "--kind", "cursor", "--format", "json"
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_KIND_MISMATCH for d in payload)


def test_cli_inspect_outbound_symlink_is_0002():
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        outside = Path(tmp) / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        src.mkdir()
        _write_min_plugin(src, with_hook=False, with_unknown=False)
        (src / "escape.txt").symlink_to(outside)
        before = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        result = _run_agentc("inspect", str(src), "--format", "json")
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_SOURCE_UNREADABLE for d in payload)
        after = {p.as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        assert after == before


def test_redact_fixture_source_has_no_literal_github_token():
    import re

    source = Path(__file__).read_text(encoding="utf-8")
    assert re.search(r"ghp_[A-Za-z0-9]{20,}", source) is None


def test_redact_home_path_and_github_token():
    from agentc.security.redact import redact_portable_text

    token = "ghp_" + "abcdefghijklmnopqrstuvwxyz012345"
    text = (
        "install from /Users/demo/.cursor/plugins\n"
        f"token={token}\n"
        "ok=${GITHUB_TOKEN}\n"
    )
    out, reasons = redact_portable_text(text)
    assert "/" + "Users" + "/" not in out
    assert "ghp_" not in out
    assert "${GITHUB_TOKEN}" in out
    assert "<redacted-home-path>" in out
    assert "<redacted-secret>" in out
    assert "home-path" in reasons
    assert "secret" in reasons


def main() -> int:
    tests = (
        test_p4_codes_exist,
        test_redact_fixture_source_has_no_literal_github_token,
        test_redact_home_path_and_github_token,
        test_build_plan_classifies_skill_and_unresolved,
        test_build_plan_rejects_non_cursor_plugin,
        test_build_plan_outbound_symlink_is_0002,
        test_build_plan_packaging_file_is_unresolved_unknown,
        test_build_plan_skill_tree_redactions_include_non_skill_md,
        test_build_plan_rule_command_agent_dest_files,
        test_help_lists_inspect_without_scripts,
        test_cli_inspect_json_is_five_key_array,
        test_cli_inspect_text_lists_unresolved_and_does_not_write,
        test_cli_inspect_unknown_is_0008,
        test_cli_inspect_kind_cursor_on_workspace_is_0007,
        test_cli_inspect_outbound_symlink_is_0002,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
