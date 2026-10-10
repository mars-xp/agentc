#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.model import Target
from plugin_compiler.skills import (
    SkillDocument,
    document_frontmatter_fields,
    load_skill_document,
    parse_skill_markdown,
    render_skill_markdown,
)


SOURCE = """---
name: demo-skill
description: Shared helper
allowed-tools: Read
cursor-only-key: nope
---
# Demo

Do not rewrite this body.
"""


def test_parse_strips_paired_quotes_from_name_and_single_line_description():
    text = """---
name: "demo-skill"
description: "Shared helper"
---
# Demo

Do not rewrite this body.
"""
    document = parse_skill_markdown(text, "skills/demo-skill/SKILL.md")
    assert document.name == "demo-skill"
    assert document.description == "Shared helper"


def test_parse_keeps_body_and_extra_keys():
    document = parse_skill_markdown(SOURCE, "skills/demo-skill/SKILL.md")
    assert document.name == "demo-skill"
    assert document.description == "Shared helper"
    assert document.body.startswith("# Demo")
    assert ("allowed-tools", "Read") in document.extra_fields
    assert ("cursor-only-key", "nope") in document.extra_fields


def test_render_claude_keeps_allowed_tools_and_drops_unknown():
    document = parse_skill_markdown(SOURCE, "skills/demo-skill/SKILL.md")
    text, issues = render_skill_markdown(document, Target.CLAUDE_CODE)
    assert "allowed-tools: Read" in text
    assert "cursor-only-key" not in text
    assert "# Demo" in text
    assert "Do not rewrite this body." in text
    assert any(issue.code == "dropped-frontmatter-field" for issue in issues)
    assert all(issue.severity == "warn" for issue in issues)


def test_render_cursor_and_codex_drop_non_common_keys():
    document = parse_skill_markdown(SOURCE, "skills/demo-skill/SKILL.md")
    for target in (Target.CURSOR, Target.CODEX):
        text, issues = render_skill_markdown(document, target)
        assert "name: demo-skill" in text
        assert "allowed-tools" not in text
        assert "cursor-only-key" not in text
        assert "Do not rewrite this body." in text
        codes = {issue.code for issue in issues}
        assert "dropped-frontmatter-field" in codes


def test_parse_missing_frontmatter_raises():
    try:
        parse_skill_markdown("# No header\n", "skills/demo-skill/SKILL.md")
    except RuntimeError as exc:
        assert str(exc) == "skills/demo-skill/SKILL.md: missing YAML frontmatter"
    else:
        raise AssertionError("expected RuntimeError")


def test_parse_incomplete_frontmatter_raises():
    try:
        parse_skill_markdown("---\nname: demo-skill\n", "skills/demo-skill/SKILL.md")
    except RuntimeError as exc:
        assert str(exc) == "skills/demo-skill/SKILL.md: incomplete YAML frontmatter"
    else:
        raise AssertionError("expected RuntimeError")


def test_parse_empty_name_or_description_raises():
    try:
        parse_skill_markdown(
            "---\nname:\ndescription: Shared helper\n---\nbody\n",
            "skills/demo-skill/SKILL.md",
        )
    except RuntimeError as exc:
        assert "skills/demo-skill/SKILL.md" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for empty name")
    try:
        parse_skill_markdown(
            "---\nname: demo-skill\ndescription:\n---\nbody\n",
            "skills/demo-skill/SKILL.md",
        )
    except RuntimeError as exc:
        assert "skills/demo-skill/SKILL.md" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for empty description")


def test_document_frontmatter_fields_literal_keeps_blank_lines():
    block = """name: demo-skill
description: |
  first

  third"""
    name, fields = document_frontmatter_fields(block)
    assert name == "demo-skill"
    assert ("description", "first\n\nthird") in fields


def test_literal_description_keeps_blank_lines():
    source = """---
name: demo-skill
description: |
  first

  third
---
Body.
"""
    document = parse_skill_markdown(source, "skills/demo-skill/SKILL.md")
    assert document.description == "first\n\nthird"
    text, _ = render_skill_markdown(document, Target.CURSOR)
    reparsed = parse_skill_markdown(text, "skills/demo-skill/SKILL.md")
    assert reparsed.description == "first\n\nthird"


def test_parse_folded_description_matches_packaging():
    folded = """---
name: demo-skill
description: >-
  First line
  second line
allowed-tools: Read
---
Body stays.
"""
    literal = """---
name: demo-skill
description: |
  First line
  second line
---
Body stays.
"""
    folded_doc = parse_skill_markdown(folded, "skills/demo-skill/SKILL.md")
    literal_doc = parse_skill_markdown(literal, "skills/demo-skill/SKILL.md")
    assert folded_doc.description == "First line second line"
    assert literal_doc.description == "First line\nsecond line"
    assert folded_doc.body == "Body stays.\n"
    assert ("allowed-tools", "Read") in folded_doc.extra_fields


def test_load_skill_document_uses_plugin_relative_path():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        skill_md = plugin / "skills" / "demo-skill" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text(SOURCE, encoding="utf-8")
        document = load_skill_document(skill_md, plugin)
        assert document.source_path == "skills/demo-skill/SKILL.md"
        assert document.name == "demo-skill"
        assert document.body.startswith("# Demo")


def _rendered_frontmatter(text: str) -> dict:
    header = text.split("\n---\n", 1)[0]
    block = header[3:].strip("\n")
    payload = yaml.safe_load(block)
    assert isinstance(payload, dict)
    return payload


def test_render_description_styles():
    document = parse_skill_markdown(SOURCE, "skills/demo-skill/SKILL.md")
    long_desc = "x" * 81
    colon_desc = "note: keep colon"
    multiline = SkillDocument(
        name=document.name,
        description="line one\nline two",
        extra_fields=(),
        body=document.body,
        source_path=document.source_path,
    )
    long_doc = SkillDocument(
        name=document.name,
        description=long_desc,
        extra_fields=(),
        body=document.body,
        source_path=document.source_path,
    )
    colon_doc = SkillDocument(
        name=document.name,
        description=colon_desc,
        extra_fields=(),
        body=document.body,
        source_path=document.source_path,
    )
    short_text, _ = render_skill_markdown(document, Target.CURSOR)
    short_header = _rendered_frontmatter(short_text)
    assert short_header["name"] == "demo-skill"
    assert short_header["description"] == "Shared helper"
    long_text, _ = render_skill_markdown(long_doc, Target.CURSOR)
    long_header = _rendered_frontmatter(long_text)
    assert long_header["description"] == long_desc
    colon_text, _ = render_skill_markdown(colon_doc, Target.CURSOR)
    colon_header = _rendered_frontmatter(colon_text)
    assert colon_header["description"] == colon_desc
    multi_text, _ = render_skill_markdown(multiline, Target.CURSOR)
    multi_header = _rendered_frontmatter(multi_text)
    assert multi_header["description"] == "line one\nline two"
    assert "\r" not in short_text


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
