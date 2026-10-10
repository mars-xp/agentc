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


def test_p4_kind_codes_exist():
    from agentc.model.diagnostics import CODE_KIND_MISMATCH, CODE_KIND_UNKNOWN

    assert CODE_KIND_MISMATCH == "AGC-P4-0007"
    assert CODE_KIND_UNKNOWN == "AGC-P4-0008"


def test_fingerprint_plugin_wins_over_dot_cursor():
    from agentc.importers.detect import fingerprint_hits

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".cursor-plugin").mkdir()
        (root / ".cursor-plugin" / "plugin.json").write_text(
            '{"name": "p"}\n', encoding="utf-8"
        )
        (root / ".cursor").mkdir()
        (root / ".cursor" / "skills").mkdir()
        assert fingerprint_hits(root) == ("cursor",)


def test_fingerprint_two_plugin_manifests_ambiguous():
    from agentc.importers.detect import fingerprint_hits, resolve_kind
    from agentc.model.diagnostics import CODE_KIND_UNKNOWN

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".cursor-plugin").mkdir()
        (root / ".cursor-plugin" / "plugin.json").write_text(
            '{"name": "a"}\n', encoding="utf-8"
        )
        (root / ".codex-plugin").mkdir()
        (root / ".codex-plugin" / "plugin.json").write_text(
            '{"name": "b"}\n', encoding="utf-8"
        )
        hits = fingerprint_hits(root)
        assert "cursor" in hits and "codex" in hits
        kind, code = resolve_kind(root, None)
        assert kind is None
        assert code == CODE_KIND_UNKNOWN


def test_resolve_kind_cursor_on_workspace_is_0007():
    from agentc.importers.detect import resolve_kind
    from agentc.model.diagnostics import CODE_KIND_MISMATCH

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".cursor").mkdir()
        (root / ".cursor" / "skills").mkdir()
        kind, code = resolve_kind(root, "cursor")
        assert kind is None
        assert code == CODE_KIND_MISMATCH


def test_resolve_workspace_and_user():
    from agentc.importers.detect import fingerprint_hits, resolve_kind
    from agentc.model.diagnostics import CODE_KIND_MISMATCH

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        (proj / ".cursor" / "skills").mkdir(parents=True)
        assert fingerprint_hits(proj) == ("cursor-workspace",)
        user = Path(tmp) / "user"
        user.mkdir()
        (user / "ide_state.json").write_text("{}\n", encoding="utf-8")
        (user / "skills").mkdir()
        assert fingerprint_hits(user) == ("cursor-user",)
        kind, code = resolve_kind(proj, "cursor-user")
        assert kind is None
        assert code == CODE_KIND_MISMATCH


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


def _write_skill(dir_path: Path, name: str = "hello") -> None:
    skill = dir_path / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n",
        encoding="utf-8",
        newline="\n",
    )


def test_inspect_auto_workspace_ok():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        _write_skill(proj / ".cursor")
        result = _run_agentc("inspect", str(proj), "--format", "json")
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_inspect_kind_mismatch_fails():
    from agentc.model.diagnostics import CODE_KIND_MISMATCH

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        _write_skill(proj / ".cursor")
        result = _run_agentc(
            "inspect", str(proj), "--kind", "codex", "--format", "json"
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_KIND_MISMATCH for d in payload)


def test_inspect_unknown_dir_fails_0008():
    from agentc.model.diagnostics import CODE_KIND_UNKNOWN

    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        result = _run_agentc("inspect", str(empty), "--format", "json")
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_KIND_UNKNOWN for d in payload)


def test_inspect_kind_cursor_on_workspace_is_0007():
    from agentc.model.diagnostics import CODE_KIND_MISMATCH

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        _write_skill(proj / ".cursor")
        result = _run_agentc(
            "inspect", str(proj), "--kind", "cursor", "--format", "json"
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_KIND_MISMATCH for d in payload)


def test_inspect_plugin_without_kind_still_ok():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plugin"
        plugin.mkdir()
        (plugin / ".cursor-plugin").mkdir()
        (plugin / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "0.0.1"}) + "\n",
            encoding="utf-8",
        )
        _write_skill(plugin)
        result = _run_agentc("inspect", str(plugin), "--format", "json")
        assert result.returncode == 0, result.stdout + result.stderr


def test_inspect_workspace_dot_cursor_symlink_escape_is_0002():
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        outside = base / "outside-cursor"
        _write_skill(outside, name="leaked")
        proj = base / "proj"
        proj.mkdir()
        (proj / ".cursor").symlink_to(outside)
        result = _run_agentc("inspect", str(proj), "--format", "json")
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_SOURCE_UNREADABLE for d in payload)
        assert CODE_SOURCE_UNREADABLE == "AGC-P4-0002"


def main() -> int:
    tests = [
        test_p4_kind_codes_exist,
        test_fingerprint_plugin_wins_over_dot_cursor,
        test_fingerprint_two_plugin_manifests_ambiguous,
        test_resolve_kind_cursor_on_workspace_is_0007,
        test_resolve_workspace_and_user,
        test_inspect_auto_workspace_ok,
        test_inspect_kind_mismatch_fails,
        test_inspect_unknown_dir_fails_0008,
        test_inspect_kind_cursor_on_workspace_is_0007,
        test_inspect_plugin_without_kind_still_ok,
        test_inspect_workspace_dot_cursor_symlink_escape_is_0002,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
