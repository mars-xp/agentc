# -*- coding: utf-8 -*-
"""Parse a Claude Code-layout plugin directory into ParsedPlugin."""
from __future__ import annotations

import json
import re
from pathlib import Path

from plugin_compiler.contracts import (
    default_component_entries,
    source_requires_semver,
)
from plugin_compiler.documents import (
    extend_scanned_load_issues,
    issue_from_load_error,
    load_scanned_documents,
)
from plugin_compiler.manifests import (
    PluginIdentity,
    extension_fields_from_manifest,
    identity_from_manifest_dict,
)
from plugin_compiler.model import CapabilityKind, CapabilityRef, CompilerIssue
from plugin_compiler.skills import SkillDocument, load_skill_document
from plugin_targets.packaging_overlays import load_target_overlays_for_parse
from plugin_targets.source_paths import (
    FILE_EXPAND_KINDS,
    DeclaredPathError,
    assert_skill_md_contained,
    component_has_inline_object,
    declared_object_or_path_values,
    declared_paths_error,
    experimental_nested_paths,
    files_for_path,
    normalize_declared_paths,
    posix_relative,
    resolve_declared_path,
    scan_default_skill_dirs,
    scan_skill_root,
    skill_dirs_for_path,
    walk_host_default_entries,
)
from plugin_targets.internal_refs import declared_internal_resources, internal_ref_issues
from plugin_targets.spi import DeclaredResource, ParsedPlugin, SourceExtension

CLAUDE_MANIFEST_RELATIVE = Path(".claude-plugin/plugin.json")
_COMPONENT_KEYS = frozenset(
    {
        "skills",
        "mcpServers",
        "hooks",
        "rules",
        "commands",
        "agents",
        "workflows",
        "lspServers",
        "outputStyles",
        "experimental",
    }
)
_KIND_BY_COMPONENT = {
    "skills": CapabilityKind.SKILL,
    "mcpServers": CapabilityKind.MCP,
    "hooks": CapabilityKind.HOOK,
    "rules": CapabilityKind.RULE,
    "commands": CapabilityKind.COMMAND,
    "agents": CapabilityKind.AGENT,
    "workflows": CapabilityKind.WORKFLOW,
    "lspServers": CapabilityKind.LSP,
    "outputStyles": CapabilityKind.OUTPUT_STYLE,
}
_OBJECT_COMPONENT_KEYS = frozenset({"hooks", "mcpServers", "lspServers"})
_EXPERIMENTAL_KIND = {
    "themes": CapabilityKind.THEME,
    "monitors": CapabilityKind.MONITOR,
}


def _host_default_entries() -> tuple[tuple[str, CapabilityKind], ...]:
    mapped: list[tuple[str, CapabilityKind]] = []
    for relative, kind_str in default_component_entries("claude-code"):
        try:
            kind = CapabilityKind(kind_str)
        except ValueError:
            continue
        mapped.append((relative, kind))
    return tuple(mapped)


_CLAUDE_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _claude_identity_issues(identity, manifest_path: Path) -> list[CompilerIssue]:
    issues: list[CompilerIssue] = []
    version = identity.version
    must_check_semver = version is not None or source_requires_semver("claude-code")
    if must_check_semver and (
        not isinstance(version, str) or _CLAUDE_SEMVER.fullmatch(version) is None
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


def _capability(kind: CapabilityKind, source_path: str) -> CapabilityRef:
    return CapabilityRef(
        capability_id="{0}:{1}".format(kind.value, source_path),
        kind=kind,
        source_path=source_path,
        source_dialect="claude-code",
    )


def _collect_host_defaults(
    plugin_dir: Path,
) -> tuple[tuple[DeclaredResource, ...], tuple[CapabilityRef, ...]]:
    declared_pairs, cap_pairs, _errors = walk_host_default_entries(
        plugin_dir, _host_default_entries()
    )
    declared = tuple(
        DeclaredResource(source_path=source_path, kind=kind)
        for source_path, kind in declared_pairs
    )
    capabilities = tuple(
        _capability(kind, source_path) for kind, source_path in cap_pairs
    )
    return declared, capabilities


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
                        skill_md, plugin_dir, source_dialect="claude-code"
                    )
                )
            except RuntimeError as exc:
                issue = issue_from_load_error(
                    exc, posix_relative(plugin_dir, skill_md)
                )
                if issue is not None:
                    issues.append(issue)

    found, _discovery_errors = scan_default_skill_dirs(plugin_dir)
    for skill_dir in found:
        _add_skill_dir(skill_dir)

    default_declared, default_caps = _collect_host_defaults(plugin_dir)
    for resource in default_declared:
        _add_declared(resource.source_path, resource.kind)
    for capability in default_caps:
        _add_capability(capability.kind, capability.source_path)

    manifest_relative = CLAUDE_MANIFEST_RELATIVE.as_posix()
    for key, value in fields.items():
        if key not in _COMPONENT_KEYS:
            continue
        kind = _KIND_BY_COMPONENT.get(key)
        if key == "experimental":
            pairs, error = experimental_nested_paths(value)
            if error is not None:
                continue
            for field, declared_path in pairs:
                nested_kind = _EXPERIMENTAL_KIND[field]
                try:
                    resolved = resolve_declared_path(plugin_dir, declared_path)
                except DeclaredPathError:
                    continue
                relative = posix_relative(plugin_dir, resolved)
                _add_declared(relative, nested_kind.value)
                if nested_kind in FILE_EXPAND_KINDS:
                    for file_path in files_for_path(resolved, nested_kind):
                        file_rel = posix_relative(plugin_dir, file_path)
                        _add_declared(file_rel, nested_kind.value)
                        _add_capability(nested_kind, file_rel)
                    continue
                if resolved.exists():
                    _add_capability(nested_kind, relative)
            continue
        if key in _OBJECT_COMPONENT_KEYS:
            paths, error = declared_object_or_path_values(value)
            if error is not None or kind is None:
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
                    relative = posix_relative(plugin_dir, resolved)
                    _add_declared(relative, kind.value)
                    if kind in FILE_EXPAND_KINDS:
                        for file_path in files_for_path(resolved, kind):
                            file_rel = posix_relative(plugin_dir, file_path)
                            _add_declared(file_rel, kind.value)
                            _add_capability(kind, file_rel)
                        continue
                    if resolved.exists():
                        _add_capability(kind, relative)
            continue
        if declared_paths_error(value) is not None:
            continue
        for declared_path in normalize_declared_paths(value):
            try:
                resolved = resolve_declared_path(plugin_dir, declared_path)
            except DeclaredPathError:
                continue
            relative = posix_relative(plugin_dir, resolved)
            if key == "skills":
                _add_declared(relative, CapabilityKind.SKILL.value)
                for skill_dir in skill_dirs_for_path(resolved):
                    _add_skill_dir(skill_dir)
                continue
            if kind is None:
                _add_declared(relative, "file")
                continue
            _add_declared(relative, kind.value)
            if kind in FILE_EXPAND_KINDS:
                for file_path in files_for_path(resolved, kind):
                    file_rel = posix_relative(plugin_dir, file_path)
                    _add_declared(file_rel, kind.value)
                    _add_capability(kind, file_rel)
                continue
            if resolved.exists():
                _add_capability(kind, relative)

    return tuple(declared), tuple(capabilities), tuple(skills), tuple(issues)


def _claude_identity_from_dir(
    plugin_dir: Path, manifest_path: Path
) -> tuple[dict, PluginIdentity]:
    if not manifest_path.is_file():
        identity = PluginIdentity(
            name=plugin_dir.name,
            version=None,
            description=None,
            author=None,
        )
        return {}, identity
    raw = _read_json_object(manifest_path)
    return raw, identity_from_manifest_dict(raw, path=manifest_path)


def validate_claude_source(plugin_dir: Path) -> tuple[CompilerIssue, ...]:
    plugin_dir = Path(plugin_dir)
    manifest_path = plugin_dir / CLAUDE_MANIFEST_RELATIVE
    issues: list[CompilerIssue] = []
    try:
        raw, identity = _claude_identity_from_dir(plugin_dir, manifest_path)
    except Exception as exc:
        return (
            CompilerIssue(
                severity="block",
                code="identity-failed",
                path=str(manifest_path),
                message="load Claude Code identity raised {0}: {1}".format(
                    type(exc).__name__, exc
                ),
            ),
        )
    issues.extend(_claude_identity_issues(identity, manifest_path))
    extension = extension_fields_from_manifest(raw)
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
    _, _, default_errors = walk_host_default_entries(
        plugin_dir, _host_default_entries()
    )
    for exc in default_errors:
        issues.append(
            CompilerIssue(
                severity="block",
                code=exc.code,
                path=str(plugin_dir),
                message=exc.message,
            )
        )
    for key, value in extension.items():
        if key not in _COMPONENT_KEYS:
            continue
        if value is None:
            continue
        if key == "experimental":
            pairs, error = experimental_nested_paths(value)
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
            for field, declared_path in pairs:
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
                                field, declared_path
                            ),
                        )
                    )
            continue
        if key in _OBJECT_COMPONENT_KEYS:
            paths, error = declared_object_or_path_values(value)
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
        error = declared_paths_error(value)
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
        for declared_path in normalize_declared_paths(value):
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
    extend_scanned_load_issues(
        issues,
        plugin_dir,
        component_caps,
        default_source_host="claude-code",
    )
    issues.extend(
        internal_ref_issues(
            plugin_dir,
            extension,
            source_dialect="claude-code",
            extra_field_maps=tuple(
                item.fields
                for item in load_target_overlays_for_parse(
                    plugin_dir,
                    source_dialect="claude-code",
                    extension_fields=extension,
                )
            ),
        )
    )
    return tuple(issues)


def parse_claude_plugin(plugin_dir: Path) -> ParsedPlugin:
    plugin_dir = Path(plugin_dir)
    manifest_path = plugin_dir / CLAUDE_MANIFEST_RELATIVE
    raw, identity = _claude_identity_from_dir(plugin_dir, manifest_path)
    extension_fields = extension_fields_from_manifest(raw)
    declared_resources, component_caps, skills, skill_issues = _collect_from_manifest(
        plugin_dir, extension_fields
    )
    metadata_cap = _capability(
        CapabilityKind.PLUGIN_METADATA,
        CLAUDE_MANIFEST_RELATIVE.as_posix(),
    )
    capabilities = tuple(
        sorted(
            (*component_caps, metadata_cap),
            key=lambda item: item.capability_id,
        )
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
        default_source_host="claude-code",
        collected_issues=load_issues,
    )
    if not skills:
        skills = scanned_skills
    overlays = load_target_overlays_for_parse(
        plugin_dir,
        source_dialect="claude-code",
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
        source_dialect="claude-code",
        extra_field_maps=extra_maps,
    )
    return ParsedPlugin(
        plugin_dir=plugin_dir,
        plugin_name=identity.name,
        source_dialect="claude-code",
        identity=identity,
        source_extension=SourceExtension(
            dialect="claude-code", fields=extension_fields
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
