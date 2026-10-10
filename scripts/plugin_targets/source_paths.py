# -*- coding: utf-8 -*-
"""Shared path helpers for Codex / Claude Code source parse."""
from __future__ import annotations

from pathlib import Path, PureWindowsPath

from plugin_compiler.model import CapabilityKind

FILE_EXPAND_KINDS = frozenset(
    {
        CapabilityKind.COMMAND,
        CapabilityKind.AGENT,
        CapabilityKind.WORKFLOW,
        CapabilityKind.RULE,
        CapabilityKind.HOOK,
        CapabilityKind.MCP,
        CapabilityKind.LSP,
        CapabilityKind.OUTPUT_STYLE,
        CapabilityKind.THEME,
        CapabilityKind.MONITOR,
    }
)
_KIND_FILE_SUFFIXES: dict[CapabilityKind, frozenset[str]] = {
    CapabilityKind.COMMAND: frozenset({".md"}),
    CapabilityKind.AGENT: frozenset({".md"}),
    CapabilityKind.OUTPUT_STYLE: frozenset({".md"}),
    CapabilityKind.THEME: frozenset({".json"}),
    CapabilityKind.MONITOR: frozenset({".json"}),
    CapabilityKind.HOOK: frozenset({".json"}),
    CapabilityKind.MCP: frozenset({".json"}),
    CapabilityKind.LSP: frozenset({".json"}),
}


class DeclaredPathError(ValueError):
    """Declared component path is empty, absolute, or outside the plugin root."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def posix_relative(plugin_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(plugin_dir.resolve()).as_posix()


def declared_paths_error(value: object) -> str | None:
    if isinstance(value, str):
        if not value.strip():
            return "must be a non-empty string path"
        return None
    if isinstance(value, list):
        if not value:
            return "must be a non-empty array of string paths"
        for item in value:
            if not isinstance(item, str) or not item.strip():
                return "every array item must be a non-empty string path"
        return None
    return "must be a string path or array of paths"


def is_inline_object_component(value: object) -> bool:
    if isinstance(value, dict):
        return True
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) for item in value)
    )


_EXPERIMENTAL_NESTED_FIELDS = ("themes", "monitors")


def experimental_nested_paths(
    value: object,
) -> tuple[tuple[tuple[str, str], ...] | None, str | None]:
    """Parse Claude ``experimental`` nested themes/monitors path fields.

    Returns ``(pairs, None)`` where each pair is ``(field, path)``, or
    ``((), error)`` when the value is not a nested path object.
    """
    if not isinstance(value, dict):
        return (), "must be an object with themes/monitors path fields"
    pairs: list[tuple[str, str]] = []
    for field in _EXPERIMENTAL_NESTED_FIELDS:
        if field not in value:
            continue
        nested = value[field]
        error = declared_paths_error(nested)
        if error is not None:
            return (), "experimental.{0} {1}".format(field, error)
        for path in normalize_declared_paths(nested):
            pairs.append((field, path))
    return tuple(pairs), None


def declared_object_or_path_values(
    value: object,
) -> tuple[tuple[str, ...] | None, str | None]:
    """Parse hooks / mcpServers / lspServers: path, object, or mixed array.

    Returns ``(None, None)`` for a fully inline object or object array,
    ``(paths, None)`` for string paths (mixed arrays keep only the strings),
    or ``((), error)`` when the value is illegal.
    """
    if is_inline_object_component(value):
        return None, None
    if isinstance(value, list) and value:
        paths: list[str] = []
        for item in value:
            if isinstance(item, dict):
                continue
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
                continue
            return (), "every array item must be a non-empty string path or object"
        if not paths:
            return None, None
        return tuple(paths), None
    error = declared_paths_error(value)
    if error is not None:
        return (), error
    return normalize_declared_paths(value), None


def component_has_inline_object(value: object) -> bool:
    if isinstance(value, dict):
        return True
    if isinstance(value, list):
        return any(isinstance(item, dict) for item in value)
    return False


def normalize_declared_paths(value: object) -> tuple[str, ...]:
    if declared_paths_error(value) is not None:
        return ()
    if isinstance(value, str):
        return (value.strip(),)
    return tuple(item.strip() for item in value)


def _reject_symlink(path: Path, plugin_root: Path) -> None:
    if not path.is_symlink():
        return
    try:
        path.resolve().relative_to(plugin_root)
    except ValueError as exc:
        raise DeclaredPathError(
            "path-escape",
            "declared path symlink escapes plugin root: {0}".format(path),
        ) from exc


def _reject_any_symlink(path: Path) -> None:
    if not path.is_symlink():
        return
    raise DeclaredPathError(
        "path-escape",
        "skill path must not be a symlink: {0}".format(path),
    )


def assert_skill_md_contained(skill_md: Path, plugin_root: Path) -> None:
    plugin_root = Path(plugin_root).resolve()
    skill_md = Path(skill_md)
    current = skill_md if skill_md.is_absolute() else plugin_root / skill_md
    try:
        relative = current.relative_to(plugin_root)
    except ValueError:
        try:
            relative = (current.parent.resolve() / current.name).relative_to(
                plugin_root
            )
        except ValueError as exc:
            raise DeclaredPathError(
                "path-escape",
                "skill file escapes plugin root: {0}".format(skill_md),
            ) from exc
        current = plugin_root.joinpath(*relative.parts)
    walking = plugin_root
    _reject_any_symlink(walking)
    _reject_symlink(walking, plugin_root)
    for part in relative.parts:
        walking = walking / part
        _reject_any_symlink(walking)
        _reject_symlink(walking, plugin_root)
    try:
        walking.resolve().relative_to(plugin_root)
    except ValueError as exc:
        raise DeclaredPathError(
            "path-escape",
            "skill file escapes plugin root: {0}".format(skill_md),
        ) from exc


def resolve_declared_path(plugin_dir: Path, declared: str) -> Path:
    plugin_root = Path(plugin_dir).resolve()
    cleaned = declared.strip().replace("\\", "/")
    if not cleaned:
        raise DeclaredPathError("path-escape", "declared path is empty")
    if cleaned.startswith("/") or cleaned.startswith("~"):
        raise DeclaredPathError(
            "path-escape",
            "declared path must be relative to the plugin root: {0}".format(declared),
        )
    if PureWindowsPath(cleaned).drive or (
        len(cleaned) >= 2 and cleaned[1] == ":" and cleaned[0].isalpha()
    ):
        raise DeclaredPathError(
            "path-escape",
            "declared path must not be drive-qualified: {0}".format(declared),
        )
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.rstrip("/")
    if not cleaned or cleaned == ".":
        return plugin_root
    parts = tuple(part for part in cleaned.split("/") if part and part != ".")
    if any(part == ".." for part in parts):
        raise DeclaredPathError(
            "path-escape",
            "declared path must not contain '..': {0}".format(declared),
        )
    current = plugin_root
    for part in parts:
        current = current / part
        _reject_symlink(current, plugin_root)
    if current.exists():
        resolved = current.resolve()
        try:
            resolved.relative_to(plugin_root)
        except ValueError as exc:
            raise DeclaredPathError(
                "path-escape",
                "declared path escapes plugin root: {0}".format(declared),
            ) from exc
        return resolved
    return current


def scan_skill_root(
    plugin_dir: Path, skills_root: Path
) -> tuple[tuple[Path, ...], tuple[DeclaredPathError, ...]]:
    plugin_root = Path(plugin_dir).resolve()
    if not skills_root.exists():
        return (), ()
    try:
        _reject_any_symlink(skills_root)
        _reject_symlink(skills_root, plugin_root)
    except DeclaredPathError as exc:
        return (), (exc,)
    if not skills_root.is_dir():
        return (), ()
    found: list[Path] = []
    errors: list[DeclaredPathError] = []
    root_skill_md = skills_root / "SKILL.md"
    if root_skill_md.is_symlink() or root_skill_md.is_file():
        try:
            assert_skill_md_contained(root_skill_md, plugin_root)
        except DeclaredPathError as exc:
            errors.append(exc)
    for child in sorted(skills_root.iterdir(), key=lambda item: item.name):
        if child.name.startswith(".") or not child.is_dir():
            continue
        try:
            _reject_any_symlink(child)
            _reject_symlink(child, plugin_root)
        except DeclaredPathError as exc:
            errors.append(exc)
            continue
        skill_md = child / "SKILL.md"
        if not (skill_md.is_symlink() or skill_md.is_file()):
            continue
        try:
            assert_skill_md_contained(skill_md, plugin_root)
        except DeclaredPathError as exc:
            errors.append(exc)
            continue
        found.append(child)
    return tuple(found), tuple(errors)


def scan_default_skill_dirs(
    plugin_dir: Path,
) -> tuple[tuple[Path, ...], tuple[DeclaredPathError, ...]]:
    plugin_root = Path(plugin_dir).resolve()
    return scan_skill_root(plugin_root, plugin_root / "skills")


def default_skill_dirs(plugin_dir: Path) -> tuple[Path, ...]:
    found, errors = scan_default_skill_dirs(plugin_dir)
    if errors:
        raise errors[0]
    return found


def skill_dirs_for_path(resolved: Path) -> tuple[Path, ...]:
    if resolved.is_file() and resolved.name == "SKILL.md":
        return (resolved.parent,)
    if not resolved.is_dir():
        return ()
    if (resolved / "SKILL.md").is_file():
        return (resolved,)
    return tuple(
        sorted(
            child
            for child in resolved.iterdir()
            if child.is_dir() and (child / "SKILL.md").is_file()
        )
    )


def file_matches_kind_suffix(path: Path, kind: CapabilityKind) -> bool:
    suffixes = _KIND_FILE_SUFFIXES.get(kind)
    if suffixes is None:
        return True
    if kind is CapabilityKind.MONITOR and path.name == "monitors.json":
        return True
    return path.suffix.lower() in suffixes


def files_for_path(
    resolved: Path, kind: CapabilityKind | None = None
) -> tuple[Path, ...]:
    if resolved.is_file():
        files = (resolved,)
    elif resolved.is_dir():
        files = tuple(sorted(path for path in resolved.rglob("*") if path.is_file()))
    else:
        files = ()
    if kind is None:
        return files
    return tuple(path for path in files if file_matches_kind_suffix(path, kind))


def walk_host_default_entries(
    plugin_dir: Path,
    entries: tuple[tuple[str, CapabilityKind], ...],
) -> tuple[
    tuple[tuple[str, str], ...],
    tuple[tuple[CapabilityKind, str], ...],
    tuple[DeclaredPathError, ...],
]:
    """Scan host default files/dirs if they exist. Absent paths are not errors."""
    declared: list[tuple[str, str]] = []
    capabilities: list[tuple[CapabilityKind, str]] = []
    errors: list[DeclaredPathError] = []
    seen_declared: set[tuple[str, str]] = set()
    seen_caps: set[tuple[str, str]] = set()

    def _add_declared(source_path: str, kind: str) -> None:
        key = (source_path, kind)
        if key in seen_declared:
            return
        seen_declared.add(key)
        declared.append(key)

    def _add_capability(kind: CapabilityKind, source_path: str) -> None:
        key = (kind.value, source_path)
        if key in seen_caps:
            return
        seen_caps.add(key)
        capabilities.append((kind, source_path))

    for declared_path, kind in entries:
        try:
            resolved = resolve_declared_path(plugin_dir, declared_path)
        except DeclaredPathError as exc:
            errors.append(exc)
            continue
        if not resolved.exists():
            continue
        relative = posix_relative(plugin_dir, resolved)
        _add_declared(relative, kind.value)
        if resolved.is_dir():
            for file_path in files_for_path(resolved, kind):
                try:
                    file_rel = posix_relative(plugin_dir, file_path)
                except ValueError:
                    errors.append(
                        DeclaredPathError(
                            "path-escape",
                            "declared path symlink escapes plugin root: {0}".format(
                                file_path
                            ),
                        )
                    )
                    continue
                _add_declared(file_rel, kind.value)
                _add_capability(kind, file_rel)
            continue
        if resolved.is_file():
            _add_capability(kind, relative)
    return tuple(declared), tuple(capabilities), tuple(errors)
