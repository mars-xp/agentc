#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex marketplace.json fields (review 5.5)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_targets.claude_code.manifest import build_claude_manifest, write_claude_manifest
from plugin_targets.codex.marketplace import (
    MARKETPLACE_NAME,
    upsert_marketplace_plugin,
    write_marketplace,
)
from plugin_targets.cursor.manifest import build_cursor_manifest, write_cursor_manifest


def _read_marketplace(staging: Path) -> dict:
    path = staging / ".agents" / "plugins" / "marketplace.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _tiny_plugin(root: Path) -> Path:
    plugin = root / "demo-plugin"
    (plugin / ".cursor-plugin").mkdir(parents=True)
    (plugin / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "description": "tiny",
            }
        ),
        encoding="utf-8",
    )
    (plugin / "skills" / "demo-skill").mkdir(parents=True)
    (plugin / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: d\n---\n# demo\n",
        encoding="utf-8",
    )
    return plugin


def _assert_no_marketplace_schema_keys(payload: dict, label: str) -> None:
    assert "category" not in payload, (label, payload)
    assert "policy" not in payload, (label, payload)
    interface = payload.get("interface")
    if isinstance(interface, dict):
        assert "displayName" not in interface, (label, payload)
    dumped = json.dumps(payload)
    assert '"installation"' not in dumped, (label, payload)


def _expect_runtime_error(staging: Path, rels: list[str]) -> None:
    try:
        write_marketplace(staging, rels)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError for {0}".format(rels))


def test_marketplace_json_has_codex_required_fields():
    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        out = write_marketplace(staging, ["plugins/demo-plugin"])
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["name"] == MARKETPLACE_NAME
        assert payload["interface"]["displayName"]
        entry = payload["plugins"][0]
        assert entry["name"] == "demo-plugin"
        assert entry["category"] == "Developer Tools"
        assert "installation" in entry["policy"]
        assert "authentication" in entry["policy"]
        assert entry["policy"]["installation"] == "AVAILABLE"
        assert entry["policy"]["authentication"] == "ON_INSTALL"
        assert entry["source"] == {
            "source": "local",
            "path": "./plugins/demo-plugin",
        }


def test_marketplace_rejects_parent_escape():
    with TemporaryDirectory() as tmp:
        staging = Path(tmp) / "st"
        staging.mkdir()
        _expect_runtime_error(staging, ["../../outside-plugin"])


def test_marketplace_rejects_empty_dotdot_and_non_plugin_shape():
    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        for rel in (
            "",
            "   ",
            "../escape",
            "plugins/../other",
            "plugins/nested/extra",
            "foo/bar",
        ):
            _expect_runtime_error(staging, [rel])


def test_marketplace_accepts_single_segment_plugin_name():
    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        out = write_marketplace(staging, ["sample-plugin"])
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["plugins"][0]["name"] == "sample-plugin"
        assert payload["plugins"][0]["source"]["path"] == "./plugins/sample-plugin"


def test_marketplace_upsert_appends_and_preserves_sibling_entry():
    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "alpha-plugin").mkdir(parents=True)
        (staging / "plugins" / "beta-plugin").mkdir(parents=True)
        custom = {
            "name": "custom-market",
            "interface": {"displayName": "Custom Local Marketplace"},
            "plugins": [
                {
                    "name": "alpha-plugin",
                    "category": "Productivity",
                    "policy": {
                        "installation": "INSTALLED_BY_DEFAULT",
                        "authentication": "ON_USE",
                        "products": ["codex"],
                    },
                    "source": {
                        "source": "local",
                        "path": "./plugins/alpha-plugin",
                    },
                }
            ],
        }
        market = staging / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True, exist_ok=True)
        market.write_text(json.dumps(custom, indent=2) + "\n", encoding="utf-8")
        upsert_marketplace_plugin(staging, "beta-plugin")
        payload = _read_marketplace(staging)
        assert payload["name"] == "custom-market"
        assert payload["interface"]["displayName"] == "Custom Local Marketplace"
        names = [entry["name"] for entry in payload["plugins"]]
        assert names == ["alpha-plugin", "beta-plugin"]
        alpha = payload["plugins"][0]
        assert alpha["category"] == "Productivity"
        assert alpha["policy"] == {
            "installation": "INSTALLED_BY_DEFAULT",
            "authentication": "ON_USE",
            "products": ["codex"],
        }


def test_marketplace_upsert_rejects_corrupt_file():
    from plugin_targets.codex.marketplace import MarketplaceError

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "keep-plugin").mkdir(parents=True)
        market = staging / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True, exist_ok=True)
        market.write_text("{not-json", encoding="utf-8")
        raised = False
        try:
            upsert_marketplace_plugin(staging, "keep-plugin")
        except MarketplaceError:
            raised = True
        assert raised
        assert market.read_text(encoding="utf-8") == "{not-json"


def test_marketplace_upsert_preserves_same_name_policy():
    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "keep-plugin").mkdir(parents=True)
        custom = {
            "name": "custom-market",
            "interface": {"displayName": "Custom Local Marketplace"},
            "plugins": [
                {
                    "name": "keep-plugin",
                    "category": "Productivity",
                    "policy": {
                        "installation": "INSTALLED_BY_DEFAULT",
                        "authentication": "ON_USE",
                        "products": ["codex"],
                    },
                    "source": {
                        "source": "local",
                        "path": "./plugins/keep-plugin",
                    },
                    "extraKeep": True,
                }
            ],
        }
        market = staging / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True, exist_ok=True)
        market.write_text(json.dumps(custom, indent=2) + "\n", encoding="utf-8")
        upsert_marketplace_plugin(staging, "keep-plugin")
        payload = _read_marketplace(staging)
        entry = payload["plugins"][0]
        assert entry["name"] == "keep-plugin"
        assert entry["category"] == "Productivity"
        assert entry["policy"] == {
            "installation": "INSTALLED_BY_DEFAULT",
            "authentication": "ON_USE",
            "products": ["codex"],
        }
        assert entry["extraKeep"] is True
        assert entry["source"] == {
            "source": "local",
            "path": "./plugins/keep-plugin",
        }


def _write_marketplace(staging: Path, payload: dict) -> Path:
    if "interface" not in payload:
        payload = dict(payload)
        payload["interface"] = {"displayName": "Test Marketplace"}
    market = staging / ".agents" / "plugins" / "marketplace.json"
    market.parent.mkdir(parents=True, exist_ok=True)
    market.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return market


def _valid_local_entry(name: str) -> dict:
    return {
        "name": name,
        "category": "Developer Tools",
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "source": {
            "source": "local",
            "path": "./plugins/{0}".format(name),
        },
    }


def test_marketplace_read_rejects_spaced_name_and_non_object_interface():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "demo").mkdir(parents=True)
        _write_marketplace(
            staging,
            {
                "name": "bad name",
                "interface": "not-an-object",
                "plugins": [],
            },
        )
        raised = False
        try:
            read_marketplace_payload(staging)
        except MarketplaceError:
            raised = True
        assert raised


def test_marketplace_read_rejects_missing_or_non_string_display_name():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "demo").mkdir(parents=True)
        for interface in ({}, {"displayName": 1}, {"displayName": "  "}):
            _write_marketplace(
                staging,
                {
                    "name": "custom-market",
                    "interface": interface,
                    "plugins": [],
                },
            )
            raised = False
            try:
                read_marketplace_payload(staging)
            except MarketplaceError:
                raised = True
            assert raised, interface


def test_marketplace_read_rejects_non_list_policy_products():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "demo").mkdir(parents=True)
        _write_marketplace(
            staging,
            {
                "name": "custom-market",
                "plugins": [
                    {
                        "name": "demo",
                        "category": "Developer Tools",
                        "policy": {
                            "installation": "AVAILABLE",
                            "authentication": "ON_INSTALL",
                            "products": "codex",
                        },
                        "source": {
                            "source": "local",
                            "path": "./plugins/demo",
                        },
                    }
                ],
            },
        )
        raised = False
        try:
            read_marketplace_payload(staging)
        except MarketplaceError:
            raised = True
        assert raised


def test_marketplace_read_rejects_illegal_authentication_and_missing_category():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "demo").mkdir(parents=True)
        cases = [
            {
                "authentication": "NEVER",
                "category": "Developer Tools",
            },
            {
                "authentication": None,
                "category": "Developer Tools",
                "drop_auth": True,
            },
            {
                "authentication": "ON_INSTALL",
                "drop_category": True,
            },
            {
                "authentication": "ON_INSTALL",
                "category": 1,
            },
            {
                "authentication": "ON_INSTALL",
                "category": "  ",
            },
        ]
        for case in cases:
            entry = {
                "name": "demo",
                "source": {"source": "local", "path": "./plugins/demo"},
                "policy": {"installation": "AVAILABLE"},
            }
            if not case.get("drop_auth"):
                entry["policy"]["authentication"] = case["authentication"]
            if not case.get("drop_category"):
                entry["category"] = case["category"]
            _write_marketplace(
                staging,
                {
                    "name": "custom-market",
                    "plugins": [entry],
                },
            )
            raised = False
            try:
                read_marketplace_payload(staging)
            except MarketplaceError:
                raised = True
            assert raised, case


def test_marketplace_read_rejects_non_string_names_illegal_source_and_policy():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _write_marketplace(
            staging,
            {
                "name": 123,
                "plugins": [
                    {
                        "name": 456,
                        "source": "not-an-object",
                        "policy": {"installation": "NOPE"},
                    }
                ],
            },
        )
        raised = False
        try:
            read_marketplace_payload(staging)
        except MarketplaceError:
            raised = True
        assert raised, "numeric names / illegal source / illegal policy must fail closed"


def test_marketplace_read_rejects_sibling_invalid_plugin_path():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "alpha-plugin").mkdir(parents=True)
        _write_marketplace(
            staging,
            {
                "name": "custom-market",
                "plugins": [
                    _valid_local_entry("alpha-plugin"),
                    _valid_local_entry("gone-plugin"),
                ],
            },
        )
        raised = False
        try:
            read_marketplace_payload(staging)
        except MarketplaceError:
            raised = True
        assert raised, "sibling ./plugins/gone-plugin must fail before host CLI"


def test_marketplace_read_rejects_source_path_name_mismatch():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "alpha").mkdir(parents=True)
        (staging / "plugins" / "beta").mkdir(parents=True)
        _write_marketplace(
            staging,
            {
                "name": "custom-market",
                "plugins": [
                    _valid_local_entry("alpha"),
                    {
                        "name": "beta",
                        "category": "Developer Tools",
                        "policy": {
                            "installation": "AVAILABLE",
                            "authentication": "ON_INSTALL",
                        },
                        "source": {
                            "source": "local",
                            "path": "./plugins/alpha",
                        },
                    },
                ],
            },
        )
        raised = False
        try:
            read_marketplace_payload(staging)
        except MarketplaceError:
            raised = True
        assert raised, "beta must not accept source.path ./plugins/alpha even if both dirs exist"


def test_marketplace_sibling_invalid_path_does_not_replace_dest():
    from plugin_compiler.validation import CommandResult
    from plugin_targets.codex import install as codex_install
    from plugin_targets.spi import InstallRequest

    class RecordingRunner:
        structure_only = True

        def __init__(self) -> None:
            self.commands: list[tuple[str, ...]] = []

        def __call__(self, command, env=None):
            recorded = tuple(str(item) for item in command)
            self.commands.append(recorded)
            if recorded[:3] == ("codex", "plugin", "list"):
                return CommandResult(
                    0,
                    "demo-plugin@custom-market  installed, enabled  1.2.3\n",
                    "",
                )
            if recorded[:3] == ("codex", "plugin", "add") or recorded[:4] == (
                "codex",
                "plugin",
                "marketplace",
                "add",
            ):
                return CommandResult(0, "ok\n", "")
            return CommandResult(0, "Plugin validation passed: demo-plugin\n", "")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        published = root / "published-codex"
        (published / ".codex-plugin").mkdir(parents=True)
        (published / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "demo-plugin",
                    "version": "1.2.3",
                    "description": "demo",
                    "author": {"name": "demo"},
                    "interface": {
                        "displayName": "Demo",
                        "shortDescription": "short",
                        "longDescription": "long",
                        "developerName": "demo",
                        "category": "Productivity",
                        "capabilities": ["Read"],
                        "defaultPrompt": ["hello"],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (published / "runtime-host.json").write_text(
            json.dumps({"host": "codex"}) + "\n", encoding="utf-8"
        )
        install_root = root / "market"
        dest = install_root / "plugins" / "demo-plugin"
        dest.mkdir(parents=True)
        marker = dest / "KEEP.txt"
        marker.write_text("keep\n", encoding="utf-8")
        (install_root / "plugins" / "alpha-plugin").mkdir(parents=True)
        _write_marketplace(
            install_root,
            {
                "name": "custom-market",
                "plugins": [
                    _valid_local_entry("alpha-plugin"),
                    _valid_local_entry("gone-plugin"),
                ],
            },
        )
        runner = RecordingRunner()
        result = codex_install.install(
            InstallRequest(
                published_root=published,
                install_root=install_root,
                runner=runner,
            )
        )
        assert result.ok is False, result
        assert marker.read_text(encoding="utf-8") == "keep\n"
        assert dest.is_dir()
        assert not any(
            command[:2] == ("codex", "plugin") for command in runner.commands
        ), runner.commands


def test_marketplace_read_rejects_non_object_plugin_entry():
    from plugin_targets.codex.marketplace import (
        MarketplaceError,
        read_marketplace_payload,
    )

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        market = staging / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True, exist_ok=True)
        market.write_text(
            json.dumps(
                {
                    "name": "custom-market",
                    "interface": {"displayName": "Custom Local Marketplace"},
                    "plugins": [42],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raised = False
        try:
            read_marketplace_payload(staging)
        except MarketplaceError:
            raised = True
        assert raised


def test_marketplace_upsert_rejects_missing_sibling_plugin_dir():
    from plugin_targets.codex.marketplace import MarketplaceError

    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        (staging / "plugins" / "keep-plugin").mkdir(parents=True)
        write_marketplace(
            staging, ["plugins/keep-plugin", "plugins/gone-plugin"]
        )
        raised = False
        try:
            upsert_marketplace_plugin(staging, "keep-plugin")
        except MarketplaceError:
            raised = True
        assert raised
        payload = _read_marketplace(staging)
        names = [entry["name"] for entry in payload["plugins"]]
        assert names == ["keep-plugin", "gone-plugin"]


def test_marketplace_accepts_bare_plugin_name():
    with TemporaryDirectory() as tmp:
        staging = Path(tmp)
        write_marketplace(staging, ["demo-plugin"])
        payload = _read_marketplace(staging)
        assert payload["plugins"][0]["name"] == "demo-plugin"
        assert payload["plugins"][0]["source"]["path"] == "./plugins/demo-plugin"


def test_plugin_packaging_has_no_marketplace_wrapper():
    scripts = Path(__file__).resolve().parents[1]
    source = (scripts / "plugin_packaging.py").read_text(encoding="utf-8")
    assert "def write_marketplace" not in source
    assert "def _marketplace_plugin_name" not in source
    import plugin_packaging as pp

    assert not hasattr(pp, "write_marketplace")
    assert not hasattr(pp, "_marketplace_plugin_name")


def test_cursor_and_claude_plugin_json_omit_marketplace_schema_keys():
    from plugin_targets.cursor.source_parse import parse_cursor_plugin

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        plugin = _tiny_plugin(root)
        parsed = parse_cursor_plugin(plugin)
        cursor_man = build_cursor_manifest(parsed)
        claude_man = build_claude_manifest(parsed)
        _assert_no_marketplace_schema_keys(cursor_man, "cursor-builder")
        _assert_no_marketplace_schema_keys(claude_man, "claude-builder")

        cursor_dest = root / "cursor-out"
        claude_dest = root / "claude-out"
        write_cursor_manifest(parsed, cursor_dest)
        write_claude_manifest(parsed, claude_dest)
        cursor_file = json.loads(
            (cursor_dest / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        claude_file = json.loads(
            (claude_dest / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        _assert_no_marketplace_schema_keys(cursor_file, "cursor-plugin.json")
        _assert_no_marketplace_schema_keys(claude_file, "claude-plugin.json")


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
