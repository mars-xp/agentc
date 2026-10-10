from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .agents import AgentDocument
from .audits import compiler_blocking_audits
from .commands import CommandDocument
from .extras import (
    ConnectorDocument,
    LspDocument,
    MonitorDocument,
    OutputStyleDocument,
    ThemeDocument,
)
from .hooks import HookDocument
from .manifests import load_plugin_identity
from .mcp import McpDocument
from .model import CapabilityRef, CompilerIssue, Target
from .names import InvalidPluginNameError, assert_plugin_name_for_targets
from .overlap import find_overlap_issues, units_from_documents
from .overrides import OverrideConfig, load_override_config, validate_overrides
from .paths import (
    BuildLayout,
    clear_target_intermediate,
    publish_directory,
)
from .reports import apply_compatibility_gate, write_reports
from .runtime import require_python_310
from .rules import RuleDocument
from .validation import (
    CommandRunner,
    ValidationIssue,
    ValidationReport,
)
from .workflows import WorkflowDocument
from .documents import load_scanned_documents
from plugin_targets.registry import load_adapter
from plugin_targets.source import resolve_source_adapter
from plugin_targets.spi import (
    CompileRequest,
    DeclaredResource,
    ParsedPlugin,
    PublishRequest,
    ValidateRequest,
)

# Backward-compatible alias for tests / packaging that import from orchestrator.
_load_scanned_documents = load_scanned_documents


Publisher = Callable[[Path, Path], None]
_GATE_B_OVERLAP_CODES = frozenset(
    {
        "content-duplicate",
        "semantic-name-overlap",
        "semantic-line-overlap",
        "cross-form-overlap",
    }
)


@dataclass(frozen=True)
class CompileResult:
    plugin_name: str
    targets: tuple[str, ...]
    capabilities: tuple[CapabilityRef, ...]
    staged_roots: dict[str, str]
    validation: dict[str, ValidationReport]
    published_roots: dict[str, str]
    validate_only: bool = False
    override_config: OverrideConfig | None = None
    commands: tuple[CommandDocument, ...] = ()
    rules: tuple[RuleDocument, ...] = ()
    hooks: tuple[HookDocument, ...] = ()
    agents: tuple[AgentDocument, ...] = ()
    workflows: tuple[WorkflowDocument, ...] = ()
    mcp_documents: tuple[McpDocument, ...] = ()
    connectors: tuple[ConnectorDocument, ...] = ()
    lsp_documents: tuple[LspDocument, ...] = ()
    output_styles: tuple[OutputStyleDocument, ...] = ()
    themes: tuple[ThemeDocument, ...] = ()
    monitors: tuple[MonitorDocument, ...] = ()
    archives: dict[str, str] = field(default_factory=dict)
    declared_resources: tuple[DeclaredResource, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.targets) and all(
            adapter_id in self.validation
            and self.validation[adapter_id].target == adapter_id
            and self.validation[adapter_id].ok
            for adapter_id in self.targets
        )


def _plain_adapter_id(value: object) -> str:
    if isinstance(value, Target):
        return value.value
    return str(value)


def _native_target(adapter_id: str) -> Target | None:
    try:
        return Target(adapter_id)
    except ValueError:
        return None


def _validate_targets(targets: tuple[str, ...]) -> tuple[str, ...]:
    if not targets:
        raise ValueError("targets must not be empty")
    if any(not isinstance(item, str) for item in targets):
        raise ValueError("targets must contain only adapter id strings")
    normalized = tuple(_plain_adapter_id(item) for item in targets)
    if any(not item.strip() for item in normalized):
        raise ValueError("targets must contain only adapter id strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("targets must not contain duplicates")
    return normalized


def _exception_issue(
    code: str,
    path: str,
    operation: str,
    exc: Exception,
) -> ValidationIssue:
    return ValidationIssue(
        severity="block",
        code=code,
        path=path,
        message="{0} raised {1}: {2}".format(operation, type(exc).__name__, exc),
    )


def _artifact_issue(issue: CompilerIssue) -> ValidationIssue:
    return ValidationIssue(
        severity=issue.severity,
        code=issue.code,
        path=issue.path,
        message=issue.message,
    )


def _split_override_issues(
    override_issues: tuple[CompilerIssue, ...],
) -> tuple[
    tuple[CompilerIssue, ...],
    dict[str, tuple[CompilerIssue, ...]],
    tuple[ValidationIssue, ...],
]:
    global_blocks: list[CompilerIssue] = []
    per_target_blocks: dict[str, list[CompilerIssue]] = {}
    warns: list[ValidationIssue] = []
    for issue in override_issues:
        if issue.severity == "block":
            global_blocks.append(issue)
        else:
            warns.append(_artifact_issue(issue))
    frozen_per_target = {
        target: tuple(issues) for target, issues in per_target_blocks.items()
    }
    return tuple(global_blocks), frozen_per_target, tuple(warns)


def _override_issues_for_target(
    adapter_id: str,
    override_warns: tuple[ValidationIssue, ...],
    per_target_blocks: dict[str, tuple[CompilerIssue, ...]],
) -> tuple[ValidationIssue, ...]:
    return override_warns + tuple(
        _artifact_issue(issue) for issue in per_target_blocks.get(adapter_id, ())
    )


def _as_validation_issue(issue: object) -> ValidationIssue:
    if isinstance(issue, ValidationIssue):
        return issue
    return _artifact_issue(issue)  # type: ignore[arg-type]


def _ensure_block(
    issues: tuple[ValidationIssue, ...],
    *,
    code: str,
    path: str,
    message: str,
) -> tuple[ValidationIssue, ...]:
    if any(item.severity == "block" for item in issues):
        return issues
    return issues + (
        ValidationIssue(
            severity="block",
            code=code,
            path=path,
            message=message,
        ),
    )


def _overlap_issue_for_compile(
    issue: CompilerIssue, allow_overlap: bool
) -> CompilerIssue:
    if (
        allow_overlap
        and issue.code in _GATE_B_OVERLAP_CODES
        and issue.severity == "block"
    ):
        return replace(issue, severity="warn")
    return issue


def _report(
    adapter_id: str,
    plugin_root: Path,
    issues: tuple[ValidationIssue, ...],
) -> ValidationReport:
    return ValidationReport(
        target=adapter_id,
        plugin_root=str(plugin_root),
        issues=issues,
    )


def _write_current_reports(
    result: CompileResult,
    layout: BuildLayout,
) -> None:
    write_reports(result, layout.report_dir(result.plugin_name))


def source_key(plugin_dir: Path, repo_root: Path) -> str:
    relative = plugin_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    return relative.replace("/", "__")


def compile_plugin(
    plugin_dir: Path,
    repo_root: Path,
    targets: tuple[str, ...],
    validate_only: bool = False,
    runner: CommandRunner | None = None,
    publisher: Publisher = publish_directory,
    allow_overlap: bool = False,
    want_zip: bool = False,
    source_format: str = "auto",
) -> CompileResult:
    require_python_310()
    targets = _validate_targets(targets)
    plugin_dir = Path(plugin_dir)
    layout = BuildLayout.from_repo(Path(repo_root))
    source_adapter = resolve_source_adapter(plugin_dir, source_format)
    try:
        source_issues = tuple(source_adapter.validate_source(plugin_dir))
    except Exception as exc:
        try:
            plugin_name = load_plugin_identity(plugin_dir).name
        except Exception:
            plugin_name = source_key(plugin_dir, Path(repo_root))
        scan_issue = _exception_issue(
            "scan-failed",
            str(plugin_dir),
            "plugin scan",
            exc,
        )
        result = CompileResult(
            plugin_name=plugin_name,
            targets=targets,
            capabilities=(),
            staged_roots={},
            validation={
                target: _report(
                    target,
                    layout.intermediate(target, plugin_name),
                    (scan_issue,),
                )
                for target in targets
            },
            published_roots={},
            validate_only=validate_only,
        )
        write_reports(result, layout.report_dir(plugin_name))
        return result
    if any(issue.severity == "block" for issue in source_issues):
        key = source_key(plugin_dir, Path(repo_root))
        report_issues = tuple(_artifact_issue(issue) for issue in source_issues)
        result = CompileResult(
            plugin_name=key,
            targets=targets,
            capabilities=(),
            staged_roots={},
            validation={
                target: _report(target, plugin_dir, report_issues)
                for target in targets
            },
            published_roots={},
            validate_only=validate_only,
        )
        write_reports(result, layout.report_dir(key))
        return result

    published_roots: dict[str, str] = {}
    archives: dict[str, str] = {}
    validation: dict[str, ValidationReport] = {}
    staged_roots: dict[str, str] = {}
    capabilities: tuple[CapabilityRef, ...] = ()
    config: OverrideConfig | None = None
    commands: tuple[CommandDocument, ...] = ()
    rules: tuple[RuleDocument, ...] = ()
    hooks: tuple[HookDocument, ...] = ()
    agents: tuple[AgentDocument, ...] = ()
    workflows: tuple[WorkflowDocument, ...] = ()
    mcp_documents: tuple[McpDocument, ...] = ()
    connectors: tuple[ConnectorDocument, ...] = ()
    lsp_documents: tuple[LspDocument, ...] = ()
    output_styles: tuple[OutputStyleDocument, ...] = ()
    themes: tuple[ThemeDocument, ...] = ()
    monitors: tuple[MonitorDocument, ...] = ()
    declared_resources: tuple[DeclaredResource, ...] = ()
    plugin_name = ""

    def snapshot() -> CompileResult:
        return CompileResult(
            plugin_name=plugin_name,
            targets=targets,
            capabilities=capabilities,
            staged_roots=dict(staged_roots),
            validation={
                target: validation[target]
                for target in targets
                if target in validation
            },
            published_roots=dict(published_roots),
            archives=dict(archives),
            validate_only=validate_only,
            override_config=config,
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
            declared_resources=declared_resources,
        )

    try:
        parsed = source_adapter.parse(plugin_dir)
    except Exception as exc:
        try:
            plugin_name = load_plugin_identity(plugin_dir).name
        except Exception:
            plugin_name = source_key(plugin_dir, Path(repo_root))
        validation = {
            target: _report(
                target,
                layout.intermediate(target, plugin_name),
                (
                    _exception_issue(
                        "scan-failed",
                        str(plugin_dir),
                        "plugin scan",
                        exc,
                    ),
                ),
            )
            for target in targets
        }
        result = snapshot()
        _write_current_reports(result, layout)
        return result

    plugin_name = parsed.plugin_name
    source_load_blocks = tuple(
        _artifact_issue(issue)
        for issue in parsed.source_issues
        if issue.severity == "block"
    )
    if source_load_blocks:
        validation = {
            target: _report(target, plugin_dir, source_load_blocks)
            for target in targets
        }
        result = snapshot()
        _write_current_reports(result, layout)
        return result

    capabilities = parsed.capabilities
    commands = parsed.commands
    rules = parsed.rules
    hooks = parsed.hooks
    agents = parsed.agents
    workflows = parsed.workflows
    mcp_documents = parsed.mcp_documents
    connectors = parsed.connectors
    lsp_documents = parsed.lsp_documents
    output_styles = parsed.output_styles
    themes = parsed.themes
    monitors = parsed.monitors
    declared_resources = parsed.declared_resources

    try:
        native_for_names = tuple(
            native
            for adapter_id in targets
            if (native := _native_target(adapter_id)) is not None
        )
        if native_for_names:
            assert_plugin_name_for_targets(plugin_name, native_for_names)
    except InvalidPluginNameError as exc:
        validation = {
            target: _report(
                target,
                plugin_dir,
                (
                    ValidationIssue(
                        severity="block",
                        code="invalid-plugin-name",
                        path=".cursor-plugin/plugin.json",
                        message=str(exc),
                    ),
                ),
            )
            for target in targets
        }
        result = snapshot()
        _write_current_reports(result, layout)
        return result

    try:
        config = load_override_config(plugin_dir)
        override_issues = validate_overrides(config, capabilities)
    except Exception as exc:
        validation = {
            target: _report(
                target,
                plugin_dir,
                (
                    _exception_issue(
                        "override-config-failed",
                        str(plugin_dir / "packaging" / "platform-overrides.json"),
                        "load/validate overrides",
                        exc,
                    ),
                ),
            )
            for target in targets
        }
        result = snapshot()
        _write_current_reports(result, layout)
        return result
    global_override_blocks, per_target_override_blocks, override_warns = (
        _split_override_issues(override_issues)
    )
    if global_override_blocks:
        block_issues = tuple(
            _artifact_issue(issue) for issue in global_override_blocks
        )
        validation = {
            target: _report(target, plugin_dir, block_issues) for target in targets
        }
        result = snapshot()
        _write_current_reports(result, layout)
        return result

    overlap_issues = tuple(
        _overlap_issue_for_compile(issue, allow_overlap)
        for issue in find_overlap_issues(
            units_from_documents(
                plugin_dir,
                capabilities,
                commands=commands,
                rules=rules,
                agents=agents,
                workflows=workflows,
                hooks=hooks,
            )
        )
    )
    if any(issue.severity == "block" for issue in overlap_issues):
        block_issues = tuple(
            _artifact_issue(issue)
            for issue in overlap_issues
            if issue.severity == "block"
        )
        validation = {
            target: _report(target, plugin_dir, block_issues) for target in targets
        }
        result = snapshot()
        _write_current_reports(result, layout)
        return result
    override_warns = override_warns + tuple(
        _artifact_issue(issue)
        for issue in overlap_issues
        if issue.severity != "block"
    )

    def target_override_issues(adapter_id: str) -> tuple[ValidationIssue, ...]:
        return _override_issues_for_target(
            adapter_id, override_warns, per_target_override_blocks
        )

    parsed = replace(parsed, override_config=config)

    pending_publishes: list[tuple[str, Path, ValidationReport]] = []

    try:
        for adapter_id in targets:
            clear_target_intermediate(layout, adapter_id, plugin_name)
            expected_root = layout.intermediate(adapter_id, plugin_name)
            native = _native_target(adapter_id)
            try:
                blocking = (
                    compiler_blocking_audits(plugin_dir, native, parsed=parsed)
                    if native is not None
                    else []
                )
            except Exception as exc:
                validation[adapter_id] = _report(
                    adapter_id,
                    expected_root,
                    (
                        _exception_issue(
                            "source-audit-failed",
                            str(plugin_dir),
                            "{0} source audit".format(adapter_id),
                            exc,
                        ),
                    )
                    + target_override_issues(adapter_id),
                )
                continue
            if blocking:
                validation[adapter_id] = _report(
                    adapter_id,
                    plugin_dir,
                    tuple(
                        ValidationIssue(
                            severity="block",
                            code="source-audit-failed",
                            path=issue.path,
                            message="{0}: {1}".format(issue.line, issue.message),
                        )
                        for issue in blocking
                    )
                    + target_override_issues(adapter_id),
                )
                continue
            try:
                adapter = load_adapter(adapter_id)
            except Exception as exc:
                validation[adapter_id] = _report(
                    adapter_id,
                    expected_root,
                    (
                        _exception_issue(
                            "adapter-load-failed",
                            str(plugin_dir),
                            "{0} adapter load".format(adapter_id),
                            exc,
                        ),
                    )
                    + target_override_issues(adapter_id),
                )
                continue
            collected_issues: list[CompilerIssue] = []
            try:
                compile_result = adapter.compile(
                    CompileRequest(
                        parsed=parsed,
                        layout=layout,
                        runner=runner,
                        collected_issues=collected_issues,
                    )
                )
            except Exception as exc:
                validation[adapter_id] = _report(
                    adapter_id,
                    expected_root,
                    (
                        _exception_issue(
                            "stage-failed",
                            str(expected_root),
                            "{0} staging".format(adapter_id),
                            exc,
                        ),
                    )
                    + target_override_issues(adapter_id),
                )
                continue
            compile_issues = tuple(
                _as_validation_issue(issue) for issue in compile_result.issues
            )
            if (
                not compile_result.ok
                or compile_result.adapter_id != adapter_id
                or not compile_result.staged_root
            ):
                extras: list[ValidationIssue] = []
                if not compile_result.staged_root:
                    extras.append(
                        ValidationIssue(
                            severity="block",
                            code="missing-root",
                            path=str(expected_root),
                            message="{0} compile did not return staged_root".format(
                                adapter_id
                            ),
                        )
                    )
                if compile_result.adapter_id != adapter_id:
                    extras.append(
                        ValidationIssue(
                            severity="block",
                            code="adapter-id-mismatch",
                            path=str(expected_root),
                            message="{0} compile returned adapter_id {1!r}".format(
                                adapter_id, compile_result.adapter_id
                            ),
                        )
                    )
                merged = (
                    compile_issues
                    + tuple(extras)
                    + tuple(_artifact_issue(issue) for issue in collected_issues)
                    + target_override_issues(adapter_id)
                )
                if not compile_result.ok:
                    merged = _ensure_block(
                        merged,
                        code="adapter-ok-false",
                        path=str(expected_root),
                        message="{0} compile reported ok=False".format(adapter_id),
                    )
                validation[adapter_id] = _report(adapter_id, expected_root, merged)
                continue
            staged = Path(compile_result.staged_root)
            staged_roots[adapter_id] = str(staged)
            artifact_issues = compile_issues

            try:
                validate_result = adapter.validate(
                    ValidateRequest(
                        parsed=parsed,
                        staged_root=staged,
                        runner=runner,
                    )
                )
            except Exception as exc:
                validation[adapter_id] = _report(
                    adapter_id,
                    staged,
                    artifact_issues
                    + tuple(_artifact_issue(issue) for issue in collected_issues)
                    + target_override_issues(adapter_id)
                    + (
                        _exception_issue(
                            "validation-failed",
                            str(staged),
                            "{0} validation".format(adapter_id),
                            exc,
                        ),
                    ),
                )
                continue
            combined_issues = (
                tuple(_as_validation_issue(issue) for issue in validate_result.issues)
                + artifact_issues
                + tuple(_artifact_issue(issue) for issue in collected_issues)
                + target_override_issues(adapter_id)
            )
            if not validate_result.ok or validate_result.adapter_id != adapter_id:
                extras = []
                if validate_result.adapter_id != adapter_id:
                    extras.append(
                        ValidationIssue(
                            severity="block",
                            code="adapter-id-mismatch",
                            path=str(staged),
                            message="{0} validate returned adapter_id {1!r}".format(
                                adapter_id, validate_result.adapter_id
                            ),
                        )
                    )
                combined_issues = combined_issues + tuple(extras)
                if not validate_result.ok:
                    combined_issues = _ensure_block(
                        combined_issues,
                        code="adapter-ok-false",
                        path=str(staged),
                        message="{0} validate reported ok=False".format(adapter_id),
                    )
                validation[adapter_id] = _report(adapter_id, staged, combined_issues)
                continue
            combined = _report(adapter_id, staged, combined_issues)
            validation[adapter_id] = combined
            gated = apply_compatibility_gate(snapshot())
            gated_report = gated.validation.get(adapter_id)
            if gated_report is not None:
                validation[adapter_id] = gated_report
                combined = gated_report
            if not combined.ok:
                continue
            if validate_only:
                continue
            pending_publishes.append((adapter_id, staged, combined))
    except (KeyboardInterrupt, SystemExit):
        _write_current_reports(snapshot(), layout)
        raise

    if not validate_only and pending_publishes:
        all_targets_ok = all(
            target in validation and validation[target].ok for target in targets
        )
        if len(targets) == 1 or all_targets_ok:
            for adapter_id, staged, combined in pending_publishes:
                adapter = load_adapter(adapter_id)
                destination = layout.output(adapter_id, plugin_name)
                _write_current_reports(snapshot(), layout)
                try:
                    publish_result = adapter.publish_local(
                        PublishRequest(
                            parsed=parsed,
                            layout=layout,
                            staged_root=staged,
                            want_zip=want_zip,
                            publisher=publisher,
                        )
                    )
                except Exception as exc:
                    validation[adapter_id] = _report(
                        adapter_id,
                        staged,
                        combined.issues
                        + (
                            _exception_issue(
                                "publish-failed",
                                str(destination),
                                "{0} publication".format(adapter_id),
                                exc,
                            ),
                        ),
                    )
                    continue
                publish_issues = tuple(
                    _as_validation_issue(issue) for issue in publish_result.issues
                )
                merged = combined.issues + publish_issues
                if (
                    not publish_result.ok
                    or not publish_result.published_root
                    or publish_result.adapter_id != adapter_id
                    or any(issue.severity == "block" for issue in publish_issues)
                ):
                    if not publish_result.ok:
                        merged = _ensure_block(
                            merged,
                            code="adapter-ok-false",
                            path=str(destination),
                            message="{0} publish reported ok=False".format(adapter_id),
                        )
                    if not publish_result.published_root:
                        merged = _ensure_block(
                            merged,
                            code="missing-root",
                            path=str(destination),
                            message="{0} publish did not return published_root".format(
                                adapter_id
                            ),
                        )
                    if publish_result.adapter_id != adapter_id:
                        merged = _ensure_block(
                            merged,
                            code="adapter-id-mismatch",
                            path=str(destination),
                            message="{0} publish returned adapter_id {1!r}".format(
                                adapter_id, publish_result.adapter_id
                            ),
                        )
                    validation[adapter_id] = _report(adapter_id, staged, merged)
                    continue
                published_roots[adapter_id] = str(publish_result.published_root)
                if publish_result.archive_path:
                    archives[adapter_id] = publish_result.archive_path
                if publish_issues:
                    validation[adapter_id] = _report(adapter_id, staged, merged)
                _write_current_reports(snapshot(), layout)

    result = snapshot()
    _write_current_reports(result, layout)
    return result
