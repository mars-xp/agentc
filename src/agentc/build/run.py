"""Build agent-project sources through host adapters."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from agentc.build.plan import BuildPlan
from agentc.build.report import write_build_reports
from agentc.build.translate import TranslateError, translate_to_request
from agentc.matrix.load import load_matrix, resolve_profile_ids
from agentc.model.ir import CanonicalProject
from agentc.model.diagnostics import (
    CODE_ADAPTER_BLOCK,
    CODE_ARTIFACT_ESCAPE,
    CODE_MATRIX_UNSUPPORTED,
    CODE_NO_ENABLED_HOSTS,
    CODE_OVERLAY_INVALID,
    CODE_OVERLAY_MISSING,
    CODE_OVERLAY_PATH,
    CODE_OVERLAY_REMOVE_REASON,
    CODE_PROFILE_INVALID,
    CODE_PROFILE_UNKNOWN,
    CODE_TRANSLATE_FAILED,
    Diagnostic,
)
from agentc.overlay.ops import OverlayOp, parse_overlay_ops
from agentc.project.load import load_project_yaml, to_ir
from agentc.validate.engine import validate_project
from plugin_compiler.paths import require_inside
from plugin_targets.registry import load_adapter
from plugin_targets.spi import ValidateRequest

_FATAL_EXIT_CODES = frozenset(
    {
        CODE_NO_ENABLED_HOSTS,
        CODE_TRANSLATE_FAILED,
        CODE_ARTIFACT_ESCAPE,
        CODE_OVERLAY_INVALID,
        CODE_OVERLAY_REMOVE_REASON,
        CODE_OVERLAY_PATH,
        CODE_OVERLAY_MISSING,
        CODE_PROFILE_UNKNOWN,
        CODE_PROFILE_INVALID,
    }
)


def exit_code_for_build(
    diagnostics: list[Diagnostic],
    policy: str,
    *,
    source_failed: bool,
) -> int:
    if source_failed:
        return 1
    for diagnostic in diagnostics:
        if diagnostic.level != "error":
            continue
        if diagnostic.code in _FATAL_EXIT_CODES:
            return 1
    if policy == "strict":
        return 1 if any(d.level == "error" for d in diagnostics) else 0
    return 0


def build_project(
    project_root: Path,
    *,
    policy: str | None = None,
    matrix_path: Path | None = None,
    profile_overrides: tuple[str, ...] = (),
) -> tuple[list[Diagnostic], bool, str]:
    source = validate_project(project_root, policy=policy)
    if any(issue.level == "error" for issue in source):
        return (source, True, policy or "strict")

    root = project_root.resolve()
    overlay_ops, _overlay_diags = parse_overlay_ops(root)
    ir = to_ir(load_project_yaml(root / "agent-project.yaml"))
    plan = BuildPlan.from_ir(root, ir, policy)
    diagnostics: list[Diagnostic] = []

    if not plan.enabled_hosts:
        diagnostics.append(
            Diagnostic(
                code=CODE_NO_ENABLED_HOSTS,
                level="error",
                message="No enabled build targets.",
                path="/targets",
                hint="Enable at least one host under targets.",
            )
        )
        return _finish_build(
            root, diagnostics, plan.policy, overlay_ops=overlay_ops
        )

    ids, profile_diags = resolve_profile_ids(
        tuple(profile_overrides or ()), matrix_path
    )
    if profile_diags:
        diagnostics.extend(profile_diags)
        return _finish_build(
            root, diagnostics, plan.policy, overlay_ops=overlay_ops
        )
    matrix = load_matrix(matrix_path, host_ids=ids)
    allowed_hosts: list[str] = []
    for host in plan.enabled_hosts:
        blocked_kind = _matrix_blocked_kind(matrix, host, ir)
        if blocked_kind is not None:
            diagnostics.append(_matrix_unsupported_issue(host, blocked_kind))
            continue
        allowed_hosts.append(host)

    if allowed_hosts:
        try:
            request = translate_to_request(root, ir)
        except TranslateError as exc:
            diagnostics.append(
                Diagnostic(
                    code=CODE_TRANSLATE_FAILED,
                    level="error",
                    message=str(exc),
                    path="/",
                    hint=str(exc),
                )
            )
            return _finish_build(
                root, diagnostics, plan.policy, overlay_ops=overlay_ops
            )

        for host in allowed_hosts:
            diagnostics.extend(_build_host(host, request))

    diagnostics = _apply_permissive_downgrades(diagnostics, plan.policy)
    diagnostics.extend(_gitignore_notice(root))
    return _finish_build(
        root, diagnostics, plan.policy, overlay_ops=overlay_ops
    )


def _finish_build(
    project_root: Path,
    diagnostics: list[Diagnostic],
    policy: str,
    overlay_ops: tuple[OverlayOp, ...] = (),
) -> tuple[list[Diagnostic], bool, str]:
    result = list(diagnostics)
    try:
        write_build_reports(project_root, result, overlay_ops=overlay_ops)
    except ValueError as exc:
        result.append(_reports_escape_issue(str(exc)))
    return (result, False, policy)


def _reports_escape_issue(message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_ARTIFACT_ESCAPE,
        level="error",
        message=message,
        path="/reports",
        hint=message,
    )


def _build_host(host: str, request) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    collected = getattr(request, "collected_issues", None)
    if collected is not None:
        collected.clear()
    adapter = load_adapter(host)
    try:
        compile_result = adapter.compile(request)
    except (ValueError, RuntimeError) as exc:
        issues.append(_adapter_block_issue(host, str(exc)))
        return issues

    issues.extend(_block_issues_from_compiler(host, compile_result.issues))
    if collected is not None:
        issues.extend(_block_issues_from_compiler(host, collected))
    if not compile_result.ok:
        if not any(
            issue.severity == "block" for issue in compile_result.issues
        ):
            issues.append(
                _adapter_block_issue(host, "compile failed for {0}".format(host))
            )
        return issues

    staged_root = compile_result.staged_root
    if not staged_root:
        return issues

    staged = Path(staged_root)
    try:
        require_inside(request.layout.root, staged)
    except ValueError as exc:
        issues.append(_artifact_escape_issue(host, str(exc)))
        return issues

    from agentc.overlay.ops import add_files_for_host, parse_overlay_ops

    ops, overlay_diags = parse_overlay_ops(request.parsed.plugin_dir)
    if overlay_diags:
        return list(overlay_diags)
    for src, to_rel in add_files_for_host(request.parsed.plugin_dir, ops, host):
        dest = staged / to_rel
        try:
            require_inside(staged, dest)
        except ValueError as exc:
            issues.append(
                Diagnostic(
                    code=CODE_OVERLAY_PATH,
                    level="error",
                    message=str(exc),
                    path="/targets/{0}".format(host),
                    hint=str(exc),
                )
            )
            return issues
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    validate_result = adapter.validate(
        ValidateRequest(
            parsed=request.parsed,
            staged_root=staged,
            runner=None,
        )
    )
    issues.extend(_block_issues_from_compiler(host, validate_result.issues))
    if not validate_result.ok:
        return issues

    output_dir = request.layout.output(host, request.parsed.plugin_name)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(staged, output_dir)
    try:
        require_inside(request.layout.root, output_dir)
    except ValueError as exc:
        issues.append(_artifact_escape_issue(host, str(exc)))
    return issues


def _registered_capability_kinds(ir: CanonicalProject) -> tuple[str, ...]:
    kinds: list[str] = []
    if ir.skill_paths:
        kinds.append("skill")
    if ir.rule_paths:
        kinds.append("rule")
    if ir.command_paths:
        kinds.append("command")
    if ir.agent_paths:
        kinds.append("agent")
    return tuple(kinds)


def _matrix_blocked_kind(
    matrix, host: str, ir: CanonicalProject
) -> str | None:
    for kind in _registered_capability_kinds(ir):
        if not matrix.allows_success(host, kind):
            return kind
    return None


def _matrix_unsupported_issue(host: str, kind: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_MATRIX_UNSUPPORTED,
        level="error",
        message=(
            "Capability matrix does not allow {0} builds for {1}."
        ).format(kind, host),
        path="/targets/{0}".format(host),
        hint="Disable this target or choose a supported host.",
    )


def _adapter_block_issue(host: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_ADAPTER_BLOCK,
        level="error",
        message=message,
        path="/targets/{0}".format(host),
        hint=message,
    )


def _artifact_escape_issue(host: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_ARTIFACT_ESCAPE,
        level="error",
        message=message,
        path="/targets/{0}".format(host),
        hint=message,
    )


def _block_issues_from_compiler(host: str, compiler_issues) -> list[Diagnostic]:
    return [
        Diagnostic(
            code=CODE_ADAPTER_BLOCK,
            level="error",
            message=issue.message,
            path="/targets/{0}".format(host),
            hint=issue.message,
        )
        for issue in compiler_issues
        if issue.severity == "block"
    ]


def _apply_permissive_downgrades(
    diagnostics: list[Diagnostic],
    policy: str,
) -> list[Diagnostic]:
    if policy != "permissive":
        return diagnostics
    downgraded: list[Diagnostic] = []
    for diagnostic in diagnostics:
        if (
            diagnostic.level == "error"
            and diagnostic.code in (CODE_ADAPTER_BLOCK, CODE_MATRIX_UNSUPPORTED)
        ):
            downgraded.append(replace(diagnostic, level="warning"))
        else:
            downgraded.append(diagnostic)
    return downgraded


def _gitignore_notice(project_root: Path) -> list[Diagnostic]:
    gitignore = project_root / ".gitignore"
    if gitignore.is_file():
        lines = {
            line.strip()
            for line in gitignore.read_text(encoding="utf-8").splitlines()
        }
        if "build/" in lines or "build" in lines:
            return []
    return [
        Diagnostic(
            code=CODE_ARTIFACT_ESCAPE,
            level="notice",
            message="Project does not ignore build/.",
            path="/.gitignore",
            hint="Add a build/ line to .gitignore.",
        )
    ]
