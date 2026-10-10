# -*- coding: utf-8 -*-
"""Parse a Cursor-layout plugin directory into ParsedPlugin."""
from __future__ import annotations

import re
from pathlib import Path

from plugin_compiler.contracts import source_requires_semver
from plugin_compiler.documents import (
    extend_scanned_load_issues,
    issue_from_load_error,
    load_scanned_documents,
)
from plugin_compiler.manifests import (
    extension_fields_from_manifest,
    identity_from_manifest_dict,
    load_cursor_manifest_raw,
)
from plugin_compiler.model import CapabilityKind, CapabilityRef, CompilerIssue
from plugin_compiler.scanner import scan_plugin
from plugin_compiler.skills import SkillDocument, load_skill_document
from plugin_targets.packaging_overlays import load_target_overlays_for_parse
from plugin_targets.source_paths import (
    FILE_EXPAND_KINDS,
    DeclaredPathError,
    assert_skill_md_contained,
    component_has_inline_object,
    declared_object_or_path_values,
    declared_paths_error,
    files_for_path,
    normalize_declared_paths,
    posix_relative,
    resolve_declared_path,
    scan_default_skill_dirs,
    scan_skill_root,
    skill_dirs_for_path,
)
from plugin_targets.internal_refs import declared_internal_resources, internal_ref_issues
from plugin_targets.spi import DeclaredResource, ParsedPlugin, SourceExtension

CURSOR_MANIFEST_RELATIVE = Path(".cursor-plugin/plugin.json")
_COMPONENT_KEYS = frozenset(
    {
        "skills",
        "rules",
        "agents",
        "commands",
        "hooks",
        "mcpServers",
    }
)
_KIND_BY_COMPONENT = {
    "skills": CapabilityKind.SKILL,
    "rules": CapabilityKind.RULE,
    "agents": CapabilityKind.AGENT,
    "commands": CapabilityKind.COMMAND,
    "hooks": CapabilityKind.HOOK,
    "mcpServers": CapabilityKind.MCP,
}
_OBJECT_COMPONENT_KEYS = frozenset({"hooks", "mcpServers"})
_CURSOR_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _cursor_identity_issues(identity, manifest_path: Path) -> list[CompilerIssue]:
    issues: list[CompilerIssue] = []
    version = identity.version
    must_check_semver = version is not None or source_requires_semver("cursor")
    if must_check_semver and (
        not isinstance(version, str) or _CURSOR_SEMVER.fullmatch(version) is None
    ):
        issues.append(
            CompilerIssue(
                severity="block",
                code="invalid-identity",
                path=str(manifest_path),
                message=(
                    "version must be SemVer MAJOR.MINOR.PATCH "
                    "with optional -pre / +build"
                ),
            )
        )
    author = identity.author
    if author is not None:
        name = author.get("name") if isinstance(author, dict) else None
        if not isinstance(name, str) or not name:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="invalid-identity",
                    path=str(manifest_path),
                    message=(
                        "author.name must be a non-empty string "
                        "when author is present"
                    ),
                )
            )
    return issues


def _declared_resources_from_scan(
    capabilities: tuple[CapabilityRef, ...],
) -> tuple[DeclaredResource, ...]:
    """Align Cursor declared_resources with scan_plugin capability paths."""
    declared: list[DeclaredResource] = []
    seen: set[tuple[str, str]] = set()
    for capability in capabilities:
        key = (capability.source_path, capability.kind.value)
        if key in seen:
            continue
        seen.add(key)
        declared.append(
            DeclaredResource(
                source_path=capability.source_path,
                kind=capability.kind.value,
            )
        )
    return tuple(declared)


def _capability(kind: CapabilityKind, source_path: str) -> CapabilityRef:
    return CapabilityRef(
        capability_id="{0}:{1}".format(kind.value, source_path),
        kind=kind,
        source_path=source_path,
        source_dialect="cursor",
    )


def _explicit_component_keys(fields: dict) -> frozenset[str]:
    return frozenset(
        key
        for key in _COMPONENT_KEYS
        if key in fields and fields[key] is not None
    )


def _suppressed_by_explicit(source_path: str, explicit: frozenset[str]) -> bool:
    posix = source_path.replace("\\", "/")
    if "skills" in explicit and (posix == "skills" or posix.startswith("skills/")):
        return True
    if "rules" in explicit and (posix == "rules" or posix.startswith("rules/")):
        return True
    if "commands" in explicit and (
        posix == "commands" or posix.startswith("commands/")
    ):
        return True
    if "agents" in explicit and (posix == "agents" or posix.startswith("agents/")):
        return True
    if "hooks" in explicit and (posix == "hooks" or posix.startswith("hooks/")):
        return True
    if "mcpServers" in explicit and posix in {"mcp.json", ".mcp.json"}:
        return True
    return False


def _merge_capabilities(
    *groups: tuple[CapabilityRef, ...] | list[CapabilityRef],
) -> tuple[CapabilityRef, ...]:
    seen: set[str] = set()
    merged: list[CapabilityRef] = []
    for group in groups:
        for capability in group:
            if capability.capability_id in seen:
                continue
            seen.add(capability.capability_id)
            merged.append(capability)
    return tuple(sorted(merged, key=lambda item: item.capability_id))


def _merge_declared(
    *groups: tuple[DeclaredResource, ...] | list[DeclaredResource],
) -> tuple[DeclaredResource, ...]:
    seen: set[tuple[str, str]] = set()
    merged: list[DeclaredResource] = []
    for group in groups:
        for resource in group:
            key = (resource.source_path, resource.kind)
            if key in seen:
                continue
            seen.add(key)
            merged.append(resource)
    return tuple(merged)


def _collect_from_manifest(
    plugin_dir: Path, fields: dict
) -> tuple[
    tuple[DeclaredResource, ...],
    tuple[CapabilityRef, ...],
    tuple[SkillDocument, ...],
    tuple[CompilerIssue, ...],
]:
    declared: list[DeclaredResource] = []
    capabilities: list[CapabilityRef] = []
    skills: list[SkillDocument] = []
    issues: list[CompilerIssue] = []
    seen_skills: set[str] = set()
    seen_declared: set[tuple[str, str]] = set()
    seen_caps: set[str] = set()
    manifest_relative = CURSOR_MANIFEST_RELATIVE.as_posix()

    def _add_declared(source_path: str, kind: str) -> None:
        key = (source_path, kind)
        if key in seen_declared:
            return
        seen_declared.add(key)
        declared.append(DeclaredResource(source_path=source_path, kind=kind))

    def _add_capability(kind: CapabilityKind, source_path: str) -> None:
        cap = _capability(kind, source_path)
        if cap.capability_id in seen_caps:
            return
        seen_caps.add(cap.capability_id)
        capabilities.append(cap)

    def _add_skill_dir(skill_dir: Path) -> None:
        try:
            skill_rel = posix_relative(plugin_dir, skill_dir)
        except ValueError:
            return
        if skill_rel in seen_skills:
            return
        seen_skills.add(skill_rel)
        _add_declared(skill_rel, CapabilityKind.SKILL.value)
        _add_capability(CapabilityKind.SKILL, skill_rel)
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            try:
                assert_skill_md_contained(skill_md, plugin_dir)
            except DeclaredPathError:
                return
            try:
                skills.append(
                    load_skill_document(
                        skill_md, plugin_dir, source_dialect="cursor"
                    )
                )
            except RuntimeError as exc:
                issue = issue_from_load_error(
                    exc, posix_relative(plugin_dir, skill_md)
                )
                if issue is not None:
                    issues.append(issue)

    def _add_resolved_component(key: str, kind: CapabilityKind, resolved: Path) -> None:
        relative = posix_relative(plugin_dir, resolved)
        if key == "skills":
            _add_declared(relative, CapabilityKind.SKILL.value)
            for skill_dir in skill_dirs_for_path(resolved):
                _add_skill_dir(skill_dir)
            return
        _add_declared(relative, kind.value)
        if kind in FILE_EXPAND_KINDS:
            for file_path in files_for_path(resolved, kind):
                file_rel = posix_relative(plugin_dir, file_path)
                _add_declared(file_rel, kind.value)
                _add_capability(kind, file_rel)
            return
        if resolved.exists():
            _add_capability(kind, relative)

    for key, value in fields.items():
        if key not in _COMPONENT_KEYS:
            continue
        kind = _KIND_BY_COMPONENT.get(key)
        if kind is None:
            continue
        if key in _OBJECT_COMPONENT_KEYS:
            paths, error = declared_object_or_path_values(value)
            if error is not None:
                continue
            if component_has_inline_object(value):
                _add_declared(manifest_relative, kind.value)
                _add_capability(kind, manifest_relative)
            if paths:
                for declared_path in paths:
                    try:
                        resolved = resolve_declared_path(plugin_dir, declared_path)
                    except DeclaredPathError:
                        continue
                    _add_resolved_component(key, kind, resolved)
            continue
        if declared_paths_error(value) is not None:
            continue
        for declared_path in normalize_declared_paths(value):
            try:
                resolved = resolve_declared_path(plugin_dir, declared_path)
            except DeclaredPathError:
                continue
            _add_resolved_component(key, kind, resolved)

    return tuple(declared), tuple(capabilities), tuple(skills), tuple(issues)


def parse_cursor_plugin(plugin_dir: Path) -> ParsedPlugin:
    plugin_dir = Path(plugin_dir)
    raw = load_cursor_manifest_raw(plugin_dir)
    identity = identity_from_manifest_dict(
        raw, path=plugin_dir / ".cursor-plugin" / "plugin.json"
    )
    extension_fields = extension_fields_from_manifest(raw)
    (
        declared_resources,
        component_caps,
        collected_skills,
        skill_issues,
    ) = _collect_from_manifest(plugin_dir, extension_fields)
    explicit = _explicit_component_keys(extension_fields)
    kept_scan = tuple(
        capability
        for capability in scan_plugin(plugin_dir, "cursor")
        if not _suppressed_by_explicit(capability.source_path, explicit)
    )
    capabilities = _merge_capabilities(component_caps, kept_scan)
    declared_resources = _merge_declared(
        declared_resources, _declared_resources_from_scan(kept_scan)
    )
    load_issues: list[CompilerIssue] = list(skill_issues)
    (
        commands,
        rules,
        hooks,
        agents,
        workflows,
        mcp_documents,
        connectors,
        lsp_documents,
        output_styles,
        themes,
        monitors,
        scanned_skills,
    ) = load_scanned_documents(
        plugin_dir,
        capabilities,
        default_source_host="cursor",
        collected_issues=load_issues,
    )
    if "skills" in explicit:
        skills = collected_skills
    else:
        skills = collected_skills or scanned_skills
    overlays = load_target_overlays_for_parse(
        plugin_dir,
        source_dialect="cursor",
        extension_fields=extension_fields,
    )
    extra_maps = tuple(item.fields for item in overlays)
    declared_resources = declared_internal_resources(
        plugin_dir,
        extension_fields,
        hooks=hooks,
        mcp_documents=mcp_documents,
        lsp_documents=lsp_documents,
        declared=declared_resources,
        source_dialect="cursor",
        extra_field_maps=extra_maps,
    )
    return ParsedPlugin(
        plugin_dir=plugin_dir,
        plugin_name=identity.name,
        source_dialect="cursor",
        identity=identity,
        source_extension=SourceExtension(
            dialect="cursor",
            fields=extension_fields,
        ),
        capabilities=capabilities,
        skills=skills,
        commands=commands,
        rules=rules,
        hooks=hooks,
        agents=agents,
        workflows=workflows,
        mcp_documents=mcp_documents,
        connectors=connectors,
        lsp_documents=lsp_documents,
        output_styles=output_styles,
        themes=themes,
        monitors=monitors,
        override_config=None,
        target_overlays=overlays,
        declared_resources=declared_resources,
        source_issues=tuple(load_issues),
    )


def _declared_paths_for_cursor(
    key: str, value: object
) -> tuple[tuple[str, ...] | None, str | None]:
    if key in _OBJECT_COMPONENT_KEYS:
        return declared_object_or_path_values(value)
    error = declared_paths_error(value)
    if error is not None:
        return (), error
    return normalize_declared_paths(value), None


def validate_cursor_source(plugin_dir: Path) -> tuple[CompilerIssue, ...]:
    plugin_dir = Path(plugin_dir)
    manifest_path = plugin_dir / CURSOR_MANIFEST_RELATIVE
    issues: list[CompilerIssue] = []
    try:
        raw = load_cursor_manifest_raw(plugin_dir)
        identity = identity_from_manifest_dict(raw, path=manifest_path)
    except Exception as exc:
        return (
            CompilerIssue(
                severity="block",
                code="identity-failed",
                path=str(manifest_path),
                message="load plugin identity raised {0}: {1}".format(
                    type(exc).__name__, exc
                ),
            ),
        )
    issues.extend(_cursor_identity_issues(identity, manifest_path))
    extension = extension_fields_from_manifest(raw)
    if extension.get("skills") is None:
        _, discovery_errors = scan_default_skill_dirs(plugin_dir)
        for exc in discovery_errors:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code=exc.code,
                    path=str(plugin_dir / "skills"),
                    message=exc.message,
                )
            )
    for key, value in extension.items():
        if key not in _COMPONENT_KEYS:
            continue
        if value is None:
            continue
        paths, error = _declared_paths_for_cursor(key, value)
        if error is not None:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="invalid-component-path",
                    path=str(manifest_path),
                    message="component {0!r} {1}".format(key, error),
                )
            )
            continue
        if paths is None:
            continue
        for declared_path in paths:
            try:
                resolved = resolve_declared_path(plugin_dir, declared_path)
            except DeclaredPathError as exc:
                issues.append(
                    CompilerIssue(
                        severity="block",
                        code=exc.code,
                        path=declared_path,
                        message=exc.message,
                    )
                )
                continue
            if not resolved.exists():
                issues.append(
                    CompilerIssue(
                        severity="block",
                        code="missing-component-path",
                        path=declared_path,
                        message="declared {0} path does not exist: {1}".format(
                            key, declared_path
                        ),
                    )
                )
                continue
            if key == "skills":
                _, skill_errors = scan_skill_root(plugin_dir, resolved)
                for exc in skill_errors:
                    issues.append(
                        CompilerIssue(
                            severity="block",
                            code=exc.code,
                            path=declared_path,
                            message=exc.message,
                        )
                    )
    _, component_caps, _, skill_issues = _collect_from_manifest(
        plugin_dir, extension
    )
    issues.extend(skill_issues)
    explicit = _explicit_component_keys(extension)
    kept_scan = tuple(
        capability
        for capability in scan_plugin(plugin_dir, "cursor")
        if not _suppressed_by_explicit(capability.source_path, explicit)
    )
    extend_scanned_load_issues(
        issues,
        plugin_dir,
        _merge_capabilities(component_caps, kept_scan),
        default_source_host="cursor",
    )
    issues.extend(
        internal_ref_issues(
            plugin_dir,
            extension,
            source_dialect="cursor",
            extra_field_maps=tuple(
                item.fields
                for item in load_target_overlays_for_parse(
                    plugin_dir,
                    source_dialect="cursor",
                    extension_fields=extension,
                )
            ),
        )
    )
    return tuple(issues)
