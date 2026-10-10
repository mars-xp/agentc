from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Target(str, Enum):
    CURSOR = "cursor"
    CODEX = "codex"
    CLAUDE_CODE = "claude-code"


ALL_TARGETS: tuple[Target, ...] = tuple(Target)


class CapabilityKind(str, Enum):
    PLUGIN_METADATA = "plugin-metadata"
    SKILL = "skill"
    RULE = "rule"
    COMMAND = "command"
    AGENT = "agent"
    WORKFLOW = "workflow"
    HOOK = "hook"
    MCP = "mcp"
    CONNECTOR = "connector"
    LSP = "lsp"
    OUTPUT_STYLE = "output-style"
    THEME = "theme"
    MONITOR = "monitor"


@dataclass(frozen=True)
class CapabilityRef:
    capability_id: str
    kind: CapabilityKind
    source_path: str
    source_dialect: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "id": self.capability_id,
            "kind": self.kind.value,
            "source_path": self.source_path,
            "source_dialect": self.source_dialect,
        }


@dataclass(frozen=True)
class CompilerIssue:
    severity: str
    code: str
    path: str
    message: str
    target: Target | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }
        if self.target is not None:
            payload["target"] = self.target.value
        return payload
