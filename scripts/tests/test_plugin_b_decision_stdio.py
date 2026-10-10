#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.contracts.load import hook_event_decision_stdio
from plugin_compiler.hook_model import CommandHandler, ExecForm, ShellForm
from plugin_compiler.hooks import (
    _handler_has_stdio_adapter,
    _parse_stdio_adapter_ref,
    _resolve_stdio_adapter_ref,
    load_hook_document,
    remap_document,
    render_hooks_payload,
)
from plugin_compiler.model import Target
from plugin_compiler.orchestrator import compile_plugin

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent
_ADAPTER = _SCRIPTS / "plugin_compiler" / "stdio_adapters" / "decision_v1.py"
_STDIO_DECISION_FIXTURES = _TESTS / "fixtures" / "stdio_decision"
_DECISION_RED_FIXTURE = (
    _TESTS / "fixtures" / "origin_plugins" / "authoring-hook-decision-red-plugin"
)
_SOURCE_STDIN_FIXTURES = {
    "cursor": _STDIO_DECISION_FIXTURES / "stdin" / "cursor_allow.json",
    "codex": _STDIO_DECISION_FIXTURES / "stdin" / "codex_allow.json",
    "claude-code": _STDIO_DECISION_FIXTURES / "stdin" / "claude_code_allow.json",
}
_SOURCE_POLICIES = {
    "cursor": _STDIO_DECISION_FIXTURES / "policies" / "cursor_policy.py",
    "codex": _STDIO_DECISION_FIXTURES / "policies" / "codex_policy.py",
    "claude-code": _STDIO_DECISION_FIXTURES / "policies" / "claude_code_policy.py",
}
_STDIO_ADAPTER_REF = {
    "type": "stdio-decision",
    "version": 1,
    "path": "hooks/adapters/stdio_decision_v1.py",
}
_SCENARIO_COMMANDS = {
    "allow": "echo ok",
    "deny": "rm -rf /",
    "updated_input": "ls",
}

_CLAUDE_PRETOOLUSE_PROMPT_ONLY = """{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{"type": "prompt", "prompt": "allow or deny"}]
    }]
  }
}"""

_CLAUDE_PRETOOLUSE_MIXED = """{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [
        {"type": "prompt", "prompt": "allow or deny"},
        {
          "type": "command",
          "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/policy.py"
        }
      ]
    }]
  }
}"""


def _h(raw):
    return CommandHandler(form=ShellForm(command="python3 hooks/policy.py"), raw=raw)


def test_boolean_still_not_an_adapter():
    handler = _h({"command": "python3 hooks/policy.py", "stdio_adapter": True})
    assert _handler_has_stdio_adapter(handler) is False
    assert _parse_stdio_adapter_ref(handler) is None


def test_object_ref_parses():
    raw = {
        "command": "python3 hooks/policy.py",
        "stdio_adapter": {
            "type": "stdio-decision",
            "version": 1,
            "path": "hooks/adapters/stdio_decision_v1.py",
        },
    }
    handler = _h(raw)
    ref = _parse_stdio_adapter_ref(handler)
    assert ref["path"] == "hooks/adapters/stdio_decision_v1.py"
    assert _handler_has_stdio_adapter(handler) is True


def test_object_ref_strips_surrounding_spaces_on_path():
    raw = {
        "command": "python3 hooks/policy.py",
        "stdio_adapter": {
            "type": "stdio-decision",
            "version": 1,
            "path": "  hooks/adapters/stdio_decision_v1.py  ",
        },
    }
    handler = _h(raw)
    ref = _parse_stdio_adapter_ref(handler)
    assert ref["path"] == "hooks/adapters/stdio_decision_v1.py"


def test_resolve_stdio_adapter_ref_without_plugin_dir_is_missing():
    raw = {
        "command": "python3 hooks/policy.py",
        "stdio_adapter": dict(_STDIO_ADAPTER_REF),
    }
    handler = _h(raw)
    assert _resolve_stdio_adapter_ref(handler, None) is None


def test_cursor_claude_pretooluse_marked_decision_stdio():
    assert hook_event_decision_stdio(
        "preToolUse", source_host="cursor", target=Target.CLAUDE_CODE
    ) is True


def test_same_host_decision_stdio_is_false():
    assert hook_event_decision_stdio(
        "preToolUse", source_host="cursor", target=Target.CURSOR
    ) is False
    assert hook_event_decision_stdio(
        "PreToolUse", source_host="claude-code", target=Target.CLAUDE_CODE
    ) is False


def _copy_decision_fixture(work: Path) -> Path:
    plugin = work / "authoring-hook-decision-red-plugin"
    shutil.copytree(_DECISION_RED_FIXTURE, plugin)
    return plugin


def _set_stdio_adapter(plugin: Path, *, write_file: bool) -> None:
    hooks_path = plugin / "hooks" / "hooks.json"
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    payload["hooks"]["preToolUse"][0]["stdio_adapter"] = dict(_STDIO_ADAPTER_REF)
    hooks_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    adapter_path = plugin / "hooks" / "adapters" / "stdio_decision_v1.py"
    if write_file:
        adapter_path.parent.mkdir(parents=True, exist_ok=True)
        adapter_path.write_text("# stub adapter for compile tests\n", encoding="utf-8")
    elif adapter_path.is_file():
        adapter_path.unlink()


def _invocation_from_handler(entry: dict) -> str | None:
    command = entry.get("command")
    if not isinstance(command, str):
        return None
    args = entry.get("args")
    if isinstance(args, list) and args:
        return " ".join([command] + [str(part) for part in args])
    return command


def _hook_commands_from_staged(staged_root: Path) -> list[str]:
    hooks_path = staged_root / "hooks" / "hooks.json"
    if not hooks_path.is_file():
        hooks_path = staged_root / "hooks.json"
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    commands: list[str] = []
    hooks = payload.get("hooks") or {}
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            invocation = _invocation_from_handler(entry)
            if invocation is not None:
                commands.append(invocation)
            nested = entry.get("hooks")
            if isinstance(nested, list):
                for item in nested:
                    if not isinstance(item, dict):
                        continue
                    nested_invocation = _invocation_from_handler(item)
                    if nested_invocation is not None:
                        commands.append(nested_invocation)
    return commands


def _json_contains_key(obj: object, key: str) -> bool:
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_json_contains_key(value, key) for value in obj.values())
    if isinstance(obj, list):
        return any(_json_contains_key(item, key) for item in obj)
    return False


def _staged_hooks_payload(staged_root: Path) -> dict:
    hooks_path = staged_root / "hooks" / "hooks.json"
    if not hooks_path.is_file():
        hooks_path = staged_root / "hooks.json"
    return json.loads(hooks_path.read_text(encoding="utf-8"))


def _write_claude_decision_plugin(plugin: Path, *, write_adapter: bool) -> None:
    (plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "claude-decision-plugin",
                "version": "0.0.1",
                "description": "fixture",
                "skills": "./skills/",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (plugin / "skills" / "demo" / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
    (plugin / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: fixture\n---\n# Demo\n",
        encoding="utf-8",
    )
    (plugin / "packaging").mkdir(parents=True, exist_ok=True)
    (plugin / "packaging" / "platform-overrides.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_targets": ["cursor", "codex", "claude-code"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (plugin / "hooks").mkdir(parents=True, exist_ok=True)
    (plugin / "hooks" / "policy.py").write_text(
        'print(\'{"permissionDecision":"allow"}\')\n',
        encoding="utf-8",
    )
    hooks = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/policy.py",
                            "stdio_adapter": dict(_STDIO_ADAPTER_REF),
                        }
                    ],
                }
            ]
        }
    }
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps(hooks, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if write_adapter:
        adapter_path = plugin / "hooks" / "adapters" / "stdio_decision_v1.py"
        adapter_path.parent.mkdir(parents=True, exist_ok=True)
        adapter_path.write_text("# stub adapter for compile tests\n", encoding="utf-8")


def test_decision_fixture_without_adapter_blocks_codex_same_host_cursor_ok():
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        plugin = _copy_decision_fixture(work)
        cursor_result = compile_plugin(
            plugin, work, ("cursor",), validate_only=True
        )
        assert cursor_result.ok is True, [
            (issue.code, issue.message)
            for issue in cursor_result.validation["cursor"].issues
        ]
        codex_result = compile_plugin(plugin, work, ("codex",), validate_only=True)
        assert codex_result.ok is False
        assert any(
            issue.code == "hook-decision-stdio-not-equivalent"
            for issue in codex_result.validation["codex"].issues
        )


def test_decision_fixture_object_ref_missing_file_still_blocks_codex():
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        plugin = _copy_decision_fixture(work)
        _set_stdio_adapter(plugin, write_file=False)
        result = compile_plugin(plugin, work, ("codex",), validate_only=True)
        assert result.ok is False
        codes = {issue.code for issue in result.validation["codex"].issues}
        assert "hook-decision-stdio-not-equivalent" in codes


def test_decision_fixture_adapter_wraps_codex_and_stages_adapter_file():
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        plugin = _copy_decision_fixture(work)
        _set_stdio_adapter(plugin, write_file=True)
        result = compile_plugin(plugin, work, ("codex",), validate_only=True)
        assert result.ok is True, [
            (issue.code, issue.message) for issue in result.validation["codex"].issues
        ]
        source_hooks = json.loads(
            (plugin / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        assert _json_contains_key(source_hooks, "stdio_adapter")
        staged = Path(result.staged_roots["codex"])
        commands = _hook_commands_from_staged(staged)
        assert commands
        joined = "\n".join(commands)
        assert "--from cursor --to codex" in joined
        assert "hooks/adapters/stdio_decision_v1.py" in joined
        assert (staged / "hooks" / "adapters" / "stdio_decision_v1.py").is_file()
        staged_hooks = _staged_hooks_payload(staged)
        assert not _json_contains_key(staged_hooks, "stdio_adapter")


def test_decision_fixture_same_host_cursor_does_not_wrap_to_cursor():
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        plugin = _copy_decision_fixture(work)
        _set_stdio_adapter(plugin, write_file=True)
        result = compile_plugin(plugin, work, ("cursor",), validate_only=True)
        assert result.ok is True
        staged = Path(result.staged_roots["cursor"])
        commands = _hook_commands_from_staged(staged)
        joined = "\n".join(commands)
        assert "--to cursor" not in joined
        assert "--from cursor" not in joined


def test_claude_origin_decision_fixture_wraps_cursor_from_claude_code():
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        plugin = work / "claude-decision-plugin"
        _write_claude_decision_plugin(plugin, write_adapter=True)
        result = compile_plugin(
            plugin,
            work,
            ("cursor",),
            validate_only=True,
            source_format="claude-code",
        )
        assert result.ok is True, [
            (issue.code, issue.message) for issue in result.validation["cursor"].issues
        ]
        source_hooks = json.loads(
            (plugin / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        assert _json_contains_key(source_hooks, "stdio_adapter")
        staged = Path(result.staged_roots["cursor"])
        commands = _hook_commands_from_staged(staged)
        joined = "\n".join(commands)
        assert "--from claude-code --to cursor" in joined
        assert "hooks/adapters/stdio_decision_v1.py" in joined
        staged_hooks = _staged_hooks_payload(staged)
        assert not _json_contains_key(staged_hooks, "stdio_adapter")


def test_pretooluse_prompt_handler_skips_stdio_decision_block():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / "hooks" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(_CLAUDE_PRETOOLUSE_PROMPT_ONLY, encoding="utf-8")
        doc = load_hook_document(path, plugin, source_host="claude-code")
        remapped, issues = remap_document(doc, Target.CURSOR, plugin)
        codes = {issue.code for issue in issues}
        assert "hook-decision-stdio-not-equivalent" not in codes
        payload = render_hooks_payload(remapped, Target.CURSOR)
        entry = payload["hooks"]["preToolUse"][0]
        assert entry.get("prompt") == "allow or deny"


def _run_decision_adapter(
    *,
    from_host: str,
    to_host: str,
    policy_path: Path,
    stdin_payload: dict,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(_ADAPTER),
        "--from",
        from_host,
        "--to",
        to_host,
        "--",
        sys.executable,
        str(policy_path),
    ]
    return subprocess.run(
        command,
        input=json.dumps(stdin_payload),
        text=True,
        capture_output=True,
        check=False,
    )


def _stdin_payload_for(host: str, scenario: str) -> dict:
    payload = json.loads(_SOURCE_STDIN_FIXTURES[host].read_text(encoding="utf-8"))
    command = _SCENARIO_COMMANDS[scenario]
    if host == "cursor":
        payload["command"] = command
        return payload
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    else:
        tool_input = dict(tool_input)
    tool_input["command"] = command
    payload["tool_input"] = tool_input
    return payload


def _assert_cursor_stdout(data: dict, scenario: str) -> None:
    if scenario == "deny":
        assert data.get("permission") == "deny"
    else:
        assert data.get("permission") == "allow"
    if scenario == "updated_input":
        assert data.get("updated_input") == {"command": "ls -la"}
    else:
        assert "updated_input" not in data


def _assert_codex_stdout(data: dict, scenario: str) -> None:
    hso = data.get("hookSpecificOutput")
    assert isinstance(hso, dict)
    if scenario == "deny":
        assert hso.get("permissionDecision") == "deny"
    else:
        assert hso.get("permissionDecision") == "allow"
    if scenario == "updated_input":
        assert data.get("updatedInput") == {"command": "ls -la"}
    else:
        assert "updatedInput" not in data


def _assert_claude_stdout(data: dict, scenario: str) -> None:
    hso = data.get("hookSpecificOutput")
    assert isinstance(hso, dict)
    assert hso.get("hookEventName") == "PreToolUse"
    if scenario == "deny":
        assert hso.get("permissionDecision") == "deny"
    else:
        assert hso.get("permissionDecision") == "allow"
    if scenario == "updated_input":
        assert data.get("updatedInput") == {"command": "ls -la"}
    else:
        assert "updatedInput" not in data


_TARGET_ASSERTERS = {
    "cursor": _assert_cursor_stdout,
    "codex": _assert_codex_stdout,
    "claude-code": _assert_claude_stdout,
}


def test_decision_v1_unknown_from_or_to_exits_nonzero():
    policy = _SOURCE_POLICIES["cursor"]
    stdin_payload = _stdin_payload_for("cursor", "allow")
    for bad_from, bad_to in (("unknown", "cursor"), ("cursor", "unknown")):
        result = subprocess.run(
            [
                sys.executable,
                str(_ADAPTER),
                "--from",
                bad_from,
                "--to",
                bad_to,
                "--",
                sys.executable,
                str(policy),
            ],
            input=json.dumps(stdin_payload),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, (bad_from, bad_to, result.stderr)


def test_decision_v1_identity_preserves_source_host_shape():
    for host in ("cursor", "codex", "claude-code"):
        for scenario in ("allow", "deny", "updated_input"):
            result = _run_decision_adapter(
                from_host=host,
                to_host=host,
                policy_path=_SOURCE_POLICIES[host],
                stdin_payload=_stdin_payload_for(host, scenario),
            )
            assert result.returncode == 0, result.stderr
            data = json.loads(result.stdout)
            _TARGET_ASSERTERS[host](data, scenario)


def test_decision_v1_translates_target_stdin_into_source_policy_fields():
    scenarios = ("allow", "deny", "updated_input")
    targets = ("cursor", "codex", "claude-code")
    for source_host in ("cursor", "codex", "claude-code"):
        for scenario in scenarios:
            for to_host in targets:
                result = _run_decision_adapter(
                    from_host=source_host,
                    to_host=to_host,
                    policy_path=_SOURCE_POLICIES[source_host],
                    stdin_payload=_stdin_payload_for(to_host, scenario),
                )
                assert result.returncode == 0, (
                    source_host,
                    to_host,
                    scenario,
                    result.stderr,
                )
                data = json.loads(result.stdout)
                _TARGET_ASSERTERS[to_host](data, scenario)


def test_decision_v1_claude_code_to_cursor_deny():
    result = _run_decision_adapter(
        from_host="claude-code",
        to_host="cursor",
        policy_path=_SOURCE_POLICIES["claude-code"],
        stdin_payload=_stdin_payload_for("cursor", "deny"),
    )
    assert result.returncode == 0, result.stderr
    _assert_cursor_stdout(json.loads(result.stdout), "deny")


def test_decision_v1_codex_to_claude_code_updated_input():
    result = _run_decision_adapter(
        from_host="codex",
        to_host="claude-code",
        policy_path=_SOURCE_POLICIES["codex"],
        stdin_payload=_stdin_payload_for("claude-code", "updated_input"),
    )
    assert result.returncode == 0, result.stderr
    _assert_claude_stdout(json.loads(result.stdout), "updated_input")


_POLICIES = _STDIO_DECISION_FIXTURES / "policies"


def _assert_explicit_deny(data: dict, to_host: str, *, reason_contains: str | None = None) -> None:
    if to_host == "cursor":
        assert data.get("permission") == "deny", data
        reason = data.get("agent_message")
        assert isinstance(reason, str) and reason.strip(), data
    else:
        hso = data.get("hookSpecificOutput")
        assert isinstance(hso, dict), data
        assert hso.get("permissionDecision") == "deny", data
        if to_host == "claude-code":
            assert hso.get("hookEventName") == "PreToolUse", data
        reason = hso.get("permissionDecisionReason")
        assert isinstance(reason, str) and reason.strip(), data
    if reason_contains is not None:
        assert reason_contains in reason, (reason, reason_contains)


def test_decision_v1_empty_object_is_explicit_deny_across_hosts():
    policy = _POLICIES / "empty_object.py"
    pairs = (
        ("cursor", "codex"),
        ("cursor", "claude-code"),
        ("claude-code", "cursor"),
    )
    for from_host, to_host in pairs:
        result = _run_decision_adapter(
            from_host=from_host,
            to_host=to_host,
            policy_path=policy,
            stdin_payload=_stdin_payload_for(to_host, "allow"),
        )
        assert result.returncode == 0, (from_host, to_host, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        _assert_explicit_deny(data, to_host, reason_contains="stdio-decision-v1:")


def test_decision_v1_continue_without_permission_is_explicit_deny():
    policy = _POLICIES / "continue_only.py"
    result = _run_decision_adapter(
        from_host="claude-code",
        to_host="cursor",
        policy_path=policy,
        stdin_payload=_stdin_payload_for("cursor", "allow"),
    )
    assert result.returncode == 0, result.stderr
    _assert_explicit_deny(json.loads(result.stdout), "cursor")


def test_decision_v1_legacy_decision_block_is_deny_with_reason():
    policy = _POLICIES / "legacy_block.py"
    for to_host in ("cursor", "codex"):
        result = _run_decision_adapter(
            from_host="claude-code",
            to_host=to_host,
            policy_path=policy,
            stdin_payload=_stdin_payload_for(to_host, "allow"),
        )
        assert result.returncode == 0, (to_host, result.stderr)
        _assert_explicit_deny(
            json.loads(result.stdout), to_host, reason_contains="x"
        )


def test_decision_v1_unknown_permission_is_explicit_deny():
    policy = _POLICIES / "unknown_permission.py"
    result = _run_decision_adapter(
        from_host="cursor",
        to_host="codex",
        policy_path=policy,
        stdin_payload=_stdin_payload_for("codex", "allow"),
    )
    assert result.returncode == 0, result.stderr
    _assert_explicit_deny(json.loads(result.stdout), "codex")


def test_decision_v1_policy_timeout_is_explicit_deny():
    policy = _POLICIES / "sleep.py"
    command = [
        sys.executable,
        str(_ADAPTER),
        "--from",
        "cursor",
        "--to",
        "claude-code",
        "--",
        sys.executable,
        str(policy),
    ]
    result = subprocess.run(
        command,
        input=json.dumps(_stdin_payload_for("claude-code", "allow")),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "STDIO_DECISION_POLICY_TIMEOUT_SEC": "1"},
    )
    assert result.returncode == 0, result.stderr
    _assert_explicit_deny(json.loads(result.stdout), "claude-code")


def test_decision_v1_cursor_deny_reason_survives_to_claude_code():
    result = _run_decision_adapter(
        from_host="cursor",
        to_host="claude-code",
        policy_path=_POLICIES / "cursor_deny_reason.py",
        stdin_payload=_stdin_payload_for("claude-code", "allow"),
    )
    assert result.returncode == 0, result.stderr
    _assert_explicit_deny(json.loads(result.stdout), "claude-code", reason_contains="why")


def test_decision_v1_maps_codex_bash_stdin_to_cursor_shell_for_policy():
    result = _run_decision_adapter(
        from_host="cursor",
        to_host="codex",
        policy_path=_POLICIES / "cursor_shell_gate.py",
        stdin_payload=_stdin_payload_for("codex", "allow"),
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    hso = data.get("hookSpecificOutput")
    assert isinstance(hso, dict)
    assert hso.get("permissionDecision") == "allow", data


def test_decision_v1_maps_cursor_shell_stdin_to_codex_bash_for_policy():
    stdin_payload = dict(_stdin_payload_for("cursor", "allow"))
    stdin_payload["tool_name"] = "Shell"
    result = _run_decision_adapter(
        from_host="codex",
        to_host="cursor",
        policy_path=_POLICIES / "codex_bash_gate.py",
        stdin_payload=stdin_payload,
    )
    assert result.returncode == 0, result.stderr
    _assert_cursor_stdout(json.loads(result.stdout), "allow")


def test_stdio_adapter_tool_maps_match_hook_matcher_maps():
    import importlib.util

    spec = importlib.util.spec_from_file_location("decision_v1", _ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    from plugin_compiler.hooks import _MATCHER_FROM_CURSOR, _MATCHER_TO_CURSOR

    assert module._TOOL_TO_CURSOR == _MATCHER_TO_CURSOR
    assert module._TOOL_FROM_CURSOR == _MATCHER_FROM_CURSOR


def test_decision_fixture_execform_wraps_command_and_args_for_codex():
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        plugin = _copy_decision_fixture(work)
        hooks_path = plugin / "hooks" / "hooks.json"
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
        handler = payload["hooks"]["preToolUse"][0]
        handler["command"] = "python3"
        handler["args"] = ["${CURSOR_PLUGIN_ROOT}/hooks/policy.py"]
        handler["stdio_adapter"] = dict(_STDIO_ADAPTER_REF)
        hooks_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        adapter_path = plugin / "hooks" / "adapters" / "stdio_decision_v1.py"
        adapter_path.parent.mkdir(parents=True, exist_ok=True)
        adapter_path.write_text("# stub adapter for compile tests\n", encoding="utf-8")
        parsed = load_hook_document(hooks_path, plugin, source_host="cursor")
        command_handlers = [
            group.handlers[0]
            for event in parsed.events
            for group in event.groups
            if group.handlers and isinstance(group.handlers[0], CommandHandler)
        ]
        assert command_handlers
        assert isinstance(command_handlers[0].form, ExecForm)
        result = compile_plugin(plugin, work, ("codex",), validate_only=True)
        assert result.ok is True, [
            (issue.code, issue.message) for issue in result.validation["codex"].issues
        ]
        staged = Path(result.staged_roots["codex"])
        commands = _hook_commands_from_staged(staged)
        joined = "\n".join(commands)
        assert "--from cursor --to codex" in joined
        assert "hooks/adapters/stdio_decision_v1.py" in joined
        assert " -- python3 " in " {0} ".format(joined)
        staged_hooks = _staged_hooks_payload(staged)
        assert not _json_contains_key(staged_hooks, "stdio_adapter")


def test_pretooluse_mixed_group_prompt_passthrough_command_blocks_without_adapter():
    with TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plug"
        path = plugin / "hooks" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(_CLAUDE_PRETOOLUSE_MIXED, encoding="utf-8")
        (plugin / "hooks" / "policy.py").write_text('print(1)\n', encoding="utf-8")
        doc = load_hook_document(path, plugin, source_host="claude-code")
        remapped, issues = remap_document(doc, Target.CURSOR, plugin)
        codes = {issue.code for issue in issues}
        assert "hook-decision-stdio-not-equivalent" in codes
        payload = render_hooks_payload(remapped, Target.CURSOR)
        entries = payload.get("hooks", {}).get("preToolUse", [])
        prompts = [entry.get("prompt") for entry in entries if entry.get("prompt")]
        commands = [entry.get("command") for entry in entries if entry.get("command")]
        assert prompts == ["allow or deny"]
        assert commands == []


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite


def main() -> int:
    failures: list[str] = []
    for name, value in sorted(globals().items()):
        if not name.startswith("test_") or not callable(value):
            continue
        try:
            value()
            print("ok", name)
        except Exception as exc:
            failures.append("{0}: {1}".format(name, exc))
            print("FAIL", name, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
