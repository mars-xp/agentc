"""Immutable Canonical IR (规范中间表示) for agent-project/v1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

PORTABILITY_POLICIES = frozenset({"strict", "compatible", "permissive"})


@dataclass(frozen=True)
class CanonicalProject:
    project_id: str
    name: str
    version: str
    targets: Mapping[str, bool]
    skill_paths: tuple[str, ...]
    rule_paths: tuple[str, ...]
    command_paths: tuple[str, ...]
    agent_paths: tuple[str, ...]
    content_paths: tuple[str, ...]
    portability_policy: str
    scopes: tuple[str, ...]
    requirement_names: tuple[str, ...]
    forbid_absolute_paths: bool
