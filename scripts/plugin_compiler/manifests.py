from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


_CORE_IDENTITY_KEYS = frozenset(
    {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
    }
)


@dataclass(frozen=True)
class PluginIdentity:
    name: str
    version: str | None
    description: str | None
    author: object | None
    homepage: str | None = None
    repository: object | None = None
    license: str | None = None
    keywords: tuple[str, ...] | None = None


def _read_json_object(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("missing plugin manifest: {0}".format(path)) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("{0}: invalid JSON: {1}".format(path, exc)) from exc
    if not isinstance(raw, dict):
        raise RuntimeError("{0}: root must be an object".format(path))
    return raw


def identity_from_manifest_dict(raw: dict, *, path: Path | None = None) -> PluginIdentity:
    """Build PluginIdentity from a parsed JSON object (shared core fields only)."""
    label = str(path) if path is not None else "manifest"
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise RuntimeError("{0}: name must be a non-empty string".format(label))
    version = raw.get("version")
    if version is not None and not isinstance(version, str):
        raise RuntimeError("{0}: version must be a string".format(label))
    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        raise RuntimeError("{0}: description must be a string".format(label))
    author = raw.get("author")
    if author is not None and not isinstance(author, dict):
        raise RuntimeError("{0}: author must be an object".format(label))
    homepage = raw.get("homepage")
    if homepage is not None and not isinstance(homepage, str):
        raise RuntimeError("{0}: homepage must be a string".format(label))
    repository = raw.get("repository")
    if repository is not None and not isinstance(repository, (str, dict)):
        raise RuntimeError("{0}: repository must be a string or object".format(label))
    license_value = raw.get("license")
    if license_value is not None and not isinstance(license_value, str):
        raise RuntimeError("{0}: license must be a string".format(label))
    keywords = raw.get("keywords")
    if keywords is not None:
        if not isinstance(keywords, list) or not all(
            isinstance(item, str) for item in keywords
        ):
            raise RuntimeError("{0}: keywords must be an array of strings".format(label))
        keywords = tuple(keywords)
    return PluginIdentity(
        name=name,
        version=version,
        description=description,
        author=author,
        homepage=homepage,
        repository=repository,
        license=license_value,
        keywords=keywords,
    )


def extension_fields_from_manifest(raw: dict) -> dict:
    """Shallow copy of non-core keys from a source manifest object."""
    return {key: value for key, value in raw.items() if key not in _CORE_IDENTITY_KEYS}


def load_plugin_identity(plugin_dir: Path) -> PluginIdentity:
    path = plugin_dir / ".cursor-plugin" / "plugin.json"
    raw = _read_json_object(path)
    return identity_from_manifest_dict(raw, path=path)


def load_cursor_manifest_raw(plugin_dir: Path) -> dict:
    path = plugin_dir / ".cursor-plugin" / "plugin.json"
    return _read_json_object(path)


PLUGIN_MANIFEST_RELATIVES = frozenset(
    {
        ".cursor-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
    }
)


def is_plugin_manifest_source(source_path: str) -> bool:
    """True when a capability is stored on a host plugin.json rather than a sidecar file."""
    return source_path.replace("\\", "/") in PLUGIN_MANIFEST_RELATIVES
