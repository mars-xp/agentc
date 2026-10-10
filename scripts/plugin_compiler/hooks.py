from __future__ import annotations

import copy
import json
import shlex
from dataclasses import replace
from pathlib import Path

from .compiled_rules import RULE_LOADER_COMMANDS, rule_loader_command
from .contracts import (
    contains_unprefixed_relative,
    has_unsupported_shell_syntax,
    hook_cross_host_action,
    hook_event_decision_stdio,
    hook_event_not_equivalent,
    hook_host_shape,
    is_plugin_relative_token,
    mapped_hook_events,
    needs_plugin_root_prefix,
    rewrite_hook_plugin_root,
)
from .contracts.load import load_contracts
from .hook_model import (
    AgentHandler,
    CommandHandler,
    ExecForm,
    HookDocument,
    HookEventBinding,
    HookGroup,
    HookGroupModel,
    HookHandler,
    HookHandlerUnion,
    HttpHandler,
    McpToolHandler,
    PassthroughHandler,
    PromptHandler,
    ShellForm,
    parse_hook_payload,
)
from .model import CompilerIssue, Target

_CLAUDE_PLUGIN_ROOT_PREFIX = "${CLAUDE_PLUGIN_ROOT}/"
_OUTPUT_FLAGS = frozenset({"-o", "--output", "--out"})
_OFFICIAL_HOOK_FILES = frozenset({"hooks.json", "hooks/hooks.json"})
_INTERPRETERS = frozenset(
    {
        "python",
        "python2",
        "python3",
        "bash",
        "sh",
        "node",
        "nodejs",
        "ruby",
        "perl",
    }
)


def is_official_hook_source(source_path: str) -> bool:
    """True for official default sidecars and inline plugin.json hooks.

    Codex default discovery uses both ``./hooks.json`` and ``hooks/hooks.json``.
    Custom declared ``.json`` hook files are loaded by ``load_hook_document``.
    """
    from .manifests import is_plugin_manifest_source

    posix = source_path.replace("\\", "/")
    if posix in _OFFICIAL_HOOK_FILES:
        return True
    return is_plugin_manifest_source(posix)


def _source_path_for(path: Path, plugin_dir: Path) -> str:
    try:
        return path.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(path).replace("\\", "/")).as_posix()


def _plugin_root_path_prefixes() -> tuple[str, ...]:
    data = load_contracts().get("plugin_root")
    prefixes: list[str] = []
    if isinstance(data, dict):
        placeholders = data.get("placeholders")
        if isinstance(placeholders, list):
            for item in placeholders:
                if not isinstance(item, dict):
                    continue
                token = item.get("token")
                if isinstance(token, str) and token.startswith("${") and token.endswith("}"):
                    prefix = token if token.endswith("/") else token + "/"
                    if prefix not in prefixes:
                        prefixes.append(prefix)
    if _CLAUDE_PLUGIN_ROOT_PREFIX not in prefixes:
        prefixes.insert(0, _CLAUDE_PLUGIN_ROOT_PREFIX)
    return tuple(prefixes)


def _strip_plugin_root_prefix(posix: str) -> str:
    for prefix in _plugin_root_path_prefixes():
        if posix.startswith(prefix):
            return posix[len(prefix) :]
    return posix


def _starts_with_plugin_root(text: str) -> bool:
    return is_plugin_relative_token(text) and not needs_plugin_root_prefix(text)


def _normalize_script_relpath(raw: str) -> str:
    posix = raw.replace("\\", "/")
    posix = _strip_plugin_root_prefix(posix)
    while posix.startswith("./"):
        posix = posix[2:]
    return posix


def _is_interpreter_token(token: str) -> bool:
    base = token.replace("\\", "/").rsplit("/", 1)[-1]
    if base in _INTERPRETERS:
        return True
    return base.startswith("python")


def _is_output_flag(token: str) -> bool:
    return token in _OUTPUT_FLAGS


def _is_short_skippable_flag(token: str) -> bool:
    if _is_output_flag(token):
        return False
    return len(token) >= 2 and token.startswith("-") and not token.startswith("--")


def _is_plugin_relative_token(token: str) -> bool:
    return is_plugin_relative_token(token)


def _tokens_skipping_output_flags(
    tokens: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    kept: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if _is_output_flag(token):
            skip_next = True
            continue
        kept.append(token)
    return tuple(kept)


def _first_executable_token(tokens: tuple[str, ...] | list[str]) -> str | None:
    if not tokens:
        return None
    first = tokens[0]
    if _is_plugin_relative_token(first):
        return first
    if not _is_interpreter_token(first):
        return None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if _is_short_skippable_flag(token):
            index += 1
            continue
        if _is_plugin_relative_token(token):
            return token
        return None
    return None


def _has_unsupported_shell_syntax(command: str) -> bool:
    return has_unsupported_shell_syntax(command)


def _add_script_relpath(
    found: list[str], seen: set[str], raw: str, *, allow_parent: bool
) -> None:
    text = raw.strip()
    for stop in (";", "|", "&", ">", "<"):
        if stop in text:
            text = text.split(stop, 1)[0]
    text = text.strip()
    if not (
        _starts_with_plugin_root(text)
        or text.startswith("./")
        or text.startswith("hooks/")
        or (allow_parent and text.startswith("../"))
    ):
        return
    posix = _normalize_script_relpath(text)
    if not posix or posix in seen:
        return
    seen.add(posix)
    found.append(posix)


def _relpaths_from_tokens(
    tokens: tuple[str, ...] | list[str], *, collect_all_relative: bool
) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    scanned = _tokens_skipping_output_flags(tokens)
    for token in scanned:
        if _starts_with_plugin_root(token.strip()):
            _add_script_relpath(found, seen, token, allow_parent=True)
    if collect_all_relative:
        for token in scanned:
            if _is_plugin_relative_token(token):
                _add_script_relpath(found, seen, token, allow_parent=True)
        return tuple(found)
    executable = _first_executable_token(scanned)
    if executable is not None:
        _add_script_relpath(found, seen, executable, allow_parent=True)
    return tuple(found)


def plugin_relpaths_from_command(command: str) -> tuple[str, ...]:
    """Extract plugin-relative script paths from a hook command string.

    Accepts plugin-root placeholders from ``plugin_root.json``, ``./``,
    ``hooks/``, and ``../``. Uses ``shlex`` so quoted paths with spaces stay
    intact. Collects every plugin-root token plus the first executable plugin
    path, so argv decoys like ``--outdir ./build/reports`` are not treated as
    required scripts. Shell syntax outside this subset yields no paths.
    Does not execute.
    """
    if _has_unsupported_shell_syntax(command):
        return ()
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = [part for part in command.split() if part.strip()]
    return _relpaths_from_tokens(tokens, collect_all_relative=False)


def plugin_relpaths_from_handler(handler: HookHandlerUnion) -> tuple[str, ...]:
    """Extract plugin-relative script paths from a parsed hook handler."""
    if not isinstance(handler, CommandHandler):
        return ()
    form = handler.form
    if isinstance(form, ExecForm):
        tokens = (form.command,) + tuple(form.args)
        return _relpaths_from_tokens(tokens, collect_all_relative=True)
    if isinstance(form, ShellForm):
        return plugin_relpaths_from_command(form.command)
    return ()


def _union_handlers(document: HookDocument) -> tuple[HookHandlerUnion, ...]:
    found: list[HookHandlerUnion] = []
    for event in document.events:
        for group in event.groups:
            found.extend(group.handlers)
    return tuple(found)


def _collect_script_relpaths_from_handlers(
    handlers: tuple[HookHandlerUnion, ...],
) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for handler in handlers:
        for relpath in plugin_relpaths_from_handler(handler):
            if relpath in seen:
                continue
            seen.add(relpath)
            found.append(relpath)
        for relpath in _stdio_adapter_relpaths_from_handler(handler):
            if relpath in seen:
                continue
            seen.add(relpath)
            found.append(relpath)
    return tuple(found)


def _collect_script_relpaths(commands: tuple[str, ...]) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for command in commands:
        for relpath in plugin_relpaths_from_command(command):
            if relpath in seen:
                continue
            seen.add(relpath)
            found.append(relpath)
    return tuple(found)


def _script_relpaths_for_document(document: HookDocument) -> tuple[str, ...]:
    unions = _union_handlers(document)
    if unions:
        return _collect_script_relpaths_from_handlers(unions)
    return _collect_script_relpaths(tuple(handler.command for handler in document.handlers))


def load_hook_document(
    path: Path, plugin_dir: Path, source_host: str | None = None
) -> HookDocument:
    from .manifests import is_plugin_manifest_source

    source_path = _source_path_for(path, plugin_dir)
    posix = source_path.replace("\\", "/")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("{0}: invalid JSON: {1}".format(source_path, exc)) from exc
    if is_plugin_manifest_source(posix):
        if not isinstance(payload, dict):
            raise RuntimeError("{0}: root must be a JSON object".format(source_path))
        inline = payload.get("hooks")
        if not isinstance(inline, dict):
            raise RuntimeError(
                "{0}: inline hooks must be a JSON object".format(source_path)
            )
        if "hooks" in inline and isinstance(inline.get("hooks"), dict):
            payload = inline
        else:
            payload = {"hooks": inline}
    parsed = parse_hook_payload(
        payload, source_path=source_path, source_host=source_host
    )
    return replace(parsed, script_relpaths=_script_relpaths_for_document(parsed))


def mapped_events(
    name: str, target: Target, source_host: str | None
) -> tuple[str, ...] | None:
    if source_host == target.value:
        return (name,)
    direct = mapped_hook_events(name, source_host=source_host, target=target)
    if direct is not None:
        return direct
    if source_host is None:
        return None
    if source_host != target.value:
        try:
            if (
                hook_host_shape(source_host) == "nested"
                and hook_host_shape(target.value) == "nested"
            ):
                return (name,)
        except ValueError:
            pass
    rows = load_contracts().get("hook_events", {}).get("rows")
    if not isinstance(rows, list):
        return None
    compiled = load_contracts().get("hook_events", {}).get("compiled_rule_events")
    compiled_events = (
        frozenset(str(item) for item in compiled)
        if isinstance(compiled, list)
        else frozenset()
    )
    candidates: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("from_host") != target.value:
            continue
        if row.get("to_host") != source_host:
            continue
        if row.get("no-equivalent"):
            continue
        events = row.get("to_events")
        if not isinstance(events, list) or name not in events:
            continue
        from_event = row.get("from_event")
        if isinstance(from_event, str) and from_event:
            candidates.append(from_event)
    if candidates:
        for candidate in candidates:
            if candidate.lower() == name.lower():
                return (candidate,)
        if len(candidates) == 1:
            return (candidates[0],)
        return None
    if name in compiled_events:
        return None
    return None


def _unmapped_hook_event_issue(
    document: HookDocument, event_name: str, target: Target
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="unmapped-hook-event",
        path=document.source_path,
        message=(
            "unmapped hook event {0!r} for target {1}; "
            "no row in hook_events.json; "
            "{1} compilation fails; fix via the authoring/repair program; "
            "to declare a host gap use packaging/platform-overrides.json "
            "with a human-confirmed no-equivalent reason"
        ).format(event_name, target.value),
    )


def _unmapped_hook_handler_type_issue(
    document: HookDocument,
    handler_type: str,
    target: Target,
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="unmapped-hook-handler-type",
        path=document.source_path,
        message=(
            "unmapped hook handler type {0!r} for target {1}; "
            "no cross-host action in hook_handler_types.json; "
            "{1} compilation fails; fix via the authoring/repair program; "
            "to declare a host gap use packaging/platform-overrides.json "
            "with a human-confirmed no-equivalent reason"
        ).format(handler_type, target.value),
    )


def _unmapped_plugin_root_issue(
    document: HookDocument, command: str, target: Target
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="unmapped-plugin-root",
        path=document.source_path,
        message=(
            "unmapped plugin-root placeholder in {0!r} for target {1}; "
            "no mapping in plugin_root.json"
        ).format(command, target.value),
    )


def _shell_parse_unsafe(text: str) -> bool:
    if has_unsupported_shell_syntax(text):
        return True
    try:
        shlex.split(text, posix=True)
    except ValueError:
        return True
    return False


def _append_rewrite_failure_issue(
    document: HookDocument,
    text: str,
    target: Target,
    issues: list[CompilerIssue],
) -> None:
    if _shell_parse_unsafe(text) and contains_unprefixed_relative(text):
        issues.append(
            CompilerIssue(
                severity="block",
                code="unsafe-plugin-relative-shell",
                path=document.source_path,
                message=(
                    "unsafe plugin-relative path in shell command {0!r} for target {1}"
                ).format(text, target.value),
            )
        )
    else:
        issues.append(_unmapped_plugin_root_issue(document, text, target))


_STDIO_DECISION_TYPE = "stdio-decision"
_STDIO_DECISION_VERSION = 1


def _parse_stdio_adapter_ref(handler: HookHandlerUnion) -> dict | None:
    raw = getattr(handler, "raw", None)
    if not isinstance(raw, dict):
        return None
    value = raw.get("stdio_adapter")
    if not isinstance(value, dict):
        return None
    path = value.get("path")
    if value.get("type") != _STDIO_DECISION_TYPE:
        return None
    if value.get("version") != _STDIO_DECISION_VERSION:
        return None
    if not isinstance(path, str):
        return None
    normalized = path.strip().replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or ".." in normalized.split("/")
    ):
        return None
    return {
        "type": _STDIO_DECISION_TYPE,
        "version": _STDIO_DECISION_VERSION,
        "path": normalized,
    }


def _handler_has_stdio_adapter(handler: HookHandlerUnion) -> bool:
    return _parse_stdio_adapter_ref(handler) is not None


def _resolve_stdio_adapter_ref(
    handler: HookHandlerUnion,
    plugin_dir: Path | None,
) -> dict | None:
    ref = _parse_stdio_adapter_ref(handler)
    if ref is None:
        return None
    if plugin_dir is None:
        return None
    adapter_path = plugin_dir / ref["path"]
    if not adapter_path.is_file():
        return None
    return ref


def _stdio_adapter_relpaths_from_handler(
    handler: HookHandlerUnion,
) -> tuple[str, ...]:
    ref = _parse_stdio_adapter_ref(handler)
    if ref is None:
        return ()
    return (ref["path"],)


def _needs_stdio_decision_wrap(
    *,
    from_host: str,
    target: Target,
    event_name: str,
) -> bool:
    return (
        from_host != target.value
        and hook_event_decision_stdio(
            event_name, source_host=from_host, target=target
        )
    )


def _wrap_stdio_decision_command(
    document: HookDocument,
    *,
    ref: dict,
    from_host: str,
    target: Target,
    policy_command: str,
    issues: list[CompilerIssue],
) -> str | None:
    adapter_cmd = _rewrite_text_or_block(
        document,
        "python3 {0}".format(ref["path"]),
        target,
        issues,
    )
    if adapter_cmd is None:
        return None
    return "{0} --from {1} --to {2} -- {3}".format(
        adapter_cmd,
        from_host,
        target.value,
        policy_command,
    )


def _wrap_stdio_decision_command_handler(
    handler: CommandHandler,
    document: HookDocument,
    *,
    ref: dict,
    from_host: str,
    target: Target,
    issues: list[CompilerIssue],
) -> CommandHandler | None:
    form = handler.form
    if isinstance(form, ShellForm):
        wrapped = _wrap_stdio_decision_command(
            document,
            ref=ref,
            from_host=from_host,
            target=target,
            policy_command=form.command,
            issues=issues,
        )
        if wrapped is None:
            return None
        return replace(
            handler,
            form=ShellForm(command=wrapped),
            raw=_sync_command_raw(handler.raw, command=wrapped),
        )
    if not isinstance(form, ExecForm):
        return handler
    adapter_cmd = _rewrite_text_or_block(
        document,
        "python3 {0}".format(ref["path"]),
        target,
        issues,
    )
    if adapter_cmd is None:
        return None
    try:
        adapter_tokens = shlex.split(adapter_cmd, posix=True)
    except ValueError:
        adapter_tokens = [part for part in adapter_cmd.split() if part.strip()]
    if not adapter_tokens:
        return None
    new_command = adapter_tokens[0]
    new_args = tuple(
        adapter_tokens[1:]
        + ["--from", from_host, "--to", target.value, "--", form.command]
        + list(form.args)
    )
    return replace(
        handler,
        form=ExecForm(command=new_command, args=new_args),
        raw=_sync_exec_raw(handler.raw, command=new_command, args=new_args),
    )


def _decision_stdio_not_equivalent_issue(
    document: HookDocument,
    event_name: str,
    target: Target,
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="hook-decision-stdio-not-equivalent",
        path=document.source_path,
        message=(
            "hook event {0!r} command handler stdin/stdout is not equivalent "
            "for target {1}; decision output uses host-specific field names "
            "(permission/updated_input vs permissionDecision/updatedInput); "
            "fix source by adding a stdio-decision v1 object reference and "
            "adapter file, removing the decision hook event, or stop and ask "
            "a human whether the target host lacks an equivalent hook"
        ).format(event_name, target.value),
    )


def _hook_event_not_equivalent_issue(
    document: HookDocument,
    event_name: str,
    target: Target,
    *,
    to_events: tuple[str, ...],
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="hook-event-not-equivalent",
        path=document.source_path,
        message=(
            "hook event {0!r} is not equivalent to {1} on {2}; "
            "Cursor fires before MCP execution while Codex PermissionRequest "
            "covers permission prompts (including Bash); "
            "{2} compilation fails; fix via the authoring/repair program"
        ).format(event_name, ", ".join(to_events), target.value),
    )


def _handler_type_name(handler: HookHandlerUnion) -> str:
    return handler.handler_type


def _rewrite_text_or_block(
    document: HookDocument,
    text: str,
    target: Target,
    issues: list[CompilerIssue],
) -> str | None:
    rewritten = rewrite_hook_plugin_root(text, target)
    if rewritten is None:
        _append_rewrite_failure_issue(document, text, target, issues)
    return rewritten


def _remap_command_handler(
    handler: CommandHandler,
    document: HookDocument,
    target: Target,
    issues: list[CompilerIssue],
) -> CommandHandler | None:
    form = handler.form
    if isinstance(form, ShellForm):
        rewritten = _rewrite_text_or_block(document, form.command, target, issues)
        if rewritten is None:
            return None
        return replace(
            handler,
            form=ShellForm(command=rewritten),
            raw=_sync_command_raw(handler.raw, command=rewritten),
        )
    rewritten_command = _rewrite_text_or_block(
        document, form.command, target, issues
    )
    if rewritten_command is None:
        return None
    rewritten_args: list[str] = []
    for arg in form.args:
        rewritten_arg = _rewrite_text_or_block(document, arg, target, issues)
        if rewritten_arg is None:
            return None
        rewritten_args.append(rewritten_arg)
    return replace(
        handler,
        form=ExecForm(command=rewritten_command, args=tuple(rewritten_args)),
        raw=_sync_exec_raw(
            handler.raw,
            command=rewritten_command,
            args=tuple(rewritten_args),
        ),
    )


def _sync_command_raw(raw: dict, *, command: str) -> dict:
    updated = copy.deepcopy(raw)
    updated["command"] = command
    return updated


def _sync_exec_raw(raw: dict, *, command: str, args: tuple[str, ...]) -> dict:
    updated = copy.deepcopy(raw)
    updated["command"] = command
    updated["args"] = list(args)
    return updated


def _remap_handler(
    handler: HookHandlerUnion,
    *,
    document: HookDocument,
    target: Target,
    from_host: str,
    event_name: str,
    issues: list[CompilerIssue],
    plugin_dir: Path | None = None,
) -> HookHandlerUnion | None:
    handler_type = _handler_type_name(handler)
    needs_wrap = _needs_stdio_decision_wrap(
        from_host=from_host, target=target, event_name=event_name
    )
    adapter_ref = None
    if needs_wrap and isinstance(handler, CommandHandler):
        adapter_ref = _resolve_stdio_adapter_ref(handler, plugin_dir)
        if adapter_ref is None:
            issues.append(
                _decision_stdio_not_equivalent_issue(document, event_name, target)
            )
            return None
    action = hook_cross_host_action(
        handler_type, from_host=from_host, to_host=target.value
    )
    if action is None:
        issues.append(
            _unmapped_hook_handler_type_issue(document, handler_type, target)
        )
        return None
    if action in ("rewrite-plugin-root", "passthrough-fields"):
        if isinstance(handler, CommandHandler):
            remapped = _remap_command_handler(handler, document, target, issues)
            if remapped is None:
                return None
            if adapter_ref is not None:
                return _wrap_stdio_decision_command_handler(
                    remapped,
                    document,
                    ref=adapter_ref,
                    from_host=from_host,
                    target=target,
                    issues=issues,
                )
            return remapped
        if action == "rewrite-plugin-root":
            issues.append(
                _unmapped_hook_handler_type_issue(document, handler_type, target)
            )
            return None
        return replace(handler, raw=copy.deepcopy(handler.raw))
    return replace(handler, raw=copy.deepcopy(handler.raw))


_MATCHER_TO_CURSOR = {"Bash": "Shell", "^Bash$": "^Shell$"}
_MATCHER_FROM_CURSOR = {"Shell": "Bash", "^Shell$": "^Bash$"}
_NESTED_BASH_HOSTS = frozenset({"codex", "claude-code"})


def _matcher_needs_tool_rename(from_host: str, to_host: str) -> bool:
    if from_host == "cursor" and to_host in _NESTED_BASH_HOSTS:
        return True
    if to_host == "cursor" and from_host in _NESTED_BASH_HOSTS:
        return True
    return False


def _remap_matcher_string(
    value: str, *, from_host: str, to_host: str
) -> str | None:
    if not _matcher_needs_tool_rename(from_host, to_host):
        return value
    if to_host == "cursor":
        return _MATCHER_TO_CURSOR.get(value)
    return _MATCHER_FROM_CURSOR.get(value)


def _matcher_value_unmapped_issue(
    document: HookDocument,
    matcher: object,
    target: Target,
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="hook-matcher-value-unmapped",
        path=document.source_path,
        message=(
            "hook matcher {0!r} has no equivalent on {1}; "
            "only Shell/Bash and ^Shell$/^Bash$ are translatable"
        ).format(matcher, target.value),
    )


def _collect_group_matcher(group: HookGroupModel) -> object | None:
    if "matcher" in group.group_fields:
        return group.group_fields["matcher"]
    for handler in group.handlers:
        raw = getattr(handler, "raw", None)
        if isinstance(raw, dict) and "matcher" in raw:
            return raw["matcher"]
    return None


def _strip_matcher_from_handler(handler: HookHandlerUnion) -> HookHandlerUnion:
    raw = getattr(handler, "raw", None)
    if not isinstance(raw, dict) or "matcher" not in raw:
        return handler
    updated = copy.deepcopy(raw)
    updated.pop("matcher", None)
    return replace(handler, raw=updated)


def _remap_group_model(
    group: HookGroupModel,
    *,
    document: HookDocument,
    target: Target,
    from_host: str,
    event_name: str,
    issues: list[CompilerIssue],
    plugin_dir: Path | None = None,
) -> HookGroupModel | None:
    remapped_handlers: list[HookHandlerUnion] = []
    for handler in group.handlers:
        remapped = _remap_handler(
            handler,
            document=document,
            target=target,
            from_host=from_host,
            event_name=event_name,
            issues=issues,
            plugin_dir=plugin_dir,
        )
        if remapped is not None:
            remapped_handlers.append(remapped)
    if not remapped_handlers and group.handlers:
        return None

    to_host = target.value
    group_fields = copy.deepcopy(group.group_fields)
    matcher = _collect_group_matcher(group)
    if matcher is not None:
        if isinstance(matcher, str):
            remapped_matcher = _remap_matcher_string(
                matcher, from_host=from_host, to_host=to_host
            )
            if remapped_matcher is None:
                issues.append(
                    _matcher_value_unmapped_issue(document, matcher, target)
                )
                group_fields["matcher"] = matcher
            else:
                group_fields["matcher"] = remapped_matcher
        else:
            group_fields["matcher"] = copy.deepcopy(matcher)

    cleaned_handlers = tuple(
        _strip_matcher_from_handler(handler) for handler in remapped_handlers
    )
    return HookGroupModel(
        group_fields=group_fields,
        handlers=cleaned_handlers,
    )


def _legacy_command(handler: CommandHandler) -> str:
    return handler.form.command


def _legacy_views_from_events(
    events: tuple[HookEventBinding, ...],
    *,
    shape: str,
) -> tuple[tuple[HookHandler, ...], tuple[HookGroup, ...]]:
    handlers: list[HookHandler] = []
    groups: list[HookGroup] = []
    for event in events:
        for group in event.groups:
            payload = _group_model_to_payload(group, shape=shape)
            groups.append(
                HookGroup(event_name=event.event_name, payload=copy.deepcopy(payload))
            )
            for handler in group.handlers:
                if not isinstance(handler, CommandHandler):
                    continue
                handlers.append(
                    HookHandler(
                        event_name=event.event_name,
                        command=_legacy_command(handler),
                        raw=copy.deepcopy(handler.raw),
                    )
                )
    return tuple(handlers), tuple(groups)


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _handler_semantic_key(handler: HookHandlerUnion) -> tuple:
    if isinstance(handler, CommandHandler):
        if isinstance(handler.form, ExecForm):
            form_key = ("exec", handler.form.command, tuple(handler.form.args))
        else:
            form_key = ("shell", handler.form.command, ())
        return ("command", form_key, handler.timeout)
    if isinstance(handler, PromptHandler):
        return ("prompt", handler.prompt)
    if isinstance(handler, HttpHandler):
        return (
            "http",
            handler.url,
            handler.method,
            _stable_json(handler.headers),
        )
    if isinstance(handler, McpToolHandler):
        return ("mcp_tool", handler.tool, handler.server)
    if isinstance(handler, AgentHandler):
        return ("agent", _stable_json(handler.agent))
    return (handler.handler_type, _stable_json(handler.raw))


def _group_semantic_key(group: HookGroupModel) -> tuple:
    return (
        _stable_json(group.group_fields),
        tuple(_handler_semantic_key(handler) for handler in group.handlers),
    )


def _dedupe_mapped_events(
    events: list[HookEventBinding],
) -> tuple[HookEventBinding, ...]:
    order: list[str] = []
    groups_by_event: dict[str, list[HookGroupModel]] = {}
    seen: dict[str, set[tuple]] = {}
    for event in events:
        name = event.event_name
        if name not in groups_by_event:
            order.append(name)
            groups_by_event[name] = []
            seen[name] = set()
        for group in event.groups:
            key = _group_semantic_key(group)
            if key in seen[name]:
                continue
            seen[name].add(key)
            groups_by_event[name].append(group)
    return tuple(
        HookEventBinding(event_name=name, groups=tuple(groups_by_event[name]))
        for name in order
    )


def remap_document(
    document: HookDocument,
    target: Target,
    plugin_dir: Path | None = None,
) -> tuple[HookDocument, tuple[CompilerIssue, ...]]:
    if not document.events:
        return _remap_document_legacy(document, target, plugin_dir=plugin_dir)
    issues: list[CompilerIssue] = []
    from_host = document.source_host or target.value
    remapped_events: list[HookEventBinding] = []
    for event in document.events:
        mapped = mapped_events(event.event_name, target, document.source_host)
        if mapped is None:
            issues.append(
                _unmapped_hook_event_issue(document, event.event_name, target)
            )
            continue
        if hook_event_not_equivalent(
            event.event_name, source_host=document.source_host, target=target
        ):
            issues.append(
                _hook_event_not_equivalent_issue(
                    document,
                    event.event_name,
                    target,
                    to_events=mapped,
                )
            )
            continue
        remapped_groups: list[HookGroupModel] = []
        for group in event.groups:
            remapped_group = _remap_group_model(
                group,
                document=document,
                target=target,
                from_host=from_host,
                event_name=event.event_name,
                issues=issues,
                plugin_dir=plugin_dir,
            )
            if remapped_group is not None:
                remapped_groups.append(remapped_group)
        if not remapped_groups and event.groups:
            continue
        for event_name in mapped:
            remapped_events.append(
                HookEventBinding(
                    event_name=event_name,
                    groups=tuple(copy.deepcopy(remapped_groups)),
                )
            )
    remapped_events_tuple = _dedupe_mapped_events(remapped_events)
    shape = hook_host_shape(target.value)
    handlers, groups = _legacy_views_from_events(
        remapped_events_tuple, shape=shape
    )
    remapped = HookDocument(
        capability_id=document.capability_id,
        source_path=document.source_path,
        handlers=handlers,
        script_relpaths=document.script_relpaths,
        groups=groups,
        source_host=target.value,
        events=remapped_events_tuple,
    )
    return remapped, tuple(issues)


def _handler_with_command(handler: HookHandler, command: str) -> dict:
    raw = copy.deepcopy(handler.raw)
    if isinstance(raw.get("command"), str):
        raw["command"] = command
    items = raw.get("hooks")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("command") == handler.command:
                item["command"] = command
    return raw


def _remap_document_legacy(
    document: HookDocument,
    target: Target,
    plugin_dir: Path | None = None,
) -> tuple[HookDocument, tuple[CompilerIssue, ...]]:
    handlers: list[HookHandler] = []
    issues: list[CompilerIssue] = []
    for handler in document.handlers:
        mapped = mapped_events(handler.event_name, target, document.source_host)
        if mapped is None:
            issues.append(
                _unmapped_hook_event_issue(document, handler.event_name, target)
            )
            continue
        from_host = document.source_host or target.value
        if hook_event_not_equivalent(
            handler.event_name, source_host=from_host, target=target
        ):
            issues.append(
                _hook_event_not_equivalent_issue(
                    document,
                    handler.event_name,
                    target,
                    to_events=mapped,
                )
            )
            continue
        needs_wrap = _needs_stdio_decision_wrap(
            from_host=from_host,
            target=target,
            event_name=handler.event_name,
        )
        adapter_ref = None
        if needs_wrap:
            adapter_ref = _resolve_stdio_adapter_ref(handler, plugin_dir)
            if adapter_ref is None:
                issues.append(
                    _decision_stdio_not_equivalent_issue(
                        document, handler.event_name, target
                    )
                )
                continue
        rewritten = rewrite_hook_plugin_root(handler.command, target)
        if rewritten is None:
            _append_rewrite_failure_issue(
                document, handler.command, target, issues
            )
            continue
        if adapter_ref is not None:
            wrapped = _wrap_stdio_decision_command(
                document,
                ref=adapter_ref,
                from_host=from_host,
                target=target,
                policy_command=rewritten,
                issues=issues,
            )
            if wrapped is None:
                continue
            rewritten = wrapped
        raw = _handler_with_command(handler, rewritten)
        for event_name in mapped:
            handlers.append(
                HookHandler(
                    event_name=event_name,
                    command=rewritten,
                    raw=copy.deepcopy(raw),
                )
            )
    groups: list[HookGroup] = []
    for group in document.groups:
        payload = copy.deepcopy(group.payload)
        if hook_host_shape(target.value) != "flat":
            payload = _ensure_nested_group(payload)
        mapped = mapped_events(group.event_name, target, document.source_host)
        if mapped is None:
            issues.append(
                _unmapped_hook_event_issue(document, group.event_name, target)
            )
            continue
        from_host = document.source_host or target.value
        if hook_event_not_equivalent(
            group.event_name, source_host=from_host, target=target
        ):
            issues.append(
                _hook_event_not_equivalent_issue(
                    document,
                    group.event_name,
                    target,
                    to_events=mapped,
                )
            )
            continue
        rewritten_payload = _rewrite_payload_commands(payload, target)
        if rewritten_payload is None:
            command = " ".join(_payload_commands(payload))
            _append_rewrite_failure_issue(document, command, target, issues)
            continue
        for event_name in mapped:
            groups.append(
                HookGroup(
                    event_name=event_name,
                    payload=copy.deepcopy(rewritten_payload),
                )
            )
    remapped = HookDocument(
        capability_id=document.capability_id,
        source_path=document.source_path,
        handlers=tuple(handlers),
        script_relpaths=document.script_relpaths,
        groups=tuple(groups),
        source_host=target.value,
        events=(),
    )
    return remapped, tuple(issues)


def _payload_commands(payload: dict) -> tuple[str, ...]:
    found: list[str] = []
    command = payload.get("command")
    if isinstance(command, str) and command:
        found.append(command)
    items = payload.get("hooks")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            inner = item.get("command")
            if isinstance(inner, str) and inner:
                found.append(inner)
    return tuple(found)


def _rewrite_payload_commands(payload: dict, target: Target) -> dict | None:
    payload = copy.deepcopy(payload)
    command = payload.get("command")
    if isinstance(command, str) and command:
        rewritten = rewrite_hook_plugin_root(command, target)
        if rewritten is None:
            return None
        payload["command"] = rewritten
    items = payload.get("hooks")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            inner = item.get("command")
            if isinstance(inner, str) and inner:
                rewritten = rewrite_hook_plugin_root(inner, target)
                if rewritten is None:
                    return None
                item["command"] = rewritten
    return payload


def _handler_to_dict(handler: HookHandlerUnion) -> dict:
    raw = copy.deepcopy(handler.raw)
    raw.pop("hooks", None)
    raw.pop("stdio_adapter", None)
    if isinstance(handler, CommandHandler):
        if isinstance(handler.form, ShellForm):
            raw["command"] = handler.form.command
            raw.pop("args", None)
        else:
            raw["command"] = handler.form.command
            raw["args"] = list(handler.form.args)
        if handler.timeout is not None:
            raw["timeout"] = handler.timeout
        raw.setdefault("type", "command")
    elif isinstance(handler, PromptHandler):
        raw["prompt"] = handler.prompt
        raw.setdefault("type", "prompt")
    elif isinstance(handler, HttpHandler):
        raw.setdefault("type", "http")
        if handler.url is not None:
            raw["url"] = handler.url
        if handler.method is not None:
            raw["method"] = handler.method
        if handler.headers is not None:
            raw["headers"] = copy.deepcopy(handler.headers)
    elif isinstance(handler, McpToolHandler):
        raw.setdefault("type", "mcp_tool")
        if handler.tool is not None:
            raw["tool"] = handler.tool
        if handler.server is not None:
            raw["server"] = handler.server
    elif isinstance(handler, AgentHandler):
        raw.setdefault("type", "agent")
        if handler.agent is not None:
            raw["agent"] = copy.deepcopy(handler.agent)
    elif isinstance(handler, PassthroughHandler):
        raw.setdefault("type", handler.handler_type)
    return raw


def _group_model_to_payload(group: HookGroupModel, *, shape: str) -> dict:
    if shape == "flat":
        if len(group.handlers) == 1:
            payload = _handler_to_dict(group.handlers[0])
            for key, value in group.group_fields.items():
                if key not in payload:
                    payload[key] = copy.deepcopy(value)
            return payload
        entries = [_handler_to_dict(handler) for handler in group.handlers]
        if not entries:
            return {}
        payload = copy.deepcopy(entries[0])
        for key, value in group.group_fields.items():
            if key not in payload:
                payload[key] = copy.deepcopy(value)
        return payload
    payload = copy.deepcopy(group.group_fields)
    payload["hooks"] = [_handler_to_dict(handler) for handler in group.handlers]
    return payload


def _render_flat_group_entries(group: HookGroupModel) -> list[dict]:
    if not group.handlers:
        return []
    if len(group.handlers) == 1 and not group.group_fields:
        return [_handler_to_dict(group.handlers[0])]
    entries: list[dict] = []
    for handler in group.handlers:
        entry = _handler_to_dict(handler)
        for key, value in group.group_fields.items():
            if key not in entry:
                entry[key] = copy.deepcopy(value)
        entries.append(entry)
    return entries


def _render_events_payload(
    events: tuple[HookEventBinding, ...],
    *,
    shape: str,
    target: Target,
    source_host: str | None,
) -> dict[str, list]:
    hooks: dict[str, list] = {}
    for event in events:
        mapped = mapped_events(event.event_name, target, source_host)
        if mapped is None:
            continue
        for mapped_name in mapped:
            for group in event.groups:
                if shape == "flat":
                    entries = _render_flat_group_entries(group)
                    hooks.setdefault(mapped_name, []).extend(copy.deepcopy(entries))
                else:
                    hooks.setdefault(mapped_name, []).append(
                        copy.deepcopy(_group_model_to_payload(group, shape=shape))
                    )
    return hooks


def _render_events_payload_native(
    events: tuple[HookEventBinding, ...], *, shape: str
) -> dict[str, list]:
    hooks: dict[str, list] = {}
    for event in events:
        for group in event.groups:
            if shape == "flat":
                entries = _render_flat_group_entries(group)
                hooks.setdefault(event.event_name, []).extend(copy.deepcopy(entries))
            else:
                hooks.setdefault(event.event_name, []).append(
                    copy.deepcopy(_group_model_to_payload(group, shape=shape))
                )
    return hooks


def _infer_render_shape(document: HookDocument) -> str:
    if document.source_host:
        return hook_host_shape(document.source_host)
    if document.groups:
        for group in document.groups:
            if isinstance(group.payload.get("hooks"), list):
                return "nested"
    if document.events:
        for event in document.events:
            for group in event.groups:
                if len(group.handlers) > 1 or group.group_fields:
                    return "nested"
    return "flat"


def render_hooks_payload(document: HookDocument, target: Target) -> dict:
    shape = hook_host_shape(target.value)
    if document.events:
        return {
            "hooks": _render_events_payload(
                document.events,
                shape=shape,
                target=target,
                source_host=document.source_host,
            )
        }
    legacy_hooks: dict[str, list] = {}
    legacy_shape = _infer_render_shape(document)
    if legacy_shape == "flat":
        if document.groups:
            for group in document.groups:
                mapped = mapped_events(
                    group.event_name, target, document.source_host
                )
                if mapped is None:
                    continue
                entries = _flat_entries_from_group(group.payload)
                for event_name in mapped:
                    legacy_hooks.setdefault(event_name, []).extend(
                        copy.deepcopy(entries)
                    )
        else:
            for handler in document.handlers:
                mapped = mapped_events(
                    handler.event_name, target, document.source_host
                )
                if mapped is None:
                    continue
                entry = _flat_entry_from_handler(handler)
                for event_name in mapped:
                    legacy_hooks.setdefault(event_name, []).append(
                        copy.deepcopy(entry)
                    )
        return {"hooks": legacy_hooks}
    if document.groups:
        for group in document.groups:
            mapped = mapped_events(group.event_name, target, document.source_host)
            if mapped is None:
                continue
            nested = _ensure_nested_group(group.payload)
            for event_name in mapped:
                legacy_hooks.setdefault(event_name, []).append(
                    copy.deepcopy(nested)
                )
        return {"hooks": legacy_hooks}
    for handler in document.handlers:
        mapped = mapped_events(handler.event_name, target, document.source_host)
        if mapped is None:
            continue
        nested = {
            "hooks": [_nested_handler_from_raw(handler.raw, handler.command)]
        }
        for event_name in mapped:
            legacy_hooks.setdefault(event_name, []).append(copy.deepcopy(nested))
    return {"hooks": legacy_hooks}


def merge_hook_payloads(generated: dict | None, user: HookDocument) -> dict:
    hooks: dict[str, list] = {}
    source = None if generated is None else generated.get("hooks")
    if isinstance(source, dict):
        for event_name, groups in source.items():
            copied: list = []
            if isinstance(groups, list):
                copied = [copy.deepcopy(group) for group in groups]
            hooks[event_name] = copied
    if user.events:
        shape = _infer_render_shape(user)
        rendered = _render_events_payload_native(user.events, shape=shape)
        for event_name, groups in rendered.items():
            hooks.setdefault(event_name, []).extend(copy.deepcopy(groups))
    elif user.groups:
        for group in user.groups:
            hooks.setdefault(group.event_name, []).append(
                copy.deepcopy(group.payload)
            )
    else:
        for handler in user.handlers:
            hooks.setdefault(handler.event_name, []).append(
                {"hooks": [copy.deepcopy(handler.raw)]}
            )
    return {"hooks": hooks}


def _flat_entries_from_group(payload: dict) -> list[dict]:
    payload = copy.deepcopy(payload)
    items = payload.get("hooks")
    if items is None:
        return [
            {
                key: value
                for key, value in payload.items()
                if key not in ("hooks", "type")
            }
        ]
    if not isinstance(items, list):
        return []
    group_extras = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key not in ("hooks", "type", "command")
    }
    entries: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if not isinstance(command, str) or not command:
            continue
        entry = {"command": command}
        for key, value in item.items():
            if key not in ("hooks", "type", "command"):
                entry[key] = copy.deepcopy(value)
        for key, value in group_extras.items():
            if key not in entry:
                entry[key] = copy.deepcopy(value)
        entries.append(entry)
    return entries


def _nested_handler_from_raw(raw: dict, command: str) -> dict:
    raw = copy.deepcopy(raw)
    items = raw.get("hooks")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("command") == command:
                handler = {"type": "command", "command": command}
                for key, value in item.items():
                    if key not in ("hooks", "type", "command"):
                        handler[key] = copy.deepcopy(value)
                return handler
    handler = {"type": "command", "command": command}
    for key, value in raw.items():
        if key not in ("hooks", "type", "command"):
            handler[key] = copy.deepcopy(value)
    return handler


def _ensure_nested_group(payload: dict) -> dict:
    payload = copy.deepcopy(payload)
    items = payload.get("hooks")
    if isinstance(items, list):
        return payload
    command = payload.get("command")
    handler: dict = {"type": "command"}
    if isinstance(command, str) and command:
        handler["command"] = command
    for key, value in payload.items():
        if key not in ("hooks", "type", "command"):
            handler[key] = copy.deepcopy(value)
    return {"hooks": [handler]}


def _flat_entry_from_handler(handler: HookHandler) -> dict:
    raw = copy.deepcopy(handler.raw)
    items = raw.get("hooks")
    if isinstance(items, list):
        entries = _flat_entries_from_group(raw)
        if entries:
            return entries[0]
    entry = {
        key: value
        for key, value in raw.items()
        if key not in ("hooks", "type")
    }
    if "command" not in entry:
        entry["command"] = handler.command
    return entry


def _iter_hook_commands(payload: dict) -> tuple[str, ...]:
    hooks = payload.get("hooks") if isinstance(payload, dict) else None
    if not isinstance(hooks, dict):
        return ()
    commands: list[str] = []
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            items = group.get("hooks")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                command = item.get("command")
                if isinstance(command, str) and command:
                    commands.append(command)
    return tuple(commands)


def _expected_loader_commands(target: Target) -> tuple[str, ...]:
    found: list[str] = []
    mapped = RULE_LOADER_COMMANDS.get(target.value)
    if mapped:
        found.append(mapped)
    try:
        generated = rule_loader_command(target)
    except ValueError:
        generated = None
    if generated and generated not in found:
        found.append(generated)
    return tuple(found)


def rule_loader_missing_issues(
    payload: dict, target: Target, *, compiled_rules_present: bool
) -> tuple[CompilerIssue, ...]:
    if not compiled_rules_present:
        return ()
    expected = _expected_loader_commands(target)
    if not expected:
        return ()
    commands = _iter_hook_commands(payload)
    if any(
        wanted == command or wanted in command
        for wanted in expected
        for command in commands
    ):
        return ()
    return (
        CompilerIssue(
            severity="block",
            code="rule-loader-overwritten",
            path="hooks/hooks.json",
            message="compiled rules require the rule loader command after merging user hooks",
        ),
    )
