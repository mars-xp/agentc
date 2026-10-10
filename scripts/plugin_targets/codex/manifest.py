from __future__ import annotations

import json
import re
from pathlib import Path

from plugin_compiler.contracts import target_requires_semver
from plugin_compiler.manifests import PluginIdentity
from plugin_compiler.model import CompilerIssue, Target
from plugin_compiler.text import write_text_utf8_lf
from plugin_targets.manifest_common import (
    apply_overlay_or_extension,
    dropped_manifest_field_issues,
)
from plugin_targets.spi import ParsedPlugin

CODEX_MANIFEST_FIELDS = frozenset(
    {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "skills",
        "mcpServers",
        "apps",
        "hooks",
        "interface",
    }
)
CODEX_MANIFEST_RELATIVE = Path(".codex-plugin/plugin.json")
_CODEX_TARGET_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_CODEX_FUNCTIONAL_MANIFEST_FIELDS = frozenset({"variables"})
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


def codex_target_identity_issues(parsed: ParsedPlugin) -> tuple[CompilerIssue, ...]:
    if not target_requires_semver(Target.CODEX):
        return ()
    version = parsed.identity.version
    if isinstance(version, str) and _CODEX_TARGET_SEMVER.fullmatch(version) is not None:
        return ()
    return (
        CompilerIssue(
            severity="block",
            code="target-identity-version",
            path=CODEX_MANIFEST_RELATIVE.as_posix(),
            message=(
                "Codex target requires SemVer identity.version "
                "MAJOR.MINOR.PATCH with optional -pre / +build"
            ),
        ),
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


def _author_name(identity: PluginIdentity) -> str:
    author = identity.author
    if isinstance(author, dict):
        name = author.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if isinstance(identity.name, str) and identity.name.strip():
        return identity.name.strip()
    return "Unknown"


def _ensure_codex_validator_fields(result: dict, identity: PluginIdentity) -> None:
    if not isinstance(result.get("author"), dict):
        result["author"] = {"name": _author_name(identity)}
    interface = result.get("interface")
    if isinstance(interface, dict):
        return
    display_name = (
        identity.name.strip()
        if isinstance(identity.name, str) and identity.name.strip()
        else "Plugin"
    )
    description = (
        identity.description.strip()
        if isinstance(identity.description, str) and identity.description.strip()
        else display_name
    )
    result["interface"] = {
        "displayName": display_name,
        "shortDescription": description,
        "longDescription": description,
        "developerName": _author_name(identity),
        "category": "Developer Tools",
        "capabilities": ["Read"],
        "defaultPrompt": ["Use the {0} plugin.".format(display_name)],
    }


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
    if "interface" in fields and not isinstance(fields["interface"], dict):
        raise RuntimeError("overlay: interface must be an object")


def build_codex_manifest(
    parsed: ParsedPlugin,
    tree: Path | None = None,
    collected_issues: list[CompilerIssue] | None = None,
) -> dict:
    component_root = parsed.plugin_dir if tree is None else tree
    result: dict = {}
    _put_identity(result, parsed.identity)
    if collected_issues is not None:
        collected_issues.extend(codex_target_identity_issues(parsed))
    overlay_issues = apply_overlay_or_extension(
        result,
        parsed,
        Target.CODEX.value,
        whitelist=CODEX_MANIFEST_FIELDS,
        component_fields=_COMPONENT_FIELDS,
        manifest_relative=CODEX_MANIFEST_RELATIVE.as_posix(),
        validate_overlay=_validate_overlay_fields,
    )
    if collected_issues is not None:
        collected_issues.extend(overlay_issues)
        translated = frozenset(
            {"commands", "rules", "agents", "workflows", "hooks", "resources"}
        ).intersection(parsed.source_extension.fields)
        replaced = (
            frozenset({"displayName"})
            if "displayName" in parsed.source_extension.fields
            and isinstance(result.get("interface"), dict)
            else frozenset()
        )
        collected_issues.extend(
            dropped_manifest_field_issues(
                parsed,
                target_id=Target.CODEX.value,
                whitelist=CODEX_MANIFEST_FIELDS,
                manifest_relative=CODEX_MANIFEST_RELATIVE.as_posix(),
                translated_keys=translated,
                replaced_keys=replaced,
                functional_keys=_CODEX_FUNCTIONAL_MANIFEST_FIELDS,
            )
        )
    if (component_root / "skills").is_dir():
        result["skills"] = "./skills/"
    inline_mcp = parsed.source_extension.fields.get("mcpServers")
    if parsed.source_dialect == Target.CODEX.value and isinstance(inline_mcp, dict):
        result["mcpServers"] = inline_mcp
    elif (component_root / ".mcp.json").is_file():
        result["mcpServers"] = "./.mcp.json"
    if (component_root / ".app.json").is_file():
        result["apps"] = "./.app.json"
    # Official Codex validator rejects manifest field `hooks`.
    # Keep hooks/hooks.json on disk; rely on default discovery.
    result.pop("resources", None)
    _ensure_codex_validator_fields(result, parsed.identity)
    unknown = sorted(set(result).difference(CODEX_MANIFEST_FIELDS))
    if unknown:
        raise RuntimeError(
            "{0} manifest contains unsupported fields: {1}".format(
                Target.CODEX.value, ", ".join(unknown)
            )
        )
    return result


def write_codex_manifest(
    parsed: ParsedPlugin,
    destination: Path,
    collected_issues: list[CompilerIssue] | None = None,
) -> Path:
    output = destination / CODEX_MANIFEST_RELATIVE
    write_text_utf8_lf(
        output,
        json.dumps(
            build_codex_manifest(
                parsed, tree=destination, collected_issues=collected_issues
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return output
