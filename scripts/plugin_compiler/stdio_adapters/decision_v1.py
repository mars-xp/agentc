#!/usr/bin/env python3
"""stdio-decision v1 adapter: translate policy stdin/stdout across Cursor / Codex / Claude Code."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from typing import Any

_VALID_HOSTS = frozenset({"cursor", "codex", "claude-code"})
_NESTED_BASH_HOSTS = frozenset({"codex", "claude-code"})
_KNOWN_PERMISSIONS = frozenset({"allow", "deny", "ask"})
_DENY_REASON = "stdio-decision-v1: unsupported policy output"
_TIMEOUT_ENV = "STDIO_DECISION_POLICY_TIMEOUT_SEC"
_DEFAULT_TIMEOUT_SEC = 30.0
# Keep in sync with plugin_compiler.hooks matcher maps.
_TOOL_TO_CURSOR = {"Bash": "Shell", "^Bash$": "^Shell$"}
_TOOL_FROM_CURSOR = {"Shell": "Bash", "^Shell$": "^Bash$"}


def _parse_adapter_argv(argv: list[str]) -> tuple[str, str, list[str]]:
    if "--" not in argv:
        print("missing -- separator before policy command", file=sys.stderr)
        raise SystemExit(2)
    sep = argv.index("--")
    pre_argv = argv[1:sep]
    policy_argv = argv[sep + 1 :]
    if not policy_argv:
        print("missing policy command after --", file=sys.stderr)
        raise SystemExit(2)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--from", dest="from_host", required=True)
    parser.add_argument("--to", dest="to_host", required=True)
    args = parser.parse_args(pre_argv)
    from_host = args.from_host
    to_host = args.to_host
    if from_host not in _VALID_HOSTS:
        print("unknown --from host: {0}".format(from_host), file=sys.stderr)
        raise SystemExit(1)
    if to_host not in _VALID_HOSTS:
        print("unknown --to host: {0}".format(to_host), file=sys.stderr)
        raise SystemExit(1)
    return from_host, to_host, policy_argv


def _policy_timeout_sec() -> float:
    raw = os.environ.get(_TIMEOUT_ENV, str(_DEFAULT_TIMEOUT_SEC))
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SEC
    if value <= 0:
        return _DEFAULT_TIMEOUT_SEC
    return value


def _dump_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _explicit_deny(to_host: str, reason: str | None) -> dict[str, Any]:
    text = reason.strip() if isinstance(reason, str) and reason.strip() else _DENY_REASON
    if to_host == "cursor":
        return {"permission": "deny", "agent_message": text}
    hook_specific = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": text,
    }
    return {"hookSpecificOutput": hook_specific}


def _read_policy_stdout(raw_stdout: str) -> dict[str, Any]:
    text = raw_stdout.strip()
    if not text:
        raise ValueError("policy produced empty stdout")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("policy stdout is not valid JSON: {0}".format(exc)) from exc
    if not isinstance(payload, dict):
        raise ValueError("policy stdout must be a JSON object")
    return payload


def _reason_from_payload(payload: dict[str, Any], from_host: str) -> str | None:
    if from_host == "cursor":
        for key in ("agent_message", "user_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None
    hook_specific = payload.get("hookSpecificOutput")
    if isinstance(hook_specific, dict):
        value = hook_specific.get("permissionDecisionReason")
        if isinstance(value, str) and value.strip():
            return value
    value = payload.get("reason")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _legacy_permission(payload: dict[str, Any]) -> str | None:
    decision = payload.get("decision")
    if decision in ("block", "deny"):
        return "deny"
    if decision in ("approve", "allow"):
        return "allow"
    return None


def _normalized_decision(
    payload: dict[str, Any],
    from_host: str,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    permission: str | None = None
    updated_input: dict[str, Any] | None = None

    if from_host == "cursor":
        value = payload.get("permission")
        if isinstance(value, str):
            permission = value
        raw_updated = payload.get("updated_input")
        if isinstance(raw_updated, dict):
            updated_input = raw_updated
    else:
        hook_specific = payload.get("hookSpecificOutput")
        if isinstance(hook_specific, dict):
            value = hook_specific.get("permissionDecision")
            if isinstance(value, str):
                permission = value
        raw_updated = payload.get("updatedInput")
        if isinstance(raw_updated, dict):
            updated_input = raw_updated
        if permission is None:
            permission = _legacy_permission(payload)

    reason = _reason_from_payload(payload, from_host)
    return permission, updated_input, reason


def _permission_for_target(
    permission: str | None,
    updated_input: dict[str, Any] | None,
    to_host: str,
) -> str:
    if permission not in _KNOWN_PERMISSIONS:
        return "deny"
    if to_host == "cursor":
        if permission == "ask":
            return "deny"
        return permission
    if to_host == "claude-code":
        return permission
    if permission == "deny":
        return "deny"
    if permission == "ask":
        return "deny"
    if updated_input is not None:
        return "allow"
    return "allow"


def _render_target_output(
    permission: str,
    updated_input: dict[str, Any] | None,
    reason: str | None,
    to_host: str,
) -> dict[str, Any]:
    if permission == "deny":
        return _explicit_deny(to_host, reason or _DENY_REASON)
    if to_host == "cursor":
        out: dict[str, Any] = {"permission": permission}
        if updated_input is not None:
            out["updated_input"] = updated_input
        if reason:
            out["agent_message"] = reason
        return out
    hook_specific: dict[str, Any] = {"permissionDecision": permission}
    if to_host == "claude-code" or permission == "deny":
        hook_specific["hookEventName"] = "PreToolUse"
    if reason:
        hook_specific["permissionDecisionReason"] = reason
    out = {"hookSpecificOutput": hook_specific}
    if updated_input is not None:
        out["updatedInput"] = updated_input
    return out


def _translate_payload(
    payload: dict[str, Any],
    from_host: str,
    to_host: str,
) -> dict[str, Any]:
    if from_host == to_host:
        return payload
    permission, updated_input, reason = _normalized_decision(payload, from_host)
    target_permission = _permission_for_target(permission, updated_input, to_host)
    if permission not in _KNOWN_PERMISSIONS or permission is None:
        return _explicit_deny(to_host, reason or _DENY_REASON)
    if target_permission == "deny" and permission != "deny":
        return _explicit_deny(to_host, reason or _DENY_REASON)
    return _render_target_output(
        target_permission, updated_input, reason, to_host
    )


def _extract_tool_name(payload: dict[str, Any]) -> str | None:
    value = payload.get("tool_name")
    if isinstance(value, str):
        return value
    hook_specific = payload.get("hookSpecificOutput")
    if isinstance(hook_specific, dict):
        nested = hook_specific.get("tool")
        if isinstance(nested, str):
            return nested
    return None


def _map_tool_name(
    name: str | None, *, source_host: str, runtime_host: str
) -> str | None:
    if name is None or source_host == runtime_host:
        return name
    if source_host == "cursor" and runtime_host in _NESTED_BASH_HOSTS:
        return _TOOL_TO_CURSOR.get(name, name)
    if runtime_host == "cursor" and source_host in _NESTED_BASH_HOSTS:
        return _TOOL_FROM_CURSOR.get(name, name)
    return name


def _extract_command_and_input(
    payload: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    command = payload.get("command")
    if not isinstance(command, str):
        command = None
    raw_input = payload.get("tool_input")
    tool_input = dict(raw_input) if isinstance(raw_input, dict) else None
    if command is None and tool_input is not None:
        nested = tool_input.get("command")
        if isinstance(nested, str):
            command = nested
    if tool_input is None and command is not None:
        tool_input = {"command": command}
    elif tool_input is not None and command is not None and "command" not in tool_input:
        tool_input["command"] = command
    return command, tool_input


def _translate_stdin(
    payload: dict[str, Any],
    from_host: str,
    to_host: str,
) -> dict[str, Any]:
    """Translate target-host PreToolUse stdin into the source dialect."""
    if from_host == to_host:
        return payload
    out = copy.deepcopy(payload)
    tool_name = _map_tool_name(
        _extract_tool_name(payload),
        source_host=from_host,
        runtime_host=to_host,
    )
    command, tool_input = _extract_command_and_input(payload)
    if tool_name is not None:
        out["tool_name"] = tool_name
    if from_host == "cursor":
        if command is not None:
            out["command"] = command
        if tool_input is not None:
            out["tool_input"] = tool_input
        return out
    if tool_input is not None:
        out["tool_input"] = tool_input
    elif command is not None:
        out["tool_input"] = {"command": command}
    out["hook_event_name"] = "PreToolUse"
    return out


def _stdin_for_policy(stdin_data: str, from_host: str, to_host: str) -> str:
    if from_host == to_host:
        return stdin_data
    text = stdin_data.strip()
    if not text:
        raise ValueError("stdin is empty; cannot translate target host input")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("stdin is not valid JSON: {0}".format(exc)) from exc
    if not isinstance(payload, dict):
        raise ValueError("stdin must be a JSON object")
    translated = _translate_stdin(payload, from_host, to_host)
    return json.dumps(translated, ensure_ascii=False)


def _cross_host_fail_closed(from_host: str, to_host: str, reason: str) -> int:
    if from_host == to_host:
        print(reason, file=sys.stderr)
        return 1
    _dump_json(_explicit_deny(to_host, reason))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv)
    from_host, to_host, policy_argv = _parse_adapter_argv(args)
    try:
        stdin_data = _stdin_for_policy(sys.stdin.read(), from_host, to_host)
    except ValueError as exc:
        return _cross_host_fail_closed(from_host, to_host, str(exc))
    timeout = _policy_timeout_sec()
    try:
        completed = subprocess.run(
            policy_argv,
            input=stdin_data,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _cross_host_fail_closed(
            from_host,
            to_host,
            "stdio-decision-v1: policy timed out",
        )
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        return _cross_host_fail_closed(
            from_host,
            to_host,
            "stdio-decision-v1: policy exited {0}".format(completed.returncode),
        )
    try:
        payload = _read_policy_stdout(completed.stdout)
    except ValueError as exc:
        return _cross_host_fail_closed(from_host, to_host, str(exc))
    translated = _translate_payload(payload, from_host, to_host)
    _dump_json(translated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
