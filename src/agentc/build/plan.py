"""Build plan derived from CanonicalProject IR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentc.model.ir import CanonicalProject

_HOST_ORDER = ("cursor", "codex", "claude-code")


@dataclass(frozen=True)
class BuildPlan:
    enabled_hosts: tuple[str, ...]
    policy: str
    project_root: Path
    scopes: tuple[str, ...]

    @classmethod
    def from_ir(
        cls,
        project_root: Path,
        ir: CanonicalProject,
        policy_override: str | None,
    ) -> BuildPlan:
        enabled_hosts = tuple(
            host for host in _HOST_ORDER if ir.targets.get(host) is True
        )
        policy = policy_override or ir.portability_policy
        return cls(
            enabled_hosts=enabled_hosts,
            policy=policy,
            project_root=project_root.resolve(),
            scopes=ir.scopes,
        )
