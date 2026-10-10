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


def _run_agentc(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=ROOT,
    )


def _project_with_skill(dest: Path, targets: tuple[str, ...] = ("cursor",)) -> None:
    from agentc.project.create_skill import create_skill
    from agentc.project.init_project import init_project

    issues = init_project(dest, project_id="acme", name="Acme", targets=targets)
    assert not any(i.level == "error" for i in issues)
    skill_issues = create_skill(dest, "hello")
    assert not any(i.level == "error" for i in skill_issues)


def _enable_codex_in_yaml(dest: Path) -> None:
    yaml_path = dest / "agent-project.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    yaml_path.write_text(
        text.replace("codex:\n    enabled: false", "codex:\n    enabled: true"),
        encoding="utf-8",
    )


def _two_hosts_cursor_built(dest: Path) -> None:
    """init --targets cursor, build cursor, then enable codex in yaml."""
    from agentc.build.run import build_project

    _project_with_skill(dest, targets=("cursor",))
    diagnostics, source_failed, _policy = build_project(dest)
    assert source_failed is False
    assert not any(d.level == "error" for d in diagnostics)
    _enable_codex_in_yaml(dest)


def test_package_after_cursor_build_writes_zip():
    from agentc.build.package import package_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        from agentc.build.run import build_project

        build_project(dest)
        diags, failed, policy = package_project(dest)
        assert failed is False
        zips = list((dest / "build" / "dist").rglob("*.zip"))
        assert zips
        for z in zips:
            z.resolve().relative_to((dest / "build").resolve())


def test_package_without_outputs_is_0001():
    from agentc.build.package import exit_code_for_package, package_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        diags, failed, policy = package_project(dest, policy="strict")
        assert any(
            d.code == "AGC-P3-0001" and d.level == "error" for d in diags
        )
        assert exit_code_for_package(diags, "strict", source_failed=failed) == 1
        assert not list((dest / "build" / "dist").rglob("*.zip"))


def test_package_compatible_one_host_missing_keeps_error_exit_0():
    from agentc.build.package import exit_code_for_package, package_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _two_hosts_cursor_built(dest)
        diags, failed, policy = package_project(dest, policy="compatible")
        assert any(
            d.code == "AGC-P3-0001"
            and d.level == "error"
            and d.path == "/targets/codex"
            for d in diags
        )
        zips = list((dest / "build" / "dist").rglob("*.zip"))
        assert zips
        assert exit_code_for_package(diags, "compatible", source_failed=failed) == 0


def test_package_permissive_missing_output_is_warning_exit_0():
    from agentc.build.package import exit_code_for_package, package_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _two_hosts_cursor_built(dest)
        diags, failed, policy = package_project(dest, policy="permissive")
        assert any(
            d.code == "AGC-P3-0001"
            and d.level == "warning"
            and d.path == "/targets/codex"
            for d in diags
        )
        assert exit_code_for_package(diags, "permissive", source_failed=failed) == 0
        zips = list((dest / "build" / "dist").rglob("*.zip"))
        assert zips


def test_package_source_error_writes_no_zip_even_compatible():
    from agentc.build.package import exit_code_for_package, package_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        (dest / "agent-project.yaml").write_text(
            "not: valid: yaml: [", encoding="utf-8"
        )
        diags, failed, policy = package_project(dest, policy="compatible")
        assert failed is True
        assert exit_code_for_package(diags, "compatible", source_failed=True) == 1
        assert not list((dest / "build" / "dist").rglob("*.zip"))


def test_zip_destination_rejects_parent_host():
    from agentc.build.package import _zip_destination

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        dest.mkdir()
        try:
            _zip_destination(dest / "build", "..", "acme")
            raise AssertionError("expected escape")
        except ValueError:
            pass


def test_package_unsafe_project_id_maps_to_0002_no_traceback():
    from agentc.build.package import exit_code_for_package, package_project
    from agentc.build.run import build_project
    from agentc.model.diagnostics import CODE_PACKAGE_ESCAPE
    from agentc.validate.engine import validate_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        build_project(dest)
        yaml_path = dest / "agent-project.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        yaml_path.write_text(
            text.replace("  id: acme", "  id: ../evil"),
            encoding="utf-8",
        )
        validate_issues = validate_project(dest)
        assert not any(i.level == "error" for i in validate_issues)
        diags, failed, _policy = package_project(dest, policy="compatible")
        assert failed is False
        assert any(
            d.code == CODE_PACKAGE_ESCAPE and d.level == "error" for d in diags
        )
        assert exit_code_for_package(diags, "compatible", source_failed=False) == 1
        assert not list((dest / "build" / "dist").rglob("*.zip"))


def test_package_maps_escape_to_0002_fatal_under_compatible():
    from agentc.build.package import exit_code_for_package
    from agentc.model.diagnostics import CODE_PACKAGE_ESCAPE, Diagnostic

    diags = [
        Diagnostic(
            code=CODE_PACKAGE_ESCAPE,
            level="error",
            message="path escaped build root",
            path="/targets/cursor",
            hint="path escaped build root",
        )
    ]
    assert (
        exit_code_for_package(diags, "compatible", source_failed=False) == 1
    )


def test_package_io_failure_is_0003_fatal_under_compatible():
    from agentc.build.package import exit_code_for_package, package_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        from agentc.build.run import build_project

        build_project(dest)
        blocker = dest / "build" / "dist" / "cursor"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("not a directory", encoding="utf-8")
        diags, failed, policy = package_project(dest, policy="compatible")
        assert any(
            d.code == "AGC-P3-0003" and d.level == "error" for d in diags
        )
        assert exit_code_for_package(diags, "compatible", source_failed=failed) == 1


def test_cli_package_json_is_five_key_array():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        build = _run_agentc("build", str(dest), "--format", "json")
        assert build.returncode == 0, build.stdout + build.stderr
        result = _run_agentc("package", str(dest), "--format", "json")
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        for item in payload:
            assert set(item.keys()) == {"code", "level", "message", "path", "hint"}


def test_cli_package_text_no_ok_for_missing_host():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _two_hosts_cursor_built(dest)
        result = _run_agentc(
            "package", str(dest), "--policy", "compatible", "--format", "text"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        stdout = result.stdout
        assert "AGC-P3-0001" in stdout
        assert "error:" in stdout
        assert not re.search(r"host: codex\nok\b", stdout)


def test_package_writes_sha256_and_cdx_beside_zip():
    from agentc.build.package import package_project
    from agentc.build.run import build_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        build_project(dest)
        diags, failed, _policy = package_project(dest)
        assert failed is False
        zips = list((dest / "build" / "dist").rglob("*.zip"))
        assert zips
        for zpath in zips:
            sha = Path(str(zpath) + ".sha256")
            cdx = Path(str(zpath) + ".cdx.json")
            assert sha.is_file()
            assert cdx.is_file()
            hexdigest, name = sha.read_text(encoding="utf-8").strip().split()
            assert name == zpath.name
            import hashlib
            assert hexdigest == hashlib.sha256(zpath.read_bytes()).hexdigest()
            payload = json.loads(cdx.read_text(encoding="utf-8"))
            assert payload["bomFormat"] == "CycloneDX"
            assert payload["components"][0]["hashes"][0]["content"] == hexdigest


def test_package_twice_same_zip_hash():
    from agentc.build.package import package_project
    from agentc.build.run import build_project
    import hashlib

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _project_with_skill(dest, targets=("cursor",))
        build_project(dest)
        package_project(dest)
        first = {
            z.as_posix(): hashlib.sha256(z.read_bytes()).hexdigest()
            for z in (dest / "build" / "dist").rglob("*.zip")
        }
        package_project(dest)
        second = {
            z.as_posix(): hashlib.sha256(z.read_bytes()).hexdigest()
            for z in (dest / "build" / "dist").rglob("*.zip")
        }
        assert first
        assert first == second


def test_zip_plugin_tree_twice_same_bytes():
    import os
    import zipfile

    from plugin_compiler.zip_tree import zip_plugin_tree

    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        sample = tree / "a.txt"
        sample.write_text("hello\n", encoding="utf-8")
        first = Path(tmp) / "first.zip"
        second = Path(tmp) / "second.zip"
        zip_plugin_tree(tree, "plugin", first)
        os.utime(sample, (1_700_000_000, 1_700_000_000))
        zip_plugin_tree(tree, "plugin", second)
        assert first.read_bytes() == second.read_bytes()
        with zipfile.ZipFile(first) as zf:
            infos = zf.infolist()
            assert infos
            for info in infos:
                assert info.date_time == (1980, 1, 1, 0, 0, 0)


def main() -> int:
    tests = (
        test_package_after_cursor_build_writes_zip,
        test_package_without_outputs_is_0001,
        test_package_compatible_one_host_missing_keeps_error_exit_0,
        test_package_permissive_missing_output_is_warning_exit_0,
        test_package_source_error_writes_no_zip_even_compatible,
        test_zip_destination_rejects_parent_host,
        test_package_unsafe_project_id_maps_to_0002_no_traceback,
        test_package_maps_escape_to_0002_fatal_under_compatible,
        test_package_io_failure_is_0003_fatal_under_compatible,
        test_cli_package_json_is_five_key_array,
        test_cli_package_text_no_ok_for_missing_host,
        test_package_writes_sha256_and_cdx_beside_zip,
        test_package_twice_same_zip_hash,
        test_zip_plugin_tree_twice_same_bytes,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
