#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

FAKE_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def _run_agentc(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=ROOT,
    )


def test_p5_audit_codes_exist():
    from agentc.model.diagnostics import (
        CODE_AUDIT_BLOCKING,
        CODE_AUDIT_DIR,
        CODE_AUDIT_FLAG,
        CODE_AUDIT_RECORDING,
        CODE_AUDIT_UNREADABLE,
    )

    assert CODE_AUDIT_FLAG == "AGC-P5-0009"
    assert CODE_AUDIT_DIR == "AGC-P5-0010"
    assert CODE_AUDIT_UNREADABLE == "AGC-P5-0011"
    assert CODE_AUDIT_BLOCKING == "AGC-P5-0012"
    assert CODE_AUDIT_RECORDING == "AGC-P5-0013"


def test_missing_for_public_does_not_scan():
    from agentc.audit.run import audit_directory

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        secret = dest / "leak.env"
        secret.write_text(FAKE_TOKEN, encoding="utf-8")
        before = secret.read_text(encoding="utf-8")
        diags = audit_directory(dest, for_public=False)
        assert any(d.code == "AGC-P5-0009" and d.level == "error" for d in diags)
        assert not any(d.code == "AGC-P5-0012" for d in diags)
        assert secret.read_text(encoding="utf-8") == before


def test_missing_dir_does_not_scan():
    from agentc.audit.run import audit_directory

    diags = audit_directory(None, for_public=True)
    assert any(d.code == "AGC-P5-0010" and d.level == "error" for d in diags)


def test_blocking_token_is_error():
    from agentc.audit.run import audit_directory

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        (dest / "leak.env").write_text(FAKE_TOKEN, encoding="utf-8")
        diags = audit_directory(dest, for_public=True)
        assert any(d.code == "AGC-P5-0012" and d.level == "error" for d in diags)
        assert (dest / "leak.env").read_text(encoding="utf-8") == FAKE_TOKEN


def test_recording_home_path_is_warning_only():
    from agentc.audit.run import audit_directory

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        (dest / "note.md").write_text("recorded-path: /Users/alice/.codex/x\n", encoding="utf-8")
        diags = audit_directory(dest, for_public=True)
        assert any(d.code == "AGC-P5-0013" and d.level == "warning" for d in diags)
        assert not any(d.level == "error" for d in diags)


def test_missing_dir_path_is_unreadable():
    from agentc.audit.run import audit_directory

    missing = Path("/tmp/agentc-audit-does-not-exist-xyz")
    diags = audit_directory(missing, for_public=True)
    assert any(d.code == "AGC-P5-0011" and d.level == "error" for d in diags)
    assert not missing.exists()


def test_parent_escape_dir_is_unreadable():
    from agentc.audit.run import audit_directory

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        escaped = dest / ".." / "outside"
        diags = audit_directory(escaped, for_public=True)
        assert any(d.code == "AGC-P5-0011" and d.level == "error" for d in diags)


def test_cli_missing_for_public_is_0009():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        (dest / "ok.md").write_text("hello\n", encoding="utf-8")
        result = _run_agentc("audit", "--format", "json", str(dest))
        payload = json.loads(result.stdout)
        assert result.returncode == 1
        assert any(row["code"] == "AGC-P5-0009" for row in payload)
        assert set(payload[0]) == {"code", "level", "message", "path", "hint"}
        assert (dest / "ok.md").read_text(encoding="utf-8") == "hello\n"


def test_cli_missing_dir_is_0010():
    result = _run_agentc("audit", "--for-public", "--format", "json")
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert any(row["code"] == "AGC-P5-0010" for row in payload)


def test_cli_recording_only_exits_zero():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        (dest / "note.md").write_text("recorded-path: /Users/alice/.codex/x\n", encoding="utf-8")
        before = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        result = _run_agentc("audit", "--for-public", "--format", "json", str(dest))
        payload = json.loads(result.stdout)
        assert result.returncode == 0
        assert any(row["code"] == "AGC-P5-0013" for row in payload)
        after = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        assert after == before
        assert not (dest / "audit-report.md").exists()
        assert (dest / "note.md").read_text(encoding="utf-8") == "recorded-path: /Users/alice/.codex/x\n"


def test_project_validate_error_exits_one():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        (dest / "agent-project.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
        result = _run_agentc("audit", "--for-public", "--format", "json", str(dest))
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload
        assert (dest / "agent-project.yaml").exists()


def test_zip_member_token_is_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        zpath = dest / "out.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("plugin/secret.env", FAKE_TOKEN)
        result = _run_agentc("audit", "--for-public", "--format", "json", str(dest))
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(row["code"] == "AGC-P5-0012" for row in payload)
        assert zpath.is_file()


def test_zip_slip_is_0011_and_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        zpath = dest / "evil.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("../escape.env", "nope")
        before = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        result = _run_agentc("audit", "--for-public", "--format", "json", str(dest))
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(row["code"] == "AGC-P5-0011" for row in payload)
        after = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        assert after == before


def test_truncated_zip_is_0011_and_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        zpath = dest / "bad.zip"
        zpath.write_bytes(b"PK\x03\x04not-a-real-zip")
        before = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        result = _run_agentc("audit", "--for-public", "--format", "json", str(dest))
        payload = json.loads(result.stdout)
        assert result.returncode == 1
        assert any(row["code"] == "AGC-P5-0011" for row in payload)
        after = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
        assert after == before


def test_cli_protocol_envelope_has_six_keys():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        (dest / "note.md").write_text("recorded-path: /Users/alice/.codex/x\n", encoding="utf-8")
        result = _run_agentc("audit", "--for-public", "--format", "protocol", str(dest))
        payload = json.loads(result.stdout)
        assert set(payload) == {
            "protocolVersion",
            "status",
            "summary",
            "diagnostics",
            "artifacts",
            "confirmation",
        }
        assert isinstance(payload["diagnostics"], list)
        if payload["diagnostics"]:
            assert set(payload["diagnostics"][0]) == {
                "code",
                "level",
                "message",
                "path",
                "hint",
            }


def test_public_scan_rules_omit_internal_lists():
    from agentc.audit.engine import load_scan_rules, public_rules_path

    rules = load_scan_rules(public_rules_path())
    assert rules.internal_domain_suffixes == ()
    assert rules.internal_hosts == ()
    assert "pypi.org" in rules.public_package_index_hosts
    assert "example.com" in rules.example_domains


def test_pyproject_ships_runtime_packages_and_public_rules():
    text = (ROOT / "packaging" / "agentc" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'where = ["src", "scripts"]' in text
    assert 'include = ["agentc*", "plugin_compiler*", "plugin_targets*"]' in text
    assert '"agentc.audit" = ["public-scan-rules.yaml"]' in text
    assert '"agentc.project" = ["templates/*"]' in text
    assert 'py-modules = ["plugin_packaging"]' in text
    assert 'description = "Preview CLI for portable agent projects. Not a 1.0 release."' in text
    assert '"plugin_compiler.contracts" = ["*.json"]' in text
    readme = (ROOT / "packaging" / "agentc" / "README.md").read_text(encoding="utf-8")
    assert "PYTHONPATH=src:scripts python3 -m agentc audit" not in readme
    assert "PYTHONPATH=src:scripts python3 -m agentc" not in readme
    assert readme.startswith("# AgentC (preview)")
    assert "python3 -m pip install ." in readme
    for command in (
        "agentc --help",
        "agentc init",
        "agentc validate",
        "agentc build",
        "agentc package",
        "agentc audit --for-public",
        "agentc inspect",
        "agentc import",
        "agentc install",
        "agentc uninstall",
        "agentc status",
        "agentc sync",
        "agentc restore",
    ):
        assert command in readme


def test_help_without_scripts_mentions_audit():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert result.returncode == 0
    assert "audit" in result.stdout


def main() -> int:
    tests = [
        test_p5_audit_codes_exist,
        test_missing_for_public_does_not_scan,
        test_missing_dir_does_not_scan,
        test_blocking_token_is_error,
        test_recording_home_path_is_warning_only,
        test_missing_dir_path_is_unreadable,
        test_parent_escape_dir_is_unreadable,
        test_cli_missing_for_public_is_0009,
        test_cli_missing_dir_is_0010,
        test_cli_recording_only_exits_zero,
        test_project_validate_error_exits_one,
        test_zip_member_token_is_blocking,
        test_zip_slip_is_0011_and_does_not_write,
        test_truncated_zip_is_0011_and_does_not_write,
        test_cli_protocol_envelope_has_six_keys,
        test_public_scan_rules_omit_internal_lists,
        test_pyproject_ships_runtime_packages_and_public_rules,
        test_help_without_scripts_mentions_audit,
    ]
    for fn in tests:
        fn()
        print("ok", fn.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
