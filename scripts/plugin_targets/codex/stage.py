from __future__ import annotations

import shutil
from pathlib import Path, PurePath

from plugin_compiler.agents import (
    agent_conversion_issue,
    agent_skill_conflict,
    render_agent_as_skill,
)
from plugin_compiler.commands import (
    assert_codex_skill_name,
    assert_safe_command_path_name,
    command_codex_skill_conflict,
    render_codex_skill_from_command,
)
from plugin_compiler.compiled_rules import copy_rule_loader, write_compiled_rules, write_rule_hooks
from plugin_compiler.mcp import mcp_output_relative
from plugin_compiler.model import CapabilityKind, CapabilityRef, CompilerIssue, Target
from plugin_compiler.overrides import (
    OverrideConfig,
    command_binding_for,
    load_override_config,
    resolved_targets,
)
from plugin_compiler.paths import BuildLayout
from plugin_compiler.staging import (
    _capability_ref,
    _claim_output,
    _compiled_rule_present,
    _component_source_roots_from_parsed,
    _existing_skill_names,
    _extra_artifact_present,
    _generated_skill_conflict,
    _hook_artifact_present,
    _hook_presence_targets,
    _hook_unexpected_present,
    _illegal_command_name_issue,
    _load_agent_for_check,
    _load_command_for_check,
    _missing_already_explained,
    _normalized_parts,
    _presence_issue,
    _relative_from_source_path,
    _render_skill_failure_issue,
    _shared_sidecar_unexpected_issues,
    _rule_loader_check_issues,
    _shared_should_skip,
    _skill_source_roots_from_parsed,
    _remapped_skill_artifact,
    _workflow_artifact_present,
    _write_contained,
    _write_generated_extras,
    _write_generated_mcp,
    _write_generated_rules,
    _write_generated_skills,
    _write_generated_workflows,
    _write_user_hooks,
    copy_stage_files,
    iter_parsed_stage_files,
    write_runtime_host,
)
from plugin_targets.codex.manifest import write_codex_manifest
from plugin_compiler.layout_extras import extra_root_script_names
from plugin_targets.spi import ParsedPlugin

_CODEX_EXCLUDED_TOP_DIRS = frozenset({"rules", "commands", "docs", "agents"})
_CODEX_EXCLUDED_ROOT_FILES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "mcp.json",
        "使用说明.md",
        ".lsp.json",
    }
)
_CODEX_EXCLUDED_ROOT_SCRIPTS = extra_root_script_names()
_CODEX_MANIFEST = Path(".codex-plugin/plugin.json")
_EXTRAS_KINDS = frozenset(
    {
        CapabilityKind.CONNECTOR,
        CapabilityKind.LSP,
        CapabilityKind.OUTPUT_STYLE,
        CapabilityKind.THEME,
        CapabilityKind.MONITOR,
    }
)


def _codex_should_skip(
    path: Path, plugin_dir: Path, config: OverrideConfig
) -> bool:
    if _shared_should_skip(path, plugin_dir, config, Target.CODEX.value):
        return True
    relative = path.relative_to(plugin_dir)
    parts = _normalized_parts(relative)
    if not parts:
        return True
    top = parts[0]
    if top in _CODEX_EXCLUDED_TOP_DIRS:
        return True
    if len(parts) == 1 and top in _CODEX_EXCLUDED_ROOT_FILES:
        return True
    return (
        len(parts) == 2
        and top == "scripts"
        and parts[1] in _CODEX_EXCLUDED_ROOT_SCRIPTS
    )


def iter_codex_stage_files(
    parsed: ParsedPlugin,
    config: OverrideConfig | None = None,
) -> tuple[Path, ...]:
    plugin_dir = parsed.plugin_dir.resolve()
    if config is None:
        config = parsed.override_config
    if config is None:
        config = load_override_config(plugin_dir)
    return iter_parsed_stage_files(parsed, config, _codex_should_skip)


def _write_codex_commands(
    commands,
    destination: Path,
    config: OverrideConfig,
    skills,
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    existing_names = _existing_skill_names(skills, destination)
    for document in commands:
        capability = _capability_ref(
            CapabilityKind.COMMAND, document.capability_id, document.source_path
        )
        if Target.CODEX not in resolved_targets(config, capability):
            continue
        try:
            assert_safe_command_path_name(document.name)
        except ValueError as exc:
            issues.append(
                _illegal_command_name_issue(
                    document, Target.CODEX, "illegal-command-name", str(exc)
                )
            )
            continue
        if command_binding_for(config, document.capability_id) is not None:
            continue
        try:
            assert_codex_skill_name(document.name)
        except ValueError as exc:
            issues.append(
                _illegal_command_name_issue(
                    document, Target.CODEX, "illegal-codex-skill-name", str(exc)
                )
            )
            continue
        conflict = command_codex_skill_conflict(document, existing_names)
        if conflict is not None:
            issues.append(conflict)
            continue
        output = destination / "skills" / document.name / "SKILL.md"
        try:
            _write_contained(
                destination, output, render_codex_skill_from_command(document)
            )
        except ValueError as exc:
            issues.append(
                _illegal_command_name_issue(
                    document, Target.CODEX, "write-outside-destination", str(exc)
                )
            )
            continue
        existing_names.add(document.name)
    return tuple(issues)


def _write_codex_agents(
    agents,
    destination: Path,
    config: OverrideConfig,
    claimed: dict[str, str],
    existing_names: set[str],
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    for document in agents:
        capability = _capability_ref(
            CapabilityKind.AGENT, document.capability_id, document.source_path
        )
        if Target.CODEX not in resolved_targets(config, capability):
            continue
        conversion = agent_conversion_issue(document, Target.CODEX)
        if conversion is not None:
            issues.append(conversion)
            continue
        conflict = agent_skill_conflict(document, existing_names)
        if conflict is not None:
            issues.append(conflict)
            continue
        name_conflict = _generated_skill_conflict(
            document.source_path, document.name, existing_names
        )
        if name_conflict is not None:
            issues.append(name_conflict)
            continue
        relative = "skills/{0}/SKILL.md".format(document.name)
        claimed_issue = _claim_output(claimed, relative, document.capability_id)
        if claimed_issue is not None:
            issues.append(claimed_issue)
            continue
        try:
            text = render_agent_as_skill(document)
            output = destination / "skills" / document.name / "SKILL.md"
            _write_contained(destination, output, text)
        except ValueError as exc:
            issues.append(
                _render_skill_failure_issue(document.source_path, Target.CODEX, exc)
            )
            continue
        existing_names.add(document.name)
    return tuple(issues)


def stage_codex(
    parsed: ParsedPlugin,
    layout: BuildLayout,
    collected_issues: list[CompilerIssue] | None = None,
) -> Path:
    plugin_dir = parsed.plugin_dir.resolve()
    config = parsed.override_config
    if config is None:
        config = load_override_config(plugin_dir)
    destination = layout.intermediate(Target.CODEX.value, parsed.plugin_name)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    component_roots = _component_source_roots_from_parsed(parsed)
    copy_stage_files(
        plugin_dir,
        destination,
        iter_codex_stage_files(parsed, config),
        skill_source_roots=_skill_source_roots_from_parsed(parsed),
        component_source_roots=component_roots,
    )
    skill_issues = _write_generated_skills(
        parsed.skills, Target.CODEX, destination, config, plugin_dir
    )
    rule_issues, compiled_rules = _write_generated_rules(
        parsed.rules, Target.CODEX, config
    )
    command_issues = _write_codex_commands(
        parsed.commands, destination, config, parsed.skills
    )
    claimed: dict[str, str] = {}
    if compiled_rules:
        write_compiled_rules(compiled_rules, destination)
        copy_rule_loader(destination)
        write_rule_hooks(destination, Target.CODEX)
    hook_issues = _write_user_hooks(
        plugin_dir,
        Target.CODEX,
        destination,
        compiled_rules,
        config,
        parsed.hooks,
    )
    existing_names = _existing_skill_names(parsed.skills, destination)
    agent_issues = _write_codex_agents(
        parsed.agents, destination, config, claimed, existing_names
    )
    workflow_issues = _write_generated_workflows(
        parsed.workflows,
        Target.CODEX,
        destination,
        config,
        claimed,
        existing_names,
    )
    mcp_issues = _write_generated_mcp(
        parsed.mcp_documents, Target.CODEX, destination, config, claimed
    )
    extra_issues = _write_generated_extras(
        parsed.connectors,
        parsed.lsp_documents,
        parsed.output_styles,
        parsed.themes,
        parsed.monitors,
        Target.CODEX,
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
    write_codex_manifest(parsed, destination, collected_issues=collected_issues)
    write_runtime_host(destination, Target.CODEX)
    return destination


def _codex_command_artifact_present(
    capability: CapabilityRef,
    staged_root: Path,
    plugin_dir: Path | None,
    capabilities: tuple[CapabilityRef, ...],
    config: OverrideConfig | None,
) -> bool:
    if config is not None:
        binding = command_binding_for(config, capability.capability_id)
        if binding is not None:
            return (
                staged_root / "skills" / binding.codex_skill_dir / "SKILL.md"
            ).is_file()
    document = _load_command_for_check(capability, staged_root, plugin_dir)
    if document is None:
        return False
    skill_ids = {
        item.source_path.replace("\\", "/")
        for item in capabilities
        if item.kind is CapabilityKind.SKILL
    }
    generated = staged_root / "skills" / document.name / "SKILL.md"
    if not generated.is_file():
        return False
    if "skills/{0}".format(document.name) in skill_ids:
        return False
    return True


def _codex_command_unexpected_present(
    capability: CapabilityRef,
    staged_root: Path,
    plugin_dir: Path | None,
    capabilities: tuple[CapabilityRef, ...],
    config: OverrideConfig | None,
) -> bool:
    native = staged_root / _relative_from_source_path(capability.source_path)
    if config is not None and command_binding_for(
        config, capability.capability_id
    ) is not None:
        return native.is_file()
    return _codex_command_artifact_present(
        capability, staged_root, plugin_dir, capabilities, config
    )


def _codex_agent_artifact_present(
    capability: CapabilityRef,
    staged_root: Path,
    plugin_dir: Path | None,
) -> bool:
    document = _load_agent_for_check(
        capability, staged_root, plugin_dir
    )
    if document is None:
        return False
    return (staged_root / "skills" / document.name / "SKILL.md").is_file()


def check_codex_artifacts(
    capabilities: tuple[CapabilityRef, ...],
    staged_root: Path,
    config: OverrideConfig | None = None,
    plugin_dir: Path | None = None,
    explained_issues: tuple[CompilerIssue, ...] | list[CompilerIssue] | None = None,
) -> tuple[CompilerIssue, ...]:
    target = Target.CODEX
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
            exists = _codex_command_artifact_present(
                capability, staged_root, plugin_dir, capabilities, config
            )
            unexpected_exists = _codex_command_unexpected_present(
                capability, staged_root, plugin_dir, capabilities, config
            )
            unexpected_code = "unexpected-command-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.PLUGIN_METADATA:
            if (staged_root / _CODEX_MANIFEST).is_file():
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
            exists = _codex_agent_artifact_present(
                capability, staged_root, plugin_dir
            )
            unexpected_exists = exists
            unexpected_code = "unexpected-agent-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.WORKFLOW:
            exists = _workflow_artifact_present(
                capability, target, staged_root, plugin_dir
            )
            unexpected_exists = exists
            unexpected_code = "unexpected-workflow-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.MCP:
            exists = (staged_root / mcp_output_relative(target)).is_file()
            unexpected_exists = False
            unexpected_code = "unexpected-mcp-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind in _EXTRAS_KINDS:
            exists = _extra_artifact_present(capability, target, staged_root)
            unexpected_exists = (
                exists and capability.kind is not CapabilityKind.LSP
            )
            unexpected_code = "unexpected-{0}-artifact".format(capability.kind.value)
            presence_targets = resolved_targets(config, capability)
        else:
            if (staged_root / _CODEX_MANIFEST).is_file() and capability.kind is (
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
