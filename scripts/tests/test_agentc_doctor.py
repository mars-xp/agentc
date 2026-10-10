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


def test_p1_doctor_codes_exist():
    from agentc.model.diagnostics import (
        CODE_DOCTOR_DIR,
        CODE_DOCTOR_FOUND,
        CODE_DOCTOR_HOST_MISSING,
        CODE_DOCTOR_PYTHON,
        CODE_DOCTOR_VERSION,
        CODE_SKILL_EXISTS,
    )

    assert CODE_DOCTOR_PYTHON == "AGC-P1-0009"
    assert CODE_DOCTOR_FOUND == "AGC-P1-0010"
    assert CODE_DOCTOR_HOST_MISSING == "AGC-P1-0011"
    assert CODE_DOCTOR_DIR == "AGC-P1-0012"
    assert CODE_DOCTOR_VERSION == "AGC-P1-0013"
    assert CODE_SKILL_EXISTS == "AGC-P1-0008"


def test_python_ok_and_missing_hosts_are_warning():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND, CODE_DOCTOR_HOST_MISSING

    diags = run_doctor(
        None,
        version_info=(3, 11, 0),
        executable="/tmp/fake-python",
        which=lambda _name: None,
    )
    assert any(d.code == CODE_DOCTOR_FOUND and d.level == "info" for d in diags)
    missing = [d for d in diags if d.code == CODE_DOCTOR_HOST_MISSING]
    assert len(missing) == 3
    assert all(d.level == "warning" for d in missing)
    assert not any(d.level == "error" for d in diags)


def test_python_too_old_is_error_and_still_reports_hosts():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_HOST_MISSING, CODE_DOCTOR_PYTHON

    diags = run_doctor(
        None,
        version_info=(3, 9, 9),
        executable="/tmp/old-python",
        which=lambda _name: None,
    )
    assert any(d.code == CODE_DOCTOR_PYTHON and d.level == "error" for d in diags)
    assert len([d for d in diags if d.code == CODE_DOCTOR_HOST_MISSING]) == 3


def test_python_unreadable_is_error():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_PYTHON

    diags = run_doctor(
        None,
        version_info=(),
        executable="/tmp/py",
        which=lambda _name: None,
    )
    assert any(d.code == CODE_DOCTOR_PYTHON and d.level == "error" for d in diags)


def _ok_runner(stdout: bytes = b"1.2.3\n", returncode: int = 0):
    calls: list[tuple[list[str], float]] = []

    def run(argv: list[str], timeout_seconds: float):
        calls.append((list(argv), timeout_seconds))
        from agentc.doctor.run import HostVersionResult

        return HostVersionResult(False, returncode, stdout, b"")

    return calls, run


def test_found_host_is_info_with_path():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND

    calls, runner = _ok_runner()

    def fake_which(name: str) -> str | None:
        if name == "cursor":
            return "/tmp/bin/cursor"
        return None

    diags = run_doctor(
        None,
        version_info=(3, 12, 0),
        executable="/tmp/py",
        which=fake_which,
        run_host_version=runner,
    )
    found_hosts = [
        d
        for d in diags
        if d.code == CODE_DOCTOR_FOUND and d.path and "cursor" in d.path
    ]
    assert found_hosts
    assert found_hosts[0].level == "info"
    assert calls


def test_missing_host_does_not_call_runner():
    from agentc.doctor.run import run_doctor

    def boom(argv, timeout_seconds):
        raise AssertionError("runner must not run for missing hosts")

    diags = run_doctor(
        None,
        version_info=(3, 11, 0),
        executable="/tmp/py",
        which=lambda _name: None,
        run_host_version=boom,
    )
    from agentc.model.diagnostics import CODE_DOCTOR_HOST_MISSING

    assert len([d for d in diags if d.code == CODE_DOCTOR_HOST_MISSING]) == 3


def test_found_host_appends_truncated_version_to_0010():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND, CODE_DOCTOR_VERSION

    calls, runner = _ok_runner()
    diags = run_doctor(
        None,
        version_info=(3, 12, 0),
        executable="/tmp/py",
        which=lambda name: "/tmp/bin/cursor" if name == "cursor" else None,
        run_host_version=runner,
    )
    found = [
        d
        for d in diags
        if d.code == CODE_DOCTOR_FOUND and d.path and "cursor" in d.path
    ]
    assert len(found) == 1
    assert found[0].message == "Host CLI cursor found on PATH. 1.2.3"
    assert not any(d.code == CODE_DOCTOR_VERSION for d in diags)
    assert calls[0][0][1] == "--version"
    assert not isinstance(calls[0][0], str)


def test_version_timeout_keeps_0010_and_adds_0013():
    from agentc.doctor.run import HostVersionResult, run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND, CODE_DOCTOR_VERSION

    def runner(argv, timeout_seconds):
        return HostVersionResult(True, None, b"", b"")

    diags = run_doctor(
        None,
        version_info=(3, 12, 0),
        executable="/tmp/py",
        which=lambda name: "/tmp/bin/cursor" if name == "cursor" else None,
        run_host_version=runner,
        timeout_seconds=0.05,
    )
    host_infos = [
        d
        for d in diags
        if d.code == CODE_DOCTOR_FOUND and d.path and "cursor" in d.path
    ]
    warns = [d for d in diags if d.code == CODE_DOCTOR_VERSION]
    assert host_infos[0].message == "Host CLI cursor found on PATH."
    assert len(warns) == 1
    assert warns[0].level == "warning"
    assert warns[0].message == "Host CLI cursor --version timed out after 0.05s."
    assert warns[0].hint == "Doctor continues; this host can remain unused."
    assert not any(d.level == "error" and d.code == CODE_DOCTOR_VERSION for d in diags)
    found_idx = next(
        i
        for i, d in enumerate(diags)
        if d.code == CODE_DOCTOR_FOUND and d.path and "cursor" in d.path
    )
    warn_idx = next(i for i, d in enumerate(diags) if d.code == CODE_DOCTOR_VERSION)
    assert found_idx < warn_idx


def test_version_nonzero_and_empty_are_0013():
    from agentc.doctor.run import HostVersionResult, run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_VERSION

    def bad(argv, timeout_seconds):
        return HostVersionResult(False, 2, b"nope\n", b"")

    def empty(argv, timeout_seconds):
        return HostVersionResult(False, 0, b"", b"")

    def unreadable(argv, timeout_seconds):
        return HostVersionResult(False, 0, b"\xff", b"")

    for fn, needle in (
        (bad, "exited 2"),
        (empty, "no readable version text"),
        (unreadable, "no readable version text"),
    ):
        diags = run_doctor(
            None,
            version_info=(3, 12, 0),
            executable="/tmp/py",
            which=lambda name: "/tmp/bin/codex" if name == "codex" else None,
            run_host_version=fn,
        )
        warns = [d for d in diags if d.code == CODE_DOCTOR_VERSION]
        assert len(warns) == 1
        assert needle in warns[0].message


def test_version_prefers_stdout_and_truncates():
    from agentc.doctor.run import HostVersionResult, run_doctor, VERSION_TEXT_MAX
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND

    long = ("x" * 200) + "\n"

    def runner(argv, timeout_seconds):
        return HostVersionResult(False, 0, long.encode("utf-8"), b"from-err\n")

    diags = run_doctor(
        None,
        version_info=(3, 12, 0),
        executable="/tmp/py",
        which=lambda name: "/tmp/bin/claude" if name == "claude" else None,
        run_host_version=runner,
    )
    found = [
        d
        for d in diags
        if d.code == CODE_DOCTOR_FOUND and d.path and "claude" in d.path
    ]
    text = found[0].message[len("Host CLI claude found on PATH. ") :]
    assert text == ("x" * VERSION_TEXT_MAX) + "…"
    assert "from-err" not in found[0].message


def test_version_uses_stderr_when_stdout_empty():
    from agentc.doctor.run import HostVersionResult, run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND

    def runner(argv, timeout_seconds):
        return HostVersionResult(False, 0, b"\n", b"from-err\n")

    diags = run_doctor(
        None,
        version_info=(3, 12, 0),
        executable="/tmp/py",
        which=lambda name: "/tmp/bin/claude" if name == "claude" else None,
        run_host_version=runner,
    )
    found = [
        d
        for d in diags
        if d.code == CODE_DOCTOR_FOUND and d.path and "claude" in d.path
    ]
    assert found[0].message == "Host CLI claude found on PATH. from-err"


def test_python_0010_does_not_call_runner():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND

    def boom(argv, timeout_seconds):
        raise AssertionError("must not probe python")

    diags = run_doctor(
        None,
        version_info=(3, 11, 0),
        executable="/tmp/fake-python",
        which=lambda _name: None,
        run_host_version=boom,
    )
    py = [
        d
        for d in diags
        if d.code == CODE_DOCTOR_FOUND
        and d.message == "Python interpreter meets minimum version."
    ]
    assert len(py) == 1


def test_dir_runs_after_host_probe():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_FOUND, CODE_INVALID_SCHEMA

    calls, runner = _ok_runner()
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        diags = run_doctor(
            dest,
            version_info=(3, 11, 0),
            executable="/tmp/py",
            which=lambda name: "/tmp/bin/cursor" if name == "cursor" else None,
            run_host_version=runner,
        )
    found_idx = next(
        i
        for i, d in enumerate(diags)
        if d.code == CODE_DOCTOR_FOUND and d.path and "cursor" in d.path
    )
    schema_idx = next(i for i, d in enumerate(diags) if d.code == CODE_INVALID_SCHEMA)
    assert found_idx < schema_idx
    assert calls


def test_dir_0012_still_probes_found_host_first():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_DIR, CODE_DOCTOR_FOUND

    calls, runner = _ok_runner()
    missing = Path("/tmp/agentc-doctor-does-not-exist-xyz")
    diags = run_doctor(
        missing,
        version_info=(3, 11, 0),
        executable="/tmp/py",
        which=lambda name: "/tmp/bin/cursor" if name == "cursor" else None,
        run_host_version=runner,
    )
    found_idx = next(
        i
        for i, d in enumerate(diags)
        if d.code == CODE_DOCTOR_FOUND and d.path and "cursor" in d.path
    )
    dir_idx = next(i for i, d in enumerate(diags) if d.code == CODE_DOCTOR_DIR)
    assert found_idx < dir_idx
    assert calls
    assert not missing.exists()


def _env_src_only() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _run_agentc(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=env or _env_src_only(),
        cwd=ROOT,
    )


def test_parent_escape_dir_is_0012_and_skips_validate():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_DIR, CODE_INVALID_SCHEMA

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        escaped = dest / ".." / "outside"
        diags = run_doctor(
            escaped,
            version_info=(3, 11, 0),
            executable="/tmp/py",
            which=lambda _name: None,
        )
        assert any(d.code == CODE_DOCTOR_DIR and d.level == "error" for d in diags)
        assert not any(d.code == CODE_INVALID_SCHEMA for d in diags)


def test_missing_dir_is_0012():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_DIR

    missing = Path("/tmp/agentc-doctor-does-not-exist-xyz")
    diags = run_doctor(
        missing,
        version_info=(3, 11, 0),
        executable="/tmp/py",
        which=lambda _name: None,
    )
    assert any(d.code == CODE_DOCTOR_DIR and d.level == "error" for d in diags)
    assert not missing.exists()


def test_valid_dir_transmits_validate_error():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_INVALID_SCHEMA

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        diags = run_doctor(
            dest,
            version_info=(3, 11, 0),
            executable="/tmp/py",
            which=lambda _name: None,
        )
        assert any(d.code == CODE_INVALID_SCHEMA and d.level == "error" for d in diags)


def test_valid_project_dir_has_no_schema_error():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_INVALID_SCHEMA
    from agentc.project.init_project import init_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        assert init_project(dest, project_id="acme", name="Acme", targets=("cursor",)) == []
        diags = run_doctor(
            dest,
            version_info=(3, 11, 0),
            executable="/tmp/py",
            which=lambda _name: None,
        )
        assert not any(d.code == CODE_INVALID_SCHEMA for d in diags)
        assert not (dest / "doctor-report.md").exists()
        assert not (dest / "build" / "reports").exists()


def test_file_as_dir_is_0012_and_skips_validate():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_DIR, CODE_INVALID_SCHEMA

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "not-a-dir"
        dest.write_text("x\n", encoding="utf-8")
        diags = run_doctor(
            dest,
            version_info=(3, 11, 0),
            executable="/tmp/py",
            which=lambda _name: None,
        )
        assert any(d.code == CODE_DOCTOR_DIR and d.level == "error" for d in diags)
        assert not any(d.code == CODE_INVALID_SCHEMA for d in diags)


def test_outbound_symlink_dir_is_0012_and_skips_validate():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_DIR, CODE_INVALID_SCHEMA

    with tempfile.TemporaryDirectory() as tmp:
        outside = Path(tmp) / "escape-target"
        outside.mkdir()
        container = Path(tmp) / "container"
        container.mkdir()
        link = container / "link"
        link.symlink_to(outside)
        diags = run_doctor(
            link,
            version_info=(3, 11, 0),
            executable="/tmp/py",
            which=lambda _name: None,
        )
        assert any(d.code == CODE_DOCTOR_DIR and d.level == "error" for d in diags)
        assert not any(d.code == CODE_INVALID_SCHEMA for d in diags)


def test_python_too_old_with_valid_project_keeps_0009():
    from agentc.doctor.run import run_doctor
    from agentc.model.diagnostics import CODE_DOCTOR_PYTHON, CODE_INVALID_SCHEMA
    from agentc.project.init_project import init_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        assert init_project(dest, project_id="acme", name="Acme", targets=("cursor",)) == []
        diags = run_doctor(
            dest,
            version_info=(3, 9, 9),
            executable="/tmp/py",
            which=lambda _name: None,
        )
        assert any(d.code == CODE_DOCTOR_PYTHON and d.level == "error" for d in diags)
        assert not any(d.code == CODE_INVALID_SCHEMA for d in diags)


def test_cli_missing_dir_exits_1():
    missing = Path("/tmp/agentc-doctor-does-not-exist-xyz")
    with tempfile.TemporaryDirectory() as tmp:
        env = _env_src_only()
        env["PATH"] = tmp
        result = _run_agentc(
            "doctor", "--dir", str(missing), "--format", "json", env=env
        )
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert any(item["code"] == "AGC-P1-0012" for item in payload)


def test_cli_isolated_path_runs_fake_cursor_version():
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "cursor"
        fake.write_text(
            "#!{py}\nimport sys\nprint('9.9.9')\nsys.exit(0)\n".format(py=sys.executable),
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = _env_src_only()
        env["PATH"] = tmp
        result = _run_agentc("doctor", "--format", "json", env=env)
        payload = json.loads(result.stdout)
        assert result.returncode == 0
        cursor_info = [
            item
            for item in payload
            if item["code"] == "AGC-P1-0010" and item["path"] and "cursor" in item["path"]
        ]
        assert cursor_info
        assert "9.9.9" in cursor_info[0]["message"]
        assert not any(item["code"] == "AGC-P1-0013" for item in payload)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_cli_version_nonzero_is_0013_exit_0():
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "cursor"
        fake.write_text(
            "#!{py}\nimport sys\nsys.exit(2)\n".format(py=sys.executable),
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = _env_src_only()
        env["PATH"] = tmp
        result = _run_agentc("doctor", "--format", "json", env=env)
        payload = json.loads(result.stdout)
        assert result.returncode == 0
        assert any(item["code"] == "AGC-P1-0013" for item in payload)
        assert not any(
            item["level"] == "error" and item["code"] == "AGC-P1-0013" for item in payload
        )


def test_readme_documents_0013():
    text = (ROOT / "packaging" / "agentc" / "README.md").read_text(encoding="utf-8")
    assert "AGC-P1-0013" in text
    assert "--version" in text


def test_cli_json_five_keys_missing_hosts_exit_0():
    with tempfile.TemporaryDirectory() as tmp:
        env = _env_src_only()
        env["PATH"] = tmp
        result = _run_agentc("doctor", "--format", "json", env=env)
        payload = json.loads(result.stdout)
        assert result.returncode == 0
        assert isinstance(payload, list)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}
        assert any(item["code"] == "AGC-P1-0011" for item in payload)
        assert any(
            item["code"] == "AGC-P1-0010" and item["level"] == "info"
            for item in payload
        )


def test_cli_protocol_envelope():
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        env = _env_src_only()
        env["PATH"] = tmp
        result = _run_agentc("doctor", "--format", "protocol", env=env)
        obj = json.loads(result.stdout)
        assert result.returncode == 0
        assert validate_envelope(obj) == []
        assert set(obj) == {
            "protocolVersion",
            "status",
            "summary",
            "diagnostics",
            "artifacts",
            "confirmation",
        }


def test_help_lists_doctor_without_scripts():
    result = _run_agentc("--help")
    assert result.returncode == 0
    assert "doctor" in result.stdout
    assert "plugin_compiler" not in result.stderr


def main() -> int:
    tests = [
        test_p1_doctor_codes_exist,
        test_python_ok_and_missing_hosts_are_warning,
        test_python_too_old_is_error_and_still_reports_hosts,
        test_python_unreadable_is_error,
        test_found_host_is_info_with_path,
        test_missing_host_does_not_call_runner,
        test_found_host_appends_truncated_version_to_0010,
        test_version_timeout_keeps_0010_and_adds_0013,
        test_version_nonzero_and_empty_are_0013,
        test_version_prefers_stdout_and_truncates,
        test_version_uses_stderr_when_stdout_empty,
        test_python_0010_does_not_call_runner,
        test_dir_runs_after_host_probe,
        test_dir_0012_still_probes_found_host_first,
        test_parent_escape_dir_is_0012_and_skips_validate,
        test_missing_dir_is_0012,
        test_file_as_dir_is_0012_and_skips_validate,
        test_outbound_symlink_dir_is_0012_and_skips_validate,
        test_valid_dir_transmits_validate_error,
        test_valid_project_dir_has_no_schema_error,
        test_python_too_old_with_valid_project_keeps_0009,
        test_cli_missing_dir_exits_1,
        test_cli_isolated_path_runs_fake_cursor_version,
        test_cli_version_nonzero_is_0013_exit_0,
        test_cli_json_five_keys_missing_hosts_exit_0,
        test_cli_protocol_envelope,
        test_help_lists_doctor_without_scripts,
        test_readme_documents_0013,
    ]
    for test in tests:
        test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
