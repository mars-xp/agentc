from __future__ import annotations

from pathlib import Path

from plugin_compiler.model import CompilerIssue, Target
from plugin_targets.codex import stage as codex_stage
from plugin_targets.codex import validate as codex_validate
from plugin_targets.codex.manifest import codex_target_identity_issues
from plugin_targets.codex.source_parse import parse_codex_plugin, validate_codex_source
from plugin_targets.codex.zip_tree import pack_codex_zip
from plugin_targets.host_adapter import HostAdapter
from plugin_targets.spi import (
    AdapterCompileResult,
    CompileRequest,
    InstallRequest,
    InstallResult,
    ParsedPlugin,
    PublishRequest,
    PublishResult,
    ValidateRequest,
    ValidationResult,
)


class CodexAdapter(HostAdapter):
    id = "codex"
    display_name = "Codex"
    target = Target.CODEX

    def detect(self, plugin_dir: Path) -> bool:
        return (plugin_dir / ".codex-plugin" / "plugin.json").is_file()

    def validate_source(self, plugin_dir: Path) -> tuple[CompilerIssue, ...]:
        return validate_codex_source(plugin_dir)

    def parse(self, plugin_dir: Path) -> ParsedPlugin:
        return parse_codex_plugin(plugin_dir)

    def compile(self, request: CompileRequest) -> AdapterCompileResult:
        identity_issues = codex_target_identity_issues(request.parsed)
        staged = codex_stage.stage_codex(
            request.parsed,
            request.layout,
            collected_issues=request.collected_issues,
        )
        try:
            artifact_issues = identity_issues + codex_stage.check_codex_artifacts(
                request.parsed.capabilities,
                staged,
                request.parsed.override_config,
                plugin_dir=request.parsed.plugin_dir,
                explained_issues=request.collected_issues,
            )
        except Exception as exc:
            artifact_issues = identity_issues + (
                CompilerIssue(
                    severity="block",
                    code="artifact-check-failed",
                    path=str(staged),
                    message="{0} artifact reconciliation raised {1}: {2}".format(
                        self.target.value, type(exc).__name__, exc
                    ),
                ),
            )
        ok = not any(i.severity == "block" for i in artifact_issues)
        return AdapterCompileResult(
            ok=ok,
            adapter_id=self.id,
            staged_root=str(staged),
            issues=tuple(artifact_issues),
        )

    def validate(self, request: ValidateRequest) -> ValidationResult:
        report = codex_validate.validate_codex_staged(
            request.staged_root,
            runner=request.runner,
            plugin_dir=request.parsed.plugin_dir,
            config=request.parsed.override_config,
            capabilities=request.parsed.capabilities,
        )
        return ValidationResult(
            ok=report.ok,
            adapter_id=self.id,
            staged_root=str(request.staged_root),
            issues=tuple(report.issues),
            host_report=report,
        )

    def publish_local(self, request: PublishRequest) -> PublishResult:
        result = super().publish_local(request)
        archive_path = None
        if request.want_zip and result.published_root:
            archive_path = str(
                pack_codex_zip(
                    request.parsed,
                    Path(result.published_root),
                    request.layout,
                )
            )
        return PublishResult(
            ok=result.ok,
            adapter_id=result.adapter_id,
            published_root=result.published_root,
            issues=result.issues,
            archive_path=archive_path,
        )

    def install(self, request: InstallRequest) -> InstallResult:
        from plugin_targets.codex.install import install as install_codex

        return install_codex(request)


def get_adapter() -> CodexAdapter:
    return CodexAdapter()
