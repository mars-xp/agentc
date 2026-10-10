#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def test_default_matrix_allows_skill_on_three_hosts():
    from agentc.matrix.load import load_matrix

    matrix = load_matrix()
    for host in ("cursor", "codex", "claude-code"):
        assert matrix.support(host, "skill") == "supported"
        assert matrix.allows_success(host, "skill") is True
    assert matrix.allows_success("cursor", "rule") is True


def test_default_matrix_allows_rule_command_agent():
    from agentc.matrix.load import load_matrix

    matrix = load_matrix()
    for kind in ("rule", "command", "agent"):
        assert matrix.support("cursor", kind) == "supported"
        assert matrix.allows_success("cursor", kind) is True
    assert matrix.support("claude-code", "command") == "supported"
    assert matrix.allows_success("claude-code", "command") is True
    for kind in ("rule", "agent"):
        assert matrix.support("claude-code", kind) == "partial"
        assert matrix.allows_success("claude-code", kind) is True
    for kind in ("rule", "command", "agent"):
        assert matrix.support("codex", kind) == "partial"
        assert matrix.allows_success("codex", kind) is True


def test_p3_codes_exist():
    from agentc.model.diagnostics import (
        CODE_MISSING_OUTPUT,
        CODE_PACKAGE_ESCAPE,
        CODE_PACKAGE_IO,
    )

    assert CODE_MISSING_OUTPUT == "AGC-P3-0001"
    assert CODE_PACKAGE_ESCAPE == "AGC-P3-0002"
    assert CODE_PACKAGE_IO == "AGC-P3-0003"


def test_to_ir_reads_rule_command_agent_paths():
    from agentc.project.load import to_ir

    data = {
        "schemaVersion": "agent-project/v1",
        "project": {"id": "demo", "name": "Demo", "version": "0.1.0"},
        "contents": {
            "rules": ["extensions/rules/r.md"],
            "commands": ["extensions/commands/c.md"],
            "agents": ["extensions/agents/a.md"],
        },
    }
    ir = to_ir(data)
    assert ir.rule_paths == ("extensions/rules/r.md",)
    assert ir.command_paths == ("extensions/commands/c.md",)
    assert ir.agent_paths == ("extensions/agents/a.md",)


def test_unsupported_must_not_allow_success():
    from agentc.matrix.load import load_matrix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "defaults.yaml").write_text(
            "schemaVersion: agentc-host-profile/v1\n"
            "defaults:\n  cursor: cursor/x\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "cursor-x.yaml").write_text(
            "id: cursor/x\nhost: cursor\nhostVersions: declared-snapshot\n"
            "lastVerified: '2026.09'\nlimits: []\n"
            "capabilities:\n  skill: unsupported\n",
            encoding="utf-8",
            newline="\n",
        )
        matrix = load_matrix(root)
        assert matrix.support("cursor", "skill") == "unsupported"
        assert matrix.allows_success("cursor", "skill") is False


def test_host_only_only_owner_allows_success():
    from agentc.matrix.load import load_matrix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "defaults.yaml").write_text(
            "schemaVersion: agentc-host-profile/v1\n"
            "defaults:\n  cursor: cursor/x\n  codex: codex/x\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "cursor-x.yaml").write_text(
            "id: cursor/x\nhost: cursor\nhostVersions: declared-snapshot\n"
            "lastVerified: '2026.09'\nlimits: []\n"
            "capabilities:\n  skill: host-only\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "codex-x.yaml").write_text(
            "id: codex/x\nhost: codex\nhostVersions: declared-snapshot\n"
            "lastVerified: '2026.09'\nlimits: []\n"
            "capabilities:\n  skill: unsupported\n",
            encoding="utf-8",
            newline="\n",
        )
        matrix = load_matrix(root)
        assert matrix.allows_success("cursor", "skill") is True
        assert matrix.allows_success("codex", "skill") is False


def test_default_profiles_document_has_three_hosts():
    from agentc.matrix.load import load_profile_document

    doc = load_profile_document()
    assert doc["schemaVersion"] == "agentc-host-profile/v1"
    assert doc["defaults"]["cursor"] == "cursor/2026.09"
    ids = {item["id"] for item in doc["profiles"]}
    assert ids == {"cursor/2026.09", "codex/2026.09", "claude-code/2026.09"}
    cursor = next(item for item in doc["profiles"] if item["host"] == "cursor")
    assert cursor["capabilities"]["skill"] == "supported"
    assert cursor["capabilities"]["hook"] == "unsupported"
    assert "lastVerified" in cursor
    assert "hostVersions" in cursor
    assert isinstance(cursor["limits"], list)
    codex = next(item for item in doc["profiles"] if item["host"] == "codex")
    claude = next(item for item in doc["profiles"] if item["host"] == "claude-code")
    assert any("author" in item or "interface" in item for item in codex["limits"])
    assert any("agent" in item.lower() for item in claude["limits"])
    assert codex["limits"] and claude["limits"]


def test_unknown_level_does_not_allow_success():
    from agentc.matrix.load import load_matrix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "defaults.yaml").write_text(
            "schemaVersion: agentc-host-profile/v1\n"
            "defaults:\n  cursor: cursor/x\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "cursor-x.yaml").write_text(
            "id: cursor/x\nhost: cursor\nhostVersions: declared-snapshot\n"
            "lastVerified: '2026.09'\nlimits: []\n"
            "capabilities:\n  skill: unknown\n",
            encoding="utf-8",
            newline="\n",
        )
        matrix = load_matrix(root)
        assert matrix.support("cursor", "skill") == "unknown"
        assert matrix.allows_success("cursor", "skill") is False


def test_native_and_adapted_allow_success():
    from agentc.matrix.load import load_matrix

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "defaults.yaml").write_text(
            "schemaVersion: agentc-host-profile/v1\n"
            "defaults:\n  cursor: cursor/x\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "cursor-x.yaml").write_text(
            "id: cursor/x\nhost: cursor\nhostVersions: declared-snapshot\n"
            "lastVerified: '2026.09'\nlimits: []\n"
            "capabilities:\n  skill: native\n  rule: adapted\n",
            encoding="utf-8",
            newline="\n",
        )
        matrix = load_matrix(root)
        assert matrix.allows_success("cursor", "skill") is True
        assert matrix.allows_success("cursor", "rule") is True


def test_resolve_profile_ids_unknown_and_wrong_host():
    from agentc.matrix.load import resolve_profile_ids
    from agentc.model.diagnostics import CODE_PROFILE_UNKNOWN

    ids, diags = resolve_profile_ids(("cursor/does-not-exist",))
    assert any(item.code == CODE_PROFILE_UNKNOWN for item in diags)
    ids, diags = resolve_profile_ids(("cursor/does-not-exist", "cursor/2026.09"))
    assert not diags
    assert ids["cursor"] == "cursor/2026.09"


def test_resolve_profile_ids_corrupt_profile_file_is_0011():
    from agentc.matrix.load import resolve_profile_ids
    from agentc.model.diagnostics import CODE_PROFILE_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "defaults.yaml").write_text(
            "schemaVersion: agentc-host-profile/v1\n"
            "defaults:\n  cursor: cursor/x\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "cursor-x.yaml").write_text(
            "id: cursor/x\nhost: cursor\ncapabilities:\n  skill: not-a-level\n",
            encoding="utf-8",
            newline="\n",
        )
        ids, diags = resolve_profile_ids((), profiles_dir=root)
        assert any(item.code == CODE_PROFILE_INVALID for item in diags)


def test_resolve_profile_ids_id_mismatch_is_0011():
    from agentc.matrix.load import resolve_profile_ids
    from agentc.model.diagnostics import CODE_PROFILE_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "defaults.yaml").write_text(
            "schemaVersion: agentc-host-profile/v1\n"
            "defaults:\n  cursor: cursor/x\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "cursor-x.yaml").write_text(
            "id: cursor/OTHER\nhost: cursor\nhostVersions: declared-snapshot\n"
            "lastVerified: '2026.09'\nlimits: []\n"
            "capabilities:\n  skill: supported\n",
            encoding="utf-8",
            newline="\n",
        )
        ids, diags = resolve_profile_ids((), profiles_dir=root)
        assert any(item.code == CODE_PROFILE_INVALID for item in diags)


def test_p2_profile_codes_exist():
    from agentc.model.diagnostics import CODE_PROFILE_INVALID, CODE_PROFILE_UNKNOWN

    assert CODE_PROFILE_UNKNOWN == "AGC-P2-0010"
    assert CODE_PROFILE_INVALID == "AGC-P2-0011"


def test_p2_codes_exist():
    from agentc.model.diagnostics import (
        CODE_ADAPTER_BLOCK,
        CODE_ARTIFACT_ESCAPE,
        CODE_MATRIX_UNSUPPORTED,
        CODE_NO_ENABLED_HOSTS,
        CODE_TRANSLATE_FAILED,
    )

    assert CODE_NO_ENABLED_HOSTS == "AGC-P2-0001"
    assert CODE_TRANSLATE_FAILED == "AGC-P2-0002"
    assert CODE_ADAPTER_BLOCK == "AGC-P2-0003"
    assert CODE_MATRIX_UNSUPPORTED == "AGC-P2-0004"
    assert CODE_ARTIFACT_ESCAPE == "AGC-P2-0005"


def test_help_lists_matrix_and_not_as_overlay():
    import os
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.returncode == 0
    assert "matrix" in proc.stdout.split("{", 1)[-1].split("}", 1)[0]
    assert "doctor" in proc.stdout


def test_matrix_json_is_document_not_diagnostic_array():
    import json
    import os
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-m", "agentc", "matrix", "--format", "json"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert "defaults" in payload
    assert "profiles" in payload
    assert isinstance(payload["profiles"], list)
    assert "code" not in payload


def test_matrix_host_filter_only_one_profile():
    from agentc.matrix.load import load_profile_document

    doc = load_profile_document(host="cursor")
    assert {item["host"] for item in doc["profiles"]} == {"cursor"}


def test_readme_mentions_default_profile_ids():
    from agentc.matrix.load import load_profile_document

    text = (ROOT / "packaging" / "agentc" / "README.md").read_text(encoding="utf-8")
    doc = load_profile_document()
    assert "agentc matrix" in text
    assert "AGC-P2-0010" in text
    assert "AGC-P2-0011" in text
    for level in ("native", "supported", "adapted", "partial", "unsupported", "unknown"):
        assert level in text
    for host, profile_id in doc["defaults"].items():
        assert profile_id in text
        item = next(row for row in doc["profiles"] if row["host"] == host)
        assert item["id"] == profile_id
        assert item["capabilities"]["skill"] == "supported"
        assert item["capabilities"]["hook"] == "unsupported"
        assert "hook" in text and "unsupported" in text
    assert "author" in text and "interface" in text
    claude = next(row for row in doc["profiles"] if row["host"] == "claude-code")
    assert any("agent" in line.lower() for line in claude["limits"])
    assert any(line in text for line in claude["limits"]) or "Invalid input" in text


def test_allowlist_includes_host_profile_glob():
    import yaml

    allowlist_candidates = (
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-09-09-open-source-cross-platform-agent-compiler"
        / "phase-0"
        / "public-allowlist.yaml",
        ROOT
        / "packaging"
        / "agentc"
        / "docs"
        / "phase-0"
        / "public-allowlist.yaml",
    )
    for path in allowlist_candidates:
        if path.is_file():
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert "src/agentc/matrix/profiles/**" in payload["include"]
            return
    profiles = ROOT / "src" / "agentc" / "matrix" / "profiles"
    assert profiles.is_dir() and any(profiles.glob("*.yaml"))


def test_matrix_protocol_empty_artifacts():
    import json
    import os
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-m", "agentc", "matrix", "--format", "protocol"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert set(payload) == {
        "protocolVersion",
        "status",
        "summary",
        "diagnostics",
        "artifacts",
        "confirmation",
    }
    assert payload["status"] == "ok"
    assert payload["diagnostics"] == []
    assert payload["artifacts"] == []


def main() -> int:
    tests = (
        test_default_matrix_allows_skill_on_three_hosts,
        test_default_matrix_allows_rule_command_agent,
        test_p3_codes_exist,
        test_to_ir_reads_rule_command_agent_paths,
        test_unsupported_must_not_allow_success,
        test_host_only_only_owner_allows_success,
        test_p2_codes_exist,
        test_default_profiles_document_has_three_hosts,
        test_unknown_level_does_not_allow_success,
        test_native_and_adapted_allow_success,
        test_resolve_profile_ids_unknown_and_wrong_host,
        test_resolve_profile_ids_corrupt_profile_file_is_0011,
        test_resolve_profile_ids_id_mismatch_is_0011,
        test_p2_profile_codes_exist,
        test_help_lists_matrix_and_not_as_overlay,
        test_matrix_json_is_document_not_diagnostic_array,
        test_matrix_host_filter_only_one_profile,
        test_matrix_protocol_empty_artifacts,
        test_readme_mentions_default_profile_ids,
        test_allowlist_includes_host_profile_glob,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
