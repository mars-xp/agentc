from __future__ import annotations

import json
import re
from pathlib import Path

from plugin_compiler.contracts.load import (
    marketplace_interface_specs,
    marketplace_name_pattern,
    marketplace_policy_specs,
)

MARKETPLACE_NAME = "cursor-plugin-codex"
MARKETPLACE_DISPLAY_NAME = "Cursor Plugin Codex"
MARKETPLACE_CATEGORY = "Developer Tools"
MARKETPLACE_INSTALLATION = "AVAILABLE"
MARKETPLACE_AUTHENTICATION = "ON_INSTALL"
_ALLOWED_INSTALLATION = frozenset(
    {"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"}
)
_ALLOWED_AUTHENTICATION = frozenset({"ON_INSTALL", "ON_USE"})


class MarketplaceError(ValueError):
    """Existing marketplace.json is missing, unreadable, or not an object."""


_NAME_PATTERN: re.Pattern[str] | None = None


def _identifier_pattern() -> re.Pattern[str]:
    global _NAME_PATTERN
    if _NAME_PATTERN is None:
        _NAME_PATTERN = marketplace_name_pattern()
    return _NAME_PATTERN


def _require_string_name(value: object, *, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketplaceError("{0} must be a non-empty string".format(what))
    return value.strip()


def _validate_required_non_empty_string(value: object, *, what: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketplaceError("{0} must be a non-empty string".format(what))


def _validate_optional_list_of_strings(value: object, *, what: str) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise MarketplaceError("{0} must be an array".format(what))
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise MarketplaceError(
                "{0} must be a list of non-empty strings".format(what)
            )


def _require_identifier_name(value: object, *, what: str) -> str:
    name = _require_string_name(value, what=what)
    if not _identifier_pattern().fullmatch(name):
        raise MarketplaceError(
            "{0} must match marketplace identifier pattern: {1!r}".format(
                what, name
            )
        )
    return name


def _validate_marketplace_interface(data: dict) -> None:
    interface = data.get("interface")
    if not isinstance(interface, dict):
        raise MarketplaceError("marketplace interface must be an object")
    specs = marketplace_interface_specs()
    for field, kind in specs.items():
        if kind == "required-non-empty-string":
            _validate_required_non_empty_string(
                interface.get(field),
                what="marketplace interface.{0}".format(field),
            )


def _validate_policy_products(policy: dict) -> None:
    specs = marketplace_policy_specs()
    kind = specs.get("products")
    if kind == "optional-list-of-strings":
        _validate_optional_list_of_strings(
            policy.get("products"),
            what="marketplace policy.products",
        )


def _local_plugin_directory(staging_root: Path, path: str, name: str) -> Path:
    rel = path.replace("\\", "/").strip()
    if rel.startswith("./"):
        rel = rel[2:]
    parts = [part for part in rel.split("/") if part and part != "."]
    expected = ["plugins", name]
    if parts != expected or name in (".", ".."):
        raise MarketplaceError(
            "marketplace source.path must be ./plugins/{0}: {1}".format(name, path)
        )
    dest = staging_root.joinpath(*parts)
    try:
        dest.resolve().relative_to(staging_root.resolve())
    except ValueError as exc:
        raise MarketplaceError(
            "marketplace path must stay inside staging: {0}".format(path)
        ) from exc
    if not dest.is_dir():
        raise MarketplaceError(
            "marketplace plugin path is not a directory: {0}".format(path)
        )
    return dest


def _marketplace_plugin_name(rel: str) -> str:
    text = rel.replace("\\", "/").strip()
    if not text:
        raise RuntimeError("empty marketplace plugin path")
    parts = [part for part in text.split("/") if part and part != "."]
    if ".." in parts:
        raise RuntimeError(
            "marketplace path must stay inside staging: {0}".format(rel)
        )
    if parts and parts[0] == "plugins":
        if len(parts) != 2 or not parts[1]:
            raise RuntimeError(
                "marketplace path must be ./plugins/<name>: {0}".format(rel)
            )
        return parts[1]
    if len(parts) == 1:
        return parts[0]
    raise RuntimeError(
        "marketplace path must be ./plugins/<name>: {0}".format(rel)
    )


def marketplace_plugin_entry(name: str) -> dict:
    return {
        "name": name,
        "category": MARKETPLACE_CATEGORY,
        "policy": {
            "installation": MARKETPLACE_INSTALLATION,
            "authentication": MARKETPLACE_AUTHENTICATION,
        },
        "source": {
            "source": "local",
            "path": "./plugins/{0}".format(name),
        },
    }


def write_marketplace(staging_root: Path, plugin_rel_dirs: list[str]) -> Path:
    """写 staging_root/.agents/plugins/marketplace.json。"""
    plugins = [
        marketplace_plugin_entry(_marketplace_plugin_name(rel))
        for rel in plugin_rel_dirs
    ]
    payload = {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": MARKETPLACE_DISPLAY_NAME},
        "plugins": plugins,
    }
    out = staging_root / ".agents" / "plugins" / "marketplace.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def read_marketplace_payload(staging_root: Path) -> dict | None:
    market_path = staging_root / ".agents" / "plugins" / "marketplace.json"
    if not market_path.is_file():
        return None
    try:
        data = json.loads(market_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketplaceError(
            "marketplace.json is not readable JSON: {0}".format(exc)
        ) from exc
    if not isinstance(data, dict):
        raise MarketplaceError("marketplace.json root must be an object")
    _require_identifier_name(data.get("name"), what="marketplace name")
    _validate_marketplace_interface(data)
    plugins = data.get("plugins", [])
    if plugins is None:
        plugins = []
    if not isinstance(plugins, list):
        raise MarketplaceError("marketplace.json plugins must be an array")
    _marketplace_plugin_entries(plugins, staging_root)
    return data


def _marketplace_plugin_entries(plugins: list, staging_root: Path) -> list[dict]:
    names: set[str] = set()
    entries: list[dict] = []
    for entry in plugins:
        if not isinstance(entry, dict):
            raise MarketplaceError("marketplace plugin entry must be an object")
        existing_name = _require_identifier_name(
            entry.get("name"), what="marketplace plugin name"
        )
        if existing_name in names:
            raise MarketplaceError(
                "duplicate marketplace plugin name: {0}".format(existing_name)
            )
        source = entry.get("source")
        if not isinstance(source, dict):
            raise MarketplaceError("marketplace plugin source must be an object")
        kind = source.get("source")
        path = source.get("path")
        if kind != "local" or not isinstance(path, str) or not path.strip():
            raise MarketplaceError(
                "marketplace plugin source must be local with a path"
            )
        policy = entry.get("policy")
        if not isinstance(policy, dict):
            raise MarketplaceError("marketplace plugin policy must be an object")
        installation = policy.get("installation")
        if installation not in _ALLOWED_INSTALLATION:
            raise MarketplaceError(
                "illegal marketplace policy.installation: {0}".format(installation)
            )
        authentication = policy.get("authentication")
        if authentication not in _ALLOWED_AUTHENTICATION:
            raise MarketplaceError(
                "illegal marketplace policy.authentication: {0}".format(
                    authentication
                )
            )
        _validate_policy_products(policy)
        category = entry.get("category")
        if not isinstance(category, str) or not category.strip():
            raise MarketplaceError(
                "marketplace plugin category must be a non-empty string"
            )
        _local_plugin_directory(staging_root, path, existing_name)
        names.add(existing_name)
        entries.append(entry)
    return entries


def marketplace_registered_names(staging_root: Path) -> tuple[str, ...]:
    payload = read_marketplace_payload(staging_root)
    if payload is None:
        return ()
    names: list[str] = []
    for entry in _marketplace_plugin_entries(payload.get("plugins") or [], staging_root):
        names.append(entry["name"].strip())
    return tuple(names)


def _with_local_plugin_source(entry: dict, name: str) -> dict:
    updated = dict(entry)
    updated["name"] = name
    source = updated.get("source")
    if isinstance(source, dict):
        source = dict(source)
        source["source"] = "local"
        source["path"] = "./plugins/{0}".format(name)
        updated["source"] = source
    else:
        updated["source"] = {
            "source": "local",
            "path": "./plugins/{0}".format(name),
        }
    return updated


def upsert_marketplace_plugin(staging_root: Path, name: str) -> Path:
    """Merge ``name`` into marketplace.json without rewriting sibling entries."""
    existing = read_marketplace_payload(staging_root)
    if existing is None:
        return write_marketplace(staging_root, ["plugins/{0}".format(name)])
    merged: list[dict] = []
    found = False
    for entry in _marketplace_plugin_entries(existing.get("plugins") or [], staging_root):
        existing_name = entry["name"].strip()
        if existing_name == name:
            merged.append(_with_local_plugin_source(entry, name))
            found = True
            continue
        if (staging_root / "plugins" / existing_name).is_dir():
            merged.append(entry)
    if not found:
        merged.append(marketplace_plugin_entry(name))
    payload = dict(existing)
    payload["plugins"] = merged
    if not isinstance(payload.get("name"), str) or not payload.get("name", "").strip():
        payload["name"] = MARKETPLACE_NAME
    interface = payload.get("interface")
    if not isinstance(interface, dict):
        payload["interface"] = {"displayName": MARKETPLACE_DISPLAY_NAME}
    elif not str(interface.get("displayName", "")).strip():
        interface = dict(interface)
        interface["displayName"] = MARKETPLACE_DISPLAY_NAME
        payload["interface"] = interface
    out = staging_root / ".agents" / "plugins" / "marketplace.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out
