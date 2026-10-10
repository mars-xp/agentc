from __future__ import annotations

import copy
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    contains_unprefixed_relative,
    has_unsupported_shell_syntax,
    rewrite_hook_plugin_root,
)
from .model import CompilerIssue, Target


_INLINE_SECRET = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=")
_HOME_PREFIXES = ("/Users/", "C:\\Users\\")


@dataclass(frozen=True)
class McpDocument:
    capability_id: str
    source_path: str
    servers: dict


def _source_path_for(path: Path, plugin_dir: Path) -> str:
    try:
        return path.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(path).replace("\\", "/")).as_posix()


def mcp_output_relative(target: Target) -> str:
    if target is Target.CURSOR:
        return "mcp.json"
    return ".mcp.json"


def load_mcp_document(path: Path, plugin_dir: Path) -> McpDocument:
    from .manifests import is_plugin_manifest_source

    source_path = _source_path_for(path, plugin_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("{0}: invalid JSON: {1}".format(source_path, exc)) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("{0}: root must be a JSON object".format(source_path))
    raw = payload.get("mcpServers")
    if is_plugin_manifest_source(source_path):
        servers = _manifest_mcp_servers(raw, source_path)
    else:
        servers = _servers_from_payload(raw, source_path)
    return McpDocument(
        capability_id="mcp:{0}".format(source_path),
        source_path=source_path,
        servers=servers,
    )


def _servers_from_payload(servers: object, source_path: str) -> dict:
    if servers is None:
        return {}
    if isinstance(servers, dict):
        return servers
    raise RuntimeError("{0}: mcpServers must be a JSON object".format(source_path))


def _manifest_mcp_servers(servers: object, source_path: str) -> dict:
    """Inline objects from plugin.json mcpServers; path strings are loaded separately."""
    if servers is None:
        return {}
    if isinstance(servers, dict):
        return servers
    if isinstance(servers, list):
        merged: dict = {}
        for index, item in enumerate(servers):
            if isinstance(item, str):
                continue
            if not isinstance(item, dict):
                raise RuntimeError(
                    "{0}: mcpServers array items must be paths or objects".format(
                        source_path
                    )
                )
            if item and all(isinstance(value, dict) for value in item.values()):
                for name, config in item.items():
                    if name in merged:
                        raise RuntimeError(
                            "{0}: duplicate MCP server name {1!r}".format(
                                source_path, name
                            )
                        )
                    merged[name] = copy.deepcopy(config)
            else:
                merged["inline-{0}".format(index)] = copy.deepcopy(item)
        return merged
    raise RuntimeError("{0}: mcpServers must be a JSON object".format(source_path))


def merge_mcp_documents(documents: tuple[McpDocument, ...]) -> tuple[McpDocument, ...]:
    """Collapse MCP documents that share one sidecar; duplicate names fail closed."""
    from .manifests import is_plugin_manifest_source

    if len(documents) <= 1:
        return documents
    servers: dict = {}
    primary = documents[0]
    for document in documents:
        for name, config in document.servers.items():
            if name in servers:
                raise RuntimeError(
                    "{0}: duplicate MCP server name {1!r}".format(
                        document.source_path, name
                    )
                )
            servers[name] = copy.deepcopy(config)
        if is_plugin_manifest_source(document.source_path):
            primary = document
    return (
        McpDocument(
            capability_id=primary.capability_id,
            source_path=primary.source_path,
            servers=servers,
        ),
    )


def _value_looks_like_secret(value: str) -> bool:
    if _INLINE_SECRET.search(value):
        return True
    stripped = value.strip()
    return stripped.startswith("sk-") or stripped.startswith("Bearer ")


def _home_path_used_as_secret(key: str, value: str) -> bool:
    key_lower = key.lower()
    if "token" not in key_lower and "key" not in key_lower:
        return False
    return any(value.startswith(prefix) for prefix in _HOME_PREFIXES)


def _contains_secret(obj: object, parent_key: str = "") -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = key if isinstance(key, str) else str(key)
            if isinstance(value, str) and (
                _value_looks_like_secret(value)
                or _home_path_used_as_secret(name, value)
            ):
                return True
            if _contains_secret(value, name):
                return True
        return False
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, str) and _value_looks_like_secret(item):
                return True
            if _contains_secret(item, parent_key):
                return True
        return False
    if isinstance(obj, str):
        return _value_looks_like_secret(obj) or (
            bool(parent_key) and _home_path_used_as_secret(parent_key, obj)
        )
    return False


def payload_secret_issues(
    payload: object, source_path: str
) -> tuple[CompilerIssue, ...]:
    if not _contains_secret(payload):
        return ()
    return (
        CompilerIssue(
            severity="block",
            code="mcp-secret-in-source",
            path=source_path,
            message=(
                "config contains a secret or local token; keep env names only"
            ),
        ),
    )


def mcp_secret_issues(document: McpDocument) -> tuple[CompilerIssue, ...]:
    return payload_secret_issues(document.servers, document.source_path)


def _env_for_publish(servers: dict) -> dict:
    """Keep env values (placeholders and config refs). Secrets are blocked earlier."""
    return copy.deepcopy(servers)


def _shell_parse_unsafe(text: str) -> bool:
    if has_unsupported_shell_syntax(text):
        return True
    try:
        shlex.split(text, posix=True)
    except ValueError:
        return True
    return False


def _mcp_rewrite_issue(
    document: McpDocument, text: str, target: Target
) -> CompilerIssue:
    if _shell_parse_unsafe(text) and contains_unprefixed_relative(text):
        return CompilerIssue(
            severity="block",
            code="unsafe-plugin-relative-shell",
            path=document.source_path,
            message=(
                "unsafe plugin-relative path in shell command {0!r} for target {1}"
            ).format(text, target.value),
        )
    return CompilerIssue(
        severity="block",
        code="unmapped-plugin-root",
        path=document.source_path,
        message=(
            "unmapped plugin-root placeholder in {0!r} for target {1}; "
            "no mapping in plugin_root.json"
        ).format(text, target.value),
    )


def rewrite_mcp_document(
    document: McpDocument, target: Target
) -> tuple[McpDocument, tuple[CompilerIssue, ...]]:
    issues: list[CompilerIssue] = []
    servers = copy.deepcopy(document.servers)
    for _name, config in servers.items():
        if not isinstance(config, dict):
            continue
        for key in ("command", "cwd"):
            value = config.get(key)
            if isinstance(value, str) and value:
                rewritten = rewrite_hook_plugin_root(value, target)
                if rewritten is None:
                    issues.append(_mcp_rewrite_issue(document, value, target))
                else:
                    config[key] = rewritten
        args = config.get("args")
        if isinstance(args, list):
            new_args = []
            for item in args:
                if not isinstance(item, str):
                    new_args.append(item)
                    continue
                rewritten = rewrite_hook_plugin_root(item, target)
                if rewritten is None:
                    issues.append(_mcp_rewrite_issue(document, item, target))
                    new_args.append(item)
                else:
                    new_args.append(rewritten)
            config["args"] = new_args
    if issues:
        return document, tuple(issues)
    return (
        McpDocument(
            capability_id=document.capability_id,
            source_path=document.source_path,
            servers=servers,
        ),
        (),
    )


def render_mcp_json(document: McpDocument, target: Target) -> str:
    payload = {"mcpServers": _env_for_publish(document.servers)}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
