"""Shared publish / install wrappers. Hosts override compile and validate."""
from __future__ import annotations

from pathlib import Path

from plugin_compiler.model import CompilerIssue, Target
from plugin_compiler.paths import publish_directory
from plugin_targets.spi import (
    AdapterCompileResult,
    CompileRequest,
    InstallRequest,
    InstallResult,
    PublishRequest,
    PublishResult,
    ValidateRequest,
    ValidationResult,
)


class HostAdapter:
    id: str
    display_name: str
    target: Target

    def detect(self, plugin_dir: Path) -> bool:
        """Return True when this adapter claims plugin_dir as its source."""
        return False

    def validate_source(self, plugin_dir: Path) -> tuple[CompilerIssue, ...]:
        """默认空 tuple。有 block 则编排不得 compile。"""
        return ()

    def parse(self, plugin_dir: Path):
        """默认 raise RuntimeError("parse must be overridden")."""
        raise RuntimeError("parse must be overridden")

    def compile(self, request: CompileRequest) -> AdapterCompileResult:
        return AdapterCompileResult(
            ok=False,
            adapter_id=self.id,
            staged_root=None,
            issues=(
                CompilerIssue(
                    severity="block",
                    code="not-implemented",
                    path="",
                    message="compile must be overridden by the host adapter",
                ),
            ),
        )

    def validate(self, request: ValidateRequest) -> ValidationResult:
        return ValidationResult(
            ok=False,
            adapter_id=self.id,
            staged_root=str(request.staged_root),
            issues=(
                CompilerIssue(
                    severity="block",
                    code="not-implemented",
                    path="",
                    message="validate must be overridden by the host adapter",
                ),
            ),
            host_report=None,
        )

    def publish_local(self, request: PublishRequest) -> PublishResult:
        destination = request.layout.output(self.id, request.parsed.plugin_name)
        publisher = request.publisher or publish_directory
        publisher(Path(request.staged_root), destination)
        return PublishResult(
            ok=True,
            adapter_id=self.id,
            published_root=str(destination),
            issues=(),
        )

    def install(self, request: InstallRequest) -> InstallResult:
        return InstallResult(
            ok=False,
            adapter_id=self.id,
            installed_root=None,
            issues=(
                CompilerIssue(
                    severity="block",
                    code="not-implemented",
                    path="",
                    message="install not wired",
                ),
            ),
        )
