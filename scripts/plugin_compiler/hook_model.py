from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Union

from .contracts import hook_group_fields, hook_handler_types, hook_host_shape
from .model import CompilerIssue

_KNOWN_HANDLER_TYPES = frozenset(
    {"command", "prompt", "http", "mcp_tool", "agent"}
)


class HookParseError(RuntimeError):
    """Source JSON is illegal for the given host (``invalid-hook-handler``)."""

    def __init__(self, issue: CompilerIssue):
        super().__init__("{0}: {1}".format(issue.code, issue.message))
        self.issue = issue
        self.code = issue.code


@dataclass(frozen=True)
class ShellForm:
    command: str


@dataclass(frozen=True)
class ExecForm:
    command: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class CommandHandler:
    form: ShellForm | ExecForm
    raw: dict = field(default_factory=dict)
    timeout: object | None = None
    handler_type: str = "command"


@dataclass(frozen=True)
class PromptHandler:
    prompt: str
    raw: dict = field(default_factory=dict)
    handler_type: str = "prompt"


@dataclass(frozen=True)
class HttpHandler:
    url: str | None = None
    method: str | None = None
    headers: dict | None = None
    raw: dict = field(default_factory=dict)
    handler_type: str = "http"


@dataclass(frozen=True)
class McpToolHandler:
    tool: str | None = None
    server: str | None = None
    raw: dict = field(default_factory=dict)
    handler_type: str = "mcp_tool"


@dataclass(frozen=True)
class AgentHandler:
    agent: object | None = None
    raw: dict = field(default_factory=dict)
    handler_type: str = "agent"


@dataclass(frozen=True)
class PassthroughHandler:
    handler_type: str
    raw: dict = field(default_factory=dict)


HookHandlerUnion = Union[
    CommandHandler,
    PromptHandler,
    HttpHandler,
    McpToolHandler,
    AgentHandler,
    PassthroughHandler,
]


@dataclass(frozen=True)
class HookHandler:
    event_name: str
    command: str
    raw: dict


@dataclass(frozen=True)
class HookGroup:
    event_name: str
    payload: dict


@dataclass(frozen=True)
class HookGroupModel:
    group_fields: dict
    handlers: tuple[HookHandlerUnion, ...]


@dataclass(frozen=True)
class HookEventBinding:
    event_name: str
    groups: tuple[HookGroupModel, ...]


@dataclass(frozen=True)
class HookDocument:
    capability_id: str
    source_path: str
    handlers: tuple[HookHandler, ...]
    script_relpaths: tuple[str, ...]
    groups: tuple[HookGroup, ...] = ()
    source_host: str | None = None
    events: tuple[HookEventBinding, ...] = ()


def _invalid_hook_handler_error(
    source_path: str,
    event_name: str,
    handler_type: str,
    source_host: str | None,
) -> HookParseError:
    issue = CompilerIssue(
        severity="block",
        code="invalid-hook-handler",
        path=source_path,
        message=(
            "invalid hook handler type {0!r} for host {1} on event {2!r}"
        ).format(handler_type, source_host, event_name),
    )
    return HookParseError(issue)


def _shape_for(source_host: str | None) -> str | None:
    if not source_host:
        return None
    try:
        return hook_host_shape(source_host)
    except ValueError:
        return None


def _allowed_handler_types(source_host: str | None) -> frozenset[str] | None:
    if not source_host or _shape_for(source_host) is None:
        return None
    return frozenset(hook_handler_types(source_host))


def _group_field_names(source_host: str | None) -> tuple[str, ...]:
    if source_host and _shape_for(source_host) is not None:
        return hook_group_fields(source_host)
    names: list[str] = []
    for host in ("cursor", "claude-code", "codex"):
        for name in hook_group_fields(host):
            if name not in names:
                names.append(name)
    return tuple(names)


def _extract_group_fields(group: dict, source_host: str | None) -> dict:
    fields: dict = {}
    for name in _group_field_names(source_host):
        if name in group:
            fields[name] = copy.deepcopy(group[name])
    return fields


def _extract_flat_handler_group_fields(group: dict) -> dict:
    matcher = group.get("matcher")
    if matcher is None:
        return {}
    return {"matcher": copy.deepcopy(matcher)}


def _strip_matcher_from_raw(raw: dict) -> dict:
    updated = copy.deepcopy(raw)
    updated.pop("matcher", None)
    return updated


def _handler_without_matcher(handler: HookHandlerUnion) -> HookHandlerUnion:
    if "matcher" not in handler.raw:
        return handler
    return replace(handler, raw=_strip_matcher_from_raw(handler.raw))


def _infer_handler_type(item: dict) -> str | None:
    typed = item.get("type")
    if isinstance(typed, str) and typed.strip():
        return typed.strip()
    command = item.get("command")
    if isinstance(command, str) and command:
        return "command"
    if isinstance(item.get("prompt"), str):
        return "prompt"
    if isinstance(item.get("url"), str):
        return "http"
    if "tool" in item or "server" in item:
        return "mcp_tool"
    if "agent" in item:
        return "agent"
    return None


def _arg_token(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _parse_command_handler(
    item: dict, raw: dict, source_path: str, event_name: str
) -> CommandHandler:
    command = item.get("command")
    args = item.get("args")
    timeout = item.get("timeout")
    if isinstance(args, list) and args:
        if not isinstance(command, str) or not command:
            raise RuntimeError(
                "{0}: command hook for {1!r} must be a non-empty string".format(
                    source_path, event_name
                )
            )
        form: ShellForm | ExecForm = ExecForm(
            command=command,
            args=tuple(_arg_token(part) for part in args),
        )
        return CommandHandler(form=form, raw=raw, timeout=timeout)
    if not isinstance(command, str) or not command:
        raise RuntimeError(
            "{0}: command hook for {1!r} must be a non-empty string".format(
                source_path, event_name
            )
        )
    return CommandHandler(
        form=ShellForm(command=command), raw=raw, timeout=timeout
    )


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _build_handler(
    handler_type: str,
    item: dict,
    raw: dict,
    source_path: str,
    event_name: str,
) -> HookHandlerUnion:
    if handler_type == "command":
        return _parse_command_handler(item, raw, source_path, event_name)
    if handler_type == "prompt":
        prompt = item.get("prompt")
        if not isinstance(prompt, str):
            prompt = "" if prompt is None else str(prompt)
        return PromptHandler(prompt=prompt, raw=raw)
    if handler_type == "http":
        headers = item.get("headers")
        return HttpHandler(
            url=_optional_str(item.get("url")),
            method=_optional_str(item.get("method")),
            headers=copy.deepcopy(headers) if isinstance(headers, dict) else None,
            raw=raw,
        )
    if handler_type == "mcp_tool":
        return McpToolHandler(
            tool=_optional_str(item.get("tool")),
            server=_optional_str(item.get("server")),
            raw=raw,
        )
    if handler_type == "agent":
        return AgentHandler(agent=copy.deepcopy(item.get("agent")), raw=raw)
    return PassthroughHandler(handler_type=handler_type, raw=raw)


def _legacy_command(handler: CommandHandler) -> str:
    return handler.form.command


def _parse_handler_item(
    item: dict,
    *,
    source_path: str,
    event_name: str,
    source_host: str | None,
    allowed: frozenset[str] | None,
) -> HookHandlerUnion | None:
    handler_type = _infer_handler_type(item)
    if handler_type is None:
        return None
    if allowed is not None and handler_type not in allowed:
        raise _invalid_hook_handler_error(
            source_path, event_name, handler_type, source_host
        )
    if handler_type not in _KNOWN_HANDLER_TYPES and allowed is None:
        return None
    raw = copy.deepcopy(item)
    return _build_handler(handler_type, item, raw, source_path, event_name)


def _append_legacy(
    legacy_handlers: list[HookHandler],
    event_name: str,
    handler: HookHandlerUnion,
) -> None:
    if not isinstance(handler, CommandHandler):
        return
    legacy_handlers.append(
        HookHandler(
            event_name=event_name,
            command=_legacy_command(handler),
            raw=copy.deepcopy(handler.raw),
        )
    )


def _is_nested_group(group: dict, shape: str | None) -> bool:
    items = group.get("hooks")
    if isinstance(items, list):
        return True
    if shape == "nested":
        return True
    if shape == "flat":
        return False
    return "hooks" in group


def parse_hook_payload(
    payload: object, *, source_path: str, source_host: str | None = None
) -> HookDocument:
    if not isinstance(payload, dict):
        raise RuntimeError("{0}: root must be a JSON object".format(source_path))
    hooks = payload.get("hooks")
    if hooks is None:
        return HookDocument(
            capability_id="hook:{0}".format(source_path),
            source_path=source_path,
            handlers=(),
            script_relpaths=(),
            source_host=source_host,
        )
    if not isinstance(hooks, dict):
        raise RuntimeError("{0}: hooks must be a JSON object".format(source_path))
    shape = _shape_for(source_host)
    allowed = _allowed_handler_types(source_host)
    legacy_handlers: list[HookHandler] = []
    stored_groups: list[HookGroup] = []
    event_bindings: list[HookEventBinding] = []
    for event_name, groups in hooks.items():
        if not isinstance(event_name, str):
            raise RuntimeError(
                "{0}: hook event names must be strings".format(source_path)
            )
        if not isinstance(groups, list):
            raise RuntimeError(
                "{0}: hook event {1!r} must be an array".format(
                    source_path, event_name
                )
            )
        event_groups: list[HookGroupModel] = []
        for group in groups:
            if not isinstance(group, dict):
                raise RuntimeError(
                    "{0}: hook group for {1!r} must be an object".format(
                        source_path, event_name
                    )
                )
            stored_groups.append(
                HookGroup(event_name=event_name, payload=copy.deepcopy(group))
            )
            if not _is_nested_group(group, shape):
                group_fields = _extract_flat_handler_group_fields(group)
                handler = _parse_handler_item(
                    group,
                    source_path=source_path,
                    event_name=event_name,
                    source_host=source_host,
                    allowed=allowed,
                )
                parsed = () if handler is None else (_handler_without_matcher(handler),)
                if handler is not None:
                    _append_legacy(legacy_handlers, event_name, handler)
                event_groups.append(
                    HookGroupModel(group_fields=group_fields, handlers=parsed)
                )
                continue
            items = group.get("hooks")
            if items is None:
                handler = _parse_handler_item(
                    group,
                    source_path=source_path,
                    event_name=event_name,
                    source_host=source_host,
                    allowed=allowed,
                )
                parsed = () if handler is None else (handler,)
                if handler is not None:
                    _append_legacy(legacy_handlers, event_name, handler)
                event_groups.append(
                    HookGroupModel(
                        group_fields=_extract_group_fields(group, source_host),
                        handlers=parsed,
                    )
                )
                continue
            if not isinstance(items, list):
                raise RuntimeError(
                    "{0}: hooks[{1!r}] group hooks must be an array".format(
                        source_path, event_name
                    )
                )
            parsed_handlers: list[HookHandlerUnion] = []
            for item in items:
                if not isinstance(item, dict):
                    raise RuntimeError(
                        "{0}: hook handler for {1!r} must be an object".format(
                            source_path, event_name
                        )
                    )
                handler = _parse_handler_item(
                    item,
                    source_path=source_path,
                    event_name=event_name,
                    source_host=source_host,
                    allowed=allowed,
                )
                if handler is None:
                    continue
                parsed_handlers.append(_handler_without_matcher(handler))
                _append_legacy(legacy_handlers, event_name, handler)
            event_groups.append(
                HookGroupModel(
                    group_fields=_extract_group_fields(group, source_host),
                    handlers=tuple(parsed_handlers),
                )
            )
        event_bindings.append(
            HookEventBinding(event_name=event_name, groups=tuple(event_groups))
        )
    return HookDocument(
        capability_id="hook:{0}".format(source_path),
        source_path=source_path,
        handlers=tuple(legacy_handlers),
        script_relpaths=(),
        groups=tuple(stored_groups),
        source_host=source_host,
        events=tuple(event_bindings),
    )
