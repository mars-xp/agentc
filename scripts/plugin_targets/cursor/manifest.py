from __future__ import annotations

import json
from pathlib import Path

from plugin_compiler.manifests import PluginIdentity
from plugin_compiler.model import CompilerIssue, Target
from plugin_compiler.text import write_text_utf8_lf
from plugin_targets.manifest_common import (
    apply_overlay_or_extension,
    dropped_manifest_field_issues as _dropped_manifest_field_issues,
)
from plugin_targets.spi import ParsedPlugin

CURSOR_MANIFEST_FIELDS = frozenset(
    {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "logo",
        "rules",
        "agents",
        "skills",
        "commands",
        "hooks",
        "mcpServers",
        "variables",
    }
)
CURSOR_MANIFEST_RELATIVE = Path(".cursor-plugin/plugin.json")
_COMPONENT_FIELDS = frozenset(
    {
        "rules",
        "agents",
        "skills",
        "commands",
        "hooks",
        "mcpServers",
    }
)


def _put_identity(result: dict, identity: PluginIdentity) -> None:
    result["name"] = identity.name
    if identity.version is not None:
        result["version"] = identity.version
    if identity.description is not None:
        result["description"] = identity.description
    if identity.author is not None:
        result["author"] = identity.author
    if identity.homepage is not None:
        result["homepage"] = identity.homepage
    if identity.repository is not None:
        result["repository"] = identity.repository
    if identity.license is not None:
        result["license"] = identity.license
    if identity.keywords is not None:
        result["keywords"] = list(identity.keywords)


def dropped_manifest_field_issues(parsed: ParsedPlugin) -> tuple[CompilerIssue, ...]:
    """Warn when source-extension keys cannot be written into a Cursor manifest."""
    translated = frozenset({"resources"}).intersection(
        parsed.source_extension.fields
    )
    return _dropped_manifest_field_issues(
        parsed,
        target_id=Target.CURSOR.value,
        whitelist=CURSOR_MANIFEST_FIELDS,
        manifest_relative=CURSOR_MANIFEST_RELATIVE.as_posix(),
        translated_keys=translated,
    )


def build_cursor_manifest(
    parsed: ParsedPlugin,
    tree: Path | None = None,
    collected_issues: list[CompilerIssue] | None = None,
) -> dict:
    component_root = parsed.plugin_dir if tree is None else tree
    result: dict = {}
    _put_identity(result, parsed.identity)
    overlay_issues = apply_overlay_or_extension(
        result,
        parsed,
        Target.CURSOR.value,
        whitelist=CURSOR_MANIFEST_FIELDS,
        component_fields=_COMPONENT_FIELDS,
        manifest_relative=CURSOR_MANIFEST_RELATIVE.as_posix(),
        restrict_overlay_to_whitelist=True,
    )
    if collected_issues is not None:
        collected_issues.extend(overlay_issues)
    for field, relative in (
        ("rules", "rules"),
        ("agents", "agents"),
        ("skills", "skills"),
        ("commands", "commands"),
    ):
        result.pop(field, None)
        if (component_root / relative).is_dir():
            result[field] = "./{0}/".format(relative)
    if (component_root / "hooks" / "hooks.json").is_file():
        result["hooks"] = "./hooks/hooks.json"
    if (component_root / "mcp.json").is_file():
        result["mcpServers"] = "./mcp.json"
    result.pop("resources", None)
    unknown = sorted(set(result).difference(CURSOR_MANIFEST_FIELDS))
    if unknown:
        raise RuntimeError(
            "{0} manifest contains unsupported fields: {1}".format(
                Target.CURSOR.value, ", ".join(unknown)
            )
        )
    return result


def write_cursor_manifest(
    parsed: ParsedPlugin,
    destination: Path,
    collected_issues: list[CompilerIssue] | None = None,
) -> Path:
    output = destination / CURSOR_MANIFEST_RELATIVE
    write_text_utf8_lf(
        output,
        json.dumps(
            build_cursor_manifest(
                parsed, tree=destination, collected_issues=collected_issues
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return output
