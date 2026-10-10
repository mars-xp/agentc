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


def test_p4_merge_codes_exist():
    from agentc.model.diagnostics import CODE_KIND_UNKNOWN, CODE_MERGE_CONFLICT, CODE_MERGE_SOURCES

    assert CODE_MERGE_SOURCES == "AGC-P4-0009"
    assert CODE_MERGE_CONFLICT == "AGC-P4-0010"
    assert CODE_KIND_UNKNOWN == "AGC-P4-0008"


def test_compose_identical_skills_keeps_one():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem, ImportPlan, UnresolvedItem

    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a" / "skills" / "hello"
        b = Path(tmp) / "b" / "skills" / "hello"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        body = "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n"
        (a / "SKILL.md").write_text(body, encoding="utf-8", newline="\n")
        (b / "SKILL.md").write_text(body, encoding="utf-8", newline="\n")
        item_a = ConvertItem("skill", "skills/hello", "skills/hello", a)
        item_b = ConvertItem("skill", "skills/hello", "skills/hello", b)
        plan_a = ImportPlan(
            source_root=Path(tmp) / "a",
            project_id="alpha",
            project_name="alpha",
            version="0.0.1",
            license_id="Apache-2.0",
            enabled_host="cursor",
            enabled_hosts=("cursor",),
            convertibles=(item_a,),
            unresolved=(UnresolvedItem("hooks/hooks.json", "hook"),),
            redactions=(),
        )
        plan_b = ImportPlan(
            source_root=Path(tmp) / "b",
            project_id="beta",
            project_name="beta",
            version="9.9.9",
            license_id="MIT",
            enabled_host="codex",
            enabled_hosts=("codex",),
            convertibles=(item_b,),
            unresolved=(UnresolvedItem("mystery.txt", "unknown"),),
            redactions=(),
        )
        merged, diags = compose_plans((("cursor", plan_a), ("codex", plan_b)))
        assert merged is not None, diags
        assert merged.project_id == "alpha"
        assert merged.version == "0.0.1"
        assert merged.enabled_hosts == ("cursor", "codex")
        assert len(merged.convertibles) == 1
        assert merged.convertibles[0].source_resolved == a
        assert [u.source_rel for u in merged.unresolved] == [
            "hooks/hooks.json",
            "mystery.txt",
        ]
        assert not any(d.level == "error" for d in diags)
        assert merged.overlay_ops == ()


def _plan(tmp: Path, host: str, item: ConvertItem, unresolved=()) -> ImportPlan:
    from agentc.importers.pipeline import ImportPlan

    return ImportPlan(
        source_root=tmp,
        project_id="x",
        project_name="x",
        version="0.0.1",
        license_id="Apache-2.0",
        enabled_host=host,
        enabled_hosts=(host,),
        convertibles=(item,),
        unresolved=tuple(unresolved),
        redactions=(),
    )


def test_compose_cursor_only_skill_goes_to_overlay_ops():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hello = root / "c" / "skills" / "hello"
        extra = root / "x" / "skills" / "extra"
        hello.mkdir(parents=True)
        extra.mkdir(parents=True)
        (hello / "SKILL.md").write_text("# hello\n", encoding="utf-8", newline="\n")
        (hello / "scripts").mkdir()
        (hello / "scripts" / "foo.py").write_text("print(1)\n", encoding="utf-8", newline="\n")
        (extra / "SKILL.md").write_text("# extra\n", encoding="utf-8", newline="\n")
        item_h = ConvertItem("skill", "skills/hello", "skills/hello", hello)
        item_e = ConvertItem("skill", "skills/extra", "skills/extra", extra)
        merged, diags = compose_plans(
            (
                ("cursor", _plan(root / "c", "cursor", item_h)),
                ("codex", _plan(root / "x", "codex", item_e)),
            )
        )
        assert merged is not None, diags
        assert merged.convertibles == ()
        assert merged.project_id == "x"
        assert merged.enabled_hosts == ("cursor", "codex")
        tos = [(op.host, op.from_rel, op.to_rel) for op in merged.overlay_ops]
        assert tos == [
            ("codex", "skills/extra/SKILL.md", "skills/extra/SKILL.md"),
            ("cursor", "skills/hello/SKILL.md", "skills/hello/SKILL.md"),
            ("cursor", "skills/hello/scripts/foo.py", "skills/hello/scripts/foo.py"),
        ]


def test_compose_cursor_workspace_counts_as_cursor_host():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        solo = root / "ws" / "skills" / "solo"
        extra = root / "x" / "skills" / "extra"
        solo.mkdir(parents=True)
        extra.mkdir(parents=True)
        (solo / "SKILL.md").write_text("# solo\n", encoding="utf-8", newline="\n")
        (extra / "SKILL.md").write_text("# extra\n", encoding="utf-8", newline="\n")
        item_s = ConvertItem("skill", "skills/solo", "skills/solo", solo)
        item_e = ConvertItem("skill", "skills/extra", "skills/extra", extra)
        merged, diags = compose_plans(
            (
                ("cursor-workspace", _plan(root / "ws", "cursor", item_s)),
                ("codex", _plan(root / "x", "codex", item_e)),
            )
        )
        assert merged is not None, diags
        hosts = {op.host for op in merged.overlay_ops}
        assert "cursor" in hosts
        assert all(op.from_rel.startswith("skills/solo/") for op in merged.overlay_ops if op.host == "cursor")


def test_compose_symlink_only_cursor_skill_stays_in_convertibles():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        linkonly = root / "c" / "skills" / "linkonly"
        extra = root / "x" / "skills" / "extra"
        real = root / "real" / "SKILL.md"
        real.parent.mkdir(parents=True)
        real.write_text("# real\n", encoding="utf-8", newline="\n")
        linkonly.mkdir(parents=True)
        (linkonly / "SKILL.md").symlink_to(real)
        extra.mkdir(parents=True)
        (extra / "SKILL.md").write_text("# extra\n", encoding="utf-8", newline="\n")
        item_link = ConvertItem("skill", "skills/linkonly", "skills/linkonly", linkonly)
        item_e = ConvertItem("skill", "skills/extra", "skills/extra", extra)
        merged, diags = compose_plans(
            (
                ("cursor", _plan(root / "c", "cursor", item_link)),
                ("codex", _plan(root / "x", "codex", item_e)),
            )
        )
        assert merged is not None, diags
        assert any(c.dest_rel == "skills/linkonly" for c in merged.convertibles)
        assert not any("linkonly" in op.to_rel for op in merged.overlay_ops)
        assert any(
            op.host == "codex" and op.to_rel == "skills/extra/SKILL.md"
            for op in merged.overlay_ops
        )


def test_compose_hook_stays_unresolved_not_overlay():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem, UnresolvedItem

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "a" / "skills" / "hello"
        b = root / "b" / "skills" / "hello"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        body = "# hello\n"
        (a / "SKILL.md").write_text(body, encoding="utf-8", newline="\n")
        (b / "SKILL.md").write_text(body, encoding="utf-8", newline="\n")
        item_a = ConvertItem("skill", "skills/hello", "skills/hello", a)
        item_b = ConvertItem("skill", "skills/hello", "skills/hello", b)
        merged, diags = compose_plans(
            (
                (
                    "cursor",
                    _plan(root / "a", "cursor", item_a, (UnresolvedItem("hooks/hooks.json", "hook"),)),
                ),
                ("codex", _plan(root / "b", "codex", item_b)),
            )
        )
        assert merged is not None, diags
        assert merged.overlay_ops == ()
        assert any(u.reason == "hook" for u in merged.unresolved)


def test_compose_different_bytes_is_0010():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem, ImportPlan
    from agentc.model.diagnostics import CODE_MERGE_CONFLICT

    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.md"
        b = Path(tmp) / "b.md"
        a.write_text("# one\n", encoding="utf-8")
        b.write_text("# two\n", encoding="utf-8")
        item_a = ConvertItem("rule", "rules/house.md", "extensions/rules/house.md", a)
        item_b = ConvertItem("rule", "rules/house.md", "extensions/rules/house.md", b)
        base = dict(
            source_root=Path(tmp),
            project_id="x",
            project_name="x",
            version="0.0.1",
            license_id="Apache-2.0",
            enabled_host="cursor",
            enabled_hosts=("cursor",),
            unresolved=(),
            redactions=(),
        )
        plan_a = ImportPlan(convertibles=(item_a,), **base)
        plan_b = ImportPlan(convertibles=(item_b,), **base)
        merged, diags = compose_plans((("cursor", plan_a), ("codex", plan_b)))
        assert merged is None
        assert any(d.code == CODE_MERGE_CONFLICT and d.path == "extensions/rules/house.md" for d in diags)


def test_compose_kind_mismatch_is_0010():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem, ImportPlan
    from agentc.model.diagnostics import CODE_MERGE_CONFLICT

    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.md"
        b = Path(tmp) / "b.md"
        a.write_text("# same\n", encoding="utf-8")
        b.write_text("# same\n", encoding="utf-8")
        item_a = ConvertItem("rule", "x.md", "shared.md", a)
        item_b = ConvertItem("command", "y.md", "shared.md", b)
        base = dict(
            source_root=Path(tmp),
            project_id="x",
            project_name="x",
            version="0.0.1",
            license_id="Apache-2.0",
            enabled_host="cursor",
            enabled_hosts=("cursor",),
            unresolved=(),
            redactions=(),
        )
        merged, diags = compose_plans(
            (
                ("cursor", ImportPlan(convertibles=(item_a,), **base)),
                ("codex", ImportPlan(convertibles=(item_b,), **base)),
            )
        )
        assert merged is None
        assert any(d.code == CODE_MERGE_CONFLICT and d.path == "shared.md" for d in diags)


def test_redacted_home_paths_count_as_same():
    from agentc.importers.merge import compose_plans
    from agentc.importers.pipeline import ConvertItem, ImportPlan

    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.md"
        b = Path(tmp) / "b.md"
        a.write_text("path /Users/alice/proj\n", encoding="utf-8")
        b.write_text("path /Users/bob/proj\n", encoding="utf-8")
        item_a = ConvertItem("rule", "r.md", "extensions/rules/r.md", a)
        item_b = ConvertItem("rule", "r.md", "extensions/rules/r.md", b)
        base = dict(
            source_root=Path(tmp),
            project_id="x",
            project_name="x",
            version="0.0.1",
            license_id="Apache-2.0",
            enabled_host="cursor",
            enabled_hosts=("cursor",),
            unresolved=(),
            redactions=(),
        )
        merged, diags = compose_plans(
            (
                ("cursor", ImportPlan(convertibles=(item_a,), **base)),
                ("codex", ImportPlan(convertibles=(item_b,), **base)),
            )
        )
        assert merged is not None, diags
        assert len(merged.convertibles) == 1


def _env(src_only: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    if src_only:
        env["PYTHONPATH"] = str(ROOT / "src")
    else:
        env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def _run_agentc(*args: str, src_only: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(src_only=src_only),
        cwd=ROOT,
    )


def _write_cursor_plugin(root: Path, *, skill_body: str, name: str = "demo-plugin") -> None:
    (root / ".cursor-plugin").mkdir(parents=True)
    (root / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.0.1", "license": "Apache-2.0"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    skill = root / "skills" / "hello"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(skill_body, encoding="utf-8", newline="\n")


def _write_codex_plugin(root: Path, *, skill_body: str, name: str = "demo-codex") -> None:
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.0.1"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    skill = root / "skills" / "hello"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(skill_body, encoding="utf-8", newline="\n")


def _write_host_plugin(
    root: Path, *, kind: str, skill_name: str, skill_body: str, name: str
) -> None:
    if kind == "cursor":
        manifest_dir = ".cursor-plugin"
        extra = {"license": "Apache-2.0"}
    else:
        manifest_dir, extra = ".codex-plugin", {}
    (root / manifest_dir).mkdir(parents=True)
    payload = {"name": name, "version": "0.0.1", **extra}
    (root / manifest_dir / "plugin.json").write_text(
        json.dumps(payload) + "\n", encoding="utf-8", newline="\n"
    )
    skill = root / "skills" / skill_name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(skill_body, encoding="utf-8", newline="\n")


def test_merge_too_few_sources_is_0009():
    from agentc.model.diagnostics import CODE_MERGE_SOURCES

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "plugin"
        out = Path(tmp) / "out"
        src.mkdir()
        _write_cursor_plugin(src, skill_body="# hello\n")
        result = _run_agentc(
            "import", "merge", "--cursor", str(src), "--output", str(out),
            "--format", "json",
        )
        assert result.returncode == 1
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_MERGE_SOURCES for d in payload)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_merge_without_yes_does_not_create_out():
    from agentc.model.diagnostics import CODE_UNCONFIRMED

    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        body = "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n"
        _write_cursor_plugin(cursor, skill_body=body)
        _write_codex_plugin(codex, skill_body=body)
        before_c = {p.as_posix(): p.read_bytes() for p in cursor.rglob("*") if p.is_file()}
        before_x = {p.as_posix(): p.read_bytes() for p in codex.rglob("*") if p.is_file()}
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--format", "json",
        )
        assert result.returncode == 1
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_UNCONFIRMED for d in payload)
        after_c = {p.as_posix(): p.read_bytes() for p in cursor.rglob("*") if p.is_file()}
        after_x = {p.as_posix(): p.read_bytes() for p in codex.rglob("*") if p.is_file()}
        assert after_c == before_c
        assert after_x == before_x


def test_merge_conflict_does_not_write_even_with_yes():
    from agentc.model.diagnostics import CODE_MERGE_CONFLICT, CODE_UNCONFIRMED

    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        _write_cursor_plugin(cursor, skill_body="# left\n")
        _write_codex_plugin(codex, skill_body="# right\n")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 1
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_MERGE_CONFLICT for d in payload)
        assert not any(d["code"] == CODE_UNCONFIRMED for d in payload)


def test_merge_kind_mismatch_does_not_write():
    from agentc.model.diagnostics import CODE_KIND_MISMATCH

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        cursor = Path(tmp) / "cursor"
        out = Path(tmp) / "out"
        proj.mkdir()
        (proj / ".cursor").mkdir()
        (proj / ".cursor" / "skills").mkdir()
        (proj / ".cursor" / "skills" / "hello").mkdir()
        (proj / ".cursor" / "skills" / "hello" / "SKILL.md").write_text(
            "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n",
            encoding="utf-8",
            newline="\n",
        )
        cursor.mkdir()
        _write_cursor_plugin(cursor, skill_body="# hello\n")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(proj),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 1
        assert not out.exists()
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_KIND_MISMATCH for d in payload)


def test_merge_help_src_only():
    result = _run_agentc("--help", src_only=True)
    assert result.returncode == 0, result.stderr
    assert "import" in result.stdout


def test_merge_subcommand_help():
    result = _run_agentc("import", "--help", src_only=True)
    assert result.returncode == 0, result.stderr
    assert "merge" in result.stdout


def test_merge_protocol_unconfirmed_envelope():
    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        body = "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n"
        _write_cursor_plugin(cursor, skill_body=body)
        _write_codex_plugin(codex, skill_body=body)
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--format", "protocol",
        )
        assert result.returncode == 1
        assert not out.exists()
        obj = json.loads(result.stdout)
        assert set(obj) == {
            "protocolVersion",
            "status",
            "summary",
            "diagnostics",
            "artifacts",
            "confirmation",
        }
        assert obj["protocolVersion"] == "agentc-protocol/v1"
        assert obj["status"] == "unconfirmed"
        assert obj["confirmation"]["required"] is True
        assert obj["confirmation"]["flag"] == "--yes"
        assert isinstance(obj["diagnostics"], list)
        for item in obj["diagnostics"]:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_merge_yes_writes_union_and_enables_hosts():
    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        hello = "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n"
        extra = "---\nname: extra\ndescription: extra skill\n---\n\n# extra\n"
        _write_cursor_plugin(cursor, skill_body=hello, name="from-cursor")
        _write_codex_plugin(codex, skill_body=hello, name="from-codex")
        extra_dir = codex / "skills" / "extra"
        extra_dir.mkdir()
        (extra_dir / "SKILL.md").write_text(extra, encoding="utf-8", newline="\n")
        (cursor / "hooks").mkdir()
        (cursor / "hooks" / "hooks.json").write_text("{}\n", encoding="utf-8")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        yaml_text = (out / "agent-project.yaml").read_text(encoding="utf-8")
        assert "id: from-cursor" in yaml_text
        assert "cursor:" in yaml_text
        assert "codex:" in yaml_text
        assert "enabled: true" in yaml_text
        import yaml
        data = yaml.safe_load(yaml_text)
        assert data["targets"]["cursor"]["enabled"] is True
        assert data["targets"]["codex"]["enabled"] is True
        assert data["targets"]["claude-code"]["enabled"] is False
        assert (out / "skills" / "hello" / "SKILL.md").is_file()
        assert not (out / "skills" / "extra").exists()
        assert (out / "overlays" / "codex" / "skills" / "extra" / "SKILL.md").is_file()
        assert (out / "import-report.md").is_file()
        payload = json.loads(result.stdout)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}
        assert any(d["code"] == "AGC-P4-0006" and "hooks" in (d["path"] or "") for d in payload)
        assert "hooks.json" not in yaml_text


def test_merge_without_yes_prints_overlay_and_does_not_create_out():
    from agentc.model.diagnostics import CODE_IMPORT_NOTE, CODE_UNCONFIRMED

    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        _write_host_plugin(
            cursor, kind="cursor", skill_name="hello",
            skill_body="---\nname: hello\ndescription: hello skill\n---\n\n# hello\n",
            name="from-cursor",
        )
        _write_host_plugin(
            codex, kind="codex", skill_name="extra",
            skill_body="---\nname: extra\ndescription: extra skill\n---\n\n# extra\n",
            name="from-codex",
        )
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--format", "text",
        )
        assert result.returncode == 1
        assert not out.exists()
        assert "overlay:" in result.stdout
        assert "cursor add-file skills/hello/SKILL.md -> skills/hello/SKILL.md" in result.stdout
        json_result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--format", "json",
        )
        payload = json.loads(json_result.stdout)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}
        assert any(d["code"] == CODE_UNCONFIRMED for d in payload)
        assert any(
            d["code"] == CODE_IMPORT_NOTE
            and d["level"] == "notice"
            and d["path"] == "overlays/cursor/skills/hello/SKILL.md"
            for d in payload
        )


def test_merge_yes_cursor_only_skill_writes_overlay_not_generic_tree():
    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        hello = "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n"
        extra = "---\nname: extra\ndescription: extra skill\n---\n\n# extra\n"
        _write_host_plugin(cursor, kind="cursor", skill_name="hello", skill_body=hello, name="from-cursor")
        scripts = cursor / "skills" / "hello" / "scripts"
        scripts.mkdir()
        (scripts / "foo.py").write_text("print(1)\n", encoding="utf-8", newline="\n")
        _write_host_plugin(codex, kind="codex", skill_name="extra", skill_body=extra, name="from-codex")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (out / "skills" / "hello").exists()
        assert not (out / "skills" / "extra").exists()
        overlay = out / "overlays" / "cursor" / "import-merge.json"
        assert overlay.is_file()
        ops = json.loads(overlay.read_text(encoding="utf-8"))
        assert ops == [
            {"op": "add-file", "from": "skills/hello/SKILL.md", "to": "skills/hello/SKILL.md"},
            {"op": "add-file", "from": "skills/hello/scripts/foo.py", "to": "skills/hello/scripts/foo.py"},
        ]
        assert (out / "overlays" / "cursor" / "skills" / "hello" / "SKILL.md").is_file()
        assert (out / "overlays" / "cursor" / "skills" / "hello" / "scripts" / "foo.py").is_file()
        assert (out / "overlays" / "codex" / "import-merge.json").is_file()
        import yaml
        data = yaml.safe_load((out / "agent-project.yaml").read_text(encoding="utf-8"))
        assert data["contents"]["skills"] == []
        assert data["targets"]["cursor"]["enabled"] is True
        assert data["targets"]["codex"]["enabled"] is True
        assert data["targets"]["claude-code"]["enabled"] is False
        from agentc.validate.engine import validate_project
        issues = validate_project(out)
        assert not any(i.level == "error" for i in issues), issues
        payload = json.loads(result.stdout)
        for item in payload:
            assert set(item) == {"code", "level", "message", "path", "hint"}


def test_merge_yes_workspace_only_skill_uses_overlays_cursor():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        skill = ws / ".cursor" / "skills" / "solo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: solo\ndescription: solo skill\n---\n\n# solo\n",
            encoding="utf-8",
            newline="\n",
        )
        codex.mkdir()
        extra = "---\nname: extra\ndescription: extra skill\n---\n\n# extra\n"
        _write_host_plugin(codex, kind="codex", skill_name="extra", skill_body=extra, name="from-codex")
        result = _run_agentc(
            "import", "merge",
            "--cursor-workspace", str(ws), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (out / "skills" / "solo").exists()
        assert (out / "overlays" / "cursor" / "import-merge.json").is_file()
        assert (out / "overlays" / "cursor" / "skills" / "solo" / "SKILL.md").is_file()


def test_merge_yes_hook_only_on_one_host_is_unresolved_not_overlay():
    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        body = "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n"
        _write_cursor_plugin(cursor, skill_body=body)
        _write_codex_plugin(codex, skill_body=body)
        (cursor / "hooks").mkdir()
        (cursor / "hooks" / "hooks.json").write_text("{}\n", encoding="utf-8")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (out / "skills" / "hello" / "SKILL.md").is_file()
        assert (out / "overlays").is_dir()
        assert list(out.glob("overlays/*/import-merge.json")) == []
        payload = json.loads(result.stdout)
        assert any(
            d["code"] == "AGC-P4-0006" and d["level"] == "warning" and "hooks" in (d["path"] or "")
            for d in payload
        )


def test_merge_yes_cursor_only_rule_writes_overlay_not_generic_tree():
    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        (cursor / ".cursor-plugin").mkdir()
        (cursor / ".cursor-plugin" / "plugin.json").write_text(
            json.dumps({"name": "from-cursor", "version": "0.0.1", "license": "Apache-2.0"}) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (cursor / "rules").mkdir()
        (cursor / "rules" / "house.md").write_text(
            "---\ndescription: house\nalwaysApply: false\n---\n\n# house\n",
            encoding="utf-8",
            newline="\n",
        )
        extra = "---\nname: extra\ndescription: extra skill\n---\n\n# extra\n"
        _write_host_plugin(codex, kind="codex", skill_name="extra", skill_body=extra, name="from-codex")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (out / "extensions" / "rules" / "house.md").exists()
        overlay_rule = out / "overlays" / "cursor" / "extensions" / "rules" / "house.md"
        assert overlay_rule.is_file()
        overlay = out / "overlays" / "cursor" / "import-merge.json"
        assert overlay.is_file()
        ops = json.loads(overlay.read_text(encoding="utf-8"))
        assert ops == [
            {
                "op": "add-file",
                "from": "extensions/rules/house.md",
                "to": "extensions/rules/house.md",
            }
        ]
        import yaml
        data = yaml.safe_load((out / "agent-project.yaml").read_text(encoding="utf-8"))
        assert data["contents"]["rules"] == []
        from agentc.validate.engine import validate_project
        issues = validate_project(out)
        assert not any(i.level == "error" for i in issues), issues


def test_merge_conflict_does_not_write_overlay():
    from agentc.model.diagnostics import CODE_MERGE_CONFLICT

    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        _write_cursor_plugin(cursor, skill_body="# left\n")
        _write_codex_plugin(codex, skill_body="# right\n")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_MERGE_CONFLICT for d in payload)
        assert not out.exists()


def test_merge_nonempty_out_is_0003():
    from agentc.model.diagnostics import CODE_OUTPUT_NONEMPTY

    with tempfile.TemporaryDirectory() as tmp:
        cursor = Path(tmp) / "cursor"
        codex = Path(tmp) / "codex"
        out = Path(tmp) / "out"
        cursor.mkdir()
        codex.mkdir()
        body = "---\nname: hello\ndescription: hello skill\n---\n\n# hello\n"
        _write_cursor_plugin(cursor, skill_body=body)
        _write_codex_plugin(codex, skill_body=body)
        out.mkdir()
        (out / "stale.txt").write_text("nope\n", encoding="utf-8")
        result = _run_agentc(
            "import", "merge",
            "--cursor", str(cursor), "--codex", str(codex),
            "--output", str(out), "--yes", "--format", "json",
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert any(d["code"] == CODE_OUTPUT_NONEMPTY for d in payload)
        assert (out / "stale.txt").is_file()
        assert not (out / "agent-project.yaml").exists()


def main() -> int:
    tests = [
        test_p4_merge_codes_exist,
        test_compose_identical_skills_keeps_one,
        test_compose_cursor_only_skill_goes_to_overlay_ops,
        test_compose_cursor_workspace_counts_as_cursor_host,
        test_compose_symlink_only_cursor_skill_stays_in_convertibles,
        test_compose_hook_stays_unresolved_not_overlay,
        test_compose_different_bytes_is_0010,
        test_compose_kind_mismatch_is_0010,
        test_redacted_home_paths_count_as_same,
        test_merge_too_few_sources_is_0009,
        test_merge_without_yes_does_not_create_out,
        test_merge_conflict_does_not_write_even_with_yes,
        test_merge_kind_mismatch_does_not_write,
        test_merge_help_src_only,
        test_merge_subcommand_help,
        test_merge_protocol_unconfirmed_envelope,
        test_merge_yes_writes_union_and_enables_hosts,
        test_merge_without_yes_prints_overlay_and_does_not_create_out,
        test_merge_yes_cursor_only_skill_writes_overlay_not_generic_tree,
        test_merge_yes_workspace_only_skill_uses_overlays_cursor,
        test_merge_yes_hook_only_on_one_host_is_unresolved_not_overlay,
        test_merge_yes_cursor_only_rule_writes_overlay_not_generic_tree,
        test_merge_conflict_does_not_write_overlay,
        test_merge_nonempty_out_is_0003,
    ]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
