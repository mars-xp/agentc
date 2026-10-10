from __future__ import annotations

from pathlib import Path, PurePath

from .contracts import default_component_entries
from .model import CapabilityKind, CapabilityRef


_ROOT_FILES = {
    "mcp.json": (CapabilityKind.MCP, "cursor"),
    ".mcp.json": (CapabilityKind.MCP, None),
    ".app.json": (CapabilityKind.CONNECTOR, "codex"),
    ".lsp.json": (CapabilityKind.LSP, "claude-code"),
    "AGENTS.md": (CapabilityKind.RULE, "codex"),
    "CLAUDE.md": (CapabilityKind.RULE, "claude-code"),
}

_DIRECTORIES = {
    "rules": CapabilityKind.RULE,
    "commands": CapabilityKind.COMMAND,
    "agents": CapabilityKind.AGENT,
    "workflows": CapabilityKind.WORKFLOW,
    "hooks": CapabilityKind.HOOK,
    "output-styles": CapabilityKind.OUTPUT_STYLE,
    "themes": CapabilityKind.THEME,
    "monitors": CapabilityKind.MONITOR,
}

_SKIP_ROOTS = frozenset({"build", "packaging"})


def _ref(
    kind: CapabilityKind, relative: PurePath, dialect: str | None
) -> CapabilityRef:
    source_path = relative.as_posix()
    return CapabilityRef(
        capability_id="{0}:{1}".format(kind.value, source_path),
        kind=kind,
        source_path=source_path,
        source_dialect=dialect,
    )


def _skip_relative(relative: str) -> bool:
    posix = relative.replace("\\", "/")
    first = posix.split("/", 1)[0]
    return first in _SKIP_ROOTS


def _capability_kind(kind_str: str) -> CapabilityKind | None:
    try:
        return CapabilityKind(kind_str)
    except ValueError:
        return None


def scan_plugin(plugin_dir: Path, dialect: str) -> tuple[CapabilityRef, ...]:
    refs: list[CapabilityRef] = []
    seen: set[str] = set()

    def add(ref: CapabilityRef) -> None:
        if ref.capability_id in seen:
            return
        if _skip_relative(ref.source_path):
            return
        seen.add(ref.capability_id)
        refs.append(ref)

    skills = plugin_dir / "skills"
    skills_present = skills.is_dir()
    if skills_present:
        for child in sorted(skills.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                add(_ref(CapabilityKind.SKILL, child.relative_to(plugin_dir), None))
    for name, (kind, file_dialect) in _ROOT_FILES.items():
        path = plugin_dir / name
        if path.is_file():
            add(_ref(kind, path.relative_to(plugin_dir), file_dialect))
    for directory, kind in _DIRECTORIES.items():
        root = plugin_dir / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                add(_ref(kind, path.relative_to(plugin_dir), None))
    for manifest_dialect, manifest in (
        ("cursor", ".cursor-plugin/plugin.json"),
        ("codex", ".codex-plugin/plugin.json"),
        ("claude-code", ".claude-plugin/plugin.json"),
    ):
        path = plugin_dir / manifest
        if path.is_file():
            add(
                _ref(
                    CapabilityKind.PLUGIN_METADATA,
                    path.relative_to(plugin_dir),
                    manifest_dialect,
                )
            )
    for relative, kind_str in default_component_entries(dialect):
        kind = _capability_kind(kind_str)
        if kind is None:
            continue
        path = plugin_dir / relative
        if kind is CapabilityKind.SKILL and path.is_dir():
            continue
        if kind is CapabilityKind.SKILL and path.is_file() and skills_present:
            continue
        if path.is_file():
            add(_ref(kind, path.relative_to(plugin_dir), None))
            continue
        if not path.is_dir():
            continue
        for file_path in sorted(path.rglob("*")):
            if file_path.is_file():
                add(_ref(kind, file_path.relative_to(plugin_dir), None))
    return tuple(sorted(refs, key=lambda item: item.capability_id))
