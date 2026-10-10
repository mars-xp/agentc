#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.contracts import rewrite_hook_plugin_root
from plugin_compiler.model import Target
from plugin_compiler.orchestrator import compile_plugin
from plugin_compiler.validation import CommandResult


def test_rewrite_relative_hooks_path_cursor():
    got = rewrite_hook_plugin_root("python3 hooks/check.py", Target.CURSOR)
    assert got == "python3 ${CURSOR_PLUGIN_ROOT}/hooks/check.py"


def test_rewrite_relative_hooks_path_claude_and_codex():
    assert rewrite_hook_plugin_root(
        "python3 hooks/check.py", Target.CLAUDE_CODE
    ) == "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/check.py"
    assert rewrite_hook_plugin_root(
        "python3 hooks/check.py", Target.CODEX
    ) == "python3 ${PLUGIN_ROOT}/hooks/check.py"


def test_rewrite_dot_slash_whole_string():
    got = rewrite_hook_plugin_root("./scripts/x.py", Target.CURSOR)
    assert got == "${CURSOR_PLUGIN_ROOT}/scripts/x.py"


def test_rewrite_placeholder_does_not_double_prefix():
    got = rewrite_hook_plugin_root(
        "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/x.py", Target.CURSOR
    )
    assert got == "python3 ${CURSOR_PLUGIN_ROOT}/hooks/x.py"
    assert got.count("PLUGIN_ROOT") == 1


def test_rewrite_scripts_without_dot_slash_unchanged():
    raw = "python3 scripts/foo.py"
    assert rewrite_hook_plugin_root(raw, Target.CURSOR) == raw


def test_rewrite_complex_shell_with_relative_returns_none():
    assert rewrite_hook_plugin_root(
        "python3 hooks/x.py && echo", Target.CURSOR
    ) is None


def test_rewrite_complex_shell_placeholder_only_swaps_vars():
    got = rewrite_hook_plugin_root(
        "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/x.py && echo", Target.CURSOR
    )
    assert got == "python3 ${CURSOR_PLUGIN_ROOT}/hooks/x.py && echo"


def test_rewrite_unbalanced_quote_with_relative_returns_none():
    assert rewrite_hook_plugin_root(
        "python3 hooks/x.py '", Target.CURSOR
    ) is None


def test_rewrite_quoted_placeholder_path_with_spaces_keeps_quotes():
    got = rewrite_hook_plugin_root(
        'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/my script.py"',
        Target.CURSOR,
    )
    assert got == 'python3 "${CURSOR_PLUGIN_ROOT}/scripts/my script.py"'


def test_rewrite_quoted_relative_path_with_spaces_keeps_quotes():
    import shlex as _shlex

    got = rewrite_hook_plugin_root(
        'python3 "hooks/my script.py"',
        Target.CURSOR,
    )
    assert "${CURSOR_PLUGIN_ROOT}/hooks/my script.py" in got
    assert "my script.py" in got
    tokens = _shlex.split(got, posix=True)
    assert tokens[0] == "python3"
    assert tokens[1] == "${CURSOR_PLUGIN_ROOT}/hooks/my script.py"


def test_codex_token_and_shell_var_are_official_plugin_root():
    from plugin_compiler.contracts import plugin_root_token, plugin_root_shell_var
    assert plugin_root_token(Target.CODEX) == "${PLUGIN_ROOT}"
    assert plugin_root_shell_var(Target.CODEX) == "$PLUGIN_ROOT"


def test_rewrite_official_plugin_root_stays_plugin_root_on_codex():
    got = rewrite_hook_plugin_root(
        "python3 ${PLUGIN_ROOT}/scripts/server.py", Target.CODEX
    )
    assert got == "python3 ${PLUGIN_ROOT}/scripts/server.py"
    assert "CODEX_PLUGIN_ROOT" not in got


def test_cursor_emit_stays_cursor_plugin_root():
    got = rewrite_hook_plugin_root("python3 hooks/check.py", Target.CURSOR)
    assert got == "python3 ${CURSOR_PLUGIN_ROOT}/hooks/check.py"
    assert "${PLUGIN_ROOT}" not in got


def test_codex_plugin_root_alias_in_source_is_unmapped():
    from plugin_compiler.contracts import rewrite_hook_plugin_root
    assert rewrite_hook_plugin_root(
        "python3 ${CODEX_PLUGIN_ROOT}/hooks/x.py", Target.CODEX
    ) is None
    assert rewrite_hook_plugin_root(
        "python3 ${CODEX_PLUGIN_ROOT}/hooks/x.py", Target.CURSOR
    ) is None


def test_generic_plugin_root_ingests_to_cursor_emit():
    got = rewrite_hook_plugin_root(
        "python3 ${PLUGIN_ROOT}/hooks/x.py", Target.CURSOR
    )
    assert got == "python3 ${CURSOR_PLUGIN_ROOT}/hooks/x.py"
    assert "${PLUGIN_ROOT}" not in got


def test_plugin_root_contract_drops_codex_alias_and_cursor_generic_host():
    from plugin_compiler.contracts.load import load_contracts

    data = load_contracts(force=True)["plugin_root"]
    blob = json.dumps(data)
    assert "CODEX_PLUGIN_ROOT" not in blob
    generic_hosts = None
    for item in data.get("placeholders") or []:
        if isinstance(item, dict) and item.get("token") == "${PLUGIN_ROOT}":
            generic_hosts = item.get("hosts")
            break
    assert generic_hosts is not None
    assert "cursor" not in generic_hosts


class _StructureRunner:
    structure_only = True

    def __call__(self, command):
        return CommandResult(0, "ok\n", "")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _plugin_with_codex_plugin_root(root: Path) -> Path:
    plugin = root / "source" / "demo"
    _write(
        plugin / ".cursor-plugin" / "plugin.json",
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "description": "Synthetic plugin",
                "skills": "./skills/",
            }
        )
        + "\n",
    )
    _write(
        plugin / "skills" / "demo" / "SKILL.md",
        "---\nname: demo\ndescription: Synthetic\n---\n# Demo\n",
    )
    _write(
        plugin / "packaging" / "platform-overrides.json",
        json.dumps(
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
                "overrides": {},
            }
        )
        + "\n",
    )
    _write(
        plugin / "hooks" / "hooks.json",
        json.dumps(
            {
                "hooks": {
                    "sessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 ${CODEX_PLUGIN_ROOT}/hooks/x.py",
                                }
                            ]
                        }
                    ]
                }
            }
        )
        + "\n",
    )
    _write(plugin / "hooks" / "x.py", "print(1)\n")
    _write(
        plugin / "mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "command": "python3",
                        "args": ["${CODEX_PLUGIN_ROOT}/scripts/server.py"],
                    }
                }
            }
        )
        + "\n",
    )
    _write(plugin / "scripts" / "server.py", "print(1)\n")
    return plugin


def _staged_text(result) -> str:
    chunks: list[str] = []
    for staged in result.staged_roots.values():
        root = Path(staged)
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                chunks.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                continue
    return "\n".join(chunks)


def test_compile_blocks_codex_plugin_root_alias_and_does_not_emit_plugin_root():
    for target in ("cursor", "codex"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = _plugin_with_codex_plugin_root(root)
            result = compile_plugin(
                plugin,
                root,
                (target,),
                runner=_StructureRunner(),
            )
            assert result.ok is False
            report = result.validation[target]
            assert any(issue.code == "unmapped-plugin-root" for issue in report.issues)
            text = _staged_text(result)
            assert "python3 ${PLUGIN_ROOT}/hooks/x.py" not in text
            assert "${PLUGIN_ROOT}/scripts/server.py" not in text


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
