#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install path escape guards (review 5.3)."""
from __future__ import annotations

import json
import sys
import uuid
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_codex_install_rejects_dotdot_name_before_copy():
    from plugin_targets.codex import install as codex_install
    from plugin_targets.spi import InstallRequest

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_root = root / "market"
        install_root.mkdir(parents=True)
        victim = root / "victim"
        victim.mkdir(parents=True)
        marker = victim / "MARKER"
        marker.write_text("keep-me\n", encoding="utf-8")

        published = root / "published-codex"
        _write_json(
            published / ".codex-plugin" / "plugin.json",
            {"name": "../../victim", "version": "1.0.0"},
        )
        (published / "skills").mkdir(parents=True)

        result = codex_install.install(
            InstallRequest(published_root=published, install_root=install_root)
        )

        assert result.ok is False, result
        assert "install-unsafe-plugin-name" in _codes(result), result.issues
        assert marker.is_file(), "victim marker must survive"
        assert marker.read_text(encoding="utf-8") == "keep-me\n"
        assert not (victim / ".codex-plugin").exists()
        assert list(install_root.rglob("*")) == [] or not any(
            path.is_file() for path in install_root.rglob("*")
        )


def test_claude_install_rejects_dotdot_name_before_copy():
    from plugin_targets.claude_code import install as claude_install
    from plugin_targets.spi import InstallRequest

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_root = root / "claude-root"
        install_root.mkdir(parents=True)
        victim = root / "victim"
        victim.mkdir(parents=True)
        marker = victim / "MARKER"
        marker.write_text("keep-me\n", encoding="utf-8")

        published = root / "published-claude"
        _write_json(
            published / ".claude-plugin" / "plugin.json",
            {"name": "../../victim", "version": "1.0.0"},
        )
        (published / "skills").mkdir(parents=True)

        result = claude_install.install(
            InstallRequest(published_root=published, install_root=install_root)
        )

        assert result.ok is False, result
        assert "install-unsafe-plugin-name" in _codes(result), result.issues
        assert marker.is_file(), "victim marker must survive"
        assert marker.read_text(encoding="utf-8") == "keep-me\n"
        assert not (victim / ".claude-plugin").exists()
        assert not any(path.is_file() for path in install_root.rglob("*"))


def test_cursor_install_rejects_separator_in_name():
    from plugin_targets.cursor import install as cursor_install
    from plugin_targets.spi import InstallRequest

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_root = root / "cursor-local"
        install_root.mkdir(parents=True)
        outside = root / "outside"
        outside.mkdir(parents=True)
        marker = outside / "MARKER"
        marker.write_text("keep-me\n", encoding="utf-8")

        published = root / "published-cursor"
        _write_json(
            published / ".cursor-plugin" / "plugin.json",
            {"name": "evil/name", "version": "1.0.0", "skills": "./skills/"},
        )
        (published / "skills" / "demo").mkdir(parents=True)
        (published / "skills" / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")

        result = cursor_install.install(
            InstallRequest(published_root=published, install_root=install_root)
        )

        assert result.ok is False, result
        assert "install-unsafe-plugin-name" in _codes(result), result.issues
        assert marker.is_file(), "outside marker must survive"
        assert marker.read_text(encoding="utf-8") == "keep-me\n"
        assert not (install_root / "evil").exists()
        assert not (install_root / "evil" / "name").exists()
        assert not any(path.is_file() for path in install_root.rglob("*"))


def _write_plugin_zip_with_slip(
    zip_path: Path,
    *,
    manifest_rel: str,
    slip_name: str,
) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "demo-plugin/{0}".format(manifest_rel),
            json.dumps(
                {
                    "name": "demo-plugin",
                    "version": "1.0.0",
                    "skills": "./skills/",
                }
            )
            + "\n",
        )
        zf.writestr("demo-plugin/skills/demo/SKILL.md", "# demo\n")
        zf.writestr("../{0}".format(slip_name), "escaped\n")


def test_claude_zip_extract_does_not_write_dotdot_member_outside():
    from plugin_targets.claude_code import install as claude_install
    from plugin_targets.spi import InstallRequest

    slip_name = "zip-slip-claude-{0}".format(uuid.uuid4().hex)
    slip_outside = Path(gettempdir()) / slip_name
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = root / "claude-root"
            install_root.mkdir(parents=True)
            zip_path = root / "plugin.zip"
            _write_plugin_zip_with_slip(
                zip_path,
                manifest_rel=".claude-plugin/plugin.json",
                slip_name=slip_name,
            )
            try:
                result = claude_install.install(
                    InstallRequest(source=zip_path, install_root=install_root)
                )
            except RuntimeError as exc:
                raise AssertionError(
                    "unfiltered extractall left extra top-level ZIP members: {0}".format(
                        exc
                    )
                ) from exc
            assert not slip_outside.exists(), slip_outside
            assert not (root / slip_name).exists()
            assert not (install_root / slip_name).exists()
            assert result.ok is False, result
            assert "install-unsafe-plugin-name" in _codes(result), result.issues
    finally:
        if slip_outside.exists():
            slip_outside.unlink()


def test_cursor_install_accepts_dotted_name_prompts_chat():
    from plugin_targets.cursor import install as cursor_install
    from plugin_targets.spi import InstallRequest

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_root = root / "cursor-local"
        install_root.mkdir(parents=True)
        published = root / "published-cursor"
        _write_json(
            published / ".cursor-plugin" / "plugin.json",
            {
                "name": "prompts.chat",
                "version": "1.0.0",
                "skills": "./skills/",
            },
        )
        (published / "skills" / "demo").mkdir(parents=True)
        (published / "skills" / "demo" / "SKILL.md").write_text(
            "# demo\n", encoding="utf-8"
        )

        result = cursor_install.install(
            InstallRequest(published_root=published, install_root=install_root)
        )

        assert result.ok is True, result
        installed = install_root / "prompts.chat"
        assert installed.is_dir(), installed
        assert (installed / ".cursor-plugin" / "plugin.json").is_file()


def test_cursor_zip_extract_does_not_write_dotdot_member_outside():
    from plugin_targets.cursor import install as cursor_install
    from plugin_targets.spi import InstallRequest

    slip_name = "zip-slip-cursor-{0}".format(uuid.uuid4().hex)
    slip_outside = Path(gettempdir()) / slip_name
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = root / "cursor-local"
            install_root.mkdir(parents=True)
            zip_path = root / "plugin.zip"
            _write_plugin_zip_with_slip(
                zip_path,
                manifest_rel=".cursor-plugin/plugin.json",
                slip_name=slip_name,
            )
            try:
                result = cursor_install.install(
                    InstallRequest(source=zip_path, install_root=install_root)
                )
            except RuntimeError as exc:
                raise AssertionError(
                    "unfiltered extractall left extra top-level ZIP members: {0}".format(
                        exc
                    )
                ) from exc
            assert not slip_outside.exists(), slip_outside
            assert not (root / slip_name).exists()
            assert not (install_root / slip_name).exists()
            assert result.ok is False, result
            assert "install-unsafe-plugin-name" in _codes(result), result.issues
    finally:
        if slip_outside.exists():
            slip_outside.unlink()


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
