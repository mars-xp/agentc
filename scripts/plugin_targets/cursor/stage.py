from __future__ import annotations

import copy
import json
import shutil
from dataclasses import replace
from pathlib import Path, PurePath

from plugin_compiler.hooks import remap_document, render_hooks_payload
from plugin_compiler.model import CapabilityKind, CapabilityRef, CompilerIssue, Target
from plugin_compiler.overrides import OverrideConfig, load_override_config, resolved_targets
from plugin_compiler.paths import BuildLayout
from plugin_compiler.rules import (
    cursor_rule_output_path,
    is_root_rule_source,
    render_cursor_mdc,
    rule_conversion_issue,
)
from plugin_compiler.staging import (
    _capability_ref,
    _component_source_roots_from_parsed,
    _copy_user_hook_scripts,
    _existing_skill_names,
    _has_symlink_component,
    _hook_script_candidates,
    _hooks_json_capability,
    _kind_component_roots,
    _normalized_parts,
    _relative_from_source_path,
    _reserved_rule_loader_issue,
    _rule_output_relative,
    _source_owned_by_kind,
    _shared_should_skip,
    _skill_source_roots_from_parsed,
    _write_contained,
    _write_generated_agents,
    _write_generated_commands,
    _write_generated_extras,
    _write_generated_mcp,
    _write_generated_skills,
    _write_generated_workflows,
    check_target_artifacts,
    copy_stage_files,
    iter_parsed_stage_files,
    write_runtime_host,
)
from plugin_targets.cursor.manifest import (
    dropped_manifest_field_issues,
    write_cursor_manifest,
)
from plugin_targets.internal_refs import internal_ref_issues
from plugin_targets.spi import ParsedPlugin

_CURSOR_EXCLUDED_ROOT_FILES = frozenset(
    {"AGENTS.md", "CLAUDE.md", ".app.json", ".lsp.json", ".mcp.json"}
)


def _cursor_should_skip(
    path: Path, plugin_dir: Path, config: OverrideConfig
) -> bool:
    if _shared_should_skip(path, plugin_dir, config, Target.CURSOR.value):
        return True
    relative = path.relative_to(plugin_dir)
    parts = _normalized_parts(relative)
    if not parts:
        return True
    return len(parts) == 1 and parts[0] in _CURSOR_EXCLUDED_ROOT_FILES


def iter_cursor_stage_files(
    parsed: ParsedPlugin,
    config: OverrideConfig | None = None,
) -> tuple[Path, ...]:
    plugin_dir = parsed.plugin_dir.resolve()
    if config is None:
        config = parsed.override_config
    if config is None:
        config = load_override_config(plugin_dir)
    return iter_parsed_stage_files(parsed, config, _cursor_should_skip)


def _imported_root_collision(
    plugin_dir: Path, document
) -> CompilerIssue | None:
    if not is_root_rule_source(document.source_path):
        return None
    relative = cursor_rule_output_path(document)
    existing = plugin_dir.joinpath(*_normalized_parts(PurePath(relative)))
    if not existing.is_file():
        return None
    return CompilerIssue(
        severity="block",
        code="imported-root-mdc-collision",
        path=document.source_path,
        message=(
            "Cursor output {0} already exists as a source rule; "
            "will not overwrite with {1}"
        ).format(relative, document.source_path),
    )


def _write_cursor_rules(
    plugin_dir: Path,
    destination: Path,
    config: OverrideConfig,
    rules,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    for document in rules:
        capability = _capability_ref(
            CapabilityKind.RULE, document.capability_id, document.source_path
        )
        if Target.CURSOR not in resolved_targets(config, capability):
            continue
        collision = _imported_root_collision(plugin_dir, document)
        if collision is not None:
            issues.append(collision)
        if not _source_owned_by_kind(
            document.source_path,
            CapabilityKind.RULE.value,
            component_source_roots,
        ):
            continue
        conversion = rule_conversion_issue(document, Target.CURSOR)
        if conversion is not None:
            issues.append(conversion)
            continue
        if collision is not None:
            continue
        try:
            relative = _rule_output_relative(
                document.source_path,
                _kind_component_roots(
                    component_source_roots, CapabilityKind.RULE.value
                ),
            )
        except ValueError as exc:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="write-outside-destination",
                    path=document.source_path,
                    message=str(exc),
                )
            )
            continue
        output = destination / _relative_from_source_path(relative)
        _write_contained(destination, output, render_cursor_mdc(document))
    return tuple(issues)


def _write_cursor_hooks(
    plugin_dir: Path,
    destination: Path,
    config: OverrideConfig,
    hooks,
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    users = []
    for document in hooks:
        user_path = plugin_dir.joinpath(
            *_normalized_parts(PurePath(document.source_path))
        )
        if _has_symlink_component(user_path, plugin_dir):
            continue
        capability = _capability_ref(
            CapabilityKind.HOOK, document.capability_id, document.source_path
        )
        if Target.CURSOR not in resolved_targets(config, capability):
            continue
        if any(
            item.replace("\\", "/") == "hooks/rule_loader.py"
            for item in _hook_script_candidates(document)
        ) and not any(item.code == "reserved-hook-path" for item in issues):
            issues.append(_reserved_rule_loader_issue())
        issues.extend(_copy_user_hook_scripts(plugin_dir, destination, document))
        users.append(document)
    if not users:
        return tuple(issues)
    merged_hooks: dict[str, list] = {}
    for user in users:
        remapped, remap_issues = remap_document(user, Target.CURSOR, plugin_dir)
        issues.extend(remap_issues)
        payload = render_hooks_payload(
            replace(remapped, source_host=Target.CURSOR.value),
            Target.CURSOR,
        )
        source = payload.get("hooks") if isinstance(payload, dict) else None
        if not isinstance(source, dict):
            continue
        for event_name, entries in source.items():
            copied = []
            if isinstance(entries, list):
                copied = [copy.deepcopy(entry) for entry in entries]
            merged_hooks.setdefault(event_name, []).extend(copied)
    generated_path = destination / "hooks" / "hooks.json"
    _write_contained(
        destination,
        generated_path,
        json.dumps({"hooks": merged_hooks}, ensure_ascii=False, indent=2) + "\n",
    )
    return tuple(issues)


def stage_cursor(
    parsed: ParsedPlugin,
    layout: BuildLayout,
    collected_issues: list[CompilerIssue] | None = None,
) -> Path:
    plugin_dir = parsed.plugin_dir.resolve()
    config = parsed.override_config
    if config is None:
        config = load_override_config(plugin_dir)
    destination = layout.intermediate(Target.CURSOR.value, parsed.plugin_name)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    if collected_issues is not None:
        collected_issues.extend(
            internal_ref_issues(
                plugin_dir,
                parsed.source_extension.fields,
                source_dialect=parsed.source_dialect,
                extra_field_maps=tuple(
                    item.fields for item in parsed.target_overlays
                ),
            )
        )
    component_roots = _component_source_roots_from_parsed(parsed)
    copy_stage_files(
        plugin_dir,
        destination,
        iter_cursor_stage_files(parsed, config),
        skill_source_roots=_skill_source_roots_from_parsed(parsed),
        component_source_roots=component_roots,
    )
    skill_issues = _write_generated_skills(
        parsed.skills, Target.CURSOR, destination, config, plugin_dir
    )
    rule_issues = _write_cursor_rules(
        plugin_dir, destination, config, parsed.rules, component_roots
    )
    command_issues = _write_generated_commands(
        parsed.commands, Target.CURSOR, destination, config, component_roots
    )
    claimed: dict[str, str] = {}
    hook_issues = _write_cursor_hooks(
        plugin_dir, destination, config, parsed.hooks
    )
    existing_names = _existing_skill_names(parsed.skills, destination)
    agent_issues = _write_generated_agents(
        parsed.agents,
        Target.CURSOR,
        destination,
        config,
        claimed,
        existing_names,
        component_roots,
    )
    workflow_issues = _write_generated_workflows(
        parsed.workflows,
        Target.CURSOR,
        destination,
        config,
        claimed,
        existing_names,
    )
    mcp_issues = _write_generated_mcp(
        parsed.mcp_documents, Target.CURSOR, destination, config, claimed
    )
    extra_issues = _write_generated_extras(
        parsed.connectors,
        parsed.lsp_documents,
        parsed.output_styles,
        parsed.themes,
        parsed.monitors,
        Target.CURSOR,
        destination,
        config,
        claimed,
        component_roots,
    )
    dropped_issues = dropped_manifest_field_issues(parsed)
    if collected_issues is not None:
        collected_issues.extend(skill_issues)
        collected_issues.extend(rule_issues)
        collected_issues.extend(command_issues)
        collected_issues.extend(hook_issues)
        collected_issues.extend(agent_issues)
        collected_issues.extend(workflow_issues)
        collected_issues.extend(mcp_issues)
        collected_issues.extend(extra_issues)
        collected_issues.extend(dropped_issues)
    write_cursor_manifest(parsed, destination, collected_issues=collected_issues)
    write_runtime_host(destination, Target.CURSOR)
    return destination


def check_cursor_artifacts(
    capabilities: tuple[CapabilityRef, ...],
    staged_root: Path,
    config: OverrideConfig | None = None,
    plugin_dir: Path | None = None,
    explained_issues: tuple[CompilerIssue, ...] | list[CompilerIssue] | None = None,
    parsed: ParsedPlugin | None = None,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    if component_source_roots is None and parsed is not None:
        component_source_roots = _component_source_roots_from_parsed(parsed)
    return check_target_artifacts(
        capabilities,
        Target.CURSOR,
        staged_root,
        config,
        plugin_dir,
        explained_issues,
        component_source_roots,
    )
