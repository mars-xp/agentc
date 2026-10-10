# -*- coding: utf-8 -*-
"""Load capability documents from a scanned plugin tree (host-neutral)."""
from __future__ import annotations

from pathlib import Path

from .agents import AgentDocument, load_agent_document
from .commands import CommandDocument, is_bin_passthrough_source, load_command_document
from .extras import (
    ConnectorDocument,
    LspDocument,
    MonitorDocument,
    OutputStyleDocument,
    ThemeDocument,
    load_connector_document,
    load_lsp_document,
    load_monitor_document,
    load_output_style_document,
    load_theme_document,
)
from .hooks import HookDocument, load_hook_document
from .mcp import McpDocument, load_mcp_document
from .model import CapabilityKind, CapabilityRef, CompilerIssue
from .rules import (
    RuleDocument,
    is_root_rule_source,
    load_root_rule_document,
    load_rule_document,
)
from .skills import SkillDocument, load_skill_document
from .workflows import WorkflowDocument, load_workflow_document


def _path_from_source(plugin_dir: Path, source_path: str) -> Path:
    parts = tuple(
        part for part in source_path.replace("\\", "/").split("/") if part
    )
    return plugin_dir.joinpath(*parts)


def issue_from_load_error(exc: BaseException, path: str) -> CompilerIssue | None:
    code = getattr(exc, "compiler_code", None)
    if not isinstance(code, str) or not code:
        return None
    return CompilerIssue(
        severity="block",
        code=code,
        path=path,
        message=str(exc),
    )


def _resolved_source_host(
    capability: CapabilityRef, default_source_host: str | None
) -> str | None:
    source_host = capability.source_dialect
    if source_host is None:
        source_host = default_source_host
    return source_host


def _load_or_collect(collected_issues: list[CompilerIssue] | None, source_path: str, loader):
    try:
        return loader()
    except RuntimeError as exc:
        issue = issue_from_load_error(exc, source_path)
        if issue is None:
            return None
        if collected_issues is not None:
            collected_issues.append(issue)
            return None
        raise


def _load_or_collect_runtime(
    collected_issues: list[CompilerIssue] | None, source_path: str, loader
):
    try:
        return loader()
    except RuntimeError as exc:
        issue = issue_from_load_error(exc, source_path)
        if issue is None:
            issue = CompilerIssue(
                severity="block",
                code="invalid-document",
                path=source_path,
                message=str(exc),
            )
        if collected_issues is not None:
            collected_issues.append(issue)
            return None
        raise


def extend_scanned_load_issues(
    issues: list[CompilerIssue],
    plugin_dir: Path,
    capabilities: tuple[CapabilityRef, ...],
    *,
    default_source_host: str | None = None,
) -> None:
    load_issues: list[CompilerIssue] = []
    load_scanned_documents(
        plugin_dir,
        capabilities,
        default_source_host=default_source_host,
        collected_issues=load_issues,
    )
    seen = {(item.code, item.path) for item in issues}
    for issue in load_issues:
        key = (issue.code, issue.path)
        if key in seen:
            continue
        issues.append(issue)
        seen.add(key)


def load_scanned_documents(
    plugin_dir: Path,
    capabilities: tuple[CapabilityRef, ...],
    *,
    default_source_host: str | None = None,
    collected_issues: list[CompilerIssue] | None = None,
) -> tuple[
    tuple[CommandDocument, ...],
    tuple[RuleDocument, ...],
    tuple[HookDocument, ...],
    tuple[AgentDocument, ...],
    tuple[WorkflowDocument, ...],
    tuple[McpDocument, ...],
    tuple[ConnectorDocument, ...],
    tuple[LspDocument, ...],
    tuple[OutputStyleDocument, ...],
    tuple[ThemeDocument, ...],
    tuple[MonitorDocument, ...],
    tuple[SkillDocument, ...],
]:
    commands: list[CommandDocument] = []
    rules: list[RuleDocument] = []
    hooks: list[HookDocument] = []
    agents: list[AgentDocument] = []
    workflows: list[WorkflowDocument] = []
    mcp_documents: list[McpDocument] = []
    connectors: list[ConnectorDocument] = []
    lsp_documents: list[LspDocument] = []
    output_styles: list[OutputStyleDocument] = []
    themes: list[ThemeDocument] = []
    monitors: list[MonitorDocument] = []
    skills: list[SkillDocument] = []
    for capability in capabilities:
        path = _path_from_source(plugin_dir, capability.source_path)
        if capability.kind is CapabilityKind.SKILL:
            skill_md = path / "SKILL.md" if path.is_dir() else path
            if skill_md.is_file():
                source_host = _resolved_source_host(
                    capability, default_source_host
                )
                document = _load_or_collect(
                    collected_issues,
                    capability.source_path,
                    lambda: load_skill_document(
                        skill_md,
                        plugin_dir,
                        source_dialect=source_host,
                    ),
                )
                if document is not None:
                    skills.append(document)
            continue
        if not path.is_file():
            continue
        if capability.kind is CapabilityKind.COMMAND:
            if is_bin_passthrough_source(capability.source_path):
                continue
            commands.append(load_command_document(path, plugin_dir))
            continue
        if capability.kind is CapabilityKind.RULE:
            try:
                if is_root_rule_source(capability.source_path):
                    rules.append(load_root_rule_document(path, plugin_dir))
                else:
                    rules.append(load_rule_document(path, plugin_dir))
            except RuntimeError:
                continue
            continue
        if capability.kind is CapabilityKind.HOOK:
            posix = capability.source_path.replace("\\", "/")
            if posix.endswith(".json"):
                source_host = _resolved_source_host(
                    capability, default_source_host
                )
                hooks.append(
                    load_hook_document(
                        path, plugin_dir, source_host=source_host
                    )
                )
            continue
        if capability.kind is CapabilityKind.AGENT:
            source_host = _resolved_source_host(
                capability, default_source_host
            )
            document = _load_or_collect(
                collected_issues,
                capability.source_path,
                lambda: load_agent_document(
                    path, plugin_dir, source_dialect=source_host
                ),
            )
            if document is not None:
                agents.append(document)
            continue
        if capability.kind is CapabilityKind.WORKFLOW:
            workflows.append(load_workflow_document(path, plugin_dir))
            continue
        if capability.kind is CapabilityKind.MCP:
            document = _load_or_collect_runtime(
                collected_issues,
                capability.source_path,
                lambda: load_mcp_document(path, plugin_dir),
            )
            if document is not None:
                mcp_documents.append(document)
            continue
        if capability.kind is CapabilityKind.CONNECTOR:
            connectors.append(load_connector_document(path, plugin_dir))
            continue
        if capability.kind is CapabilityKind.LSP:
            document = _load_or_collect_runtime(
                collected_issues,
                capability.source_path,
                lambda: load_lsp_document(path, plugin_dir),
            )
            if document is not None:
                lsp_documents.append(document)
            continue
        if capability.kind is CapabilityKind.OUTPUT_STYLE:
            output_styles.append(load_output_style_document(path, plugin_dir))
            continue
        if capability.kind is CapabilityKind.THEME:
            themes.append(load_theme_document(path, plugin_dir))
            continue
        if capability.kind is CapabilityKind.MONITOR:
            monitors.append(load_monitor_document(path, plugin_dir))
    return (
        tuple(commands),
        tuple(rules),
        tuple(hooks),
        tuple(agents),
        tuple(workflows),
        tuple(mcp_documents),
        tuple(connectors),
        tuple(lsp_documents),
        tuple(output_styles),
        tuple(themes),
        tuple(monitors),
        tuple(skills),
    )
