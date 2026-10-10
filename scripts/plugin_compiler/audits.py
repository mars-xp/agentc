# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from plugin_packaging import (
    AuditIssue,
    blocking_audits,
    collect_audits,
    collect_claude_code_audits,
)
from plugin_targets.spi import ParsedPlugin

from .model import Target


def collect_compiler_audits(
    plugin_dir: Path,
    target: Target,
    parsed: ParsedPlugin | None = None,
) -> list[AuditIssue]:
    if target is Target.CURSOR:
        return collect_audits(plugin_dir, "cursor")
    if target is Target.CODEX:
        return collect_audits(plugin_dir, "codex")
    if target is Target.CLAUDE_CODE:
        return collect_claude_code_audits(plugin_dir, parsed=parsed)
    raise ValueError("unsupported target: {0!r}".format(target))


def compiler_blocking_audits(
    plugin_dir: Path,
    target: Target,
    parsed: ParsedPlugin | None = None,
) -> list[AuditIssue]:
    return [
        issue
        for issue in collect_compiler_audits(plugin_dir, target, parsed=parsed)
        if issue.severity == "block"
    ]
