from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .model import CapabilityKind, CompilerIssue, Target

_FRONTMATTER_FIELD = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")

_NATIVE_TARGETS: dict[CapabilityKind, frozenset[Target]] = {
    CapabilityKind.CONNECTOR: frozenset({Target.CODEX}),
    CapabilityKind.LSP: frozenset({Target.CLAUDE_CODE}),
    CapabilityKind.OUTPUT_STYLE: frozenset({Target.CLAUDE_CODE}),
    CapabilityKind.THEME: frozenset({Target.CLAUDE_CODE}),
    CapabilityKind.MONITOR: frozenset({Target.CLAUDE_CODE}),
}

_KIND_PATHS = {
    CapabilityKind.CONNECTOR: ".app.json",
    CapabilityKind.LSP: ".lsp.json",
    CapabilityKind.OUTPUT_STYLE: "output-styles/",
    CapabilityKind.THEME: "themes/",
    CapabilityKind.MONITOR: "monitors/",
}


@dataclass(frozen=True)
class ConnectorDocument:
    capability_id: str
    source_path: str
    payload: dict


@dataclass(frozen=True)
class LspDocument:
    capability_id: str
    source_path: str
    payload: dict


@dataclass(frozen=True)
class OutputStyleDocument:
    capability_id: str
    source_path: str
    name: str
    body: str
    extra_fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ThemeDocument:
    capability_id: str
    source_path: str
    payload: dict


@dataclass(frozen=True)
class MonitorDocument:
    capability_id: str
    source_path: str
    payload: dict


def native_targets(kind: CapabilityKind) -> frozenset[Target]:
    try:
        return _NATIVE_TARGETS[kind]
    except KeyError as exc:
        raise ValueError(
            "native_targets is only defined for extras kinds, not {0}".format(
                kind.value
            )
        ) from exc


def extra_conversion_issue(
    kind: CapabilityKind, target: Target
) -> CompilerIssue | None:
    if target in native_targets(kind):
        return None
    return CompilerIssue(
        severity="block",
        code="undeclared-target-unsupported",
        path=_KIND_PATHS[kind],
        message=(
            "{0} has no native component on {1} and must not be converted to a "
            "skill; {1} compilation fails; fix via the authoring/repair program; "
            "to declare a host gap use packaging/platform-overrides.json "
            "with a human-confirmed no-equivalent reason"
        ).format(kind.value, target.value),
    )


def _source_path_for(path: Path, plugin_dir: Path) -> str:
    try:
        return path.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(path).replace("\\", "/")).as_posix()


def _load_json_object(path: Path, plugin_dir: Path) -> tuple[str, dict]:
    source_path = _source_path_for(path, plugin_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("{0}: invalid JSON: {1}".format(source_path, exc)) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("{0}: root must be a JSON object".format(source_path))
    return source_path, payload


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _name_from_source_path(source_path: str) -> str:
    posix = source_path.replace("\\", "/")
    filename = posix.rsplit("/", 1)[-1]
    if filename.lower().endswith(".md"):
        return filename[:-3]
    return filename


def _split_optional_frontmatter(
    text: str, source_path: str
) -> tuple[str, tuple[tuple[str, str], ...], str]:
    if not text.startswith("---"):
        return "", (), text
    end = text.find("\n---", 3)
    if end == -1:
        raise RuntimeError("{0}: incomplete YAML frontmatter".format(source_path))
    block = text[3:end].strip("\n")
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    name = ""
    extra: list[tuple[str, str]] = []
    for line in block.splitlines():
        match = _FRONTMATTER_FIELD.match(line)
        if not match:
            continue
        key = match.group(1)
        value = match.group(2).strip()
        if key == "name":
            name = _strip_quotes(value)
            continue
        extra.append((key, value))
    return name, tuple(extra), body


def load_connector_document(path: Path, plugin_dir: Path) -> ConnectorDocument:
    source_path, payload = _load_json_object(path, plugin_dir)
    return ConnectorDocument(
        capability_id="connector:{0}".format(source_path),
        source_path=source_path,
        payload=payload,
    )


def extra_output_relative(kind: CapabilityKind, source_path: str) -> str:
    """Sidecar filename for extras stored inline on plugin.json."""
    from .manifests import is_plugin_manifest_source

    if kind is CapabilityKind.LSP:
        return ".lsp.json"
    posix = source_path.replace("\\", "/")
    if is_plugin_manifest_source(posix):
        mapped = _KIND_PATHS.get(kind)
        if mapped:
            return mapped.rstrip("/")
    return posix


def _is_lsp_server_name_mapping(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(item, dict) for item in value.values()
    )


def _unwrap_lsp_server_mapping(payload: dict, source_path: str) -> dict:
    """Unwrap plugin.json inline ``lspServers`` to a server-name mapping.

    A server whose name is ``lspServers`` (config values are not all objects)
    stays as-is. Nested wrappers unwrap only while each layer is that single
    key and the inner value is a server-name mapping.
    """
    current = payload
    if not isinstance(current, dict):
        raise RuntimeError(
            "{0}: lspServers must be a JSON object".format(source_path)
        )
    while len(current) == 1 and "lspServers" in current:
        inner = current.get("lspServers")
        if not isinstance(inner, dict):
            raise RuntimeError(
                "{0}: lspServers must be a server-name mapping or a "
                "single lspServers object".format(source_path)
            )
        if _is_lsp_server_name_mapping(inner):
            current = inner
            continue
        return copy.deepcopy(current)
    if "lspServers" in current and not _is_lsp_server_name_mapping(current):
        raise RuntimeError(
            "{0}: lspServers must be a server-name mapping or a "
            "single lspServers object".format(source_path)
        )
    return copy.deepcopy(current)


def _sidecar_lsp_server_mapping(payload: dict, source_path: str) -> dict:
    """Official sidecar root is a server-name mapping. Do not unwrap wrappers."""
    if not isinstance(payload, dict):
        raise RuntimeError(
            "{0}: LSP sidecar root must be a JSON object".format(source_path)
        )
    inner = payload.get("lspServers") if len(payload) == 1 else None
    if (
        isinstance(inner, dict)
        and _is_lsp_server_name_mapping(inner)
    ):
        raise RuntimeError(
            "{0}: LSP sidecar root must be a server-name mapping, not a "
            "wrapped lspServers object".format(source_path)
        )
    if "lspServers" in payload and not _is_lsp_server_name_mapping(payload):
        raise RuntimeError(
            "{0}: LSP sidecar root must be a server-name mapping".format(
                source_path
            )
        )
    return copy.deepcopy(payload)


def _lsp_mapping_from_payload(payload: dict, source_path: str) -> dict:
    from .manifests import is_plugin_manifest_source

    if is_plugin_manifest_source(source_path):
        lsp = payload.get("lspServers")
        if isinstance(lsp, dict):
            return _unwrap_lsp_server_mapping(lsp, source_path)
        if lsp is None:
            return {}
        if isinstance(lsp, list):
            merged: dict = {}
            for index, item in enumerate(lsp):
                if isinstance(item, str):
                    continue
                if not isinstance(item, dict):
                    raise RuntimeError(
                        "{0}: lspServers array items must be paths or objects".format(
                            source_path
                        )
                    )
                if item and all(isinstance(value, dict) for value in item.values()):
                    for name, config in item.items():
                        if name in merged:
                            raise RuntimeError(
                                "{0}: duplicate LSP server name {1!r}".format(
                                    source_path, name
                                )
                            )
                        merged[name] = copy.deepcopy(config)
                else:
                    merged["inline-{0}".format(index)] = copy.deepcopy(item)
            return merged
        raise RuntimeError(
            "{0}: lspServers must be a JSON object".format(source_path)
        )
    return _sidecar_lsp_server_mapping(payload, source_path)


def load_lsp_document(path: Path, plugin_dir: Path) -> LspDocument:
    source_path, payload = _load_json_object(path, plugin_dir)
    return LspDocument(
        capability_id="lsp:{0}".format(source_path),
        source_path=source_path,
        payload=_lsp_mapping_from_payload(payload, source_path),
    )


def merge_lsp_documents(documents: tuple[LspDocument, ...]) -> tuple[LspDocument, ...]:
    """Collapse LSP documents that share one sidecar; duplicate names fail closed."""
    from .manifests import is_plugin_manifest_source

    if len(documents) <= 1:
        return documents
    mapping: dict = {}
    primary = documents[0]
    for document in documents:
        payload = document.payload
        if not isinstance(payload, dict):
            raise RuntimeError(
                "{0}: LSP root must be a JSON object".format(document.source_path)
            )
        for name, config in payload.items():
            if name in mapping:
                raise RuntimeError(
                    "{0}: duplicate LSP server name {1!r}".format(
                        document.source_path, name
                    )
                )
            mapping[name] = copy.deepcopy(config)
        if is_plugin_manifest_source(document.source_path):
            primary = document
    return (
        LspDocument(
            capability_id=primary.capability_id,
            source_path=primary.source_path,
            payload=mapping,
        ),
    )


def load_theme_document(path: Path, plugin_dir: Path) -> ThemeDocument:
    source_path, payload = _load_json_object(path, plugin_dir)
    return ThemeDocument(
        capability_id="theme:{0}".format(source_path),
        source_path=source_path,
        payload=payload,
    )


def load_monitor_document(path: Path, plugin_dir: Path) -> MonitorDocument:
    source_path, payload = _load_json_object(path, plugin_dir)
    return MonitorDocument(
        capability_id="monitor:{0}".format(source_path),
        source_path=source_path,
        payload=payload,
    )


def load_output_style_document(path: Path, plugin_dir: Path) -> OutputStyleDocument:
    source_path = _source_path_for(path, plugin_dir)
    text = path.read_text(encoding="utf-8")
    frontmatter_name, extra_fields, body = _split_optional_frontmatter(
        text, source_path
    )
    name = frontmatter_name or _name_from_source_path(source_path)
    return OutputStyleDocument(
        capability_id="output-style:{0}".format(source_path),
        source_path=source_path,
        name=name,
        body=body,
        extra_fields=extra_fields,
    )
