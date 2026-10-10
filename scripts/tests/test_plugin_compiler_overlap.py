#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tempfile import TemporaryDirectory

from plugin_compiler.hooks import HookDocument, HookHandler
from plugin_compiler.model import CapabilityKind, CapabilityRef
from plugin_compiler.overlap import (
    TextUnit,
    find_overlap_issues,
    normalize_body_lines,
    normalize_name,
    units_from_documents,
)


def test_normalize_name_strips_quotes_and_casefolds():
    assert normalize_name('"Review-Helper"') == "review-helper"


def test_normalize_body_lines_drops_blank_and_casefolds():
    assert normalize_body_lines("  Hello \n\nWORLD\n") == ("hello", "world")


def test_identical_skill_bodies_are_content_duplicate():
    units = (
        TextUnit("skill", "skills/a/SKILL.md", "alpha", "Do the work\nThen stop\n"),
        TextUnit("skill", "skills/b/SKILL.md", "beta", "do the work\nTHEN STOP\n"),
    )
    issues = find_overlap_issues(units)
    assert any(
        issue.severity == "block"
        and issue.code == "content-duplicate"
        and issue.path == "skills/a/SKILL.md"
        and "skills/b/SKILL.md" in issue.message
        for issue in issues
    ), issues


def test_same_kind_same_normalized_name_overlaps():
    units = (
        TextUnit("skill", "skills/a/SKILL.md", '"helper"', "Alpha steps here\n"),
        TextUnit("skill", "skills/b/SKILL.md", "Helper", "Beta steps here\n"),
    )
    issues = find_overlap_issues(units)
    assert any(issue.code == "semantic-name-overlap" for issue in issues), issues


def test_line_overlap_at_threshold_is_block():
    left = "\n".join("line-{0}".format(i) for i in range(5))
    right = left + "\nextra-only-on-right\n"
    units = (
        TextUnit("skill", "skills/a/SKILL.md", "a", left),
        TextUnit("skill", "skills/b/SKILL.md", "b", right),
    )
    issues = find_overlap_issues(units)
    assert any(issue.code == "semantic-line-overlap" for issue in issues), issues


def test_two_line_skills_do_not_line_overlap():
    units = (
        TextUnit("skill", "skills/a/SKILL.md", "a", "shared\nonly-a\n"),
        TextUnit("skill", "skills/b/SKILL.md", "b", "shared\nonly-b\n"),
    )
    issues = find_overlap_issues(units)
    assert not any(issue.code == "semantic-line-overlap" for issue in issues), issues


def test_command_copying_skill_body_is_cross_form():
    body = "\n".join("step-{0}".format(i) for i in range(5))
    units = (
        TextUnit("skill", "skills/a/SKILL.md", "a", body),
        TextUnit("command", "commands/a.md", "a-cmd", body),
    )
    issues = find_overlap_issues(units)
    assert any(issue.code == "cross-form-overlap" for issue in issues), issues


def test_agent_and_workflow_vs_skill_are_cross_form():
    body = "\n".join("step-{0}".format(i) for i in range(5))
    for kind in ("agent", "workflow"):
        units = (
            TextUnit("skill", "skills/a/SKILL.md", "a", body),
            TextUnit(kind, "{0}s/a.md".format(kind), "a", body),
        )
        issues = find_overlap_issues(units)
        assert any(issue.code == "cross-form-overlap" for issue in issues), kind


def test_hook_and_rule_same_body_is_cross_form():
    body = "\n".join("rule-line-{0}".format(i) for i in range(5))
    units = (
        TextUnit("rule", "rules/always.mdc", "always", body),
        TextUnit("hook", "hooks/hooks.json", "hooks", body),
    )
    issues = find_overlap_issues(units)
    assert any(issue.code == "cross-form-overlap" for issue in issues), issues


def test_units_from_documents_loads_skills_and_joins_hook_scripts():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp)
        skill_md = plugin / "skills" / "demo" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text(
            "---\nname: demo\ndescription: Synthetic\n---\n# Demo\n",
            encoding="utf-8",
        )
        (plugin / "hooks").mkdir()
        (plugin / "hooks" / "check.py").write_text("print(1)\n", encoding="utf-8")
        argv_decoy = plugin / "build" / "reports"
        argv_decoy.parent.mkdir(parents=True)
        argv_decoy.write_text("SHOULD_NOT_JOIN\n", encoding="utf-8")
        capabilities = (
            CapabilityRef(
                "skill:skills/demo",
                CapabilityKind.SKILL,
                "skills/demo",
                None,
            ),
        )
        hooks = (
            HookDocument(
                capability_id="hook:hooks/hooks.json",
                source_path="hooks/hooks.json",
                handlers=(
                    HookHandler(
                        "SessionStart",
                        "python3 hooks/check.py --outdir build/reports --flag",
                        {},
                    ),
                    HookHandler("Stop", "python3 hooks/missing.py", {}),
                    HookHandler(
                        "Notification",
                        "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/check.py",
                        {},
                    ),
                ),
                script_relpaths=("hooks/check.py", "hooks/missing.py"),
            ),
        )
        units = units_from_documents(plugin, capabilities, hooks=hooks)
        skill_units = [unit for unit in units if unit.kind == "skill"]
        hook_units = [unit for unit in units if unit.kind == "hook"]
        assert skill_units[0].name == "demo"
        assert skill_units[0].path == "skills/demo/SKILL.md"
        assert "# Demo" in skill_units[0].body
        assert hook_units[0].name == "hooks/hooks.json"
        assert "python3 hooks/check.py --outdir build/reports --flag" in hook_units[0].body
        assert "python3 hooks/missing.py" in hook_units[0].body
        assert "print(1)" in hook_units[0].body
        assert "SHOULD_NOT_JOIN" not in hook_units[0].body


def test_mcp_and_theme_identical_bodies_are_ignored():
    body = "\n".join("x-{0}".format(i) for i in range(5))
    units = (
        TextUnit("mcp", "mcp.json", "m", body),
        TextUnit("skill", "skills/a/SKILL.md", "a", body),
        TextUnit("theme", "themes/t.md", "t", body),
    )
    issues = find_overlap_issues(units)
    assert issues == ()


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
