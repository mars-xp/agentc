from __future__ import annotations

import json
from pathlib import Path

from plugin_compiler.manifests import PluginIdentity
from plugin_compiler.model import CompilerIssue, Target
from plugin_compiler.text import write_text_utf8_lf
from plugin_targets.manifest_common import (
    apply_overlay_or_extension,
    dropped_manifest_field_issues,
)
from plugin_targets.spi import ParsedPlugin

CLAUDE_MANIFEST_FIELDS = frozenset(
    {
        "$schema",
        "name",
        "displayName",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "metadata",
        "defaultEnabled",
        "skills",
        "commands",
        "agents",
        "workflows",
        "hooks",
        "mcpServers",
        "lspServers",
        "outputStyles",
        "experimental",
        "userConfig",
        "channels",
        "dependencies",
    }
)
CLAUDE_MANIFEST_RELATIVE = Path(".claude-plugin/plugin.json")
_IDENTITY_FIELDS = frozenset({"name", "version", "author"})
_COMPONENT_FIELDS = frozenset(
    {
        "skills",
        "rules",
        "commands",
        "agents",
        "workflows",
        "hooks",
        "mcpServers",
        "apps",
        "lspServers",
        "outputStyles",
        "experimental",
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


def _manifest_component_entry(
    component_root: Path, relative: str, field: str
) -> str | list[str] | None:
    dir_path = component_root / relative
    if not dir_path.is_dir():
        return None
    if field == "agents":
        paths = sorted(
            "./{0}/{1}".format(relative, child.name)
            for child in dir_path.iterdir()
            if child.is_file()
        )
        if not paths:
            return None
        if len(paths) == 1:
            return paths[0]
        return paths
    return "./{0}/".format(relative)


def _validate_overlay_fields(fields: dict) -> None:
    forbidden = sorted(set(fields).intersection(_IDENTITY_FIELDS | _COMPONENT_FIELDS))
    if forbidden:
        raise RuntimeError(
            "overlay must not contain generated fields: {0}".format(
                ", ".join(forbidden)
            )
        )
    if "description" in fields and not isinstance(fields["description"], str):
        raise RuntimeError("overlay: description must be a string")


def build_claude_manifest(
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
        Target.CLAUDE_CODE.value,
        whitelist=CLAUDE_MANIFEST_FIELDS,
        component_fields=_COMPONENT_FIELDS,
        manifest_relative=CLAUDE_MANIFEST_RELATIVE.as_posix(),
        validate_overlay=_validate_overlay_fields,
    )
    if collected_issues is not None:
        collected_issues.extend(overlay_issues)
        translated = frozenset(
            {"commands", "rules", "agents", "workflows", "hooks", "resources"}
        ).intersection(parsed.source_extension.fields)
        collected_issues.extend(
            dropped_manifest_field_issues(
                parsed,
                target_id=Target.CLAUDE_CODE.value,
                whitelist=CLAUDE_MANIFEST_FIELDS,
                manifest_relative=CLAUDE_MANIFEST_RELATIVE.as_posix(),
                translated_keys=translated,
            )
        )
    for field, relative in (
        ("skills", "skills"),
        ("commands", "commands"),
        ("agents", "agents"),
        ("workflows", "workflows"),
        ("outputStyles", "output-styles"),
    ):
        result.pop(field, None)
        entry = _manifest_component_entry(component_root, relative, field)
        if entry is not None:
            result[field] = entry
    if (component_root / "hooks" / "hooks.json").is_file():
        result["hooks"] = "./hooks/hooks.json"
    if (component_root / ".mcp.json").is_file():
        result["mcpServers"] = "./.mcp.json"
    if (component_root / ".lsp.json").is_file():
        result["lspServers"] = "./.lsp.json"
    experimental = {}
    if (component_root / "themes").is_dir():
        experimental["themes"] = "./themes/"
    if (component_root / "monitors" / "monitors.json").is_file():
        experimental["monitors"] = "./monitors/monitors.json"
    if experimental:
        result["experimental"] = experimental
    result.pop("resources", None)
    unknown = sorted(set(result).difference(CLAUDE_MANIFEST_FIELDS))
    if unknown:
        raise RuntimeError(
            "{0} manifest contains unsupported fields: {1}".format(
                Target.CLAUDE_CODE.value, ", ".join(unknown)
            )
        )
    return result


def write_claude_manifest(
    parsed: ParsedPlugin,
    destination: Path,
    collected_issues: list[CompilerIssue] | None = None,
) -> Path:
    output = destination / CLAUDE_MANIFEST_RELATIVE
    write_text_utf8_lf(
        output,
        json.dumps(
            build_claude_manifest(
                parsed, tree=destination, collected_issues=collected_issues
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return output
