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


def _run_agentc(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=cwd or ROOT,
    )


def _init_project(dest: Path) -> None:
    result = _run_agentc(
        "init", str(dest), "--id", "acme", "--name", "Acme", "--targets", "cursor"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_agentc_package_imports():
    import agentc

    assert agentc is not None


def test_cli_init_and_validate_json():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        init = _run_agentc(
            "init",
            str(dest),
            "--id",
            "cli-test",
            "--name",
            "CLI Test",
            "--targets",
            "all",
            "--license",
            "Apache-2.0",
        )
        assert init.returncode == 0, init.stdout + init.stderr
        assert (dest / "agent-project.yaml").is_file()

        validate = _run_agentc(
            "validate",
            str(dest),
            "--policy",
            "strict",
            "--format",
            "json",
        )
        assert validate.returncode == 0, validate.stdout + validate.stderr
        payload = json.loads(validate.stdout)
        assert isinstance(payload, list)
        assert not any(d["level"] == "error" for d in payload)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_cli_init_refuses_nonempty():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        dest.mkdir()
        (dest / "x").write_text("1", encoding="utf-8")
        result = _run_agentc(
            "init",
            str(dest),
            "--id",
            "x",
            "--name",
            "X",
            "--targets",
            "all",
        )
        assert result.returncode != 0


def test_cli_create_skill():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        assert (
            _run_agentc(
                "init",
                str(dest),
                "--id",
                "acme",
                "--name",
                "Acme",
                "--targets",
                "cursor",
            ).returncode
            == 0
        )

        create = _run_agentc(
            "create",
            "skill",
            "my-skill",
            "--project",
            str(dest),
        )
        assert create.returncode == 0, create.stdout + create.stderr
        assert (dest / "skills" / "my-skill" / "SKILL.md").is_file()


def test_main_returns_error_on_diagnostics():
    from agentc.cli.main import main

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        dest.mkdir()
        (dest / "x").write_text("1", encoding="utf-8")
        code = main(
            [
                "init",
                str(dest),
                "--id",
                "x",
                "--name",
                "X",
                "--targets",
                "all",
            ]
        )
        assert code == 1


def test_cli_validate_relative_dir_emits_json():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        _init_project(dest)
        assert (
            _run_agentc(
                "create", "skill", "demo", "--project", str(dest)
            ).returncode
            == 0
        )

        validate = _run_agentc("validate", "proj", "--format", "json", cwd=Path(tmp))
        assert validate.returncode == 0, validate.stdout + validate.stderr
        assert json.loads(validate.stdout) == []


def test_cli_format_json_before_sub_subcommand():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        _init_project(dest)

        result = _run_agentc(
            "create", "--format", "json", "skill", "Bad_Name", "--project", str(dest)
        )
        assert result.returncode == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert [d["code"] for d in payload] == ["AGC-P1-0004"]

        trailing = _run_agentc(
            "create", "skill", "Bad_Name", "--project", str(dest), "--format", "json"
        )
        assert trailing.returncode == 1
        assert json.loads(trailing.stdout) == payload


def test_cli_create_rule_writes_file_and_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init_project(dest)
        result = _run_agentc("create", "rule", "house-style", "--project", str(dest))
        assert result.returncode == 0, result.stdout + result.stderr
        rel = "extensions/rules/house-style.md"
        assert (dest / rel).is_file()
        text = (dest / "agent-project.yaml").read_text(encoding="utf-8")
        assert "house-style" in text


def test_cli_create_command_writes_file_and_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init_project(dest)
        result = _run_agentc(
            "create", "command", "run-checks", "--project", str(dest)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        rel = "extensions/commands/run-checks.md"
        assert (dest / rel).is_file()
        text = (dest / "agent-project.yaml").read_text(encoding="utf-8")
        assert "run-checks" in text


def test_cli_create_agent_writes_file_and_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init_project(dest)
        result = _run_agentc("create", "agent", "reviewer", "--project", str(dest))
        assert result.returncode == 0, result.stdout + result.stderr
        rel = "extensions/agents/reviewer.md"
        assert (dest / rel).is_file()
        text = (dest / "agent-project.yaml").read_text(encoding="utf-8")
        assert "reviewer" in text


def test_cli_create_rule_rejects_invalid_name():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init_project(dest)
        result = _run_agentc("create", "rule", "Foo", "--project", str(dest))
        assert result.returncode != 0


def test_cli_create_rule_rejects_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _init_project(dest)
        first = _run_agentc("create", "rule", "dup", "--project", str(dest))
        assert first.returncode == 0, first.stdout + first.stderr
        second = _run_agentc("create", "rule", "dup", "--project", str(dest))
        assert second.returncode != 0


def test_cli_rejects_unknown_target_value():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        result = _run_agentc(
            "init",
            str(dest),
            "--id",
            "x",
            "--name",
            "X",
            "--targets",
            "cursr,codex",
            "--format",
            "json",
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert [d["code"] for d in payload] == ["AGC-P1-0004"]
        assert not dest.exists()


def main() -> int:
    tests = (
        test_agentc_package_imports,
        test_cli_init_and_validate_json,
        test_cli_init_refuses_nonempty,
        test_cli_create_skill,
        test_main_returns_error_on_diagnostics,
        test_cli_validate_relative_dir_emits_json,
        test_cli_format_json_before_sub_subcommand,
        test_cli_create_rule_writes_file_and_yaml,
        test_cli_create_command_writes_file_and_yaml,
        test_cli_create_agent_writes_file_and_yaml,
        test_cli_create_rule_rejects_invalid_name,
        test_cli_create_rule_rejects_duplicate,
        test_cli_rejects_unknown_target_value,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
