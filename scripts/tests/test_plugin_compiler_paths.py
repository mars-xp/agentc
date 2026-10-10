#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.paths import (
    BuildLayout,
    clear_plugin_build,
    clear_target_intermediate,
    clear_target_output,
    publish_directory,
)
from plugin_targets.registry import enabled_ids

UNSAFE_PLUGIN_NAMES = (
    "",
    ".",
    "..",
    "../escape",
    "a/b",
    "a\\b",
    "/abs",
    "C:foo",
    "C:",
)


def test_build_layout_uses_single_repo_build_root():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        layout = BuildLayout.from_repo(root)
        expected = root.resolve() / "build"
        assert layout.root == expected
        assert layout.intermediate("cursor", "demo") == (
            expected / "intermediates" / "cursor" / "demo"
        )
        assert layout.output("codex", "demo") == (
            expected / "outputs" / "codex" / "demo"
        )
        assert layout.report_dir("demo") == expected / "reports" / "demo"
        assert layout.temporary("demo") == expected / "tmp" / "demo"


def test_build_layout_paths_stay_under_build_root():
    with TemporaryDirectory() as tmp:
        layout = BuildLayout.from_repo(Path(tmp))
        build_root = layout.root.resolve()
        paths = [
            layout.intermediate("cursor", "demo"),
            layout.output("codex", "demo"),
            layout.report_dir("demo"),
            layout.temporary("demo"),
        ]
        for path in paths:
            resolved = path.resolve()
            assert resolved.is_relative_to(build_root), resolved


def test_build_layout_rejects_unsafe_plugin_names():
    with TemporaryDirectory() as tmp:
        layout = BuildLayout.from_repo(Path(tmp))
        methods = (
            lambda name: layout.intermediate("cursor", name),
            lambda name: layout.output("cursor", name),
            lambda name: layout.report_dir(name),
            lambda name: layout.temporary(name),
        )
        for bad in UNSAFE_PLUGIN_NAMES:
            for method in methods:
                try:
                    method(bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        "expected ValueError for {0!r}".format(bad)
                    )


def test_clear_plugin_build_rejects_unsafe_plugin_names():
    with TemporaryDirectory() as tmp:
        layout = BuildLayout.from_repo(Path(tmp))
        for bad in UNSAFE_PLUGIN_NAMES:
            try:
                clear_plugin_build(layout, bad)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "expected ValueError for {0!r}".format(bad)
                )


def test_clear_plugin_build_removes_all_selected_plugin_paths():
    with TemporaryDirectory() as tmp:
        layout = BuildLayout.from_repo(Path(tmp))
        selected_paths = []
        for adapter_id in enabled_ids():
            selected_paths.append(layout.intermediate(adapter_id, "demo"))
            selected_paths.append(layout.output(adapter_id, "demo"))
        selected_paths.extend(
            [
                layout.report_dir("demo"),
                layout.temporary("demo"),
            ]
        )
        keep_paths = [
            layout.intermediate("cursor", "keep"),
            layout.output("codex", "keep"),
            layout.report_dir("keep"),
            layout.temporary("keep"),
        ]
        for path in selected_paths + keep_paths:
            path.mkdir(parents=True)
            (path / "marker.txt").write_text("marker", encoding="utf-8")

        clear_plugin_build(layout, "demo")

        for path in selected_paths:
            assert not path.exists(), path
        for path in keep_paths:
            assert (path / "marker.txt").is_file(), path


def test_clear_target_helpers_only_remove_the_named_target():
    with TemporaryDirectory() as tmp:
        layout = BuildLayout.from_repo(Path(tmp))
        cursor_intermediate = layout.intermediate("cursor", "demo")
        codex_intermediate = layout.intermediate("codex", "demo")
        cursor_output = layout.output("cursor", "demo")
        claude_output = layout.output("claude-code", "demo")
        keep_plugin = layout.output("cursor", "keep")
        for path in (
            cursor_intermediate,
            codex_intermediate,
            cursor_output,
            claude_output,
            keep_plugin,
        ):
            path.mkdir(parents=True)
            (path / "marker.txt").write_text("marker", encoding="utf-8")

        clear_target_intermediate(layout, "cursor", "demo")
        clear_target_output(layout, "claude-code", "demo")

        assert not cursor_intermediate.exists()
        assert (codex_intermediate / "marker.txt").is_file()
        assert (cursor_output / "marker.txt").is_file()
        assert not claude_output.exists()
        assert (keep_plugin / "marker.txt").is_file()


def test_publish_directory_replaces_old_output():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        destination = root / "outputs" / "demo"
        source.mkdir()
        destination.mkdir(parents=True)
        (source / "new.txt").write_text("new", encoding="utf-8")
        (destination / "old.txt").write_text("old", encoding="utf-8")

        publish_directory(source, destination)

        assert (destination / "new.txt").read_text(encoding="utf-8") == "new"
        assert not (destination / "old.txt").exists()
        assert source.is_dir(), "发布不得破坏中间产物"


def test_publish_directory_cleans_pending_on_copy_failure():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        destination = root / "outputs" / "demo"
        source.mkdir()
        destination.mkdir(parents=True)
        (source / "new.txt").write_text("new", encoding="utf-8")
        (destination / "old.txt").write_text("old", encoding="utf-8")
        parent = destination.parent

        def simulated_copytree(source_path, pending_path, *args, **kwargs):
            pending_path.mkdir(parents=True, exist_ok=True)
            (pending_path / "partial.marker").write_text(
                "partial", encoding="utf-8"
            )
            raise OSError("simulated copy failure")

        with patch(
            "plugin_compiler.paths.shutil.copytree",
            side_effect=simulated_copytree,
        ):
            try:
                publish_directory(source, destination)
            except OSError:
                pass
            else:
                raise AssertionError("expected OSError")

        assert list(parent.glob(".demo.pending-*")) == []
        assert list(parent.rglob("partial.marker")) == []
        assert (destination / "old.txt").read_text(encoding="utf-8") == "old"


def test_publish_directory_cleans_pending_on_replace_failure():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        destination = root / "outputs" / "demo"
        source.mkdir()
        destination.mkdir(parents=True)
        (source / "new.txt").write_text("new", encoding="utf-8")
        (destination / "old.txt").write_text("old", encoding="utf-8")
        parent = destination.parent
        destination_str = str(destination)

        original_replace = __import__("os").replace

        def failing_replace(src, dst):
            if str(dst) == destination_str and ".pending-" in str(src):
                raise OSError("simulated replace failure")
            return original_replace(src, dst)

        with patch("plugin_compiler.paths.os.replace", side_effect=failing_replace):
            try:
                publish_directory(source, destination)
            except OSError:
                pass
            else:
                raise AssertionError("expected OSError")

        assert list(parent.glob(".demo.pending-*")) == []
        assert (destination / "old.txt").read_text(encoding="utf-8") == "old"


def test_publish_directory_keyboard_interrupt_after_old_rename_restores_destination():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        destination = root / "outputs" / "demo"
        sibling = root / "outputs" / "other-host"
        source.mkdir()
        destination.mkdir(parents=True)
        sibling.mkdir()
        (source / "new.txt").write_text("new", encoding="utf-8")
        (destination / "old.txt").write_text("old", encoding="utf-8")
        (sibling / "keep.txt").write_text("keep", encoding="utf-8")
        parent = destination.parent
        destination_str = str(destination)

        original_replace = __import__("os").replace

        def interrupt_pending_promote(src, dst):
            if str(dst) == destination_str and ".pending-" in str(src):
                raise KeyboardInterrupt
            return original_replace(src, dst)

        with patch(
            "plugin_compiler.paths.os.replace",
            side_effect=interrupt_pending_promote,
        ):
            try:
                publish_directory(source, destination)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("expected KeyboardInterrupt")

        assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
        assert not (destination / "new.txt").exists()
        assert list(parent.glob(".demo.pending-*")) == []
        assert list(parent.glob(".demo.old-*")) == []
        assert (sibling / "keep.txt").read_text(encoding="utf-8") == "keep"


def main() -> int:
    failures = []
    for key, value in sorted(globals().items()):
        if key.startswith("test_") and callable(value):
            try:
                value()
                print("PASS", key)
            except Exception as exc:
                failures.append((key, exc))
                print("FAIL", key, type(exc).__name__, exc)
    print("failures:", len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
