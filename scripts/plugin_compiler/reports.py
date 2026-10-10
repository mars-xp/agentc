from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from plugin_targets.internal_refs import official_plugin_relative

from .agents import AgentDocument
from .commands import CommandDocument
from .extras import extra_output_relative
from .mcp import mcp_output_relative
from .model import CapabilityKind, CapabilityRef, Target
from .overrides import command_binding_for, resolved_targets
from .staging import (
    _COMPONENT_DEFAULT_DIRS,
    _component_output_relative,
    _component_source_roots_from_parsed,
    _source_owned_by_kind,
)
from .text import write_text_utf8_lf
from .validation import ValidationIssue, ValidationReport
from .workflows import WorkflowDocument

if TYPE_CHECKING:
    from .orchestrator import CompileResult


_REPORT_NAMES = (
    "capabilities.json",
    "compatibility.json",
    "artifacts.json",
    "validation.json",
    "report.md",
)
_DECLARED_UNSUPPORTED_KINDS = frozenset(
    {
        CapabilityKind.SKILL,
        CapabilityKind.RULE,
        CapabilityKind.COMMAND,
        CapabilityKind.HOOK,
        CapabilityKind.AGENT,
        CapabilityKind.WORKFLOW,
        CapabilityKind.MCP,
        CapabilityKind.CONNECTOR,
        CapabilityKind.LSP,
        CapabilityKind.OUTPUT_STYLE,
        CapabilityKind.THEME,
        CapabilityKind.MONITOR,
    }
)
_HOOK_TRUST_REMINDER = (
    "Codex / Claude Code 插件钩子需宿主信任后才会真正注入。"
)
_GATE_B_OVERLAP_CODES = frozenset(
    {
        "content-duplicate",
        "semantic-name-overlap",
        "semantic-line-overlap",
        "cross-form-overlap",
    }
)
_GATE_A_PACKAGE_CODES = frozenset(
    {
        "duplicate-output-path",
        "generated-skill-conflict",
        "command-skill-name-conflict",
        "hook-script-escape",
        "mcp-secret-in-source",
        "missing-hook-script",
        "illegal-codex-skill-name",
    }
)
_GATE_B_DEMOTED_TO_WARNING = "门 B 已按本次命令改为警告"
_COMPATIBILITY_BLOCK_STATUSES = frozenset(
    {"blocked_missing_artifact", "unexpected_artifact_present"}
)
_INFRASTRUCTURE_ARTIFACTS = frozenset(
    {
        "runtime-host.json",
        "hooks/rule_loader.py",
        "hooks/hooks.json",
        "hooks.json",
        "mcp.json",
        ".mcp.json",
        ".lsp.json",
    }
)
_INFRASTRUCTURE_PREFIXES = ("compiled-rules/",)
_KIND_DEFAULT_DIR_PREFIXES = {
    CapabilityKind.THEME: "themes/",
    CapabilityKind.MONITOR: "monitors/",
}
_HOOK_SIDECARS = ("hooks/hooks.json", "hooks.json")
_LSP_SIDECAR = ".lsp.json"


def _adapter_id(value: object) -> str:
    if isinstance(value, Target):
        return value.value
    return str(value)


def _as_native_target(value: object) -> Target | None:
    if isinstance(value, Target):
        return value
    try:
        return Target(_adapter_id(value))
    except ValueError:
        return None


def _lookup(mapping: dict, key: object):
    if key in mapping:
        return mapping[key]
    return mapping.get(_adapter_id(key))


def _has(mapping: dict, key: object) -> bool:
    return key in mapping or _adapter_id(key) in mapping


def _inventory(
    inventories: dict, target: object
) -> tuple[str, ...]:
    found = _lookup(inventories, target)
    return () if found is None else found


def _issue_dict(issue: ValidationIssue, target: object | None = None) -> dict:
    result = {
        "severity": issue.severity,
        "code": issue.code,
        "path": issue.path,
        "message": issue.message,
    }
    if target is not None:
        result = {"target": _adapter_id(target), **result}
    return result


def _staged_artifacts(staged_root: str | None) -> tuple[str, ...]:
    if staged_root is None:
        return ()
    root = Path(staged_root)
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def _capability_artifacts(
    capability: CapabilityRef,
    inventory: tuple[str, ...],
    result: "CompileResult" | None = None,
    target: Target | None = None,
) -> tuple[str, ...]:
    source = capability.source_path.rstrip("/")
    matches = tuple(
        path for path in inventory if path == source or path.startswith(source + "/")
    )
    generated = _generated_capability_artifacts(
        capability, inventory, result, target
    )
    combined = list(matches)
    for path in generated:
        if path not in combined:
            combined.append(path)
    if combined or capability.kind is not CapabilityKind.PLUGIN_METADATA:
        return tuple(combined)
    return tuple(
        path
        for path in inventory
        if path.endswith("/plugin.json") and path.startswith(".")
    )


def _compiled_rule_body_artifact(source_path: str) -> str:
    posix = source_path.replace("\\", "/").lstrip("/")
    return "compiled-rules/bodies/{0}.txt".format(posix)


def _command_document(
    result: "CompileResult" | None, capability: CapabilityRef
) -> CommandDocument | None:
    if result is None:
        return None
    source = capability.source_path.replace("\\", "/")
    for document in result.commands:
        if document.capability_id == capability.capability_id:
            return document
        if document.source_path.replace("\\", "/") == source:
            return document
    return None


def _agent_document(
    result: "CompileResult" | None, capability: CapabilityRef
) -> AgentDocument | None:
    if result is None:
        return None
    source = capability.source_path.replace("\\", "/")
    for document in result.agents:
        if document.capability_id == capability.capability_id:
            return document
        if document.source_path.replace("\\", "/") == source:
            return document
    return None


def _workflow_document(
    result: "CompileResult" | None, capability: CapabilityRef
) -> WorkflowDocument | None:
    if result is None:
        return None
    source = capability.source_path.replace("\\", "/")
    for document in result.workflows:
        if document.capability_id == capability.capability_id:
            return document
        if document.source_path.replace("\\", "/") == source:
            return document
    return None


def _append_if_present(
    extra: list[str], inventory: tuple[str, ...], relative: str
) -> None:
    if relative and relative in inventory and relative not in extra:
        extra.append(relative)


def _component_roots(result: "CompileResult" | None) -> dict[str, frozenset[str]]:
    if result is None:
        return {}
    return _component_source_roots_from_parsed(result)


def _capability_enters_target(
    result: "CompileResult" | None,
    capability: CapabilityRef,
    target: Target | None,
) -> bool:
    if target is None:
        return False
    if result is not None and result.override_config is not None:
        if target not in resolved_targets(result.override_config, capability):
            return False
    kind = capability.kind.value
    if kind in _COMPONENT_DEFAULT_DIRS:
        if not _source_owned_by_kind(
            capability.source_path, kind, _component_roots(result)
        ):
            return False
    return True


def _generated_capability_artifacts(
    capability: CapabilityRef,
    inventory: tuple[str, ...],
    result: "CompileResult" | None,
    target: Target | None,
) -> tuple[str, ...]:
    native = _as_native_target(target)
    extra: list[str] = []
    if not _capability_enters_target(result, capability, native):
        return ()
    if capability.kind.value in _COMPONENT_DEFAULT_DIRS:
        remapped = _component_output_relative(
            capability.source_path,
            capability.kind.value,
            _component_roots(result).get(capability.kind.value, frozenset()),
        )
        posix = remapped.replace("\\", "/").rstrip("/")
        if posix:
            _append_if_present(extra, inventory, posix)
            prefix = posix if posix.endswith("/") else posix + "/"
            for path in inventory:
                if path.startswith(prefix) and path not in extra:
                    extra.append(path)
    if capability.kind is CapabilityKind.RULE:
        body = _compiled_rule_body_artifact(capability.source_path)
        if body in inventory:
            extra.append(body)
            if "compiled-rules/index.json" in inventory:
                extra.insert(0, "compiled-rules/index.json")
    if capability.kind is CapabilityKind.COMMAND and native == Target.CODEX:
        document = _command_document(result, capability)
        if document is not None:
            binding = None
            if result is not None and result.override_config is not None:
                binding = command_binding_for(
                    result.override_config, capability.capability_id
                )
            skill_dir = (
                binding.codex_skill_dir if binding is not None else document.name
            )
            skill_md = "skills/{0}/SKILL.md".format(skill_dir)
            _append_if_present(extra, inventory, skill_md)
    if capability.kind is CapabilityKind.AGENT and native == Target.CODEX:
        document = _agent_document(result, capability)
        if document is not None and document.convertible_to_skill:
            skill_md = "skills/{0}/SKILL.md".format(document.name)
            _append_if_present(extra, inventory, skill_md)
    if capability.kind is CapabilityKind.WORKFLOW and native in (
        Target.CODEX,
        Target.CURSOR,
    ):
        document = _workflow_document(result, capability)
        if document is not None and document.convertible_to_skill:
            skill_md = "skills/{0}/SKILL.md".format(document.name)
            _append_if_present(extra, inventory, skill_md)
    if capability.kind is CapabilityKind.MCP:
        if native is not None:
            _append_if_present(extra, inventory, mcp_output_relative(native))
        for relative in _mcp_expected_relpaths(result, capability):
            _append_if_present(extra, inventory, relative)
    if capability.kind is CapabilityKind.HOOK:
        for relative in _HOOK_SIDECARS:
            _append_if_present(extra, inventory, relative)
        for relative in _hook_expected_relpaths(result, capability):
            _append_if_present(extra, inventory, relative)
    if capability.kind is CapabilityKind.LSP:
        sidecar = extra_output_relative(capability.kind, capability.source_path)
        _append_if_present(extra, inventory, sidecar or _LSP_SIDECAR)
        _append_if_present(extra, inventory, _LSP_SIDECAR)
    if capability.kind is CapabilityKind.SKILL:
        prefix = _remapped_skill_prefix(capability)
        if prefix:
            for path in inventory:
                if path.startswith(prefix) and path not in extra:
                    extra.append(path)
    prefix = _KIND_DEFAULT_DIR_PREFIXES.get(capability.kind)
    if prefix:
        for path in inventory:
            if path.startswith(prefix) and path not in extra:
                extra.append(path)
    return tuple(extra)


def _remapped_skill_prefix(capability: CapabilityRef) -> str | None:
    if capability.kind is not CapabilityKind.SKILL:
        return None
    posix = capability.source_path.replace("\\", "/").rstrip("/")
    parts = [part for part in posix.split("/") if part]
    if parts and parts[-1] == "SKILL.md":
        parts = parts[:-1]
    if not parts:
        return None
    if len(parts) >= 2 and parts[0] == "skills":
        dir_name = parts[1]
    else:
        dir_name = parts[-1]
    if not dir_name:
        return None
    return "skills/{0}/".format(dir_name)


def _has_missing_artifact(
    report: ValidationReport | None,
    capability: CapabilityRef,
) -> bool:
    if report is None:
        return True
    return any(
        issue.code == "missing-target-artifact"
        and issue.path == capability.source_path
        for issue in report.issues
    )


def _has_skill_discovery_failure(
    report: ValidationReport | None,
    capability: CapabilityRef,
) -> bool:
    if report is None or capability.kind is not CapabilityKind.SKILL:
        return False
    return any(
        issue.code == "skill-not-discoverable"
        and issue.path == capability.source_path
        for issue in report.issues
    )


def _is_carried_by_skill(
    result: "CompileResult",
    capability: CapabilityRef,
    target: Target,
    inventory: tuple[str, ...],
) -> bool:
    native = _as_native_target(target)
    if native is None:
        return False
    if capability.kind is CapabilityKind.COMMAND:
        if native != Target.CODEX:
            return False
        config = result.override_config
        if config is None:
            return False
        binding = command_binding_for(config, capability.capability_id)
        if binding is None:
            return False
        skill_md = "skills/{0}/SKILL.md".format(binding.codex_skill_dir)
        return skill_md in inventory
    if capability.kind is CapabilityKind.AGENT:
        if native != Target.CODEX:
            return False
        document = _agent_document(result, capability)
        if document is None or not document.convertible_to_skill:
            return False
        skill_md = "skills/{0}/SKILL.md".format(document.name)
        return skill_md in inventory
    if capability.kind is CapabilityKind.WORKFLOW:
        if native not in (Target.CODEX, Target.CURSOR):
            return False
        document = _workflow_document(result, capability)
        if document is None or not document.convertible_to_skill:
            return False
        skill_md = "skills/{0}/SKILL.md".format(document.name)
        return skill_md in inventory
    return False


def _plugin_relative_from_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    relative = official_plugin_relative(value)
    if relative:
        return relative.replace("\\", "/")
    return None


def _mcp_expected_relpaths(
    result: "CompileResult",
    capability: CapabilityRef,
) -> tuple[str, ...]:
    if capability.kind is not CapabilityKind.MCP:
        return ()
    source = capability.source_path.replace("\\", "/")
    found: list[str] = []
    seen: set[str] = set()
    for document in result.mcp_documents:
        if (
            document.capability_id != capability.capability_id
            and document.source_path.replace("\\", "/") != source
        ):
            continue
        for config in document.servers.values():
            if not isinstance(config, dict):
                continue
            for key in ("command", "cwd"):
                relative = _plugin_relative_from_value(config.get(key))
                if relative and relative not in seen:
                    seen.add(relative)
                    found.append(relative)
            args = config.get("args")
            if isinstance(args, list):
                for item in args:
                    relative = _plugin_relative_from_value(item)
                    if relative and relative not in seen:
                        seen.add(relative)
                        found.append(relative)
    return tuple(found)


def _hook_expected_relpaths(
    result: "CompileResult",
    capability: CapabilityRef,
) -> tuple[str, ...]:
    if capability.kind is not CapabilityKind.HOOK:
        return ()
    source = capability.source_path.replace("\\", "/")
    found: list[str] = []
    seen: set[str] = set()
    for document in result.hooks:
        if (
            document.capability_id != capability.capability_id
            and document.source_path.replace("\\", "/") != source
        ):
            continue
        for relative in document.script_relpaths:
            posix = relative.replace("\\", "/")
            if posix and posix not in seen:
                seen.add(posix)
                found.append(posix)
    return tuple(found)


def _is_infrastructure_artifact(path: str) -> bool:
    posix = path.replace("\\", "/")
    if posix in _INFRASTRUCTURE_ARTIFACTS:
        return True
    name = posix.rsplit("/", 1)[-1]
    if name == "plugin.json":
        return True
    return any(posix.startswith(prefix) for prefix in _INFRASTRUCTURE_PREFIXES)


def _compatibility_status(
    result: "CompileResult",
    capability: CapabilityRef,
    target: Target,
    artifacts: tuple[str, ...],
    inventory: tuple[str, ...] = (),
) -> str:
    native = _as_native_target(target)
    if native is None:
        return "staged_artifact_present"
    config = result.override_config
    if (
        config is not None
        and capability.kind in _DECLARED_UNSUPPORTED_KINDS
        and native not in resolved_targets(config, capability)
    ):
        if artifacts:
            return "unexpected_artifact_present"
        return "declared_unsupported"
    if not _capability_enters_target(result, capability, native):
        if artifacts:
            return "unexpected_artifact_present"
        return "declared_unsupported"
    if _is_carried_by_skill(result, capability, native, inventory):
        return "carried_by_skill"
    missing = _has_missing_artifact(_lookup(result.validation, target), capability)
    if not artifacts:
        missing = True
    if _has_skill_discovery_failure(_lookup(result.validation, target), capability):
        missing = True
    expected_mcp = _mcp_expected_relpaths(result, capability)
    if expected_mcp and any(path not in inventory for path in expected_mcp):
        missing = True
    return "blocked_missing_artifact" if missing else "staged_artifact_present"


def _write_json(path: Path, payload: object) -> None:
    write_text_utf8_lf(
        path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def _target_ids(result: "CompileResult") -> list[str]:
    return [_adapter_id(target) for target in result.targets]


def _inventories_for(result: "CompileResult") -> dict[str, tuple[str, ...]]:
    inventories: dict[str, tuple[str, ...]] = {}
    for target in result.targets:
        inventories[_adapter_id(target)] = _staged_artifacts(
            _lookup(result.staged_roots, target)
        )
    return inventories


def _capabilities_payload(result: "CompileResult") -> dict:
    return {
        "plugin_name": result.plugin_name,
        "targets": _target_ids(result),
        "capabilities": [item.to_dict() for item in result.capabilities],
    }


def _claimed_artifact_paths(
    result: "CompileResult",
    target: object,
    inventory: tuple[str, ...],
) -> set[str]:
    native = _as_native_target(target)
    if native is None:
        return set(inventory)
    claimed: set[str] = set()
    for capability in result.capabilities:
        claimed.update(
            _capability_artifacts(capability, inventory, result, native)
        )
    return claimed


def _declared_resource_matches(resource, path: str) -> bool:
    source = str(getattr(resource, "source_path", "") or "").replace("\\", "/")
    source = source.rstrip("/")
    posix = path.replace("\\", "/")
    if not source or not posix:
        return False
    if posix == source:
        return True
    if not posix.startswith(source + "/"):
        return False
    kind = getattr(resource, "kind", "")
    if kind == "directory":
        return True
    last = source.rsplit("/", 1)[-1]
    return "." not in last


def _is_declared_resource_path(result: "CompileResult", path: str) -> bool:
    for resource in result.declared_resources:
        if _declared_resource_matches(resource, path):
            return True
    return False


def _unclaimed_artifact_paths(
    result: "CompileResult",
    target: object,
    inventory: tuple[str, ...],
) -> tuple[str, ...]:
    claimed = _claimed_artifact_paths(result, target, inventory)
    return tuple(
        path
        for path in inventory
        if path not in claimed
        and not _is_infrastructure_artifact(path)
        and not _is_declared_resource_path(result, path)
    )


def _compatibility_payload(
    result: "CompileResult",
    inventories: dict[str, tuple[str, ...]],
) -> dict:
    capabilities = []
    for capability in result.capabilities:
        target_entries = []
        for target in result.targets:
            artifacts = _capability_artifacts(
                capability,
                _inventory(inventories, target),
                result,
                target,
            )
            target_entries.append(
                {
                    "target": _adapter_id(target),
                    "status": _compatibility_status(
                        result,
                        capability,
                        target,
                        artifacts,
                        _inventory(inventories, target),
                    ),
                    "artifacts": list(artifacts),
                }
            )
        capabilities.append(
            {
                **capability.to_dict(),
                "targets": target_entries,
            }
        )
    requested = list(_target_ids(result))
    for target in result.targets:
        adapter_id = _adapter_id(target)
        for path in _unclaimed_artifact_paths(
            result, target, _inventory(inventories, target)
        ):
            capabilities.append(
                {
                    "id": "unexpected:{0}".format(path),
                    "kind": "unexpected-artifact",
                    "source_path": path,
                    "source_dialect": None,
                    "targets": [
                        {
                            "target": adapter_id,
                            "status": "unexpected_artifact_present",
                            "artifacts": [path],
                        }
                    ],
                }
            )
    return {
        "plugin_name": result.plugin_name,
        "targets": requested,
        "capabilities": capabilities,
    }


def _artifacts_payload(
    result: "CompileResult",
    inventories: dict[str, tuple[str, ...]],
) -> dict:
    return {
        "plugin_name": result.plugin_name,
        "targets": _target_ids(result),
        "source_capabilities": [
            {
                "id": capability.capability_id,
                "source_path": capability.source_path,
            }
            for capability in result.capabilities
        ],
        "staged_targets": [
            {
                "target": _adapter_id(target),
                "staged_root": _lookup(result.staged_roots, target),
                "artifacts": list(_inventory(inventories, target)),
            }
            for target in result.targets
        ],
        "published_roots": [
            {
                "target": _adapter_id(target),
                "published_root": _lookup(result.published_roots, target),
            }
            for target in result.targets
            if _has(result.published_roots, target)
        ],
    }


def _validation_payload(result: "CompileResult") -> dict:
    validation = []
    issues = []
    for target in result.targets:
        report = _lookup(result.validation, target)
        if report is None:
            validation.append(
                {
                    "target": _adapter_id(target),
                    "status": "missing",
                    "plugin_root": _lookup(result.staged_roots, target),
                    "published": _has(result.published_roots, target),
                    "issues": [],
                }
            )
            continue
        issue_payloads = [_issue_dict(issue) for issue in report.issues]
        validation.append(
            {
                "target": _adapter_id(target),
                "status": "passed" if report.ok else "blocked",
                "plugin_root": report.plugin_root,
                "published": _has(result.published_roots, target),
                "issues": issue_payloads,
            }
        )
        issues.extend(_issue_dict(issue, target) for issue in report.issues)
    return {
        "plugin_name": result.plugin_name,
        "targets": _target_ids(result),
        "ok": result.ok,
        "validation": validation,
        "issues": issues,
    }


def _publication_summary(result: "CompileResult") -> list[str]:
    if result.validate_only:
        return ["本次 --validate-only，未改正式输出。", ""]
    if not result.ok and result.published_roots:
        lines = ["已发布："]
        for target in result.targets:
            published = _lookup(result.published_roots, target)
            if published is None:
                continue
            lines.append("- `{0}`: `{1}`".format(_adapter_id(target), published))
        lines.extend(["", "本轮未发布："])
        for target in result.targets:
            if _has(result.published_roots, target):
                continue
            lines.append("- `{0}`: 本轮未发布".format(_adapter_id(target)))
        lines.append("")
        return lines
    if not result.ok:
        return ["本轮没有新的正式包。", ""]
    if result.published_roots:
        return ["全部请求目标已通过结构校验并完成发布。", ""]
    return ["结构校验通过；本次没有记录正式输出。", ""]


def _compatibility_status_label(status: str) -> str:
    if status == "declared_unsupported":
        return "已声明不进入该平台，不阻止其它点名平台发布"
    if status == "blocked_missing_artifact":
        return "需要产物但缺失，或技能未能被发现"
    if status == "staged_artifact_present":
        return "已写入本次中间产物"
    if status == "carried_by_skill":
        return "由技能承载，不另生成该平台命令产物"
    if status == "unexpected_artifact_present":
        return "出现了源里未声明、不应发布的产物"
    return status


def _hook_trust_reminder_needed(
    result: "CompileResult",
    inventories: dict[str, tuple[str, ...]],
) -> bool:
    requested = {_adapter_id(item) for item in result.targets}
    for adapter_id in ("codex", "claude-code"):
        if adapter_id not in requested:
            continue
        inventory = _inventory(inventories, adapter_id)
        if any(
            path == "hooks/rule_loader.py" or path.startswith("hooks/")
            for path in inventory
        ):
            return True
    return False


def _issue_line(issue: ValidationIssue) -> str:
    message = issue.message.replace("\r\n", "\n").replace("\r", "\n")
    return "  - [{0}] `{1}` `{2}`: {3}".format(
        issue.severity,
        issue.code,
        issue.path,
        message.replace("\n", "\n    "),
    )


def _collect_issues_with_codes(
    result: "CompileResult",
    codes: frozenset[str],
) -> tuple[ValidationIssue, ...]:
    collected: list[ValidationIssue] = []
    seen: set[tuple[str, str, str, str]] = set()
    for target in result.targets:
        report = _lookup(result.validation, target)
        if report is None:
            continue
        for issue in report.issues:
            if issue.code not in codes:
                continue
            key = (issue.severity, issue.code, issue.path, issue.message)
            if key in seen:
                continue
            seen.add(key)
            collected.append(issue)
    return tuple(collected)


def _overlap_section(result: "CompileResult") -> list[str]:
    gate_a = _collect_issues_with_codes(result, _GATE_A_PACKAGE_CODES)
    gate_b = _collect_issues_with_codes(result, _GATE_B_OVERLAP_CODES)
    lines = [
        "## 重叠",
        "",
        "### 门 A",
        "",
    ]
    if gate_a:
        lines.extend(_issue_line(issue) for issue in gate_a)
    else:
        lines.append("无。")
    lines.extend(["", "### 门 B", ""])
    if gate_b:
        lines.extend(_issue_line(issue) for issue in gate_b)
        if all(issue.severity == "warn" for issue in gate_b):
            lines.extend(["", _GATE_B_DEMOTED_TO_WARNING])
    else:
        lines.append("无。")
    lines.append("")
    return lines


def _markdown(result: "CompileResult") -> str:
    lines = [
        "# Plugin compiler report",
        "",
        "- Plugin: `{0}`".format(result.plugin_name),
        "- Targets: {0}".format(
            ", ".join("`{0}`".format(_adapter_id(target)) for target in result.targets)
        ),
        "- Result: {0}".format("passed" if result.ok else "blocked"),
        "",
    ]
    lines.extend(_publication_summary(result))
    inventories = _inventories_for(result)
    lines.extend(
        [
            "本阶段仅记录源码能力、实际中间产物和结构校验结果。",
            "",
        ]
    )
    if _hook_trust_reminder_needed(result, inventories):
        lines.extend([_HOOK_TRUST_REMINDER, ""])
    lines.extend(
        [
            "## Validation",
            "",
        ]
    )
    for target in result.targets:
        report = _lookup(result.validation, target)
        if report is None:
            lines.append("- `{0}`: missing validation".format(_adapter_id(target)))
            continue
        lines.append(
            "- `{0}`: {1}".format(
                _adapter_id(target),
                "passed" if report.ok else "blocked",
            )
        )
        for issue in report.issues:
            lines.append(_issue_line(issue))
    lines.extend(["", *_overlap_section(result)])
    lines.extend(
        [
            "## Compatibility",
            "",
        ]
    )
    payload = _compatibility_payload(result, inventories)
    if not payload["capabilities"]:
        lines.append("本轮没有登记能力。")
    for row in payload["capabilities"]:
        for item in row["targets"]:
            lines.append(
                "- `{0}` @ `{1}`: `{2}`（{3}）".format(
                    row["id"],
                    item["target"],
                    item["status"],
                    _compatibility_status_label(item["status"]),
                )
            )
    return "\n".join(lines) + "\n"


def apply_compatibility_gate(result: "CompileResult") -> "CompileResult":
    inventories = _inventories_for(result)
    payload = _compatibility_payload(result, inventories)
    blocked: dict[str, set[str]] = {}
    unexpected_paths: dict[str, list[str]] = {}
    for row in payload["capabilities"]:
        for item in row["targets"]:
            status = item["status"]
            if status not in _COMPATIBILITY_BLOCK_STATUSES:
                continue
            blocked.setdefault(item["target"], set()).add(status)
            if status == "unexpected_artifact_present":
                path = str(row.get("source_path") or "")
                if path and path not in unexpected_paths.setdefault(item["target"], []):
                    unexpected_paths[item["target"]].append(path)
    if not blocked:
        return result
    new_validation = dict(result.validation)
    for target in result.targets:
        adapter_id = _adapter_id(target)
        statuses = blocked.get(adapter_id)
        if not statuses:
            continue
        report = _lookup(new_validation, target)
        if report is None:
            continue
        issues = list(report.issues)
        if "blocked_missing_artifact" in statuses and not any(
            issue.code == "blocked-missing-artifact" and issue.severity == "block"
            for issue in issues
        ):
            issues.append(
                ValidationIssue(
                    severity="block",
                    code="blocked-missing-artifact",
                    path="",
                    message="compatibility blocked: missing required staged artifact",
                )
            )
        if "unexpected_artifact_present" in statuses:
            existing_paths = {
                issue.path
                for issue in issues
                if issue.code == "unexpected-artifact-present"
                and issue.severity == "block"
            }
            paths = unexpected_paths.get(adapter_id) or [""]
            for path in paths:
                if path in existing_paths:
                    continue
                issues.append(
                    ValidationIssue(
                        severity="block",
                        code="unexpected-artifact-present",
                        path=path,
                        message="compatibility blocked: unexpected staged artifact",
                    )
                )
                existing_paths.add(path)
        updated = ValidationReport(
            target=report.target,
            plugin_root=report.plugin_root,
            issues=tuple(issues),
        )
        if target in new_validation:
            new_validation[target] = updated
        else:
            new_validation[adapter_id] = updated
    return replace(result, validation=new_validation)


def write_reports(
    result: "CompileResult",
    report_dir: Path,
) -> tuple[Path, ...]:
    report_dir.mkdir(parents=True, exist_ok=True)
    inventories = _inventories_for(result)
    payloads = (
        _capabilities_payload(result),
        _compatibility_payload(result, inventories),
        _artifacts_payload(result, inventories),
        _validation_payload(result),
    )
    paths = tuple(report_dir / name for name in _REPORT_NAMES)
    for path, payload in zip(paths[:4], payloads):
        _write_json(path, payload)
    write_text_utf8_lf(paths[4], _markdown(result))
    return paths
