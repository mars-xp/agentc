#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.agents import (
    agent_conversion_issue,
    agent_native_relative,
    agent_skill_conflict,
    load_agent_document,
    parse_agent_markdown,
    render_agent_as_skill,
    render_agent_markdown,
)
from plugin_compiler.model import Target
from plugin_compiler.skills import parse_skill_markdown


CONVERTIBLE = """---
name: review-helper
description: Review a diff
---
# Review helper

Read the diff and comment.
"""

ISOLATED = """---
name: locked-reviewer
description: Isolated reviewer
tools: Read, Grep
---
# Locked

Must not become a skill.
"""

AGENT_LIST = """---
name: review-helper
description: Review a diff
allowed-tools:
  - Read
  - Grep
---
# Review helper

Read the diff and comment.
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


def test_agent_same_host_keeps_yaml_list():
    document = parse_agent_markdown(
        AGENT_LIST, "agents/review-helper.md", source_dialect="cursor"
    )
    text, issues = render_agent_markdown(document, Target.CURSOR)
    assert "- Read" in text
    assert issues == ()
    assert "allowed-tools:" in text


def test_agent_without_frontmatter_writes_body_only():
    raw = "# Review helper\n\nRead the diff.\n"
    document = parse_agent_markdown(
        raw, "agents/review-helper.md", source_dialect="cursor"
    )
    text, issues = render_agent_markdown(document, Target.CURSOR)
    assert text == raw
    assert not text.startswith("---")
    assert issues == ()


def test_agent_codex_conversion_still_name_description_only():
    document = parse_agent_markdown(CONVERTIBLE, "agents/review-helper.md")
    text = render_agent_as_skill(document)
    assert "name: review-helper" in text or "review-helper" in text
    assert "tools:" not in text
    skill = parse_skill_markdown(text, "skills/review-helper/SKILL.md")
    assert skill.name == "review-helper"


def test_convertible_agent_is_flagged_true():
    document = parse_agent_markdown(CONVERTIBLE, "agents/review-helper.md")
    assert document.convertible_to_skill is True
    assert document.name == "review-helper"
    assert document.capability_id == "agent:agents/review-helper.md"
    assert document.source_path == "agents/review-helper.md"
    assert document.frontmatter_fields == (
        ("name", "review-helper"),
        ("description", "Review a diff"),
    )
    assert agent_conversion_issue(document, Target.CODEX) is None
    assert agent_conversion_issue(document, Target.CURSOR) is None
    assert agent_conversion_issue(document, Target.CLAUDE_CODE) is None


def test_isolated_agent_is_not_convertible():
    document = parse_agent_markdown(ISOLATED, "agents/locked-reviewer.md")
    assert document.convertible_to_skill is False
    issue = agent_conversion_issue(document, Target.CODEX)
    assert issue is not None and issue.code == "undeclared-target-unsupported"
    assert issue.severity == "block"
    assert issue.path == "agents/locked-reviewer.md"
    assert "packaging/platform-overrides.json" in issue.message
    assert agent_conversion_issue(document, Target.CURSOR) is None
    assert agent_conversion_issue(document, Target.CLAUDE_CODE) is None


def test_isolation_keys_block_conversion():
    for key in ISOLATION_KEYS:
        text = (
            "---\n"
            "name: locked-reviewer\n"
            "description: Isolated reviewer\n"
            "{0}: present\n"
            "---\n"
            "# Locked\n"
        ).format(key)
        document = parse_agent_markdown(text, "agents/locked-reviewer.md")
        assert document.convertible_to_skill is False, key
        issue = agent_conversion_issue(document, Target.CODEX)
        assert issue is not None and issue.code == "undeclared-target-unsupported", key


def test_extra_frontmatter_key_is_not_convertible():
    text = """---
name: review-helper
description: Review a diff
argument-hint: "[diff]"
---
# Review helper

Read the diff and comment.
"""
    document = parse_agent_markdown(text, "agents/review-helper.md")
    assert document.convertible_to_skill is False
    issue = agent_conversion_issue(document, Target.CODEX)
    assert issue is not None and issue.code == "undeclared-target-unsupported"


def test_render_agent_as_skill_uses_kebab_name():
    document = parse_agent_markdown(CONVERTIBLE, "agents/review-helper.md")
    text = render_agent_as_skill(document)
    assert text.startswith("---\n")
    assert "name: review-helper\n" in text
    assert "Read the diff" in text
    assert "description: Review a diff\n" in text
    assert "\r" not in text


def test_parse_agent_quoted_name_strips_quotes_for_skill_conversion():
    text = """---
name: "review-helper"
description: "Review a diff"
---
# Review helper

Read the diff and comment.
"""
    document = parse_agent_markdown(text, "agents/review-helper.md")
    assert document.name == "review-helper"
    assert dict(document.frontmatter_fields)["name"] == "review-helper"
    assert dict(document.frontmatter_fields)["description"] == "Review a diff"
    rendered = render_agent_as_skill(document)
    skill = parse_skill_markdown(rendered, "skills/review-helper/SKILL.md")
    assert skill.name == "review-helper"
    assert skill.description == "Review a diff"


def test_parse_agent_multiline_description_round_trips_as_skill():
    expected = {
        "|": "Build safely\nThen publish",
        ">": "Build safely Then publish",
        ">-": "Build safely Then publish",
    }
    for marker, description in expected.items():
        text = (
            "---\n"
            "name: review-helper\n"
            "description: {0}\n"
            "  Build safely\n"
            "  Then publish\n"
            "---\n"
            "# Review helper\n"
        ).format(marker)
        document = parse_agent_markdown(text, "agents/review-helper.md")
        assert document.convertible_to_skill is True, marker
        assert dict(document.frontmatter_fields)["description"] == description, marker
        rendered = render_agent_as_skill(document)
        skill = parse_skill_markdown(rendered, "skills/review-helper/SKILL.md")
        assert skill.name == "review-helper"
        assert skill.description == description, marker


def test_render_agent_as_skill_rejects_isolated():
    document = parse_agent_markdown(ISOLATED, "agents/locked-reviewer.md")
    try:
        render_agent_as_skill(document)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_agent_skill_conflict_when_skill_id_exists():
    document = parse_agent_markdown(CONVERTIBLE, "agents/review-helper.md")
    issue = agent_skill_conflict(document, {"review-helper"})
    assert issue is not None
    assert issue.severity == "block"
    assert issue.code == "generated-skill-conflict"
    assert issue.path == "agents/review-helper.md"
    assert agent_skill_conflict(document, {"other-skill"}) is None


def test_agent_native_relative_keeps_source_path():
    document = parse_agent_markdown(CONVERTIBLE, "agents/review-helper.md")
    assert agent_native_relative(document) == "agents/review-helper.md"
    nested = parse_agent_markdown(CONVERTIBLE, "agents/team/review-helper.md")
    assert agent_native_relative(nested) == "agents/team/review-helper.md"


def test_load_agent_document_uses_plugin_relative_path():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / "agents" / "review-helper.md"
        path.parent.mkdir(parents=True)
        path.write_text(CONVERTIBLE, encoding="utf-8", newline="\n")
        document = load_agent_document(path, plugin)
        assert document.source_path == "agents/review-helper.md"
        assert document.capability_id == "agent:agents/review-helper.md"
        assert document.convertible_to_skill is True
        assert document.name == "review-helper"


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
