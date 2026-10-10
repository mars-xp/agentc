#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
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


def _project_with_skill(dest: Path, targets: tuple[str, ...] = ("cursor",)) -> None:
    from agentc.project.create_skill import create_skill
    from agentc.project.init_project import init_project

    issues = init_project(dest, project_id="acme", name="Acme", targets=targets)
    assert not any(i.level == "error" for i in issues)
    skill_issues = create_skill(dest, "hello")
    assert not any(i.level == "error" for i in skill_issues)


def _project_with_skill_and_rule(
    dest: Path, targets: tuple[str, ...] = ("cursor",)
) -> None:
    from agentc.project.create_extension import create_rule

    _project_with_skill(dest, targets=targets)
    rule_issues = create_rule(dest, "house-style")
    assert not any(i.level == "error" for i in rule_issues)


def _profiles_dir(
    parent: Path, capabilities: dict[str, str], host: str = "cursor"
) -> Path:
    root = parent / "profiles"
    root.mkdir()
    profile_id = "{0}/x".format(host)
    (root / "defaults.yaml").write_text(
        "schemaVersion: agentc-host-profile/v1\n"
        "defaults:\n  {0}: {1}\n".format(host, profile_id),
        encoding="utf-8",
        newline="\n",
    )
    cap_lines = "\n".join(
        "  {0}: {1}".format(name, level) for name, level in capabilities.items()
    )
    (root / "{0}-x.yaml".format(host)).write_text(
        "id: {0}\nhost: {1}\nhostVersions: declared-snapshot\n"
        "lastVerified: '2026.09'\nlimits: []\n"
        "capabilities:\n{2}\n".format(profile_id, host, cap_lines),
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_source_errors_do_not_call_adapters():
    from agentc.build.run import build_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        dest.mkdir()
        (dest / "agent-project.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
        diagnostics, source_failed, _policy = build_project(dest)
        assert source_failed is True
        assert any(d.level == "error" for d in diagnostics)
        assert not (dest / "build").exists()


def test_no_enabled_hosts_is_0001():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_NO_ENABLED_HOSTS

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        yaml_path = dest / "agent-project.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        yaml_path.write_text(
            text.replace("enabled: true", "enabled: false"),
            encoding="utf-8",
        )
        diagnostics, source_failed, _policy = build_project(dest)
        assert source_failed is False
        assert any(d.code == CODE_NO_ENABLED_HOSTS and d.level == "error" for d in diagnostics)
        assert exit_code_for_build(diagnostics, "strict", source_failed=False) == 1


def test_matrix_unsupported_is_0004_not_success():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_MATRIX_UNSUPPORTED

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        matrix = _profiles_dir(Path(tmp), {"skill": "unsupported"})
        diagnostics, source_failed, _policy = build_project(dest, matrix_path=matrix)
        assert source_failed is False
        blocked = [
            d for d in diagnostics
            if d.code == CODE_MATRIX_UNSUPPORTED and d.level == "error"
        ]
        assert blocked
        assert "skill" in blocked[0].message
        assert exit_code_for_build(diagnostics, "strict", source_failed=False) == 1
        compat = exit_code_for_build(diagnostics, "compatible", source_failed=False)
        assert compat == 0
        assert any(d.code == CODE_MATRIX_UNSUPPORTED and d.level == "error" for d in diagnostics)


def test_compatible_keeps_error_level():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_MATRIX_UNSUPPORTED

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        matrix = _profiles_dir(Path(tmp), {"skill": "unsupported"})
        diagnostics, _failed, _policy = build_project(dest, policy="compatible", matrix_path=matrix)
        assert exit_code_for_build(diagnostics, "compatible", source_failed=False) == 0
        assert any(d.code == CODE_MATRIX_UNSUPPORTED and d.level == "error" for d in diagnostics)


def test_permissive_maps_block_to_warning():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_MATRIX_UNSUPPORTED

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        matrix = _profiles_dir(Path(tmp), {"skill": "unsupported"})
        diagnostics, _failed, _policy = build_project(dest, policy="permissive", matrix_path=matrix)
        assert exit_code_for_build(diagnostics, "permissive", source_failed=False) == 0
        assert any(d.code == CODE_MATRIX_UNSUPPORTED and d.level == "warning" for d in diagnostics)


def test_skill_and_rule_build_writes_outputs_and_report_files():
    from agentc.build.run import build_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_rule(dest, targets=("cursor",))
        diagnostics, source_failed, _policy = build_project(dest)
        assert source_failed is False
        assert (dest / "build" / "outputs").is_dir()
        reports = dest / "build" / "reports"
        assert (reports / "report.md").is_file()
        assert (reports / "diagnostics.json").is_file()
        payload = json.loads(
            (reports / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert isinstance(payload, dict)
        assert isinstance(payload["diagnostics"], list)
        for item in payload["diagnostics"]:
            assert set(item.keys()) == {"code", "level", "message", "path", "hint"}
        result = _run_agentc("build", str(dest), "--format", "json")
        assert result.returncode == 0, result.stdout + result.stderr
        stdout_payload = json.loads(result.stdout)
        assert isinstance(stdout_payload, list)
        for item in stdout_payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_cursor_build_emits_rule_artifact():
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_TRANSLATE_FAILED

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_rule(dest, targets=("cursor",))
        diagnostics, source_failed, _policy = build_project(dest)
        assert source_failed is False
        assert not any(
            d.code == CODE_TRANSLATE_FAILED and d.level == "error" for d in diagnostics
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        files = [p for p in outputs.rglob("*") if p.is_file()]
        assert files
        rule_artifacts = [
            p for p in files if "house-style" in p.name or "house-style" in p.as_posix()
        ]
        assert rule_artifacts, "expected a house-style rule artifact under build/outputs/"


def test_matrix_unsupported_rule_blocks_host_with_rule_registered():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_MATRIX_UNSUPPORTED

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_rule(dest, targets=("cursor",))
        matrix = _profiles_dir(
            Path(tmp), {"skill": "supported", "rule": "unsupported"}
        )
        diagnostics, source_failed, _policy = build_project(dest, matrix_path=matrix)
        assert source_failed is False
        assert any(d.code == CODE_MATRIX_UNSUPPORTED and d.level == "error" for d in diagnostics)
        assert exit_code_for_build(diagnostics, "strict", source_failed=False) == 1
        assert not (dest / "build" / "outputs").exists()


def test_artifacts_stay_under_project_build():
    from agentc.build.run import build_project
    from plugin_compiler.paths import BuildLayout

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        diagnostics, source_failed, _policy = build_project(dest)
        assert source_failed is False
        layout = BuildLayout.from_repo(dest)
        error_0003 = [
            d for d in diagnostics if d.code == "AGC-P2-0003" and d.level == "error"
        ]
        assert not error_0003, "cursor build must not hit AGC-P2-0003: {0}".format(
            error_0003
        )
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir(), "successful build must write build/outputs/"
        files = [p for p in outputs.rglob("*") if p.is_file()]
        assert files, "build/outputs/ must contain artifact files"
        for path in files:
            path.resolve().relative_to(layout.root.resolve())
        if dest.joinpath("build").exists():
            for path in dest.joinpath("build").rglob("*"):
                if path.is_file():
                    path.resolve().relative_to(layout.root.resolve())
        assert not any(d.code == "AGC-P2-0005" and d.level == "error" for d in diagnostics)


def _project_adapter_block(dest: Path) -> None:
    """源码层可通过、真实 Adapter compile 会 block：project.id 为 `..`，
    BuildLayout.intermediate 拒绝该 plugin_name。仍走 load_adapter，禁止 mock。"""
    _project_with_skill(dest, targets=("cursor",))
    yaml_path = dest / "agent-project.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    yaml_path.write_text(text.replace("id: acme", "id: '..'"), encoding="utf-8")


def test_strict_adapter_block_exit_1_code_0003():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_adapter_block(dest)
        diagnostics, source_failed, policy = build_project(dest, policy="strict")
        assert source_failed is False
        assert any(d.code == CODE_ADAPTER_BLOCK and d.level == "error" for d in diagnostics)
        assert exit_code_for_build(diagnostics, policy, source_failed=False) == 1


def test_compatible_adapter_block_exit_0_keeps_error():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_adapter_block(dest)
        diagnostics, source_failed, policy = build_project(dest, policy="compatible")
        assert source_failed is False
        assert exit_code_for_build(diagnostics, policy, source_failed=False) == 0
        assert any(d.code == CODE_ADAPTER_BLOCK and d.level == "error" for d in diagnostics)


def _run_agentc(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=ROOT,
    )


def test_cli_build_skill_and_rule_json_through():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill_and_rule(dest, targets=("cursor",))
        result = _run_agentc("build", str(dest), "--format", "json")
        assert result.returncode == 0, result.stdout + result.stderr
        outputs = dest / "build" / "outputs"
        assert outputs.is_dir()
        assert [p for p in outputs.rglob("*") if p.is_file()]
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_cli_build_json_is_array():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        result = _run_agentc("build", str(dest), "--format", "json")
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_cli_build_strict_no_hosts_exit_1():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        yaml_path = dest / "agent-project.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        yaml_path.write_text(text.replace("enabled: true", "enabled: false"), encoding="utf-8")
        result = _run_agentc("build", str(dest), "--policy", "strict", "--format", "json")
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P2-0001" for d in payload)


def test_cli_build_compatible_no_hosts_still_exit_1():
    """AGC-P2-0001 不是 Adapter block：即使 --policy compatible 也 exit 1。"""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        yaml_path = dest / "agent-project.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        yaml_path.write_text(text.replace("enabled: true", "enabled: false"), encoding="utf-8")
        result = _run_agentc(
            "build", str(dest), "--policy", "compatible", "--format", "json"
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == "AGC-P2-0001" and d["level"] == "error" for d in payload)


def test_permissive_adapter_block_is_warning():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_ADAPTER_BLOCK

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_adapter_block(dest)
        diagnostics, source_failed, policy = build_project(dest, policy="permissive")
        assert source_failed is False
        assert exit_code_for_build(diagnostics, policy, source_failed=False) == 0
        assert any(d.code == CODE_ADAPTER_BLOCK and d.level == "warning" for d in diagnostics)


def test_cli_build_translate_failure_text_no_ok():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        (dest / "skills" / "hello" / "SKILL.md").unlink()
        result = _run_agentc("build", str(dest), "--policy", "strict")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "AGC-P2-0002" in result.stdout
        assert not any(line == "ok" for line in result.stdout.splitlines())


def test_cli_build_permissive_adapter_block_text_no_ok_for_blocked_host():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_adapter_block(dest)
        result = _run_agentc("build", str(dest), "--policy", "permissive")
        assert result.returncode == 0, result.stdout + result.stderr
        stdout = result.stdout
        assert "AGC-P2-0003" in stdout
        assert "warning:" in stdout
        assert "host: cursor" in stdout
        assert not re.search(r"host: cursor\nok\b", stdout)


def test_build_unknown_profile_exits_1_and_skips_outputs():
    from agentc.cli.main import main as agentc_main
    from agentc.model.diagnostics import CODE_PROFILE_UNKNOWN

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        code = agentc_main(
            ["build", str(dest), "--profile", "cursor/does-not-exist", "--format", "json"]
        )
        assert code == 1
        outputs = dest / "build" / "outputs"
        if outputs.exists():
            assert list(outputs.iterdir()) == []


def test_profile_unknown_is_fatal_under_compatible():
    from agentc.build.run import exit_code_for_build
    from agentc.model.diagnostics import CODE_PROFILE_UNKNOWN, Diagnostic

    diagnostic = Diagnostic(
        code=CODE_PROFILE_UNKNOWN,
        level="error",
        message="unknown profile",
        path="/targets/cursor",
        hint="unknown profile",
    )
    assert (
        exit_code_for_build([diagnostic], "compatible", source_failed=False) == 1
    )


def test_build_named_default_profile_still_writes_cursor_output():
    from agentc.cli.main import main as agentc_main

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        code = agentc_main(
            ["build", str(dest), "--profile", "cursor/2026.09", "--format", "json"]
        )
        assert code == 0
        assert (dest / "build" / "outputs").exists()
        assert any((dest / "build" / "outputs").rglob("*"))


def test_build_last_profile_wins_unknown_then_default():
    from agentc.cli.main import main as agentc_main

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        code = agentc_main(
            [
                "build",
                str(dest),
                "--profile",
                "cursor/does-not-exist",
                "--profile",
                "cursor/2026.09",
                "--format",
                "json",
            ]
        )
        assert code == 0


def test_build_invalid_profiles_dir_is_0011_fatal():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_PROFILE_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        bad = Path(tmp) / "profiles"
        bad.mkdir()
        (bad / "defaults.yaml").write_text("not-a-mapping\n", encoding="utf-8", newline="\n")
        diagnostics, source_failed, policy = build_project(
            dest, policy="compatible", matrix_path=bad
        )
        assert source_failed is False
        assert any(item.code == CODE_PROFILE_INVALID for item in diagnostics)
        assert exit_code_for_build(diagnostics, policy, source_failed=False) == 1
        outputs = dest / "build" / "outputs"
        if outputs.exists():
            assert list(outputs.iterdir()) == []


def test_build_corrupt_profile_yaml_is_0011_fatal():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_PROFILE_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        profiles = Path(tmp) / "profiles"
        profiles.mkdir()
        (profiles / "defaults.yaml").write_text(
            "schemaVersion: agentc-host-profile/v1\n"
            "defaults:\n  cursor: cursor/x\n",
            encoding="utf-8",
            newline="\n",
        )
        (profiles / "cursor-x.yaml").write_text(
            "id: cursor/x\nhost: cursor\ncapabilities:\n  skill: not-a-level\n",
            encoding="utf-8",
            newline="\n",
        )
        diagnostics, source_failed, policy = build_project(
            dest, policy="compatible", matrix_path=profiles
        )
        assert source_failed is False
        assert any(item.code == CODE_PROFILE_INVALID for item in diagnostics)
        assert exit_code_for_build(diagnostics, policy, source_failed=False) == 1
        outputs = dest / "build" / "outputs"
        if outputs.exists():
            assert list(outputs.iterdir()) == []


def test_build_profile_id_mismatch_is_0011_fatal():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_PROFILE_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        profiles = _profiles_dir(Path(tmp), {"skill": "supported"})
        yaml_path = profiles / "cursor-x.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        yaml_path.write_text(
            text.replace("id: cursor/x", "id: cursor/OTHER"),
            encoding="utf-8",
        )
        diagnostics, source_failed, policy = build_project(
            dest, policy="compatible", matrix_path=profiles
        )
        assert source_failed is False
        assert any(item.code == CODE_PROFILE_INVALID for item in diagnostics)
        assert exit_code_for_build(diagnostics, policy, source_failed=False) == 1
        outputs = dest / "build" / "outputs"
        if outputs.exists():
            assert list(outputs.iterdir()) == []


def test_build_unknown_skill_level_is_0004_strict():
    from agentc.build.run import build_project, exit_code_for_build
    from agentc.model.diagnostics import CODE_MATRIX_UNSUPPORTED

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        matrix = _profiles_dir(Path(tmp), {"skill": "unknown"})
        diagnostics, source_failed, policy = build_project(
            dest, matrix_path=matrix, policy="strict"
        )
        assert source_failed is False
        assert any(item.code == CODE_MATRIX_UNSUPPORTED for item in diagnostics)
        assert exit_code_for_build(diagnostics, policy, source_failed=False) == 1


def main() -> int:
    tests = (
        test_source_errors_do_not_call_adapters,
        test_no_enabled_hosts_is_0001,
        test_matrix_unsupported_is_0004_not_success,
        test_matrix_unsupported_rule_blocks_host_with_rule_registered,
        test_compatible_keeps_error_level,
        test_permissive_maps_block_to_warning,
        test_skill_and_rule_build_writes_outputs_and_report_files,
        test_cursor_build_emits_rule_artifact,
        test_artifacts_stay_under_project_build,
        test_strict_adapter_block_exit_1_code_0003,
        test_compatible_adapter_block_exit_0_keeps_error,
        test_permissive_adapter_block_is_warning,
        test_cli_build_translate_failure_text_no_ok,
        test_cli_build_permissive_adapter_block_text_no_ok_for_blocked_host,
        test_cli_build_skill_and_rule_json_through,
        test_cli_build_json_is_array,
        test_cli_build_strict_no_hosts_exit_1,
        test_cli_build_compatible_no_hosts_still_exit_1,
        test_build_unknown_profile_exits_1_and_skips_outputs,
        test_profile_unknown_is_fatal_under_compatible,
        test_build_named_default_profile_still_writes_cursor_output,
        test_build_last_profile_wins_unknown_then_default,
        test_build_invalid_profiles_dir_is_0011_fatal,
        test_build_corrupt_profile_yaml_is_0011_fatal,
        test_build_profile_id_mismatch_is_0011_fatal,
        test_build_unknown_skill_level_is_0004_strict,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
