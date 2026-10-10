#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

SKILL_MD = "---\nname: demo\ndescription: demo skill\n---\n\n# demo\n"
UNKNOWN_TEXT = b"not a skill\n"
BLOB = b"\x00\x01\x02\xff"
HIDDEN_TEXT = b"hidden\n"
CACHE_TEXT = b"cache\n"

BLIND_PATHS = (
    "notes/unknown.txt",
    "resources/blob.bin",
    ".hidden",
    ".hidden/keep.txt",
    "empty-dir",
    "build",
    "build/cache.txt",
)

SOURCE_CASES = (
    ("cursor", "cursor", "skills/demo/SKILL.md"),
    ("cursor-workspace", "cursor", ".cursor/skills/demo/SKILL.md"),
    ("codex", "codex", "skills/demo/SKILL.md"),
    ("codex-workspace", "codex", ".agents/skills/demo/SKILL.md"),
    ("claude-code", "claude-code", "skills/demo/SKILL.md"),
    ("claude-code-workspace", "claude-code", ".claude/skills/demo/SKILL.md"),
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dir_digest(records: list[tuple[str, str, str]]) -> str:
    payload = bytearray(b"dir\0")
    for name, kind, digest in records:
        payload += name.encode("utf-8")
        payload += b"\0"
        payload += kind.encode("ascii")
        payload += b"\0"
        payload += digest.encode("ascii")
        payload += b"\n"
    return _sha(bytes(payload))


def _fingerprint(entries: list[tuple[str, str, int, str]]) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"exclusions\0.git/\n")
    for path, kind, size, digest in entries:
        hasher.update(f"{path}\0{kind}\0{size}\0{digest}\n".encode("utf-8"))
    return hasher.hexdigest()


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
            rel = name if not rel_dir else f"{rel_dir}/{name}"
            if path.is_symlink():
                rows.append((rel, "symlink", os.readlink(path)))
            elif path.is_dir():
                rows.append((rel, "dir", ""))
            else:
                rows.append((rel, "file", _sha(path.read_bytes())))
    rows.sort()
    return tuple(rows)


def _assert_error(diag, code: str, path: str) -> None:
    assert diag.code == code
    assert diag.level == "error"
    assert diag.path == path
    assert isinstance(diag.message, str) and diag.message
    assert isinstance(diag.hint, str)


def _write_skill(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL_MD, encoding="utf-8", newline="\n")


def _write_plugin_json(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.json").write_text(
        '{"name":"demo","version":"0.0.1"}\n',
        encoding="utf-8",
        newline="\n",
    )


def _write_common_tree(root: Path) -> None:
    notes = root / "notes"
    notes.mkdir()
    (notes / "unknown.txt").write_bytes(UNKNOWN_TEXT)
    resources = root / "resources"
    resources.mkdir()
    (resources / "blob.bin").write_bytes(BLOB)
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / "keep.txt").write_bytes(HIDDEN_TEXT)
    (root / "empty-dir").mkdir()
    build = root / "build"
    build.mkdir()
    (build / "cache.txt").write_bytes(CACHE_TEXT)


def _write_source(root: Path, kind: str) -> None:
    root.mkdir()
    _write_common_tree(root)
    if kind == "cursor":
        _write_plugin_json(root / ".cursor-plugin")
        _write_skill(root / "skills" / "demo")
    elif kind == "cursor-workspace":
        (root / ".cursor").mkdir()
        _write_skill(root / ".cursor" / "skills" / "demo")
    elif kind == "codex":
        _write_plugin_json(root / ".codex-plugin")
        _write_skill(root / "skills" / "demo")
    elif kind == "codex-workspace":
        _write_skill(root / ".agents" / "skills" / "demo")
    elif kind == "claude-code":
        _write_plugin_json(root / ".claude-plugin")
        _write_skill(root / "skills" / "demo")
    elif kind == "claude-code-workspace":
        _write_skill(root / ".claude" / "skills" / "demo")
    else:
        raise AssertionError(kind)


def _paths_of(inventory) -> set[str]:
    return {entry.path for entry in inventory.entries}


def test_six_sources_keep_scanner_blind_spots() -> None:
    from agentc.workspace.inventory import FIXED_EXCLUSIONS, inventory_source
    from agentc.workspace.source import SOURCE_KINDS, detect_source
    from plugin_compiler.scanner import scan_plugin

    assert SOURCE_KINDS == (
        "cursor",
        "cursor-workspace",
        "codex",
        "codex-workspace",
        "claude-code",
        "claude-code-workspace",
    )
    assert FIXED_EXCLUSIONS == (".git/",)
    for kind, dialect, skill_rel in SOURCE_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plugin"
            _write_source(root, kind)
            before = _signature(root)
            scanned = {
                ref.source_path for ref in scan_plugin(root, dialect)
            }
            inventory, diags = inventory_source(root)
            detected, kind_diags = detect_source(root, None)
            detected_explicit, explicit_diags = detect_source(root, kind)
            after = _signature(root)
            assert after == before
            assert diags == ()
            assert inventory is not None
            assert inventory.exclusions == FIXED_EXCLUSIONS
            assert detected == kind and kind_diags == ()
            assert detected_explicit == kind and explicit_diags == ()
            present = _paths_of(inventory)
            assert skill_rel in present
            for rel in BLIND_PATHS:
                assert rel not in scanned, (kind, rel, sorted(scanned))
                assert rel in present, (kind, rel)


def test_hash_rules_and_clean_recheck() -> None:
    from agentc.workspace.inventory import inventory_source, recheck_inventory

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "empty-dir").mkdir()
        notes = root / "notes"
        notes.mkdir()
        (notes / "unknown.txt").write_bytes(UNKNOWN_TEXT)
        os.symlink("notes/unknown.txt", root / "link")
        before = _signature(root)
        inventory, diags = inventory_source(root)
        again, again_diags = inventory_source(root)
        after = _signature(root)
        assert after == before
        assert diags == () and again_diags == ()
        assert inventory == again
        assert inventory is not None
        file_digest = _sha(b"file\0" + UNKNOWN_TEXT)
        empty_digest = _dir_digest([])
        notes_digest = _dir_digest([("unknown.txt", "file", file_digest)])
        link_raw = b"notes/unknown.txt"
        link_digest = _sha(b"symlink\0" + link_raw)
        expected = {
            "empty-dir": ("dir", 0, empty_digest),
            "notes": ("dir", 0, notes_digest),
            "notes/unknown.txt": ("file", len(UNKNOWN_TEXT), file_digest),
            "link": ("symlink", len(link_raw), link_digest),
        }
        assert [entry.path for entry in inventory.entries] == sorted(expected)
        for entry in inventory.entries:
            kind, size, digest = expected[entry.path]
            assert entry.kind == kind
            assert entry.size == size
            assert entry.digest == digest
        assert inventory.fingerprint == _fingerprint(
            [
                (path, kind, size, digest)
                for path, (kind, size, digest) in sorted(expected.items())
            ]
        )
        assert recheck_inventory(root, inventory) == ()
        assert _signature(root) == before


def test_root_git_is_the_only_fixed_exclusion() -> None:
    from agentc.workspace.inventory import FIXED_EXCLUSIONS, inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        git = root / ".git"
        git.mkdir()
        (git / "config").write_text("root-git\n", encoding="utf-8", newline="\n")
        (git / "objects").mkdir()
        nested = root / "pkg" / ".git"
        nested.mkdir(parents=True)
        (nested / "config").write_text("nested-git\n", encoding="utf-8", newline="\n")
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        (root / "build").mkdir()
        (root / "build" / "cache.txt").write_bytes(CACHE_TEXT)
        (root / "packaging").mkdir()
        (root / "packaging" / "note.txt").write_text(
            "pack\n", encoding="utf-8", newline="\n"
        )
        (root / ".hidden").mkdir()
        (root / ".hidden" / "keep.txt").write_bytes(HIDDEN_TEXT)
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert diags == ()
        assert inventory is not None
        assert inventory.exclusions == FIXED_EXCLUSIONS == (".git/",)
        present = _paths_of(inventory)
        assert ".git" not in present
        assert ".git/config" not in present
        assert ".git/objects" not in present
        assert "pkg/.git" in present
        assert "pkg/.git/config" in present
        assert "keep.txt" in present
        assert "build/cache.txt" in present
        assert "packaging/note.txt" in present
        assert ".hidden/keep.txt" in present


def test_zero_hits() -> None:
    from agentc.model.diagnostics import CODE_KIND_UNKNOWN
    from agentc.workspace.source import detect_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "readme.txt").write_text("hello\n", encoding="utf-8", newline="\n")
        before = _signature(root)
        kind, diags = detect_source(root, None)
        assert _signature(root) == before
        assert kind is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_KIND_UNKNOWN, ".")
        assert CODE_KIND_UNKNOWN == "AGC-P4-0008"
        assert "(none)" in diags[0].message


def test_double_hits_do_not_change_import() -> None:
    from agentc.importers.detect import IMPORT_KINDS, resolve_kind
    from agentc.model.diagnostics import CODE_KIND_UNKNOWN
    from agentc.workspace.source import detect_source

    assert IMPORT_KINDS == (
        "cursor",
        "cursor-workspace",
        "cursor-user",
        "codex",
        "claude-code",
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        _write_plugin_json(root / ".cursor-plugin")
        _write_plugin_json(root / ".codex-plugin")
        before = _signature(root)
        kind, diags = detect_source(root, None)
        explicit, explicit_diags = detect_source(root, "cursor")
        resolved, code = resolve_kind(root, "cursor")
        assert _signature(root) == before
        assert kind is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_KIND_UNKNOWN, ".")
        assert "cursor" in diags[0].message and "codex" in diags[0].message
        assert explicit is None
        assert len(explicit_diags) == 1
        _assert_error(explicit_diags[0], CODE_KIND_UNKNOWN, ".")
        assert resolved == "cursor" and code is None


def test_explicit_kind_mismatch() -> None:
    from agentc.model.diagnostics import CODE_KIND_MISMATCH, CODE_KIND_UNKNOWN
    from agentc.workspace.source import detect_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        _write_source(root, "cursor")
        before = _signature(root)
        kind, diags = detect_source(root, "codex")
        assert _signature(root) == before
        assert kind is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_KIND_MISMATCH, ".")
        assert CODE_KIND_MISMATCH == "AGC-P4-0007"
        assert "codex" in diags[0].message
        assert "cursor" in diags[0].message

        empty = Path(tmp) / "empty"
        empty.mkdir()
        (empty / "readme.txt").write_text("x\n", encoding="utf-8", newline="\n")
        rejected, rejected_diags = detect_source(empty, "cursor-user")
        assert rejected is None
        assert len(rejected_diags) == 1
        _assert_error(rejected_diags[0], CODE_KIND_MISMATCH, ".")
        assert rejected_diags[0].code != CODE_KIND_UNKNOWN


def test_symlink_escape() -> None:
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        outside = base / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("secret\n", encoding="utf-8", newline="\n")
        root = base / "plugin"
        root.mkdir()
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        os.symlink(outside, root / "leak")
        before = _signature(root)
        secret_bytes = secret.read_bytes()
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert secret.read_bytes() == secret_bytes
        assert inventory is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_SOURCE_UNREADABLE, "leak")
        assert CODE_SOURCE_UNREADABLE == "AGC-P4-0002"
        assert "escapes" in diags[0].message


def test_symlink_cycle() -> None:
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        os.symlink("loop", root / "loop")
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert inventory is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_SOURCE_UNREADABLE, "loop")
        assert "cycle" in diags[0].message


def test_unreadable_directory() -> None:
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        locked = root / "locked"
        locked.mkdir()
        (locked / "x.txt").write_text("x\n", encoding="utf-8", newline="\n")
        before = _signature(root)
        os.chmod(locked, 0)
        try:
            inventory, diags = inventory_source(root)
        finally:
            os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        assert _signature(root) == before
        assert inventory is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_SOURCE_UNREADABLE, "locked")
        assert "unreadable" in diags[0].message


def test_illegal_encoding() -> None:
    from agentc.model.diagnostics import CODE_INVALID_UTF8
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        os.symlink(b"bad\xff.txt", os.path.join(os.fsencode(root), b"bad-link"))
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert inventory is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_INVALID_UTF8, "bad-link")
        assert CODE_INVALID_UTF8 == "AGC-P1-0006"
        assert "UTF-8" in diags[0].message


def test_recheck_reports_add_delete_rename_and_content() -> None:
    from agentc.model.diagnostics import CODE_WORKSPACE_CHANGED
    from agentc.workspace.inventory import inventory_source, recheck_inventory

    assert CODE_WORKSPACE_CHANGED == "AGC-P7-0002"

    def _changed(root: Path, previous, mutate) -> set[str]:
        before = _signature(root)
        mutate()
        mutated = _signature(root)
        diags = recheck_inventory(root, previous)
        assert _signature(root) == mutated
        assert mutated != before
        assert diags
        for diag in diags:
            _assert_error(diag, CODE_WORKSPACE_CHANGED, diag.path or "")
        return {diag.path for diag in diags}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        notes = root / "notes"
        notes.mkdir()
        target = notes / "unknown.txt"
        target.write_bytes(UNKNOWN_TEXT)
        inventory, diags = inventory_source(root)
        assert diags == () and inventory is not None
        paths = _changed(
            root,
            inventory,
            lambda: target.write_bytes(b"changed\n"),
        )
        assert "notes/unknown.txt" in paths

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        inventory, diags = inventory_source(root)
        assert diags == () and inventory is not None
        paths = _changed(
            root,
            inventory,
            lambda: (root / "extra.txt").write_text(
                "extra\n", encoding="utf-8", newline="\n"
            ),
        )
        assert "extra.txt" in paths

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        notes = root / "notes"
        notes.mkdir()
        target = notes / "unknown.txt"
        target.write_bytes(UNKNOWN_TEXT)
        inventory, diags = inventory_source(root)
        assert diags == () and inventory is not None
        paths = _changed(root, inventory, target.unlink)
        assert "notes/unknown.txt" in paths

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        notes = root / "notes"
        notes.mkdir()
        target = notes / "unknown.txt"
        target.write_bytes(UNKNOWN_TEXT)
        inventory, diags = inventory_source(root)
        assert diags == () and inventory is not None
        renamed = notes / "renamed.txt"
        paths = _changed(root, inventory, lambda: target.rename(renamed))
        assert "notes/unknown.txt" in paths
        assert "notes/renamed.txt" in paths


def test_output_must_not_overlap_input() -> None:
    from agentc.model.diagnostics import CODE_WORKSPACE_OVERLAP
    from agentc.workspace.inventory import output_overlap

    assert CODE_WORKSPACE_OVERLAP == "AGC-P7-0001"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "plugin"
        source.mkdir()
        (source / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        before = _signature(source)
        inside = output_overlap(source, source / "out")
        contains = output_overlap(source, base)
        same = output_overlap(source, source)
        sibling = output_overlap(source, base / "out")
        assert _signature(source) == before
        for diag in (inside, contains, same):
            assert diag is not None
            _assert_error(diag, CODE_WORKSPACE_OVERLAP, ".")
        assert sibling is None


def test_dangling_symlink_inside_root_is_recorded() -> None:
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        os.symlink("missing.txt", root / "inside-link")
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert diags == () and inventory is not None
        raw = b"missing.txt"
        entry = next(item for item in inventory.entries if item.path == "inside-link")
        assert entry.kind == "symlink"
        assert entry.size == len(raw)
        assert entry.digest == _sha(b"symlink\0" + raw)


def test_dangling_symlink_outside_root_escapes() -> None:
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        os.symlink("../no-such-outside", root / "outside-link")
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert inventory is None
        assert len(diags) == 1
        _assert_error(diags[0], CODE_SOURCE_UNREADABLE, "outside-link")
        assert "escapes" in diags[0].message
        assert "unreadable" not in diags[0].message


def test_unreadable_clue_does_not_select_another_kind() -> None:
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE
    from agentc.workspace.source import detect_source

    cases = (
        (".cursor-plugin/plugin.json", "codex"),
        (".codex-plugin/plugin.json", "cursor-workspace"),
        (".claude-plugin/plugin.json", "codex"),
        (".cursor", "codex"),
        (".agents/skills", "codex"),
        (".claude/skills", "codex"),
    )
    for rel, other in cases:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plugin"
            root.mkdir()
            if other == "codex":
                _write_plugin_json(root / ".codex-plugin")
            else:
                (root / ".cursor").mkdir()
            clue = root.joinpath(*rel.split("/"))
            if rel.endswith("plugin.json"):
                locked = clue.parent
                locked.mkdir(parents=True, exist_ok=True)
                clue.write_text("{}\n", encoding="utf-8", newline="\n")
            else:
                locked = clue
                locked.mkdir(parents=True, exist_ok=True)
            before = _signature(root)
            os.chmod(locked, 0)
            try:
                kind, diags = detect_source(root, None)
            finally:
                os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            assert _signature(root) == before
            assert kind is None, (rel, kind)
            assert kind != other
            matched = [diag for diag in diags if diag.path == rel]
            assert len(matched) == 1, (rel, [(d.code, d.path) for d in diags])
            _assert_error(matched[0], CODE_SOURCE_UNREADABLE, rel)


def test_root_git_file_and_symlink_stay_in_inventory() -> None:
    from agentc.workspace.inventory import FIXED_EXCLUSIONS, inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        data = b"gitfile\n"
        (root / ".git").write_bytes(data)
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert diags == () and inventory is not None
        assert inventory.exclusions == FIXED_EXCLUSIONS == (".git/",)
        entry = next(item for item in inventory.entries if item.path == ".git")
        assert entry.kind == "file"
        assert entry.size == len(data)
        assert entry.digest == _sha(b"file\0" + data)
        assert ".git/config" not in _paths_of(inventory)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        (root / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
        os.symlink("keep.txt", root / ".git")
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert diags == () and inventory is not None
        raw = b"keep.txt"
        entry = next(item for item in inventory.entries if item.path == ".git")
        assert entry.kind == "symlink"
        assert entry.size == len(raw)
        assert entry.digest == _sha(b"symlink\0" + raw)


def test_inventory_does_not_execute_scripts() -> None:
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        root.mkdir()
        script = root / "tools" / "touch-ran.sh"
        script.parent.mkdir()
        script.write_text(
            "#!/bin/sh\ntouch \"$(dirname \"$0\")/ran.txt\"\n",
            encoding="utf-8",
            newline="\n",
        )
        script.chmod(0o755)
        before = _signature(root)
        inventory, diags = inventory_source(root)
        assert _signature(root) == before
        assert diags == () and inventory is not None
        assert "tools/touch-ran.sh" in _paths_of(inventory)
        assert not (script.parent / "ran.txt").exists()


TARGETS = ("cursor", "codex", "claude-code")
SECRET_TOKEN = "ghp_" + ("A" * 20)
SKILL_BODY_SENTENCE = "UNIQUE_SKILL_BODY_SENTENCE_7f3a"
HELPER_SCRIPT = "print('helper')\n"


def _cursor_manifest() -> str:
    return (
        '{"name":"demo","version":"0.0.1","description":"demo",'
        '"author":{"name":"demo"}}\n'
    )


def _skill_text(body: str) -> str:
    return (
        "---\nname: demo\ndescription: demo skill\n---\n\n# demo\n\n" + body
    )


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


def _compile(root: Path, output: Path, targets=TARGETS):
    from agentc.workspace.compile import compile_workspace

    return compile_workspace(root, targets, output)


def _outcome(result, path: str, target: str):
    matched = [
        item
        for item in result.outcomes
        if item.path == path and item.target == target
    ]
    assert len(matched) == 1, (path, target, matched)
    return matched[0]


def _zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _write_one_skill(root: Path, kind: str) -> None:
    root.mkdir()
    if kind == "cursor":
        _write_plugin_json(root / ".cursor-plugin")
        _write_skill(root / "skills" / "demo")
    elif kind == "cursor-workspace":
        _write_skill(root / ".cursor" / "skills" / "demo")
    elif kind == "codex":
        _write_plugin_json(root / ".codex-plugin")
        _write_skill(root / "skills" / "demo")
    elif kind == "codex-workspace":
        _write_skill(root / ".agents" / "skills" / "demo")
    elif kind == "claude-code":
        _write_plugin_json(root / ".claude-plugin")
        _write_skill(root / "skills" / "demo")
    elif kind == "claude-code-workspace":
        _write_skill(root / ".claude" / "skills" / "demo")
    else:
        raise AssertionError(kind)


def _zip_relative_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        names = [
            name.replace("\\", "/").strip("/")
            for name in archive.namelist()
            if name.strip() and not name.endswith("/")
        ]
    roots = {name.split("/", 1)[0] for name in names if "/" in name}
    relative: list[str] = []
    for name in names:
        if len(roots) == 1 and "/" in name:
            relative.append(name.split("/", 1)[1])
        else:
            relative.append(name)
    return set(relative)


def _zip_has_target(relative: set[str], target_path: str) -> bool:
    text = target_path.replace("\\", "/").strip("/")
    if text in relative:
        return True
    prefix = text + "/"
    return any(name.startswith(prefix) for name in relative)


def test_six_sources_report_paths_match_zip() -> None:
    from agentc.workspace.run import run_workspace_build

    groups = {
        "cursor-workspace": ".cursor",
        "codex-workspace": ".agents",
        "claude-code-workspace": ".claude",
    }
    for kind, target, _skill_rel in SOURCE_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "plugin"
            output = base / "out"
            _write_one_skill(root, kind)
            result = run_workspace_build(
                source=root,
                targets=(target,),
                source_kind=kind,
                output_dir=output,
            )
            assert result.exit_code == 0, (kind, result.diagnostics, result.summary)
            report = json.loads(
                (output / "workspace-build-report.json").read_text(encoding="utf-8")
            )
            assert report["sourceKind"] == kind
            assert len(report["archives"]) == 1
            relative = _zip_relative_names(Path(report["archives"][0]["path"]))
            assert "skills/demo/SKILL.md" in relative
            for entry in report["entries"]:
                for row in entry["targets"]:
                    target_path = row["targetPath"]
                    if not target_path:
                        continue
                    assert _zip_has_target(relative, target_path), (
                        kind,
                        entry["path"],
                        target_path,
                        sorted(relative),
                    )
            group = groups.get(kind)
            if group is None:
                continue
            row = next(item for item in report["entries"] if item["path"] == group)
            assert row["targets"][0]["status"] == "container", (kind, row)
            assert row["targets"][0]["targetPath"] is None
            assert group not in relative
            assert not any(name.startswith(group + "/") for name in relative)


def test_placeholder_secret_stays_in_body_text_and_not_in_paths() -> None:
    from agentc.security.redact import redact_portable_text, scrub_secret_text

    token = "ghp_" + ("Z" * 36)
    wrapped = "${" + token + "}"
    kept, reasons = redact_portable_text("token=" + wrapped)
    assert "secret" not in reasons
    assert token in kept
    assert token not in scrub_secret_text(wrapped)
    assert "<redacted-secret>" in scrub_secret_text(wrapped)


def test_clean_cursor_plugin_builds_three_targets() -> None:
    from agentc.workspace.inventory import inventory_source

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "plugin"
        output = base / "out"
        _write_clean_cursor(root)
        before = _signature(root)
        inventory, diags = inventory_source(root)
        result = _compile(root, output)
        assert _signature(root) == before
        assert diags == () and inventory is not None
        assert result.ok
        assert len(result.zips) == 3
        assert len(set(result.zips)) == 3
        for zip_path in result.zips:
            path = Path(zip_path)
            assert path.is_file()
            assert path.stat().st_size > 0
            assert any(name.endswith("SKILL.md") for name in _zip_names(path))
        assert not (root / "agent-project.yaml").exists()
        assert not (output / "agent-project.yaml").exists()
        for entry in inventory.entries:
            for target in TARGETS:
                outcome = _outcome(result, entry.path, target)
                assert outcome.status in {"transformed", "bundled", "empty"}
                if outcome.status in {"transformed", "bundled"}:
                    assert outcome.target_path


def test_workspace_bundles_all_regular_source_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "plugin"
        output = base / "out"
        _write_clean_cursor(root)
        (root / "notes").mkdir()
        (root / "notes" / "unknown.txt").write_bytes(UNKNOWN_TEXT)
        (root / "resources").mkdir()
        (root / "resources" / "blob.bin").write_bytes(BLOB)
        script = root / "scripts" / "helper.py"
        script.parent.mkdir()
        script.write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('ran.txt').write_text('ran\\n')\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / ".gitignore").write_text("build/\n", encoding="utf-8")
        (root / "build").mkdir()
        (root / "build" / "previous.zip").write_bytes(BLOB)
        before = _signature(root)
        result = _compile(root, output)
        assert _signature(root) == before
        assert not (script.parent / "ran.txt").exists()
        assert result.ok
        assert len(result.zips) == 3
        for rel in (
            "notes/unknown.txt",
            "resources/blob.bin",
            "scripts/helper.py",
            ".gitignore",
        ):
            for target in TARGETS:
                outcome = _outcome(result, rel, target)
                assert outcome.status == "bundled"
                assert outcome.target_path == rel
            for zip_path in result.zips:
                assert any(name.endswith("/" + rel) for name in _zip_names(Path(zip_path)))
        for target in TARGETS:
            assert _outcome(result, "build/previous.zip", target).status == "ignored"
        for zip_path in result.zips:
            assert not any(name.endswith("/build/previous.zip") for name in _zip_names(Path(zip_path)))


def test_workspace_reports_compiler_inputs_and_target_limits() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "plugin"
        _write_clean_cursor(root)
        _write_skill(root / "skills" / "localonly")
        packaging = root / "packaging"
        packaging.mkdir()
        (packaging / "platform-overrides.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_targets": list(TARGETS),
                    "overrides": {
                        "skill:skills/localonly": {
                            "targets": ["cursor"],
                            "reason": "Cursor only",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        private_file = root / "skills" / "demo" / "config" / "access.local"
        private_file.parent.mkdir()
        private_file.write_text("local configuration\n", encoding="utf-8")
        result = _compile(root, base / "out")
        assert result.ok, result.diagnostics
        for target in TARGETS:
            assert _outcome(
                result, "packaging/platform-overrides.json", target
            ).status == "consumed"
            assert _outcome(
                result, "skills/demo/config/access.local", target
            ).status == "protected"
        assert _outcome(result, "skills/localonly/SKILL.md", "cursor").status == "transformed"
        for target in ("codex", "claude-code"):
            assert _outcome(result, "skills/localonly/SKILL.md", target).status == "excluded"
        for zip_path in result.zips:
            names = _zip_names(Path(zip_path))
            assert not any("packaging/" in name or name.endswith("access.local") for name in names)


def test_named_helper_script_is_bundled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "plugin"
        output = base / "out"
        _write_clean_cursor(root, body="Run scripts/helper.py before answering.\n")
        script = root / "scripts" / "helper.py"
        script.parent.mkdir()
        script.write_text(HELPER_SCRIPT, encoding="utf-8", newline="\n")
        before = _signature(root)
        result = _compile(root, output)
        assert _signature(root) == before
        assert result.ok
        assert len(result.zips) == 3
        for target in TARGETS:
            outcome = _outcome(result, "scripts/helper.py", target)
            assert outcome.status == "bundled"
            assert outcome.target_path
        for zip_path in result.zips:
            names = _zip_names(Path(zip_path))
            assert any(name.endswith("scripts/helper.py") for name in names)


def test_empty_directory_stays_out_of_zip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "plugin"
        output = base / "out"
        _write_clean_cursor(root)
        (root / "empty-dir").mkdir()
        before = _signature(root)
        result = _compile(root, output)
        assert _signature(root) == before
        assert result.ok
        assert len(result.zips) == 3
        for target in TARGETS:
            outcome = _outcome(result, "empty-dir", target)
            assert outcome.status == "empty"
            assert not outcome.target_path
        for zip_path in result.zips:
            names = _zip_names(Path(zip_path))
            assert all("empty-dir" not in name for name in names)


def test_secret_fails_without_leaking_into_zip_or_report() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "plugin"
        output = base / "out"
        _write_clean_cursor(
            root,
            body="TOKEN {0}\n{1}\n".format(SECRET_TOKEN, SKILL_BODY_SENTENCE),
        )
        before = _signature(root)
        result = _compile(root, output)
        assert _signature(root) == before
        assert not result.ok
        assert result.zips == ()
        report = result.report_text
        assert SECRET_TOKEN not in report
        assert SKILL_BODY_SENTENCE not in report
        assert list(output.rglob("*.zip")) == []
        for path in output.rglob("*"):
            if path.is_file():
                assert SECRET_TOKEN.encode("ascii") not in path.read_bytes()


def test_one_rejected_target_drops_every_zip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "plugin"
        output = base / "out"
        _write_clean_cursor(root)
        agent = root / "agents" / "helper.md"
        agent.parent.mkdir()
        agent.write_text(
            "---\nname: helper\nmodel: demo\n---\n\nDo work.\n",
            encoding="utf-8",
            newline="\n",
        )
        before = _signature(root)
        result = _compile(root, output)
        assert _signature(root) == before
        assert not result.ok
        assert result.zips == ()
        assert list(output.rglob("*.zip")) == []
        rejected = _outcome(result, "agents/helper.md", "codex")
        assert rejected.status == "error"
        assert rejected.path == "agents/helper.md"
        assert rejected.capability
        assert rejected.reason
        for target in ("cursor", "claude-code"):
            kept = _outcome(result, "agents/helper.md", target)
            assert kept.status == "transformed"
            assert kept.target_path


def test_later_adapter_exception_deletes_written_zips() -> None:
    import plugin_targets.registry as registry

    real_load = registry.load_adapter
    written: list[str] = []

    class RaisingAdapter:
        id = "codex"

        def compile(self, request):
            raise RuntimeError("codex compile failed")

        def validate(self, request):
            raise AssertionError("validate should not run after compile raised")

        def publish_local(self, request):
            raise AssertionError("publish should not run after compile raised")

    def load_adapter(adapter_id):
        adapter = real_load(adapter_id)
        if adapter_id == "cursor":
            original = adapter.publish_local

            def publish_local(request):
                result = original(request)
                assert result.archive_path
                assert Path(result.archive_path).is_file()
                written.append(result.archive_path)
                return result

            adapter.publish_local = publish_local
            return adapter
        if adapter_id == "codex":
            return RaisingAdapter()
        return adapter

    registry.load_adapter = load_adapter
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "plugin"
            output = base / "out"
            _write_clean_cursor(root)
            before = _signature(root)
            result = _compile(root, output)
            assert _signature(root) == before
            assert written
            assert not Path(written[0]).exists()
            assert not result.ok
            assert result.zips == ()
            assert list(output.rglob("*.zip")) == []
    finally:
        registry.load_adapter = real_load


def test_validate_skips_machine_codex_script() -> None:
    import plugin_targets.registry as registry

    real_load = registry.load_adapter
    validated: list[str] = []

    def load_adapter(adapter_id):
        adapter = real_load(adapter_id)
        original = adapter.validate

        def validate(request):
            validated.append(adapter_id)
            assert request.runner is not None
            assert getattr(request.runner, "structure_only", False) is True
            return original(request)

        adapter.validate = validate
        return adapter

    registry.load_adapter = load_adapter
    saved = {
        key: os.environ.get(key)
        for key in ("HOME", "CODEX_HOME", "CODEX_PLUGIN_VALIDATOR")
    }
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            os.environ["HOME"] = str(home)
            os.environ.pop("CODEX_HOME", None)
            os.environ.pop("CODEX_PLUGIN_VALIDATOR", None)
            root = base / "plugin"
            output = base / "out"
            _write_clean_cursor(root)
            result = _compile(root, output)
            assert result.ok
            assert len(result.zips) == 3
            assert set(validated) == set(TARGETS)
            assert not (home / ".codex").exists()
    finally:
        registry.load_adapter = real_load
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_unreadable_secret_scan_fails_at_relative_path() -> None:
    from agentc.model.diagnostics import CODE_SOURCE_UNREADABLE
    from pathlib import Path as PathType

    original = PathType.read_bytes

    def read_bytes(self):
        if self.name == "locked.bin":
            stack = traceback.extract_stack()
            if any(
                frame.name in {"_file_has_secret", "_secret_scan"}
                for frame in stack
            ):
                raise PermissionError(13, "Permission denied", str(self))
        return original(self)

    PathType.read_bytes = read_bytes
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "plugin"
            output = base / "out"
            _write_clean_cursor(root)
            (root / "locked.bin").write_bytes(BLOB)
            before = _signature(root)
            result = _compile(root, output)
            assert _signature(root) == before
            assert not result.ok
            assert result.zips == ()
            matched = [
                diag
                for diag in result.diagnostics
                if diag.path == "locked.bin" and diag.code == CODE_SOURCE_UNREADABLE
            ]
            assert len(matched) == 1
            assert matched[0].level == "error"
            assert matched[0].message
    finally:
        PathType.read_bytes = original


def main() -> int:
    tests = [
        test_six_sources_keep_scanner_blind_spots,
        test_hash_rules_and_clean_recheck,
        test_root_git_is_the_only_fixed_exclusion,
        test_zero_hits,
        test_double_hits_do_not_change_import,
        test_explicit_kind_mismatch,
        test_symlink_escape,
        test_symlink_cycle,
        test_unreadable_directory,
        test_illegal_encoding,
        test_recheck_reports_add_delete_rename_and_content,
        test_output_must_not_overlap_input,
        test_dangling_symlink_inside_root_is_recorded,
        test_dangling_symlink_outside_root_escapes,
        test_unreadable_clue_does_not_select_another_kind,
        test_root_git_file_and_symlink_stay_in_inventory,
        test_inventory_does_not_execute_scripts,
        test_six_sources_report_paths_match_zip,
        test_placeholder_secret_stays_in_body_text_and_not_in_paths,
        test_clean_cursor_plugin_builds_three_targets,
        test_workspace_bundles_all_regular_source_files,
        test_workspace_reports_compiler_inputs_and_target_limits,
        test_named_helper_script_is_bundled,
        test_empty_directory_stays_out_of_zip,
        test_secret_fails_without_leaking_into_zip_or_report,
        test_one_rejected_target_drops_every_zip,
        test_later_adapter_exception_deletes_written_zips,
        test_validate_skips_machine_codex_script,
        test_unreadable_secret_scan_fails_at_relative_path,
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
