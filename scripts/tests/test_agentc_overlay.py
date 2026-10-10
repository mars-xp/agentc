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


def _write_min_project(dest: Path) -> None:
    (dest / "skills" / "hello").mkdir(parents=True)
    (dest / "overlays" / "cursor").mkdir(parents=True)
    (dest / "overlays" / "codex").mkdir(parents=True)
    (dest / "overlays" / "claude-code").mkdir(parents=True)
    (dest / "agent-project.yaml").write_text(
        "schemaVersion: agent-project/v1\n"
        "project:\n"
        "  id: demo\n"
        "  name: Demo\n"
        "  version: 0.0.1\n"
        "contents:\n"
        "  skills:\n"
        "    - skills/hello\n",
        encoding="utf-8",
    )
    (dest / "skills" / "hello" / "SKILL.md").write_text(
        "---\nname: hello\ndescription: d\n---\n# Hello\n",
        encoding="utf-8",
    )


def test_p2_overlay_codes_exist():
    from agentc.model.diagnostics import (
        CODE_ARTIFACT_ESCAPE,
        CODE_OVERLAY_INVALID,
        CODE_OVERLAY_MISSING,
        CODE_OVERLAY_PATH,
        CODE_OVERLAY_REMOVE_REASON,
    )

    assert CODE_ARTIFACT_ESCAPE == "AGC-P2-0005"
    assert CODE_OVERLAY_INVALID == "AGC-P2-0006"
    assert CODE_OVERLAY_REMOVE_REASON == "AGC-P2-0007"
    assert CODE_OVERLAY_PATH == "AGC-P2-0008"
    assert CODE_OVERLAY_MISSING == "AGC-P2-0009"


def test_no_overlay_json_is_ok():
    from agentc.overlay.ops import check_overlays

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _write_min_project(dest)
        assert check_overlays(dest) == []


def test_object_json_is_invalid():
    from agentc.overlay.ops import check_overlays
    from agentc.model.diagnostics import CODE_OVERLAY_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _write_min_project(dest)
        (dest / "overlays" / "codex" / "plugin.json").write_text(
            json.dumps({"description": "x"}) + "\n", encoding="utf-8"
        )
        diags = check_overlays(dest)
        assert any(d.code == CODE_OVERLAY_INVALID and d.level == "error" for d in diags)


def test_remove_without_reason_is_error():
    from agentc.overlay.ops import check_overlays
    from agentc.model.diagnostics import CODE_OVERLAY_REMOVE_REASON

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _write_min_project(dest)
        (dest / "overlays" / "codex" / "plugin.json").write_text(
            json.dumps([{"op": "remove", "field": "author", "reason": ""}]) + "\n",
            encoding="utf-8",
        )
        diags = check_overlays(dest)
        assert any(
            d.code == CODE_OVERLAY_REMOVE_REASON and d.level == "error" for d in diags
        )


def test_add_file_escape_is_error():
    from agentc.overlay.ops import check_overlays
    from agentc.model.diagnostics import CODE_OVERLAY_PATH

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _write_min_project(dest)
        (dest / "overlays" / "codex" / "plugin.json").write_text(
            json.dumps(
                [{"op": "add-file", "from": "../secret.txt", "to": "assets/x.txt"}]
            )
            + "\n",
            encoding="utf-8",
        )
        diags = check_overlays(dest)
        assert any(d.code == CODE_OVERLAY_PATH and d.level == "error" for d in diags)


def test_add_file_missing_source_is_error():
    from agentc.overlay.ops import check_overlays
    from agentc.model.diagnostics import CODE_OVERLAY_MISSING

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _write_min_project(dest)
        (dest / "overlays" / "codex" / "plugin.json").write_text(
            json.dumps(
                [{"op": "add-file", "from": "nope.png", "to": "assets/nope.png"}]
            )
            + "\n",
            encoding="utf-8",
        )
        diags = check_overlays(dest)
        assert any(d.code == CODE_OVERLAY_MISSING and d.level == "error" for d in diags)


def test_validate_overlay_error_does_not_write_build():
    from agentc.validate.engine import validate_project
    from agentc.model.diagnostics import CODE_OVERLAY_INVALID

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _write_min_project(dest)
        (dest / "overlays" / "cursor" / "plugin.json").write_text("{}\n", encoding="utf-8")
        diags = validate_project(dest)
        assert any(d.code == CODE_OVERLAY_INVALID and d.level == "error" for d in diags)
        assert not (dest / "build").exists()


def test_fields_for_host_set_then_remove():
    from agentc.overlay.ops import OverlayOp, fields_for_host

    ops = (
        OverlayOp(host="codex", op="set", json_path="overlays/codex/a.json", field="description", value="A"),
        OverlayOp(host="codex", op="set", json_path="overlays/codex/a.json", field="author", value={"name": "X"}),
        OverlayOp(host="codex", op="remove", json_path="overlays/codex/a.json", field="author", reason="not in overlay fields"),
    )
    fields, removes = fields_for_host(ops, "codex")
    assert fields["description"] == "A"
    assert "author" not in fields
    assert "author" in removes


def test_build_overlay_error_exits_1_and_does_not_apply():
    import json
    from agentc.cli.main import main as agentc_main

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "p"
        _write_min_project(dest)
        (dest / "overlays" / "codex" / "plugin.json").write_text("{}\n", encoding="utf-8")
        code = agentc_main(["build", str(dest), "--format", "json"])
        assert code == 1
        outputs = dest / "build" / "outputs"
        if outputs.exists():
            assert list(outputs.iterdir()) == []


def test_build_three_ops_in_report_and_add_file_visible():
    import json
    import shutil
    from agentc.build.run import build_project

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "host-skills"
        shutil.copytree(ROOT / "integrations" / "host-skills", dest)
        extra = dest / "overlays" / "cursor" / "extra.txt"
        extra.write_text("hello-overlay\n", encoding="utf-8")
        cursor_json = dest / "overlays" / "cursor" / "plugin.json"
        ops = json.loads(cursor_json.read_text(encoding="utf-8"))
        if not isinstance(ops, list):
            ops = []
        ops.extend(
            [
                {"op": "set", "field": "description", "value": "from-overlay"},
                {
                    "op": "remove",
                    "field": "homepage",
                    "reason": "not needed in overlay test",
                },
                {"op": "add-file", "from": "extra.txt", "to": "assets/extra.txt"},
            ]
        )
        cursor_json.write_text(json.dumps(ops) + "\n", encoding="utf-8")
        diags, source_failed, _policy = build_project(dest)
        assert source_failed is False
        report = (dest / "build" / "reports" / "report.md").read_text(encoding="utf-8")
        assert "overlay" in report.lower()
        assert "set" in report
        assert "remove" in report
        assert "add-file" in report
        payload = json.loads(
            (dest / "build" / "reports" / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert isinstance(payload, dict)
        assert isinstance(payload["diagnostics"], list)
        overlay_ops = payload["overlay"]
        kinds = {item["op"] for item in overlay_ops}
        assert {"set", "remove", "add-file"} <= kinds
        found = list((dest / "build" / "outputs").rglob("extra.txt"))
        assert found
        assert "hello-overlay" in found[0].read_text(encoding="utf-8")


def test_overlay_path_error_exits_1_under_compatible():
    from agentc.build.run import exit_code_for_build
    from agentc.model.diagnostics import CODE_OVERLAY_PATH, Diagnostic

    diagnostic = Diagnostic(
        code=CODE_OVERLAY_PATH,
        level="error",
        message="overlay path escaped",
        path="/targets/cursor",
        hint="overlay path escaped",
    )
    assert (
        exit_code_for_build([diagnostic], "compatible", source_failed=False) == 1
    )


def test_help_has_no_overlay_command():
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
    assert "doctor" in proc.stdout
    assert "{overlay}" not in proc.stdout.replace(" ", "")
    # 子命令列表里不能单独出现 overlay
    assert "overlay" not in proc.stdout.split("{", 1)[-1].split("}", 1)[0]


def test_host_skills_validate_ok():
    from agentc.validate.engine import validate_project

    diags = validate_project(ROOT / "integrations" / "host-skills")
    assert not any(d.level == "error" and d.code.startswith("AGC-P2-000") and d.code >= "AGC-P2-0006" for d in diags)


def main() -> int:
    tests = (
        test_p2_overlay_codes_exist,
        test_no_overlay_json_is_ok,
        test_object_json_is_invalid,
        test_remove_without_reason_is_error,
        test_add_file_escape_is_error,
        test_add_file_missing_source_is_error,
        test_validate_overlay_error_does_not_write_build,
        test_fields_for_host_set_then_remove,
        test_build_overlay_error_exits_1_and_does_not_apply,
        test_build_three_ops_in_report_and_add_file_visible,
        test_overlay_path_error_exits_1_under_compatible,
        test_help_has_no_overlay_command,
        test_host_skills_validate_ok,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
