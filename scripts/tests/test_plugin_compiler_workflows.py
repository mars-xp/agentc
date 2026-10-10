#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.model import Target
from plugin_compiler.skills import parse_skill_markdown
from plugin_compiler.workflows import (
    load_workflow_document,
    parse_workflow_markdown,
    render_workflow_as_skill,
    workflow_conversion_issue,
    workflow_native_relative,
)


CONVERTIBLE = """---
name: ship-steps
description: Ship the plugin
---
# Ship

1. Build
2. Publish
"""

SCHEDULED = """---
name: nightly-ship
description: Nightly
schedule: "0 9 * * *"
---
# Nightly
"""

ISOLATION_KEYS = (
    "tools",
    "disallowedTools",
    "permissions",
    "permission",
    "isolation",
    "model",
    "memory",
    "mcp",
    "schedule",
    "cron",
    "on",
)


def test_convertible_workflow_flagged_true():
    document = parse_workflow_markdown(CONVERTIBLE, "workflows/ship-steps.md")
    assert document.convertible_to_skill is True
    assert document.name == "ship-steps"
    assert document.capability_id == "workflow:workflows/ship-steps.md"
    assert document.source_path == "workflows/ship-steps.md"
    assert document.frontmatter_fields == (
        ("name", "ship-steps"),
        ("description", "Ship the plugin"),
    )
    assert workflow_conversion_issue(document, Target.CLAUDE_CODE) is None
    assert workflow_conversion_issue(document, Target.CURSOR) is None
    assert workflow_conversion_issue(document, Target.CODEX) is None


def test_scheduled_workflow_fails_on_cursor_and_codex():
    document = parse_workflow_markdown(SCHEDULED, "workflows/nightly-ship.md")
    assert document.convertible_to_skill is False
    cursor_issue = workflow_conversion_issue(document, Target.CURSOR)
    assert cursor_issue is not None
    assert cursor_issue.code == "undeclared-target-unsupported"
    assert cursor_issue.severity == "block"
    assert cursor_issue.path == "workflows/nightly-ship.md"
    assert "packaging/platform-overrides.json" in cursor_issue.message
    codex_issue = workflow_conversion_issue(document, Target.CODEX)
    assert codex_issue is not None
    assert codex_issue.code == "undeclared-target-unsupported"
    assert workflow_conversion_issue(document, Target.CLAUDE_CODE) is None


def test_isolation_keys_block_conversion():
    for key in ISOLATION_KEYS:
        text = (
            "---\n"
            "name: nightly-ship\n"
            "description: Nightly\n"
            "{0}: present\n"
            "---\n"
            "# Nightly\n"
        ).format(key)
        document = parse_workflow_markdown(text, "workflows/nightly-ship.md")
        assert document.convertible_to_skill is False, key
        cursor_issue = workflow_conversion_issue(document, Target.CURSOR)
        assert (
            cursor_issue is not None
            and cursor_issue.code == "undeclared-target-unsupported"
        ), key
        codex_issue = workflow_conversion_issue(document, Target.CODEX)
        assert (
            codex_issue is not None
            and codex_issue.code == "undeclared-target-unsupported"
        ), key
        assert workflow_conversion_issue(document, Target.CLAUDE_CODE) is None, key


def test_extra_frontmatter_key_is_not_convertible():
    text = """---
name: ship-steps
description: Ship the plugin
argument-hint: "[plugin]"
---
# Ship

1. Build
2. Publish
"""
    document = parse_workflow_markdown(text, "workflows/ship-steps.md")
    assert document.convertible_to_skill is False
    issue = workflow_conversion_issue(document, Target.CURSOR)
    assert issue is not None and issue.code == "undeclared-target-unsupported"
    issue = workflow_conversion_issue(document, Target.CODEX)
    assert issue is not None and issue.code == "undeclared-target-unsupported"
    assert workflow_conversion_issue(document, Target.CLAUDE_CODE) is None


def test_render_workflow_as_skill():
    document = parse_workflow_markdown(CONVERTIBLE, "workflows/ship-steps.md")
    text = render_workflow_as_skill(document)
    assert text.startswith("---\n")
    assert "name: ship-steps\n" in text
    assert "Build" in text
    assert "description: Ship the plugin\n" in text
    assert "\r" not in text


def test_parse_workflow_multiline_description_round_trips_as_skill():
    expected = {
        "|": "Build safely\nThen publish",
        ">": "Build safely Then publish",
        ">-": "Build safely Then publish",
    }
    for marker, description in expected.items():
        text = (
            "---\n"
            "name: ship-steps\n"
            "description: {0}\n"
            "  Build safely\n"
            "  Then publish\n"
            "---\n"
            "# Ship\n"
        ).format(marker)
        document = parse_workflow_markdown(text, "workflows/ship-steps.md")
        assert document.convertible_to_skill is True, marker
        assert dict(document.frontmatter_fields)["description"] == description, marker
        rendered = render_workflow_as_skill(document)
        skill = parse_skill_markdown(rendered, "skills/ship-steps/SKILL.md")
        assert skill.name == "ship-steps"
        assert skill.description == description, marker


def test_render_workflow_as_skill_rejects_scheduled():
    document = parse_workflow_markdown(SCHEDULED, "workflows/nightly-ship.md")
    try:
        render_workflow_as_skill(document)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_workflow_native_relative_keeps_source_path():
    document = parse_workflow_markdown(CONVERTIBLE, "workflows/ship-steps.md")
    assert workflow_native_relative(document) == "workflows/ship-steps.md"
    nested = parse_workflow_markdown(CONVERTIBLE, "workflows/team/ship-steps.md")
    assert workflow_native_relative(nested) == "workflows/team/ship-steps.md"


def test_load_workflow_document_uses_plugin_relative_path():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / "workflows" / "ship-steps.md"
        path.parent.mkdir(parents=True)
        path.write_text(CONVERTIBLE, encoding="utf-8", newline="\n")
        document = load_workflow_document(path, plugin)
        assert document.source_path == "workflows/ship-steps.md"
        assert document.capability_id == "workflow:workflows/ship-steps.md"
        assert document.convertible_to_skill is True
        assert document.name == "ship-steps"


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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
