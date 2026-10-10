#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
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


def test_p6_code_exists():
    from agentc.model.diagnostics import CODE_PROTOCOL_EMIT

    assert CODE_PROTOCOL_EMIT == "AGC-P6-0001"


def test_wrap_shape_and_status():
    from agentc.model.diagnostics import Diagnostic
    from agentc.protocol.envelope import PROTOCOL_VERSION, wrap
    from agentc.protocol.schema import validate_envelope

    diag = Diagnostic("AGC-P1-0001", "error", "x", None, "h")
    obj = wrap(
        diagnostics=[diag],
        exit_code=1,
        artifacts=[],
        confirmation_required=False,
        summary="failed",
    )
    assert obj["protocolVersion"] == PROTOCOL_VERSION == "agentc-protocol/v1"
    assert set(obj) == {
        "protocolVersion",
        "status",
        "summary",
        "diagnostics",
        "artifacts",
        "confirmation",
    }
    assert obj["status"] == "error"
    assert obj["diagnostics"] == [
        {
            "code": "AGC-P1-0001",
            "level": "error",
            "message": "x",
            "path": None,
            "hint": "h",
        }
    ]
    assert obj["artifacts"] == []
    assert obj["confirmation"] == {"required": False, "flag": None}
    assert validate_envelope(obj) == []

    unconfirmed = wrap(
        diagnostics=[],
        exit_code=1,
        artifacts=[],
        confirmation_required=True,
        summary="need --yes",
    )
    assert unconfirmed["status"] == "unconfirmed"
    assert unconfirmed["confirmation"] == {"required": True, "flag": "--yes"}


def test_schema_file_exists():
    path = ROOT / "schemas" / "agentc-protocol" / "v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["required"] == [
        "protocolVersion",
        "status",
        "summary",
        "diagnostics",
        "artifacts",
        "confirmation",
    ]


def _run_agentc(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=cwd or ROOT,
    )


def test_init_protocol_ok_and_json_still_array():
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        proto = _run_agentc(
            "init",
            str(dest),
            "--id",
            "acme",
            "--name",
            "Acme",
            "--targets",
            "cursor",
            "--format",
            "protocol",
        )
        assert proto.returncode == 0, proto.stdout + proto.stderr
        obj = json.loads(proto.stdout)
        assert isinstance(obj, dict)
        assert validate_envelope(obj) == []
        assert obj["status"] == "ok"
        assert dest.resolve().as_posix() in obj["artifacts"]

        again = Path(tmp) / "json-proj"
        as_json = _run_agentc(
            "init",
            str(again),
            "--id",
            "acme",
            "--name",
            "Acme",
            "--targets",
            "cursor",
            "--format",
            "json",
        )
        assert as_json.returncode == 0
        payload = json.loads(as_json.stdout)
        assert isinstance(payload, list)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_help_src_only_still_works():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-m", "agentc", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _write_min_cursor_plugin(root: Path) -> None:
    (root / ".cursor-plugin").mkdir(parents=True)
    (root / ".cursor-plugin" / "plugin.json").write_text(
        '{"name": "demo-plugin", "version": "0.0.1"}\n',
        encoding="utf-8",
    )
    skill = root / "skills" / "hello"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n",
        encoding="utf-8",
    )


def test_install_protocol_yes_writes_dest_artifact():
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = tmp_path / "payload"
        payload.mkdir()
        plugin_dir = payload / ".cursor-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            '{"name": "demo-plugin"}\n', encoding="utf-8"
        )
        zip_path = tmp_path / "p.zip"
        import zipfile

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(plugin_dir / "plugin.json", "payload/.cursor-plugin/plugin.json")
        root = tmp_path / "root"
        root.mkdir()
        result = _run_agentc(
            "install",
            "cursor",
            "--from",
            str(zip_path),
            "--root",
            str(root),
            "--yes",
            "--format",
            "protocol",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        obj = json.loads(result.stdout)
        assert validate_envelope(obj) == []
        assert obj["status"] == "ok"
        dest = (root / "demo-plugin").resolve().as_posix()
        assert dest in obj["artifacts"]
        assert (root / "demo-plugin").is_dir()


def test_install_protocol_yes_idempotent_empty_artifacts():
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = tmp_path / "payload"
        payload.mkdir()
        plugin_dir = payload / ".cursor-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            '{"name": "demo-plugin"}\n', encoding="utf-8"
        )
        zip_path = tmp_path / "p.zip"
        import zipfile

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(plugin_dir / "plugin.json", "payload/.cursor-plugin/plugin.json")
        root = tmp_path / "root"
        root.mkdir()
        first = _run_agentc(
            "install",
            "cursor",
            "--from",
            str(zip_path),
            "--root",
            str(root),
            "--yes",
            "--format",
            "protocol",
        )
        assert first.returncode == 0, first.stdout + first.stderr
        first_obj = json.loads(first.stdout)
        assert validate_envelope(first_obj) == []
        dest = (root / "demo-plugin").resolve()
        assert dest.as_posix() in first_obj["artifacts"]

        manifest = dest / ".cursor-plugin" / "plugin.json"
        before = manifest.read_bytes()
        mtime = manifest.stat().st_mtime_ns
        second = _run_agentc(
            "install",
            "cursor",
            "--from",
            str(zip_path),
            "--root",
            str(root),
            "--yes",
            "--format",
            "protocol",
        )
        assert second.returncode == 0, second.stdout + second.stderr
        second_obj = json.loads(second.stdout)
        assert validate_envelope(second_obj) == []
        assert second_obj["status"] == "ok"
        assert second_obj["artifacts"] == []
        assert manifest.read_bytes() == before
        assert manifest.stat().st_mtime_ns == mtime


def test_install_protocol_unconfirmed_does_not_write():
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = tmp_path / "payload"
        payload.mkdir()
        plugin_dir = payload / ".cursor-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            '{"name": "demo-plugin"}\n', encoding="utf-8"
        )
        zip_path = tmp_path / "p.zip"
        import zipfile

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(plugin_dir / "plugin.json", "payload/.cursor-plugin/plugin.json")
        root = tmp_path / "root"
        root.mkdir()
        result = _run_agentc(
            "install",
            "cursor",
            "--from",
            str(zip_path),
            "--root",
            str(root),
            "--format",
            "protocol",
        )
        assert result.returncode == 1
        obj = json.loads(result.stdout)
        assert validate_envelope(obj) == []
        assert obj["status"] == "unconfirmed"
        assert obj["confirmation"] == {"required": True, "flag": "--yes"}
        assert obj["artifacts"] == []
        assert not (root / "demo-plugin").exists()


def test_import_protocol_unconfirmed_does_not_write():
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_min_cursor_plugin(src)
        result = _run_agentc(
            "import",
            "cursor",
            "--from",
            str(src),
            "--output",
            str(out),
            "--format",
            "protocol",
        )
        assert result.returncode == 1, result.stdout + result.stderr
        obj = json.loads(result.stdout)
        assert validate_envelope(obj) == []
        assert obj["status"] == "unconfirmed"
        assert obj["confirmation"] == {"required": True, "flag": "--yes"}
        assert obj["artifacts"] == []
        assert not out.exists()


def test_package_protocol_lists_zip_artifacts():
    from agentc.project.create_skill import create_skill
    from agentc.project.init_project import init_project
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        issues = init_project(dest, project_id="acme", name="Acme", targets=("cursor",))
        assert not any(i.level == "error" for i in issues)
        assert not any(i.level == "error" for i in create_skill(dest, "hello"))
        build = _run_agentc("build", str(dest), "--format", "protocol")
        assert build.returncode == 0, build.stdout + build.stderr
        pkg = _run_agentc("package", str(dest), "--format", "protocol")
        assert pkg.returncode == 0, pkg.stdout + pkg.stderr
        obj = json.loads(pkg.stdout)
        assert validate_envelope(obj) == []
        assert obj["status"] == "ok"
        assert obj["artifacts"]
        for item in obj["artifacts"]:
            assert item.endswith(".zip")
            assert Path(item).is_file()


def test_protocol_subcommands_emit_valid_envelope():
    from agentc.project.create_skill import create_skill
    from agentc.project.init_project import init_project
    from agentc.protocol.schema import validate_envelope

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "proj"
        issues = init_project(dest, project_id="acme", name="Acme", targets=("cursor",))
        assert not any(i.level == "error" for i in issues)

        validate = _run_agentc("validate", str(dest), "--format", "protocol")
        assert validate.returncode == 0, validate.stdout + validate.stderr
        validate_obj = json.loads(validate.stdout)
        assert isinstance(validate_obj, dict)
        assert validate_envelope(validate_obj) == []

        create = _run_agentc(
            "create",
            "skill",
            "hello",
            "--project",
            str(dest),
            "--format",
            "protocol",
        )
        assert create.returncode == 0, create.stdout + create.stderr
        create_obj = json.loads(create.stdout)
        assert isinstance(create_obj, dict)
        assert validate_envelope(create_obj) == []

        plugin = Path(tmp) / "plugin"
        plugin.mkdir()
        _write_min_cursor_plugin(plugin)
        inspect = _run_agentc("inspect", str(plugin), "--format", "protocol")
        assert inspect.returncode == 0, inspect.stdout + inspect.stderr
        inspect_obj = json.loads(inspect.stdout)
        assert isinstance(inspect_obj, dict)
        assert validate_envelope(inspect_obj) == []

        build = _run_agentc("build", str(dest), "--format", "protocol")
        assert build.returncode == 0, build.stdout + build.stderr
        build_obj = json.loads(build.stdout)
        assert isinstance(build_obj, dict)
        assert validate_envelope(build_obj) == []


def test_emit_protocol_repairs_invalid_object(capsys_stdout=None):
    import io
    from contextlib import redirect_stdout, redirect_stderr

    from agentc.model.diagnostics import CODE_PROTOCOL_EMIT
    from agentc.protocol.envelope import emit_protocol
    from agentc.protocol.schema import validate_envelope

    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        emit_protocol({"nope": 1})
    obj = json.loads(buf.getvalue())
    assert validate_envelope(obj) == []
    assert obj["status"] == "error"
    assert any(d["code"] == CODE_PROTOCOL_EMIT for d in obj["diagnostics"])


def test_protocol_package_does_not_import_plugin_compiler():
    pkg = ROOT / "src" / "agentc" / "protocol"
    for path in pkg.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("plugin_compiler")
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("plugin_compiler")


def main() -> int:
    tests = [
        test_p6_code_exists,
        test_wrap_shape_and_status,
        test_schema_file_exists,
        test_init_protocol_ok_and_json_still_array,
        test_help_src_only_still_works,
        test_install_protocol_yes_writes_dest_artifact,
        test_install_protocol_yes_idempotent_empty_artifacts,
        test_install_protocol_unconfirmed_does_not_write,
        test_import_protocol_unconfirmed_does_not_write,
        test_package_protocol_lists_zip_artifacts,
        test_protocol_subcommands_emit_valid_envelope,
        test_emit_protocol_repairs_invalid_object,
        test_protocol_package_does_not_import_plugin_compiler,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
