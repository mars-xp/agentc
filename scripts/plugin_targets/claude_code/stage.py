from __future__ import annotations

import shutil
from pathlib import Path, PurePath

from plugin_compiler.compiled_rules import copy_rule_loader, write_compiled_rules, write_rule_hooks
from plugin_compiler.mcp import mcp_output_relative
from plugin_compiler.model import CapabilityKind, CapabilityRef, CompilerIssue, Target
from plugin_compiler.overrides import OverrideConfig, load_override_config, resolved_targets
from plugin_compiler.paths import BuildLayout
from plugin_compiler.staging import (
    _agent_artifact_present,
    _capability_ref,
    _compiled_rule_present,
    _component_output_relative,
    _component_source_roots_from_parsed,
    _existing_skill_names,
    _extra_artifact_present,
    _hook_artifact_present,
    _hook_presence_targets,
    _hook_unexpected_present,
    _kind_component_roots,
    _missing_already_explained,
    _normalized_parts,
    _presence_issue,
    _relative_from_source_path,
    _rule_loader_check_issues,
    _shared_should_skip,
    _skill_source_roots_from_parsed,
    _remapped_skill_artifact,
    _shared_sidecar_unexpected_issues,
    _write_generated_agents,
    _write_generated_commands,
    _write_generated_extras,
    _write_generated_mcp,
    _write_generated_rules,
    _write_generated_skills,
    _write_native_markdown,
    _write_user_hooks,
    copy_stage_files,
    iter_parsed_stage_files,
    write_runtime_host,
)
from plugin_compiler.workflows import workflow_conversion_issue, workflow_native_relative
from plugin_targets.claude_code.manifest import write_claude_manifest
from plugin_compiler.layout_extras import extra_root_script_names
from plugin_targets.spi import ParsedPlugin

_CLAUDE_EXCLUDED_TOP_DIRS = frozenset({"rules", "commands"})
_CLAUDE_EXCLUDED_ROOT_FILES = frozenset(
    {"AGENTS.md", "CLAUDE.md", "mcp.json", ".app.json"}
)
_CLAUDE_EXCLUDED_ROOT_SCRIPTS = extra_root_script_names()
_CLAUDE_MANIFEST = Path(".claude-plugin/plugin.json")
_EXTRAS_KINDS = frozenset(
    {
        CapabilityKind.CONNECTOR,
        CapabilityKind.LSP,
        CapabilityKind.OUTPUT_STYLE,
        CapabilityKind.THEME,
        CapabilityKind.MONITOR,
    }
)


def _claude_should_skip(
    path: Path, plugin_dir: Path, config: OverrideConfig
) -> bool:
    if _shared_should_skip(path, plugin_dir, config, Target.CLAUDE_CODE.value):
        return True
    relative = path.relative_to(plugin_dir)
    parts = _normalized_parts(relative)
    if not parts:
        return True
    top = parts[0]
    if top in _CLAUDE_EXCLUDED_TOP_DIRS:
        return True
    if len(parts) == 1 and top in _CLAUDE_EXCLUDED_ROOT_FILES:
        return True
    return (
        len(parts) == 2
        and top == "scripts"
        and parts[1] in _CLAUDE_EXCLUDED_ROOT_SCRIPTS
    )


def iter_claude_stage_files(
    parsed: ParsedPlugin,
    config: OverrideConfig | None = None,
) -> tuple[Path, ...]:
    plugin_dir = parsed.plugin_dir.resolve()
    if config is None:
        config = parsed.override_config
    if config is None:
        config = load_override_config(plugin_dir)
    return iter_parsed_stage_files(parsed, config, _claude_should_skip)


def _write_claude_workflows(
    workflows,
    destination: Path,
    config: OverrideConfig,
    claimed: dict[str, str],
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    for document in workflows:
        capability = _capability_ref(
            CapabilityKind.WORKFLOW, document.capability_id, document.source_path
        )
        if Target.CLAUDE_CODE not in resolved_targets(config, capability):
            continue
        conversion = workflow_conversion_issue(document, Target.CLAUDE_CODE)
        if conversion is not None:
            issues.append(conversion)
            continue
        issues.extend(
            _write_native_markdown(
                document,
                workflow_native_relative(document),
                destination,
                claimed,
                Target.CLAUDE_CODE,
            )
        )
    return tuple(issues)


def stage_claude(
    parsed: ParsedPlugin,
    layout: BuildLayout,
    collected_issues: list[CompilerIssue] | None = None,
) -> Path:
    plugin_dir = parsed.plugin_dir.resolve()
    config = parsed.override_config
    if config is None:
        config = load_override_config(plugin_dir)
    destination = layout.intermediate(Target.CLAUDE_CODE.value, parsed.plugin_name)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    component_roots = _component_source_roots_from_parsed(parsed)
    copy_stage_files(
        plugin_dir,
        destination,
        iter_claude_stage_files(parsed, config),
        skill_source_roots=_skill_source_roots_from_parsed(parsed),
        component_source_roots=component_roots,
    )
    skill_issues = _write_generated_skills(
        parsed.skills, Target.CLAUDE_CODE, destination, config, plugin_dir
    )
    rule_issues, compiled_rules = _write_generated_rules(
        parsed.rules, Target.CLAUDE_CODE, config
    )
    command_issues = _write_generated_commands(
        parsed.commands,
        Target.CLAUDE_CODE,
        destination,
        config,
        component_roots,
    )
    claimed: dict[str, str] = {}
    if compiled_rules:
        write_compiled_rules(compiled_rules, destination)
        copy_rule_loader(destination)
        write_rule_hooks(destination, Target.CLAUDE_CODE)
    hook_issues = _write_user_hooks(
        plugin_dir,
        Target.CLAUDE_CODE,
        destination,
        compiled_rules,
        config,
        parsed.hooks,
    )
    existing_names = _existing_skill_names(parsed.skills, destination)
    agent_issues = _write_generated_agents(
        parsed.agents,
        Target.CLAUDE_CODE,
        destination,
        config,
        claimed,
        existing_names,
        component_roots,
    )
    workflow_issues = _write_claude_workflows(
        parsed.workflows, destination, config, claimed
    )
    mcp_issues = _write_generated_mcp(
        parsed.mcp_documents, Target.CLAUDE_CODE, destination, config, claimed
    )
    extra_issues = _write_generated_extras(
        parsed.connectors,
        parsed.lsp_documents,
        parsed.output_styles,
        parsed.themes,
        parsed.monitors,
        Target.CLAUDE_CODE,
        destination,
        config,
        claimed,
        component_roots,
    )
    if collected_issues is not None:
        collected_issues.extend(skill_issues)
        collected_issues.extend(rule_issues)
        collected_issues.extend(command_issues)
        collected_issues.extend(hook_issues)
        collected_issues.extend(agent_issues)
        collected_issues.extend(workflow_issues)
        collected_issues.extend(mcp_issues)
        collected_issues.extend(extra_issues)
    write_claude_manifest(parsed, destination, collected_issues=collected_issues)
    write_runtime_host(destination, Target.CLAUDE_CODE)
    return destination


def _claude_command_artifact_present(
    capability: CapabilityRef,
    staged_root: Path,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> bool:
    roots = _kind_component_roots(
        component_source_roots if component_source_roots is not None else {},
        CapabilityKind.COMMAND.value,
    )
    native = _relative_from_source_path(capability.source_path)
    remapped = _relative_from_source_path(
        _component_output_relative(
            capability.source_path, CapabilityKind.COMMAND.value, roots
        )
    )
    return (staged_root / remapped).is_file() or (staged_root / native).is_file()


def _claude_workflow_artifact_present(
    capability: CapabilityRef,
    staged_root: Path,
) -> bool:
    parts = _normalized_parts(PurePath(capability.source_path))
    if not parts or parts[0] != "workflows":
        return False
    return staged_root.joinpath(*parts).is_file()


def check_claude_artifacts(
    capabilities: tuple[CapabilityRef, ...],
    staged_root: Path,
    config: OverrideConfig | None = None,
    plugin_dir: Path | None = None,
    explained_issues: tuple[CompilerIssue, ...] | list[CompilerIssue] | None = None,
    parsed: ParsedPlugin | None = None,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    target = Target.CLAUDE_CODE
    if component_source_roots is None and parsed is not None:
        component_source_roots = _component_source_roots_from_parsed(parsed)
    if config is None:
        config = load_override_config(
            plugin_dir if plugin_dir is not None else staged_root
        )
    issues = []
    for capability in capabilities:
        if capability.kind is CapabilityKind.SKILL:
            artifact = _remapped_skill_artifact(capability)
            exists = (staged_root / artifact).is_file()
            unexpected_exists = exists
            unexpected_code = "unexpected-skill-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.RULE:
            exists = _compiled_rule_present(staged_root, capability.capability_id)
            unexpected_exists = exists
            unexpected_code = "unexpected-rule-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.COMMAND:
            exists = _claude_command_artifact_present(
                capability, staged_root, component_source_roots
            )
            unexpected_exists = exists
            unexpected_code = "unexpected-command-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.PLUGIN_METADATA:
            if (staged_root / _CLAUDE_MANIFEST).is_file():
                continue
            if not _missing_already_explained(capability, target, explained_issues):
                issues.append(
                    _presence_issue(
                        "missing-target-artifact", capability, target, missing=True
                    )
                )
            continue
        elif capability.kind is CapabilityKind.HOOK:
            exists = _hook_artifact_present(capability, staged_root)
            unexpected_exists = _hook_unexpected_present(
                capability, staged_root, plugin_dir
            )
            unexpected_code = "unexpected-hook-artifact"
            presence_targets = _hook_presence_targets(config, capability)
        elif capability.kind is CapabilityKind.AGENT:
            exists = _agent_artifact_present(
                capability,
                target,
                staged_root,
                plugin_dir,
                component_source_roots,
            )
            unexpected_exists = exists
            unexpected_code = "unexpected-agent-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.WORKFLOW:
            exists = _claude_workflow_artifact_present(capability, staged_root)
            unexpected_exists = exists
            unexpected_code = "unexpected-workflow-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.MCP:
            exists = (staged_root / mcp_output_relative(target)).is_file()
            unexpected_exists = False
            unexpected_code = "unexpected-mcp-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind in _EXTRAS_KINDS:
            exists = _extra_artifact_present(
                capability, target, staged_root, component_source_roots
            )
            unexpected_exists = (
                exists and capability.kind is not CapabilityKind.LSP
            )
            unexpected_code = "unexpected-{0}-artifact".format(capability.kind.value)
            presence_targets = resolved_targets(config, capability)
        else:
            if (staged_root / _CLAUDE_MANIFEST).is_file() and capability.kind is (
                CapabilityKind.PLUGIN_METADATA
            ):
                continue
            if not _missing_already_explained(capability, target, explained_issues):
                issues.append(
                    _presence_issue(
                        "missing-target-artifact", capability, target, missing=True
                    )
                )
            continue
        if target not in presence_targets:
            if unexpected_exists:
                issues.append(
                    _presence_issue(
                        unexpected_code, capability, target, missing=False
                    )
                )
            continue
        if exists:
            continue
        if _missing_already_explained(capability, target, explained_issues):
            continue
        issues.append(
            _presence_issue(
                "missing-target-artifact", capability, target, missing=True
            )
        )
    issues.extend(
        _shared_sidecar_unexpected_issues(
            capabilities, target, staged_root, config
        )
    )
    issues.extend(_rule_loader_check_issues(target, staged_root))
    return tuple(issues)
