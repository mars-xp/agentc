from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from plugin_compiler.agents import AgentDocument
from plugin_compiler.commands import CommandDocument
from plugin_compiler.extras import (
    ConnectorDocument,
    LspDocument,
    MonitorDocument,
    OutputStyleDocument,
    ThemeDocument,
)
from plugin_compiler.hooks import HookDocument
from plugin_compiler.manifests import PluginIdentity
from plugin_compiler.mcp import McpDocument
from plugin_compiler.model import CapabilityRef, CompilerIssue
from plugin_compiler.overrides import OverrideConfig
from plugin_compiler.paths import BuildLayout
from plugin_compiler.rules import RuleDocument
from plugin_compiler.skills import SkillDocument
from plugin_compiler.workflows import WorkflowDocument


Publisher = Callable[[Path, Path], None]


@dataclass(frozen=True)
class SourceDetect:
    matched: bool  # True = 本插件认领该目录


@dataclass(frozen=True)
class SourceExtension:
    dialect: str
    fields: dict  # 来源清单去掉公共核心后的剩余 JSON 对象（浅拷贝）


@dataclass(frozen=True)
class TargetOverlay:
    target_id: str
    fields: dict  # 来自 packaging/codex-plugin.json 等
    remove_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeclaredResource:
    source_path: str  # posix 相对插件根
    kind: str  # 与 CapabilityKind.value 或 "file" 对齐即可


@dataclass(frozen=True)
class ParsedPlugin:
    plugin_dir: Path
    plugin_name: str
    source_dialect: str
    identity: PluginIdentity
    source_extension: SourceExtension
    capabilities: tuple[CapabilityRef, ...]
    skills: tuple[SkillDocument, ...]
    commands: tuple[CommandDocument, ...]
    rules: tuple[RuleDocument, ...]
    hooks: tuple[HookDocument, ...]
    agents: tuple[AgentDocument, ...]
    workflows: tuple[WorkflowDocument, ...]
    mcp_documents: tuple[McpDocument, ...]
    connectors: tuple[ConnectorDocument, ...]
    lsp_documents: tuple[LspDocument, ...]
    output_styles: tuple[OutputStyleDocument, ...]
    themes: tuple[ThemeDocument, ...]
    monitors: tuple[MonitorDocument, ...]
    override_config: OverrideConfig | None
    target_overlays: tuple[TargetOverlay, ...] = ()
    declared_resources: tuple[DeclaredResource, ...] = ()
    source_issues: tuple[CompilerIssue, ...] = ()
    workspace_full_source: bool = False


@dataclass
class CompileRequest:
    parsed: ParsedPlugin
    layout: BuildLayout
    runner: object | None
    collected_issues: list[CompilerIssue]


@dataclass(frozen=True)
class AdapterCompileResult:
    ok: bool
    adapter_id: str
    staged_root: str | None
    issues: tuple[CompilerIssue, ...]


@dataclass
class ValidateRequest:
    parsed: ParsedPlugin
    staged_root: Path
    runner: object | None


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    adapter_id: str
    staged_root: str
    issues: tuple
    host_report: object | None = None


@dataclass
class PublishRequest:
    parsed: ParsedPlugin
    layout: BuildLayout
    staged_root: Path
    want_zip: bool = False
    publisher: Publisher | None = None


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    adapter_id: str
    published_root: str | None
    issues: tuple[CompilerIssue, ...] = ()
    archive_path: str | None = None


@dataclass
class InstallRequest:
    parsed: ParsedPlugin | None = None
    published_root: Path | None = None
    install_root: Path | None = None
    runner: object | None = None
    source: Path | None = None
    current_user: bool = False


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    adapter_id: str
    installed_root: str | None
    issues: tuple[CompilerIssue, ...] = ()
    suggested_argv: tuple[str, ...] = ()


class TargetAdapter(Protocol):
    id: str
    display_name: str

    def compile(self, request: CompileRequest) -> AdapterCompileResult: ...

    def validate(self, request: ValidateRequest) -> ValidationResult: ...

    def publish_local(self, request: PublishRequest) -> PublishResult: ...

    def install(self, request: InstallRequest) -> InstallResult: ...
