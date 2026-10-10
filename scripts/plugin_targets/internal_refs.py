# -*- coding: utf-8 -*-
"""Extract official plugin-root file refs into DeclaredResource (kind=file)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from plugin_compiler.contracts.load import _plugin_root_path_prefixes
from plugin_compiler.extras import LspDocument
from plugin_compiler.hooks import HookDocument, plugin_relpaths_from_command
from plugin_compiler.mcp import McpDocument
from plugin_compiler.model import CompilerIssue
from plugin_targets.source_paths import (
    DeclaredPathError,
    declared_object_or_path_values,
    resolve_declared_path,
)
from plugin_targets.spi import DeclaredResource

# This host's official default JSON sidecars only. Do not union other hosts.
_HOST_DEFAULT_SIDECARS: dict[str, tuple[tuple[str, str], ...]] = {
    "cursor": (
        ("hooks/hooks.json", "hook"),
        ("mcp.json", "mcp"),
    ),
    "claude-code": (
        ("hooks/hooks.json", "hook"),
        (".mcp.json", "mcp"),
        (".lsp.json", "lsp"),
    ),
    "codex": (
        ("hooks.json", "hook"),
        ("hooks/hooks.json", "hook"),
        (".mcp.json", "mcp"),
    ),
}
_DECLARED_SIDECAR_KEYS = (
    ("hooks", "hook"),
    ("mcpServers", "mcp"),
    ("lspServers", "lsp"),
)
_PACKAGE_METADATA_FILES = ("package.json", "package-lock.json", "bun.lockb")
_NODE_MODULES_DIR = "node_modules"


def strip_official_plugin_prefix(value: str) -> str:
    posix = value.strip().replace("\\", "/")
    for prefix in _plugin_root_path_prefixes():
        if posix.startswith(prefix):
            posix = posix[len(prefix) :]
            break
    while posix.startswith("./"):
        posix = posix[2:]
    return posix


def official_plugin_relative(value: str) -> str | None:
    """Whole-string official plugin-root relative path (`./` or placeholder)."""
    text = value.strip()
    if not text:
        return None
    if not (
        any(text.startswith(prefix) for prefix in _plugin_root_path_prefixes())
        or text.startswith("./")
    ):
        return None
    cleaned = strip_official_plugin_prefix(text)
    if not cleaned or cleaned == ".":
        return None
    return cleaned


def asset_plugin_relative(value: str) -> str | None:
    """Manifest asset field (logo): treat as a plugin-relative path unless URL."""
    text = value.strip().replace("\\", "/")
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return None
    official = official_plugin_relative(text)
    if official is not None:
        return official
    cleaned = strip_official_plugin_prefix(text)
    if not cleaned or cleaned == ".":
        return None
    return cleaned


def embedded_plugin_relatives(command: str) -> tuple[str, ...]:
    return plugin_relpaths_from_command(command)


def _relpaths_from_server_config(config: object) -> list[str]:
    if not isinstance(config, dict):
        return []
    found: list[str] = []
    command = config.get("command")
    if isinstance(command, str):
        relative = official_plugin_relative(command)
        if relative:
            found.append(relative)
    args = config.get("args")
    if isinstance(args, list):
        for item in args:
            if not isinstance(item, str):
                continue
            relative = official_plugin_relative(item)
            if relative:
                found.append(relative)
    cwd = config.get("cwd")
    if isinstance(cwd, str):
        relative = official_plugin_relative(cwd)
        if relative:
            found.append(relative)
    return found


def _iter_server_configs(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    configs: list[dict] = []

    def _extend_mapping(mapping: object) -> None:
        if isinstance(mapping, dict):
            for value in mapping.values():
                if isinstance(value, dict):
                    configs.append(value)
            return
        if isinstance(mapping, list):
            for item in mapping:
                if isinstance(item, str):
                    continue
                if not isinstance(item, dict):
                    continue
                if item and all(isinstance(value, dict) for value in item.values()):
                    configs.extend(
                        value for value in item.values() if isinstance(value, dict)
                    )
                else:
                    configs.append(item)

    wrapped = False
    for key in ("mcpServers", "lspServers"):
        if key in payload:
            wrapped = True
            _extend_mapping(payload.get(key))
    if not wrapped and payload and all(
        isinstance(value, dict) for value in payload.values()
    ):
        configs.extend(value for value in payload.values() if isinstance(value, dict))
    return configs


def _relpaths_from_servers_payload(payload: object) -> list[str]:
    found: list[str] = []
    for config in _iter_server_configs(payload):
        found.extend(_relpaths_from_server_config(config))
    return found


def _commands_from_hook_payload(payload: object) -> list[str]:
    commands: list[str] = []
    if isinstance(payload, dict):
        command = payload.get("command")
        if isinstance(command, str) and command:
            commands.append(command)
        for value in payload.values():
            commands.extend(_commands_from_hook_payload(value))
    elif isinstance(payload, list):
        for item in payload:
            commands.extend(_commands_from_hook_payload(item))
    return commands


def _relpaths_from_hook_payload(payload: object) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for command in _commands_from_hook_payload(payload):
        for relative in embedded_plugin_relatives(command):
            if relative in seen:
                continue
            seen.add(relative)
            found.append(relative)
    return found


def _relpaths_from_component_servers(value: object, wrapper: str) -> list[str]:
    if isinstance(value, dict):
        return _relpaths_from_servers_payload(value)
    if isinstance(value, list):
        return _relpaths_from_servers_payload({wrapper: value})
    return []


def _value_at_dotted(fields: dict, dotted: str) -> object:
    current: object = fields
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _relpaths_from_path_arrays(fields: dict) -> list[str]:
    from plugin_compiler.contracts import path_array_dotted_paths

    found: list[str] = []
    for dotted in path_array_dotted_paths():
        value = _value_at_dotted(fields, dotted)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, str):
                continue
            relative = asset_plugin_relative(item)
            if relative:
                found.append(relative)
    return found


def _relpaths_from_fields(fields: dict) -> list[str]:
    found: list[str] = []
    logo = fields.get("logo")
    if isinstance(logo, str):
        relative = asset_plugin_relative(logo)
        if relative:
            found.append(relative)
    interface = fields.get("interface")
    if isinstance(interface, dict):
        for value in interface.values():
            if not isinstance(value, str):
                continue
            relative = official_plugin_relative(value)
            if relative:
                found.append(relative)
    found.extend(_relpaths_from_hook_payload(fields.get("hooks")))
    found.extend(_relpaths_from_component_servers(fields.get("mcpServers"), "mcpServers"))
    found.extend(_relpaths_from_component_servers(fields.get("lspServers"), "lspServers"))
    found.extend(_relpaths_from_path_arrays(fields))
    return found


def _hook_script_parent_relpaths(hooks: tuple[HookDocument, ...]) -> list[str]:
    found: list[str] = []
    for document in hooks:
        for relative in document.script_relpaths:
            parts = tuple(
                part
                for part in relative.replace("\\", "/").split("/")
                if part and part != "."
            )
            if len(parts) < 3:
                continue
            parent = "/".join(parts[:-1])
            if parent and parent != ".":
                found.append(parent)
    return found


def _hook_script_import_re() -> re.Pattern[str] | None:
    from plugin_compiler.layout_extras import extra_hook_import_modules

    names = extra_hook_import_modules()
    if not names:
        return None
    body = "|".join(re.escape(name) for name in names)
    return re.compile(
        r"^\s*(?:import|from)\s+(" + body + r")\b",
        re.MULTILINE,
    )


_HOOK_SCRIPT_IMPORT_RE = _hook_script_import_re()


def _relpaths_from_hook_script_imports(
    plugin_dir: Path, hooks: tuple[HookDocument, ...]
) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    if _HOOK_SCRIPT_IMPORT_RE is None:
        return found
    root = Path(plugin_dir).resolve()
    for document in hooks:
        for relative in document.script_relpaths:
            try:
                script_path = resolve_declared_path(root, relative)
            except DeclaredPathError:
                continue
            if not script_path.is_file():
                continue
            try:
                text = script_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            for match in _HOOK_SCRIPT_IMPORT_RE.finditer(text):
                module = match.group(1)
                candidate = "scripts/{0}.py".format(module)
                if candidate in seen:
                    continue
                try:
                    resolved = resolve_declared_path(root, candidate)
                except DeclaredPathError:
                    continue
                if resolved.is_file():
                    seen.add(candidate)
                    found.append(candidate)
    return found


def _relpaths_from_documents(
    hooks: tuple[HookDocument, ...] = (),
    mcp_documents: tuple[McpDocument, ...] = (),
    lsp_documents: tuple[LspDocument, ...] = (),
) -> list[str]:
    found: list[str] = []
    for document in hooks:
        for handler in document.handlers:
            found.extend(embedded_plugin_relatives(handler.command))
    for document in mcp_documents:
        found.extend(_relpaths_from_servers_payload(document.servers))
    for document in lsp_documents:
        found.extend(_relpaths_from_servers_payload(document.payload))
    return found


def _declared_sidecar_relatives(fields: dict, key: str) -> tuple[str, ...]:
    if key not in fields:
        return ()
    paths, error = declared_object_or_path_values(fields[key])
    if error is not None or not paths:
        return ()
    found: list[str] = []
    for declared in paths:
        cleaned = strip_official_plugin_prefix(declared)
        if not cleaned:
            continue
        parts = tuple(part for part in cleaned.split("/") if part and part != ".")
        if not parts or any(part == ".." for part in parts):
            continue
        found.append("/".join(parts))
    return tuple(found)


def _typed_sidecar_relatives(
    fields: dict, source_dialect: str
) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(relative: str, kind: str) -> None:
        key = (relative, kind)
        if key in seen:
            return
        seen.add(key)
        found.append(key)

    for relative, kind in _HOST_DEFAULT_SIDECARS.get(source_dialect, ()):
        _add(relative, kind)
    for field_key, kind in _DECLARED_SIDECAR_KEYS:
        for relative in _declared_sidecar_relatives(fields, field_key):
            _add(relative, kind)
    return tuple(found)


def _sidecar_files(plugin_dir: Path, relative: str) -> tuple[Path, ...]:
    parts = tuple(part for part in relative.split("/") if part and part != ".")
    if not parts or any(part == ".." for part in parts):
        return ()
    resolved = Path(plugin_dir).joinpath(*parts)
    if resolved.is_file():
        return (resolved,)
    if not resolved.is_dir():
        return ()
    return tuple(
        sorted(
            path
            for path in resolved.rglob("*")
            if path.is_file() and path.suffix.lower() == ".json"
        )
    )


def _relpaths_from_sidecars(
    plugin_dir: Path, fields: dict, source_dialect: str
) -> list[str]:
    found: list[str] = []
    seen_pairs: set[tuple[Path, str]] = set()
    for relative, kind in _typed_sidecar_relatives(fields, source_dialect):
        for path in _sidecar_files(plugin_dir, relative):
            pair = (path, kind)
            if pair in seen_pairs or path.is_symlink():
                continue
            seen_pairs.add(pair)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if kind == "hook":
                found.extend(_relpaths_from_hook_payload(payload))
            else:
                found.extend(_relpaths_from_servers_payload(payload))
    return found


def candidate_internal_relpaths(
    plugin_dir: Path,
    fields: dict,
    *,
    hooks: tuple[HookDocument, ...] = (),
    mcp_documents: tuple[McpDocument, ...] = (),
    lsp_documents: tuple[LspDocument, ...] = (),
    source_dialect: str = "",
    extra_field_maps: tuple[dict, ...] = (),
) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    field_relpaths: list[str] = []
    for field_map in (fields, *extra_field_maps):
        field_relpaths.extend(_relpaths_from_fields(field_map))
    for relative in (
        *field_relpaths,
        *_relpaths_from_documents(hooks, mcp_documents, lsp_documents),
        *_hook_script_parent_relpaths(hooks),
        *_relpaths_from_hook_script_imports(plugin_dir, hooks),
        *_relpaths_from_sidecars(plugin_dir, fields, source_dialect),
    ):
        posix = relative.replace("\\", "/")
        if not posix or posix in seen:
            continue
        seen.add(posix)
        found.append(posix)
    return tuple(found)


def _symlink_on_relative(plugin_dir: Path, relative: str) -> bool:
    plugin_root = Path(plugin_dir).resolve()
    parts = tuple(
        part for part in relative.replace("\\", "/").split("/") if part and part != "."
    )
    current = plugin_root
    for part in parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def check_internal_ref(plugin_dir: Path, relative: str) -> CompilerIssue | None:
    try:
        resolved = resolve_declared_path(plugin_dir, relative)
    except DeclaredPathError as exc:
        return CompilerIssue(
            severity="block",
            code=exc.code,
            path=relative,
            message=exc.message,
        )
    if _symlink_on_relative(plugin_dir, relative):
        return CompilerIssue(
            severity="block",
            code="path-escape",
            path=relative,
            message="internal ref must not be a symlink: {0}".format(relative),
        )
    if resolved.is_dir():
        return None
    if not resolved.is_file():
        return CompilerIssue(
            severity="block",
            code="missing-internal-ref",
            path=relative,
            message="declared plugin-internal file does not exist: {0}".format(
                relative
            ),
        )
    return None


def internal_ref_issues(
    plugin_dir: Path,
    fields: dict,
    *,
    source_dialect: str = "",
    extra_field_maps: tuple[dict, ...] = (),
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    seen: set[str] = set()
    for relative in candidate_internal_relpaths(
        plugin_dir,
        fields,
        source_dialect=source_dialect,
        extra_field_maps=extra_field_maps,
    ):
        if relative in seen:
            continue
        seen.add(relative)
        issue = check_internal_ref(plugin_dir, relative)
        if issue is not None:
            issues.append(issue)
    return tuple(issues)


def _present_package_root_resources(plugin_dir: Path) -> tuple[DeclaredResource, ...]:
    root = Path(plugin_dir).resolve()
    found: list[DeclaredResource] = []
    for name in _PACKAGE_METADATA_FILES:
        path = root / name
        if path.is_file() and not path.is_symlink():
            found.append(DeclaredResource(source_path=name, kind="file"))
    if (root / _NODE_MODULES_DIR).is_dir() and not _symlink_on_relative(
        plugin_dir, _NODE_MODULES_DIR
    ):
        found.append(
            DeclaredResource(source_path=_NODE_MODULES_DIR, kind="directory")
        )
    return tuple(found)


def declared_internal_resources(
    plugin_dir: Path,
    fields: dict,
    *,
    hooks: tuple[HookDocument, ...] = (),
    mcp_documents: tuple[McpDocument, ...] = (),
    lsp_documents: tuple[LspDocument, ...] = (),
    declared: tuple[DeclaredResource, ...] = (),
    source_dialect: str = "",
    extra_field_maps: tuple[dict, ...] = (),
) -> tuple[DeclaredResource, ...]:
    seen: set[tuple[str, str]] = set()
    merged: list[DeclaredResource] = []
    for resource in declared:
        key = (resource.source_path, resource.kind)
        if key in seen:
            continue
        seen.add(key)
        merged.append(resource)
    for relative in candidate_internal_relpaths(
        plugin_dir,
        fields,
        hooks=hooks,
        mcp_documents=mcp_documents,
        lsp_documents=lsp_documents,
        source_dialect=source_dialect,
        extra_field_maps=extra_field_maps,
    ):
        if check_internal_ref(plugin_dir, relative) is not None:
            continue
        try:
            resolved = resolve_declared_path(plugin_dir, relative)
        except DeclaredPathError:
            continue
        if resolved.is_dir():
            if relative in {".", ""}:
                continue
            key = (relative, "directory")
            if key in seen:
                continue
            seen.add(key)
            merged.append(DeclaredResource(source_path=relative, kind="directory"))
            continue
        if not resolved.is_file():
            continue
        key = (relative, "file")
        if key in seen:
            continue
        seen.add(key)
        merged.append(DeclaredResource(source_path=relative, kind="file"))
    for resource in _present_package_root_resources(plugin_dir):
        key = (resource.source_path, resource.kind)
        if key in seen:
            continue
        seen.add(key)
        merged.append(resource)
    return tuple(merged)
