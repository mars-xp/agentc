#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.mcp import (
    McpDocument,
    load_mcp_document,
    mcp_output_relative,
    mcp_secret_issues,
    render_mcp_json,
    rewrite_mcp_document,
)
from plugin_compiler.model import Target


CLEAN = {"mcpServers": {"demo": {"command": "npx", "args": ["-y", "demo-mcp"]}}}
SECRET = {
    "mcpServers": {
        "demo": {
            "command": "npx",
            "env": {"API_KEY": "sk-live-secret"},
        }
    }
}


def test_render_mcp_json_uses_target_filename_contract():
    assert mcp_output_relative(Target.CURSOR) == "mcp.json"
    assert mcp_output_relative(Target.CODEX) == ".mcp.json"
    assert mcp_output_relative(Target.CLAUDE_CODE) == ".mcp.json"


def test_load_and_render_roundtrip_without_secret():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / "mcp.json"
        path.write_text(json.dumps(CLEAN), encoding="utf-8", newline="\n")
        document = load_mcp_document(path, plugin)
        assert document.capability_id == "mcp:mcp.json"
        assert mcp_secret_issues(document) == ()
        text = render_mcp_json(document, Target.CODEX)
        payload = json.loads(text)
        assert "demo" in payload["mcpServers"]


def test_secret_value_is_block_issue():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".mcp.json"
        path.write_text(json.dumps(SECRET), encoding="utf-8", newline="\n")
        document = load_mcp_document(path, plugin)
        issues = mcp_secret_issues(document)
        assert any(item.code == "mcp-secret-in-source" for item in issues)


def test_load_mcp_document_rejects_mcp_servers_list():
    from plugin_compiler.mcp import load_mcp_document

    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        plugin.mkdir()
        path = plugin / ".mcp.json"
        path.write_text(
            json.dumps({"mcpServers": ["./other.json"]}),
            encoding="utf-8",
            newline="\n",
        )
        try:
            document = load_mcp_document(path, plugin)
        except RuntimeError:
            return
        raise AssertionError(
            "list mcpServers must not load as empty mapping: {0!r}".format(
                document.servers
            )
        )


def test_merge_mcp_documents_merges_disjoint_path_maps():
    from plugin_compiler.mcp import McpDocument, merge_mcp_documents

    left = McpDocument(
        capability_id="mcp:a.json",
        source_path="a.json",
        servers={"alpha": {"command": "a"}},
    )
    right = McpDocument(
        capability_id="mcp:b.json",
        source_path="b.json",
        servers={"beta": {"command": "b"}},
    )
    merged = merge_mcp_documents((left, right))
    assert len(merged) == 1
    assert merged[0].servers == {
        "alpha": {"command": "a"},
        "beta": {"command": "b"},
    }


def test_rewrite_mcp_document_rewrites_cwd_and_args_to_cursor():
    document = McpDocument(
        capability_id="mcp:mcp.json",
        source_path="mcp.json",
        servers={
            "demo": {
                "command": "python3",
                "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/server.py"],
                "cwd": "./data",
            }
        },
    )
    rewritten, issues = rewrite_mcp_document(document, Target.CURSOR)
    assert issues == ()
    server = rewritten.servers["demo"]
    assert server["args"] == ["${CURSOR_PLUGIN_ROOT}/scripts/server.py"]
    assert server["cwd"] == "${CURSOR_PLUGIN_ROOT}/data"
    assert server["command"] == "python3"


def test_rewrite_mcp_document_blocks_complex_shell_relative_command():
    document = McpDocument(
        capability_id="mcp:mcp.json",
        source_path="mcp.json",
        servers={
            "demo": {"command": "python3 hooks/x.py && echo"},
        },
    )
    rewritten, issues = rewrite_mcp_document(document, Target.CURSOR)
    assert any(item.code == "unsafe-plugin-relative-shell" for item in issues)


def test_render_mcp_json_uses_rewritten_servers():
    document = McpDocument(
        capability_id="mcp:mcp.json",
        source_path="mcp.json",
        servers={
            "demo": {
                "command": "python3",
                "args": ["${CURSOR_PLUGIN_ROOT}/scripts/server.py"],
                "cwd": "${CURSOR_PLUGIN_ROOT}/data",
                "env": {"TOKEN": "keep-name-only"},
            }
        },
    )
    payload = json.loads(render_mcp_json(document, Target.CURSOR))
    assert payload["mcpServers"]["demo"]["args"] == [
        "${CURSOR_PLUGIN_ROOT}/scripts/server.py"
    ]
    assert payload["mcpServers"]["demo"]["cwd"] == "${CURSOR_PLUGIN_ROOT}/data"
    assert payload["mcpServers"]["demo"]["env"]["TOKEN"] == "keep-name-only"


def test_render_mcp_json_keeps_placeholder_env():
    document = McpDocument(
        capability_id="mcp:mcp.json",
        source_path="mcp.json",
        servers={
            "demo": {
                "command": "demo-server",
                "env": {"API_TOKEN": "${API_TOKEN}"},
            }
        },
    )
    payload = json.loads(render_mcp_json(document, Target.CODEX))
    assert payload["mcpServers"]["demo"]["env"]["API_TOKEN"] == "${API_TOKEN}"


def test_compile_plugin_ok_does_not_empty_placeholder_mcp_env():
    from plugin_compiler.orchestrator import compile_plugin
    from plugin_compiler.validation import CommandResult

    class Runner:
        structure_only = True

        def __call__(self, command):
            return CommandResult(0, "Validation passed\n", "")

    with TemporaryDirectory() as tmp:
        repo = Path(tmp)
        plugin = repo / "plugins" / "mcp-env-keep"
        manifest = plugin / ".cursor-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": "mcp-env-keep",
                    "version": "0.0.1",
                    "description": "demo",
                    "author": {"name": "demo"},
                    "mcpServers": "./mcp.json",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        skill = plugin / "skills" / "only" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: only\ndescription: demo\n---\n", encoding="utf-8"
        )
        (plugin / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "demo": {
                            "command": "demo-server",
                            "env": {"API_TOKEN": "${API_TOKEN}"},
                        }
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = compile_plugin(
            plugin,
            repo,
            ("cursor",),
            runner=Runner(),
            source_format="cursor",
        )
        assert result.ok, {
            adapter_id: [(issue.code, issue.message) for issue in report.issues]
            for adapter_id, report in result.validation.items()
        }
        published = Path(result.published_roots["cursor"])
        payload = json.loads((published / "mcp.json").read_text(encoding="utf-8"))
        assert payload["mcpServers"]["demo"]["env"]["API_TOKEN"] == "${API_TOKEN}"


def test_merge_mcp_documents_duplicate_server_name_raises():
    from plugin_compiler.mcp import McpDocument, merge_mcp_documents

    left = McpDocument(
        capability_id="mcp:a.json",
        source_path="a.json",
        servers={"demo": {"command": "a"}},
    )
    right = McpDocument(
        capability_id="mcp:b.json",
        source_path="b.json",
        servers={"demo": {"command": "b"}},
    )
    try:
        merged = merge_mcp_documents((left, right))
    except RuntimeError:
        return
    raise AssertionError(
        "duplicate server names must not silently merge: {0!r}".format(
            merged[0].servers if merged else merged
        )
    )


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
