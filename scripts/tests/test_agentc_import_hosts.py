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


def _assert_imported(out: Path, *, host: str) -> None:
    yaml_text = (out / "agent-project.yaml").read_text(encoding="utf-8")
    assert f"{host}:" in yaml_text
    assert (out / "skills" / "hello" / "SKILL.md").is_file()
    assert (out / "import-report.md").is_file()


def test_import_workspace_without_yes_does_not_create_out():
    from agentc.model.diagnostics import CODE_UNCONFIRMED

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        out = Path(tmp) / "out"
        _write_skill(proj / ".cursor")
        (proj / ".cursor" / "mcp.json").write_text("{}\n", encoding="utf-8")
        before = {
            p.as_posix(): p.read_bytes()
            for p in proj.rglob("*")
            if p.is_file()
        }
        result = _run_agentc(
            "import", "cursor-workspace", "--from", str(proj),
            "--output", str(out), "--format", "json",
        )
        assert result.returncode == 1
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_UNCONFIRMED for d in payload)
        assert any(
            d["code"] == "AGC-P4-0006" and d["path"] == "mcp.json"
            for d in payload
        )
        after = {
            p.as_posix(): p.read_bytes()
            for p in proj.rglob("*")
            if p.is_file()
        }
        assert after == before


def test_import_workspace_yes_writes_project():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        out = Path(tmp) / "out"
        _write_skill(proj / ".cursor")
        result = _run_agentc(
            "import", "cursor-workspace", "--from", str(proj),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_imported(out, host="cursor")
        payload = json.loads(result.stdout)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_import_user_host_config_unresolved():
    with tempfile.TemporaryDirectory() as tmp:
        user = Path(tmp) / "user"
        out = Path(tmp) / "out"
        user.mkdir()
        (user / "ide_state.json").write_text("{}\n", encoding="utf-8")
        (user / "argv.json").write_text("{}\n", encoding="utf-8")
        _write_skill(user)
        (user / "plugins").mkdir()
        (user / "plugins" / "stale.txt").write_text("x\n", encoding="utf-8")
        result = _run_agentc(
            "import", "cursor-user", "--from", str(user),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_imported(out, host="cursor")
        payload = json.loads(result.stdout)
        notes = [d for d in payload if d["code"] == "AGC-P4-0006"]
        assert any(d["path"] == "ide_state.json" for d in notes)
        assert any("plugins/stale.txt" in (d["path"] or "") for d in notes)
        assert "ide_state.json" not in (out / "agent-project.yaml").read_text(
            encoding="utf-8"
        )


def test_import_codex_and_claude():
    with tempfile.TemporaryDirectory() as tmp:
        for kind, manifest, host in (
            ("codex", ".codex-plugin/plugin.json", "codex"),
            ("claude-code", ".claude-plugin/plugin.json", "claude-code"),
        ):
            src = Path(tmp) / kind
            out = Path(tmp) / f"out-{kind}"
            src.mkdir()
            man = src / manifest
            man.parent.mkdir(parents=True)
            man.write_text(
                json.dumps({"name": f"demo-{kind}", "version": "0.0.1"}) + "\n",
                encoding="utf-8",
            )
            _write_skill(src)
            (src / "hooks").mkdir()
            (src / "hooks" / "hooks.json").write_text("{}\n", encoding="utf-8")
            result = _run_agentc(
                "import", kind, "--from", str(src),
                "--output", str(out), "--yes", "--format", "json",
            )
            assert result.returncode == 0, result.stdout + result.stderr
            _assert_imported(out, host=host)
            payload = json.loads(result.stdout)
            assert any(
                d["code"] == "AGC-P4-0006" and "hooks" in (d["path"] or "")
                for d in payload
            )


def test_import_workspace_dot_cursor_symlink_escape_is_0002():
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        outside = base / "outside-cursor"
        _write_skill(outside, name="leaked")
        proj = base / "proj"
        proj.mkdir()
        (proj / ".cursor").symlink_to(outside)
        out = base / "out"
        result = _run_agentc(
            "import", "cursor-workspace", "--from", str(proj),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 1
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_SOURCE_UNREADABLE for d in payload)
        assert CODE_SOURCE_UNREADABLE == "AGC-P4-0002"
        assert not (out / "skills" / "leaked").exists()


def test_import_kind_mismatch_does_not_write():
    from agentc.model.diagnostics import CODE_KIND_MISMATCH

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        out = Path(tmp) / "out"
        _write_skill(proj / ".cursor")
        result = _run_agentc(
            "import", "codex", "--from", str(proj),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 1
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_KIND_MISMATCH for d in payload)


def test_import_protocol_unconfirmed_envelope():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        out = Path(tmp) / "out"
        _write_skill(proj / ".cursor")
        result = _run_agentc(
            "import", "cursor-workspace", "--from", str(proj),
            "--output", str(out), "--format", "protocol",
        )
        assert result.returncode == 1
        assert not out.exists()
        obj = json.loads(result.stdout)
        assert obj["protocolVersion"] == "agentc-protocol/v1"
        assert obj["status"] == "unconfirmed"
        assert obj["confirmation"]["required"] is True
        assert obj["confirmation"]["flag"] == "--yes"
        assert isinstance(obj["diagnostics"], list)


def test_help_src_only():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def main() -> int:
    tests = [
        test_import_workspace_without_yes_does_not_create_out,
        test_import_workspace_yes_writes_project,
        test_import_user_host_config_unresolved,
        test_import_codex_and_claude,
        test_import_workspace_dot_cursor_symlink_escape_is_0002,
        test_import_kind_mismatch_does_not_write,
        test_import_protocol_unconfirmed_envelope,
        test_help_src_only,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
