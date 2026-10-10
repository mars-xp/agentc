#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
import zipfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

PROTOCOL_KEYS = {
    "protocolVersion",
    "status",
    "summary",
    "diagnostics",
    "artifacts",
    "confirmation",
}
DIAG_KEYS = {"code", "level", "message", "path", "hint"}
REPORT_NAME = "workspace-build-report.json"
MARKER_NAME = ".agentc-workspace.json"
SCHEMA = "agentc-workspace-build/v1"
VISIBLE_SENTENCE = "VISIBLE_SKILL_BODY_a17c"
SECOND_SENTENCE = "ROUND_TWO_SENTENCE_c9e1"
SECRET_TOKEN = "ghp_" + ("B" * 20)
SECRET_SENTENCE = "SECRET_SKILL_BODY_e44d"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _output_key(source: Path) -> str:
    normalized = source.expanduser().resolve().as_posix()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _cursor_manifest() -> str:
    return (
        '{"name":"demo","version":"0.0.1","description":"demo",'
        '"author":{"name":"demo"}}\n'
    )


def _skill_text(body: str) -> str:
    return "---\nname: demo\ndescription: demo skill\n---\n\n# demo\n\n" + body


def _write_clean_cursor(root: Path, body: str = "") -> None:
    root.mkdir()
    manifest_dir = root / ".cursor-plugin"
    manifest_dir.mkdir()
    (manifest_dir / "plugin.json").write_text(
        _cursor_manifest(), encoding="utf-8", newline="\n"
    )
    skill_dir = root / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        _skill_text(body), encoding="utf-8", newline="\n"
    )


def _signature(root: Path) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        for name in list(dirnames) + list(filenames):
            path = current / name
            rel = name if not rel_dir else "{0}/{1}".format(rel_dir, name)
            if path.is_symlink():
                rows.append((rel, "symlink", os.readlink(path)))
            elif path.is_dir():
                rows.append((rel, "dir", ""))
            else:
                rows.append((rel, "file", _sha(path.read_bytes())))
    rows.sort()
    return tuple(rows)


@contextmanager
def _isolated_home(home: Path):
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


def _call_main(*args: str) -> tuple[int, str, str]:
    from agentc.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = main(list(args))
        except SystemExit as exc:
            if isinstance(exc.code, int):
                code = exc.code
            else:
                code = 0 if exc.code is None else 1
    if not isinstance(code, int):
        raise AssertionError(code)
    return code, stdout.getvalue(), stderr.getvalue()


def _parse_protocol(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, stdout
    payload = json.loads(lines[-1])
    assert set(payload) == PROTOCOL_KEYS
    assert payload["protocolVersion"] == "agentc-protocol/v1"
    assert payload["status"] in {"ok", "error", "unconfirmed"}
    assert isinstance(payload["summary"], str)
    assert isinstance(payload["diagnostics"], list)
    assert isinstance(payload["artifacts"], list)
    assert payload["confirmation"] == {"required": False, "flag": None}
    for item in payload["diagnostics"]:
        assert set(item) == DIAG_KEYS
    for item in payload["artifacts"]:
        assert isinstance(item, str)
    return payload


def _report_from_hint(payload: dict) -> tuple[Path, dict]:
    found: list[Path] = []
    for item in payload["diagnostics"]:
        hint = item["hint"] or ""
        if hint.endswith(REPORT_NAME):
            found.append(Path(hint))
    assert len(found) == 1, payload["diagnostics"]
    path = found[0]
    assert path.is_file(), path
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == SCHEMA
    return path, document


def _zip_contains(path: Path, text: str) -> bool:
    needle = text.encode("utf-8")
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if needle in archive.read(name):
                return True
    return False


def _zip_hashes(root: Path) -> set[str]:
    return {_sha(path.read_bytes()) for path in root.rglob("*.zip") if path.is_file()}


def _plugin_name(zip_path: Path, host: str) -> str:
    suffix = {
        "cursor": ".cursor-plugin/plugin.json",
        "codex": ".codex-plugin/plugin.json",
        "claude-code": ".claude-plugin/plugin.json",
    }[host]
    with zipfile.ZipFile(zip_path) as archive:
        matches = [name for name in archive.namelist() if name.endswith(suffix)]
        assert len(matches) == 1, matches
        payload = json.loads(archive.read(matches[0]))
    name = str(payload["name"])
    assert name
    return name


def _dest_files(dest: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if not dest.exists():
        return files
    for path in dest.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files[path.relative_to(dest).as_posix()] = path.read_bytes()
    return files


def test_argument_errors_are_nonzero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        output = base / "out"

        missing_source, missing_out, _missing_err = _call_main(
            "workspace", "build", "--targets", "cursor", "--format", "protocol"
        )
        assert missing_source != 0
        assert "source" in (missing_out + _missing_err).lower()

        unknown_kind, kind_out, kind_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--source-kind",
            "nope",
            "--format",
            "protocol",
        )
        assert unknown_kind != 0
        assert "nope" in (kind_out + kind_err)

        for targets in ("", "cursor,", ",cursor", "cursor,cursor", "cursor,nope"):
            code, stdout, _stderr = _call_main(
                "workspace",
                "build",
                str(source),
                "--targets",
                targets,
                "--output",
                str(output / targets.replace(",", "_")),
                "--format",
                "protocol",
            )
            assert code != 0, targets
            payload = _parse_protocol(stdout)
            assert payload["status"] == "error"
            assert payload["artifacts"] == []

        text_code, text_out, _text_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor,cursor",
            "--output",
            str(output / "text"),
            "--format",
            "text",
        )
        assert text_code != 0
        assert "error:" in text_out
        assert "hint:" in text_out

        json_code, json_out, _json_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "nope",
            "--output",
            str(output / "json"),
            "--format",
            "json",
        )
        assert json_code != 0
        listed = json.loads(json_out)
        assert isinstance(listed, list) and listed
        assert set(listed[0]) == DIAG_KEYS

        before = _signature(source)
        mismatch, mismatch_out, _mismatch_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--source-kind",
            "codex",
            "--output",
            str(output / "mismatch"),
            "--format",
            "protocol",
        )
        assert mismatch != 0
        mismatch_payload = _parse_protocol(mismatch_out)
        assert mismatch_payload["artifacts"] == []
        assert _signature(source) == before


def test_success_protocol_report_and_no_install() -> None:
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        before = _signature(source)
        with _isolated_home(home):
            code, stdout, stderr = _call_main(
                "workspace",
                "build",
                str(source),
                "--targets",
                "cursor",
                "--source-kind",
                "cursor",
                "--format",
                "protocol",
            )
            assert code == 0, stdout + stderr
            payload = _parse_protocol(stdout)
            assert payload["status"] == "ok"
            output = home / ".agentc" / "workspace-builds" / _output_key(source)
            report_path = output / REPORT_NAME
            assert report_path.is_file()
            document = json.loads(report_path.read_text(encoding="utf-8"))
            assert document["schemaVersion"] == SCHEMA
            assert document["sourceRoot"] == source.resolve().as_posix()
            assert document["sourceKind"] == "cursor"
            inventory, diags = inventory_source(source)
            assert diags == () and inventory is not None
            assert document["inputFingerprint"] == inventory.fingerprint
            assert document["targets"] == ["cursor"]
            assert document["exclusions"] == [".git/"]
            assert isinstance(document["roundId"], str) and document["roundId"]
            rows = {entry["path"]: entry for entry in document["entries"]}
            assert set(rows) == {entry.path for entry in inventory.entries}
            for entry in inventory.entries:
                row = rows[entry.path]
                assert row["kind"] == entry.kind
                assert row["digest"] == entry.digest
                assert len(row["targets"]) == 1
                target_row = row["targets"][0]
                assert target_row["target"] == "cursor"
                assert target_row["status"] in {"transformed", "bundled", "empty"}
                if target_row["status"] in {"transformed", "bundled"}:
                    assert target_row["targetPath"]
            assert document["diagnostics"] == []
            assert len(document["archives"]) == 1
            archive = document["archives"][0]
            assert archive["target"] == "cursor"
            zip_path = Path(archive["path"])
            assert zip_path.is_file()
            assert archive["sha256"] == _sha(zip_path.read_bytes())
            assert _zip_contains(zip_path, VISIBLE_SENTENCE)
            artifact_paths = {Path(item).resolve().as_posix() for item in payload["artifacts"]}
            assert artifact_paths == {
                zip_path.resolve().as_posix(),
                report_path.resolve().as_posix(),
            }
            report_text = report_path.read_text(encoding="utf-8")
            assert VISIBLE_SENTENCE not in report_text
            assert SECRET_TOKEN not in report_text
            assert list(output.rglob("*codex*.zip")) == []
            assert list(output.rglob("*claude-code*.zip")) == []
            assert not (home / ".cursor").exists()
            assert not (home / ".codex").exists()
            assert not (home / ".claude").exists()
            assert not (source / "agent-project.yaml").exists()
            assert _signature(source) == before


def test_failure_report_and_empty_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        source = base / "plugin"
        output = base / "out"
        _write_clean_cursor(
            source,
            body="{0}\n{1}\n".format(SECRET_TOKEN, SECRET_SENTENCE),
        )
        before = _signature(source)
        with _isolated_home(home):
            code, stdout, stderr = _call_main(
                "workspace",
                "build",
                str(source),
                "--targets",
                "cursor,codex",
                "--output",
                str(output),
                "--format",
                "protocol",
            )
        assert code != 0, stdout + stderr
        payload = _parse_protocol(stdout)
        assert payload["status"] == "error"
        assert payload["artifacts"] == []
        report_path, document = _report_from_hint(payload)
        assert report_path.parent == output.resolve()
        assert document["archives"] == []
        assert document["targets"] == ["cursor", "codex"]
        report_bytes = report_path.read_bytes()
        assert SECRET_TOKEN.encode("ascii") not in report_bytes
        assert SECRET_SENTENCE.encode("utf-8") not in report_bytes
        assert list(output.rglob("*.zip")) == []
        assert not (home / ".cursor").exists()
        assert _signature(source) == before

        json_code, json_out, _json_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(base / "json-out"),
            "--format",
            "json",
        )
        assert json_code != 0
        listed = json.loads(json_out)
        assert isinstance(listed, list) and listed
        assert set(listed[0]) == DIAG_KEYS
        assert any(SECRET_TOKEN not in json.dumps(item) for item in listed)
        assert SECRET_TOKEN not in json_out
        assert SECRET_SENTENCE not in json_out


def test_rebuild_changes_zip_and_hides_old_round() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        output = base / "out"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        first_code, first_out, first_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert first_code == 0, first_out + first_err
        first_payload = _parse_protocol(first_out)
        first_report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
        first_round = first_report["roundId"]
        first_zip = Path(first_report["archives"][0]["path"])
        first_name = first_zip.name
        first_bytes = first_zip.read_bytes()
        skill = source / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            _skill_text(SECOND_SENTENCE + "\n"), encoding="utf-8", newline="\n"
        )
        second_code, second_out, second_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert second_code == 0, second_out + second_err
        second_payload = _parse_protocol(second_out)
        second_report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
        assert second_report["roundId"] != first_round
        second_zip = Path(second_report["archives"][0]["path"])
        assert second_zip.is_file()
        assert second_zip.read_bytes() != first_bytes
        assert _zip_contains(second_zip, SECOND_SENTENCE)
        assert not _zip_contains(second_zip, VISIBLE_SENTENCE)
        old_zip = output / "previous" / first_round / first_name
        assert old_zip.is_file()
        assert _sha(old_zip.read_bytes()) == _sha(first_bytes)
        assert _zip_contains(old_zip, VISIBLE_SENTENCE)
        published = {Path(item).resolve() for item in second_payload["artifacts"]}
        assert old_zip.resolve() not in published
        assert all(
            Path(item["path"]).resolve() != old_zip.resolve()
            for item in second_report["archives"]
        )
        assert first_payload["status"] == "ok"
        marker = json.loads((output / MARKER_NAME).read_text(encoding="utf-8"))
        assert marker["currentRound"] == second_report["roundId"]
        stale = [item for item in marker["managed"] if item.get("stale") is True]
        assert any(item.get("round") == first_round for item in stale)
        assert SECOND_SENTENCE not in (output / REPORT_NAME).read_text(encoding="utf-8")


def test_failed_round_zip_survives_next_success() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        output = base / "out"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        first_code, first_out, first_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert first_code == 0, first_out + first_err
        first_report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
        first_round = first_report["roundId"]
        first_zip = Path(first_report["archives"][0]["path"])
        first_name = first_zip.name
        first_bytes = first_zip.read_bytes()
        stray = source / "stray-link"
        stray.symlink_to("skills/demo/SKILL.md")
        failed_code, failed_out, failed_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert failed_code != 0, failed_out + failed_err
        failed_payload = _parse_protocol(failed_out)
        assert failed_payload["artifacts"] == []
        live = output / first_name
        assert live.is_file()
        assert live.read_bytes() == first_bytes
        rejected_code, rejected_out, rejected_err = _call_main(
            "install", "cursor", "--from", str(live),
            "--root", str(base / "installed"), "--yes", "--format", "protocol",
        )
        assert rejected_code != 0, rejected_out + rejected_err
        assert any(
            item["code"] == "AGC-P5-0018"
            for item in _parse_protocol(rejected_out)["diagnostics"]
        )
        stray.unlink()
        skill = source / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            _skill_text(SECOND_SENTENCE + "\n"), encoding="utf-8", newline="\n"
        )
        code, stdout, stderr = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert code == 0, stdout + stderr
        payload = _parse_protocol(stdout)
        report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
        old_zip = output / "previous" / first_round / first_name
        new_zip = output / first_name
        assert old_zip.is_file()
        assert old_zip.resolve() != new_zip.resolve()
        assert old_zip.read_bytes() == first_bytes
        assert new_zip.is_file()
        assert new_zip.read_bytes() != first_bytes
        assert _zip_contains(new_zip, SECOND_SENTENCE)
        assert not _zip_contains(old_zip, SECOND_SENTENCE)
        published = {Path(item).resolve() for item in payload["artifacts"]}
        assert new_zip.resolve() in published
        assert old_zip.resolve() not in published
        archive_paths = {Path(item["path"]).resolve() for item in report["archives"]}
        assert archive_paths == {new_zip.resolve()}


def test_publish_keeps_preexisting_swap_backup() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        output = base / "out"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        first_code, first_out, first_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert first_code == 0, first_out + first_err
        backup = output.with_name(output.name + ".agentc-backup")
        backup.mkdir()
        kept = backup / "user-file.txt"
        kept.write_text("keep-user-backup\n", encoding="utf-8", newline="\n")
        skill = source / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            _skill_text(SECOND_SENTENCE + "\n"), encoding="utf-8", newline="\n"
        )
        code, stdout, stderr = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert code == 0, stdout + stderr
        assert kept.is_file()
        assert kept.read_text(encoding="utf-8") == "keep-user-backup\n"
        assert _zip_contains(output / "demo-0.0.1-cursor.zip", SECOND_SENTENCE)


def test_source_change_during_build_fails() -> None:
    import agentc.workspace.run as run_mod

    real = run_mod.compile_workspace

    def wrapped(plugin_dir, targets, output_dir, source_kind=None):
        result = real(plugin_dir, targets, output_dir, source_kind=source_kind)
        skill = Path(plugin_dir) / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            _skill_text(SECOND_SENTENCE + "\n"), encoding="utf-8", newline="\n"
        )
        return result

    run_mod.compile_workspace = wrapped
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "plugin"
            output = base / "out"
            _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
            code, stdout, stderr = _call_main(
                "workspace",
                "build",
                str(source),
                "--targets",
                "cursor",
                "--output",
                str(output),
                "--format",
                "protocol",
            )
            assert code != 0, stdout + stderr
            payload = _parse_protocol(stdout)
            assert payload["artifacts"] == []
            _report_path, document = _report_from_hint(payload)
            assert document["archives"] == []
            assert any(item["code"] == "AGC-P7-0002" for item in payload["diagnostics"])
            assert list(output.rglob("*.zip")) == []
            assert SECOND_SENTENCE not in (output / REPORT_NAME).read_text(encoding="utf-8")
    finally:
        run_mod.compile_workspace = real


def test_one_target_failure_publishes_no_new_zip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        output = base / "out"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        first_code, first_out, first_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert first_code == 0, first_out + first_err
        before_hashes = _zip_hashes(output)
        assert len(before_hashes) == 1
        first_report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
        agent = source / "agents" / "helper.md"
        agent.parent.mkdir()
        agent.write_text(
            "---\nname: helper\nmodel: demo\n---\n\nDo work.\n",
            encoding="utf-8",
            newline="\n",
        )
        code, stdout, stderr = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor,codex",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert code != 0, stdout + stderr
        payload = _parse_protocol(stdout)
        assert payload["artifacts"] == []
        _report_path, document = _report_from_hint(payload)
        assert document["archives"] == []
        assert _zip_hashes(output) == before_hashes
        assert not any(
            path.name.endswith("-codex.zip")
            for path in output.rglob("*.zip")
            if path.is_file()
        )
        marker = json.loads((output / MARKER_NAME).read_text(encoding="utf-8"))
        assert marker["currentRound"] == document["roundId"]
        assert marker["currentRound"] != first_report["roundId"]
        stale = [item for item in marker["managed"] if item.get("stale") is True]
        assert any(item.get("round") == first_report["roundId"] for item in stale)
        listed = {Path(item["path"]).resolve() for item in document["archives"]}
        assert all(item.resolve() not in listed for item in output.rglob("*.zip"))


def test_publish_failure_leaves_no_partial_zip() -> None:
    import agentc.workspace.run as run_mod

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        output = base / "out"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        first_code, first_out, first_err = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor,codex,claude-code",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert first_code == 0, first_out + first_err
        before_hashes = _zip_hashes(output)
        assert len(before_hashes) == 3
        skill = source / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            _skill_text(SECOND_SENTENCE + "\n"), encoding="utf-8", newline="\n"
        )
        real = run_mod._replace
        state = {"failed": False}

        def flaky(src: Path, dst: Path) -> None:
            if dst.resolve() == output.resolve() and not state["failed"]:
                state["failed"] = True
                raise OSError(28, "No space left on device")
            real(src, dst)

        run_mod._replace = flaky
        try:
            code, stdout, stderr = _call_main(
                "workspace",
                "build",
                str(source),
                "--targets",
                "cursor,codex,claude-code",
                "--output",
                str(output),
                "--format",
                "protocol",
            )
        finally:
            run_mod._replace = real
        assert state["failed"]
        assert code != 0, stdout + stderr
        payload = _parse_protocol(stdout)
        assert payload["artifacts"] == []
        _report_path, document = _report_from_hint(payload)
        assert document["archives"] == []
        assert any(item["code"] == "AGC-P7-0006" for item in payload["diagnostics"])
        zips = [path for path in output.rglob("*.zip") if path.is_file()]
        assert len(zips) == 3
        assert {_sha(path.read_bytes()) for path in zips} == before_hashes
        assert not any(_zip_contains(path, SECOND_SENTENCE) for path in zips)


def test_report_coverage_gap_fails() -> None:
    import agentc.workspace.run as run_mod
    from dataclasses import replace

    real = run_mod.compile_workspace

    def wrapped(plugin_dir, targets, output_dir, source_kind=None):
        result = real(plugin_dir, targets, output_dir, source_kind=source_kind)
        outcomes = tuple(
            item for item in result.outcomes if item.path != "skills/demo/SKILL.md"
        )
        return replace(result, outcomes=outcomes)

    run_mod.compile_workspace = wrapped
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "plugin"
            output = base / "out"
            _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
            code, stdout, stderr = _call_main(
                "workspace",
                "build",
                str(source),
                "--targets",
                "cursor",
                "--output",
                str(output),
                "--format",
                "protocol",
            )
            assert code != 0, stdout + stderr
            payload = _parse_protocol(stdout)
            assert payload["artifacts"] == []
            _report_path, document = _report_from_hint(payload)
            assert document["archives"] == []
            assert list(output.rglob("*.zip")) == []
            assert any(
                item["code"] == "AGC-P7-0005" and item["path"] == "skills/demo/SKILL.md"
                for item in payload["diagnostics"]
            )
            covered = {entry["path"] for entry in document["entries"]}
            assert "skills/demo/SKILL.md" in covered
    finally:
        run_mod.compile_workspace = real


def test_path_escape_and_output_overlap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        outside = base / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("secret\n", encoding="utf-8", newline="\n")
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        os.symlink(outside, source / "leak")
        before = _signature(source)
        secret_bytes = secret.read_bytes()
        output = base / "out"
        code, stdout, stderr = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert code != 0, stdout + stderr
        payload = _parse_protocol(stdout)
        assert payload["artifacts"] == []
        assert any(
            item["code"] == "AGC-P4-0002" and item["path"] == "leak"
            for item in payload["diagnostics"]
        )
        _report_path, document = _report_from_hint(payload)
        assert document["archives"] == []
        assert list(output.rglob("*.zip")) == []
        assert secret.read_bytes() == secret_bytes
        assert _signature(source) == before

        clean = base / "clean"
        _write_clean_cursor(clean, body=VISIBLE_SENTENCE + "\n")
        clean_before = _signature(clean)
        inside = clean / "nested-out"
        overlap_code, overlap_out, overlap_err = _call_main(
            "workspace",
            "build",
            str(clean),
            "--targets",
            "cursor",
            "--output",
            str(inside),
            "--format",
            "protocol",
        )
        assert overlap_code != 0, overlap_out + overlap_err
        overlap_payload = _parse_protocol(overlap_out)
        assert overlap_payload["artifacts"] == []
        assert any(item["code"] == "AGC-P7-0001" for item in overlap_payload["diagnostics"])
        assert _signature(clean) == clean_before
        assert not inside.exists()

        parent_code, parent_out, parent_err = _call_main(
            "workspace",
            "build",
            str(clean),
            "--targets",
            "cursor",
            "--output",
            str(base),
            "--format",
            "protocol",
        )
        assert parent_code != 0, parent_out + parent_err
        parent_payload = _parse_protocol(parent_out)
        assert parent_payload["artifacts"] == []
        assert _signature(clean) == clean_before
        assert list(base.rglob("*.zip")) == []

        blocked = base / "blocked"
        blocked.mkdir()
        (blocked / "user.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        unmanaged_code, unmanaged_out, unmanaged_err = _call_main(
            "workspace",
            "build",
            str(clean),
            "--targets",
            "cursor",
            "--output",
            str(blocked),
            "--format",
            "protocol",
        )
        assert unmanaged_code != 0, unmanaged_out + unmanaged_err
        unmanaged_payload = _parse_protocol(unmanaged_out)
        assert unmanaged_payload["artifacts"] == []
        assert any(item["code"] == "AGC-P7-0006" for item in unmanaged_payload["diagnostics"])
        assert (blocked / "user.txt").read_text(encoding="utf-8") == "keep\n"
        assert list(blocked.iterdir()) == [blocked / "user.txt"]
        assert _signature(clean) == clean_before


def test_install_status_restore_keeps_unmanaged_files() -> None:
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        source = base / "plugin"
        output = base / "out"
        absent_code, absent_out, absent_err = _call_main(
            "status", "--kind", "cursor", "--name", "demo",
            "--root", str(base / "empty-install"), "--format", "protocol",
        )
        assert absent_code == 0, absent_out + absent_err
        assert any(
            item["code"] == "AGC-P5-0020"
            for item in _parse_protocol(absent_out)["diagnostics"]
        )
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PYTHONPATH"] = os.pathsep.join(
            (str(ROOT / "src"), str(ROOT / "scripts"))
        )
        built = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentc",
                "workspace",
                "build",
                str(source),
                "--targets",
                "cursor,codex,claude-code",
                "--output",
                str(output),
                "--format",
                "protocol",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=ROOT,
        )
        assert built.returncode == 0, built.stdout + built.stderr
        payload = _parse_protocol(built.stdout)
        report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
        by_target = {item["target"]: Path(item["path"]) for item in report["archives"]}
        assert set(by_target) == {"cursor", "codex", "claude-code"}
        assert {Path(item).resolve() for item in payload["artifacts"]} >= {
            path.resolve() for path in by_target.values()
        }
        for host, zip_path in by_target.items():
            host_root = base / "install" / host
            host_root.mkdir(parents=True)
            keep = host_root / "user-keep.txt"
            keep.write_text("keep-{0}\n".format(host), encoding="utf-8", newline="\n")
            notes = host_root / "notes" / "plain.txt"
            notes.parent.mkdir()
            notes.write_text("plain\n", encoding="utf-8", newline="\n")
            name = _plugin_name(zip_path, host)
            installed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentc",
                    "install",
                    host,
                    "--from",
                    str(zip_path),
                    "--root",
                    str(host_root),
                    "--yes",
                    "--format",
                    "text",
                ],
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
            )
            assert installed.returncode == 0, installed.stdout + installed.stderr
            dest = host_root / name
            snapshot = _dest_files(dest)
            assert snapshot
            assert any(path.endswith("SKILL.md") for path in snapshot)
            status = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentc",
                    "status",
                    "--kind",
                    host,
                    "--name",
                    name,
                    "--root",
                    str(host_root),
                    "--format",
                    "text",
                ],
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
            )
            assert status.returncode == 0, status.stdout + status.stderr
            assert "plugin: {0}".format(name) in status.stdout
            assert "state: registered" in status.stdout
            state_code, state_out, state_err = _call_main(
                "status", "--kind", host, "--name", name,
                "--root", str(host_root), "--format", "protocol",
            )
            assert state_code == 0, state_out + state_err
            assert any(
                item["code"] == "AGC-P5-0019"
                for item in _parse_protocol(state_out)["diagnostics"]
            )
            removed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentc",
                    "uninstall",
                    host,
                    "--name",
                    name,
                    "--root",
                    str(host_root),
                    "--yes",
                ],
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
            )
            assert removed.returncode == 0, removed.stdout + removed.stderr
            assert not dest.exists()
            restored = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentc",
                    "restore",
                    host,
                    "--name",
                    name,
                    "--root",
                    str(host_root),
                    "--yes",
                ],
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
            )
            assert restored.returncode == 0, restored.stdout + restored.stderr
            assert _dest_files(dest) == snapshot
            seen = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentc",
                    "status",
                    "--kind",
                    host,
                    "--name",
                    name,
                    "--root",
                    str(host_root),
                    "--format",
                    "text",
                ],
                capture_output=True,
                text=True,
                env=env,
                cwd=ROOT,
            )
            assert seen.returncode == 0, seen.stdout + seen.stderr
            assert "state: registered" in seen.stdout
            assert keep.read_text(encoding="utf-8") == "keep-{0}\n".format(host)
            assert notes.read_text(encoding="utf-8") == "plain\n"


def _assert_path_secret_hidden(source: Path, output: Path, token: str) -> None:
    code, stdout, stderr = _call_main(
        "workspace",
        "build",
        str(source),
        "--targets",
        "cursor",
        "--output",
        str(output),
        "--format",
        "protocol",
    )
    assert code != 0, stdout + stderr
    assert token not in stdout
    assert token not in stderr
    payload = _parse_protocol(stdout)
    assert payload["artifacts"] == []
    assert token not in json.dumps(payload)
    assert any(item["code"] == "AGC-P7-0004" for item in payload["diagnostics"])
    assert any(
        (item["hint"] or "").startswith("locator=") for item in payload["diagnostics"]
    )
    report_path, _document = _report_from_hint(payload)
    report_text = report_path.read_text(encoding="utf-8")
    assert token not in report_text
    assert "<redacted-secret>" in report_text
    assert list(output.rglob("*.zip")) == []


def test_secret_filename_is_redacted() -> None:
    token = "ghp_" + ("C" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        (source / token).write_text("plain\n", encoding="utf-8", newline="\n")
        _assert_path_secret_hidden(source, base / "out", token)


def test_secret_directory_name_is_redacted() -> None:
    token = "ghp_" + ("D" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        nested = source / token
        nested.mkdir()
        (nested / "note.txt").write_text("plain\n", encoding="utf-8", newline="\n")
        _assert_path_secret_hidden(source, base / "out", token)


def test_secret_source_root_name_is_redacted() -> None:
    token = "ghp_" + ("E" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / token
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        _assert_path_secret_hidden(source, base / "out", token)


def test_placeholder_secret_filename_is_redacted() -> None:
    token = "ghp_" + ("G" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        (source / ("${" + token + "}")).write_text(
            "plain\n", encoding="utf-8", newline="\n"
        )
        _assert_path_secret_hidden(source, base / "out", token)


def test_placeholder_secret_directory_name_is_redacted() -> None:
    token = "ghp_" + ("H" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        nested = source / ("${" + token + "}")
        nested.mkdir()
        (nested / "note.txt").write_text("plain\n", encoding="utf-8", newline="\n")
        _assert_path_secret_hidden(source, base / "out", token)


def test_placeholder_secret_source_root_name_is_redacted() -> None:
    token = "ghp_" + ("I" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / ("${" + token + "}")
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        _assert_path_secret_hidden(source, base / "out", token)


def test_secret_output_path_is_rejected() -> None:
    token = "ghp_" + ("J" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        output = base / token
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        code, stdout, stderr = _call_main(
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert code != 0, stdout + stderr
        assert token not in stdout
        assert token not in stderr
        payload = _parse_protocol(stdout)
        assert payload["artifacts"] == []
        assert token not in json.dumps(payload)
        assert any(item["code"] == "AGC-P7-0004" for item in payload["diagnostics"])
        report_path, document = _report_from_hint(payload)
        report_text = report_path.read_text(encoding="utf-8")
        assert token not in report_text
        assert "<redacted-secret>" in report_text
        assert document["archives"] == []
        assert not output.exists()


def test_secret_symlink_target_is_redacted() -> None:
    token = "ghp_" + ("F" * 36)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        os.symlink(
            "{0}/../skills/demo/SKILL.md".format(token),
            source / "alias",
        )
        _assert_path_secret_hidden(source, base / "out", token)


def _agentc_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def _agentc(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )


def _cursor_zip(output: Path) -> Path:
    report = json.loads((output / REPORT_NAME).read_text(encoding="utf-8"))
    matches = [item for item in report["archives"] if item["target"] == "cursor"]
    assert len(matches) == 1, report["archives"]
    return Path(matches[0]["path"])


def _installed_skill(dest: Path) -> str:
    matches = list(dest.rglob("SKILL.md"))
    assert len(matches) == 1, matches
    return matches[0].read_text(encoding="utf-8")


def test_rebuild_then_sync_updates_same_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        source = base / "plugin"
        output = base / "out"
        host_root = base / "install"
        host_root.mkdir()
        env = _agentc_env(home)
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        built = _agentc(
            env,
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert built.returncode == 0, built.stdout + built.stderr
        first_zip = _cursor_zip(output)
        first_bytes = first_zip.read_bytes()
        name = _plugin_name(first_zip, "cursor")
        installed = _agentc(
            env,
            "install",
            "cursor",
            "--from",
            str(first_zip),
            "--root",
            str(host_root),
            "--yes",
            "--format",
            "protocol",
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        dest = host_root / name
        assert VISIBLE_SENTENCE in _installed_skill(dest)
        skill = source / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            _skill_text(SECOND_SENTENCE + "\n"), encoding="utf-8", newline="\n"
        )
        rebuilt = _agentc(
            env,
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
        second_zip = _cursor_zip(output)
        assert second_zip.read_bytes() != first_bytes
        conflict = _agentc(
            env,
            "install",
            "cursor",
            "--from",
            str(second_zip),
            "--root",
            str(host_root),
            "--yes",
            "--format",
            "protocol",
        )
        assert conflict.returncode != 0, conflict.stdout + conflict.stderr
        assert "AGC-P5-0003" in conflict.stdout
        assert VISIBLE_SENTENCE in _installed_skill(dest)
        assert SECOND_SENTENCE not in _installed_skill(dest)
        status = _agentc(
            env,
            "status",
            "--kind",
            "cursor",
            "--name",
            name,
            "--root",
            str(host_root),
            "--format",
            "text",
        )
        assert status.returncode == 0, status.stdout + status.stderr
        assert "state: registered" in status.stdout
        assert "AGC-P5-0008" not in status.stdout
        synced = _agentc(
            env,
            "sync",
            "cursor",
            "--from",
            str(second_zip),
            "--root",
            str(host_root),
            "--yes",
            "--format",
            "protocol",
        )
        assert synced.returncode == 0, synced.stdout + synced.stderr
        assert SECOND_SENTENCE in _installed_skill(dest)
        assert VISIBLE_SENTENCE not in _installed_skill(dest)


def test_failed_install_keeps_synced_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        source = base / "plugin"
        output = base / "out"
        host_root = base / "install"
        host_root.mkdir()
        env = _agentc_env(home)
        _write_clean_cursor(source, body=VISIBLE_SENTENCE + "\n")
        built = _agentc(
            env,
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert built.returncode == 0, built.stdout + built.stderr
        first_zip = _cursor_zip(output)
        name = _plugin_name(first_zip, "cursor")
        installed = _agentc(
            env,
            "install",
            "cursor",
            "--from",
            str(first_zip),
            "--root",
            str(host_root),
            "--yes",
            "--format",
            "protocol",
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        skill = source / "skills" / "demo" / "SKILL.md"
        skill.write_text(
            _skill_text(SECOND_SENTENCE + "\n"), encoding="utf-8", newline="\n"
        )
        rebuilt = _agentc(
            env,
            "workspace",
            "build",
            str(source),
            "--targets",
            "cursor",
            "--output",
            str(output),
            "--format",
            "protocol",
        )
        assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
        second_zip = _cursor_zip(output)
        synced = _agentc(
            env,
            "sync",
            "cursor",
            "--from",
            str(second_zip),
            "--root",
            str(host_root),
            "--yes",
            "--format",
            "protocol",
        )
        assert synced.returncode == 0, synced.stdout + synced.stderr
        dest = host_root / name
        current = _dest_files(dest)
        assert any(SECOND_SENTENCE.encode("utf-8") in blob for blob in current.values())
        missing = _agentc(
            env,
            "install",
            "cursor",
            "--from",
            str(base / "missing.zip"),
            "--root",
            str(host_root),
            "--yes",
            "--format",
            "protocol",
        )
        assert missing.returncode != 0, missing.stdout + missing.stderr
        assert _dest_files(dest) == current
        status = _agentc(
            env,
            "status",
            "--kind",
            "cursor",
            "--name",
            name,
            "--root",
            str(host_root),
            "--format",
            "text",
        )
        assert status.returncode == 0, status.stdout + status.stderr
        assert "state: registered" in status.stdout


def test_existing_commands_keep_their_shape() -> None:
    code, stdout, stderr = _call_main("build", "--help")
    assert code == 0, stdout + stderr
    assert "--policy" in stdout
    assert "--source-kind" not in stdout

    package_code, package_out, package_err = _call_main("package", "--help")
    assert package_code == 0, package_out + package_err
    assert "--policy" in package_out

    install_code, install_out, install_err = _call_main("install", "--help")
    assert install_code == 0, install_out + install_err
    assert "cursor" in install_out
    assert "codex" in install_out
    assert "claude-code" in install_out


def main() -> int:
    tests = [
        test_argument_errors_are_nonzero,
        test_success_protocol_report_and_no_install,
        test_failure_report_and_empty_artifacts,
        test_rebuild_changes_zip_and_hides_old_round,
        test_failed_round_zip_survives_next_success,
        test_publish_keeps_preexisting_swap_backup,
        test_source_change_during_build_fails,
        test_one_target_failure_publishes_no_new_zip,
        test_publish_failure_leaves_no_partial_zip,
        test_report_coverage_gap_fails,
        test_path_escape_and_output_overlap,
        test_install_status_restore_keeps_unmanaged_files,
        test_secret_filename_is_redacted,
        test_placeholder_secret_filename_is_redacted,
        test_placeholder_secret_directory_name_is_redacted,
        test_placeholder_secret_source_root_name_is_redacted,
        test_secret_output_path_is_rejected,
        test_secret_directory_name_is_redacted,
        test_secret_source_root_name_is_redacted,
        test_secret_symlink_target_is_redacted,
        test_rebuild_then_sync_updates_same_root,
        test_failed_install_keeps_synced_tree,
        test_existing_commands_keep_their_shape,
    ]
    for test in tests:
        try:
            test()
        except Exception:
            traceback.print_exc()
            print("FAIL", test.__name__)
            return 1
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
