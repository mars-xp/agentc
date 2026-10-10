from __future__ import annotations

from pathlib import Path
from typing import Sequence

from plugin_compiler.compiled_rules import validate_compiled_rule_bundle
from plugin_compiler.model import CapabilityRef, Target
from plugin_compiler.overrides import OverrideConfig
from plugin_compiler.validation import (
    CommandRunner,
    ValidationIssue,
    ValidationReport,
    _from_compiler_issue,
    structure_issues_for_host,
    validate_skill_discovery,
)
from plugin_targets.cursor.manifest import CURSOR_MANIFEST_FIELDS, CURSOR_MANIFEST_RELATIVE

_CURSOR_COMPONENT_FIELDS = frozenset(
    {"rules", "agents", "skills", "commands", "hooks", "mcpServers"}
)
_PATH_STRING_OR_ARRAY = "path-string-or-array"
_PATH_OR_OBJECT = "path-or-object"
_PATH_OBJECT_OR_ARRAY = "path-object-or-array"
_CURSOR_COMPONENT_SHAPES = {
    "rules": _PATH_STRING_OR_ARRAY,
    "agents": _PATH_STRING_OR_ARRAY,
    "skills": _PATH_STRING_OR_ARRAY,
    "commands": _PATH_STRING_OR_ARRAY,
    "hooks": _PATH_OR_OBJECT,
    "mcpServers": _PATH_OBJECT_OR_ARRAY,
}
_CURSOR_UNSUPPORTED_ROOT = frozenset({".codex-plugin", ".claude-plugin"})


def validate_cursor_structure(plugin_root: Path) -> tuple[ValidationIssue, ...]:
    return structure_issues_for_host(
        plugin_root,
        Target.CURSOR.value,
        CURSOR_MANIFEST_RELATIVE,
        CURSOR_MANIFEST_FIELDS,
        _CURSOR_COMPONENT_FIELDS,
        _CURSOR_COMPONENT_SHAPES,
        _CURSOR_UNSUPPORTED_ROOT,
    )


def validate_cursor_staged(
    plugin_root: Path,
    runner: CommandRunner | None = None,
    plugin_dir: Path | None = None,
    config: OverrideConfig | None = None,
    capabilities: tuple[CapabilityRef, ...] | Sequence[CapabilityRef] | None = None,
) -> ValidationReport:
    del runner  # Cursor has no official CLI validator
    plugin_root = Path(plugin_root)
    issues = list(validate_cursor_structure(plugin_root))
    if plugin_dir is not None and config is not None and capabilities is not None:
        issues.extend(
            validate_skill_discovery(
                plugin_dir,
                Target.CURSOR,
                plugin_root,
                config,
                capabilities,
            )
        )
    issues.extend(
        _from_compiler_issue(issue)
        for issue in validate_compiled_rule_bundle(plugin_root, Target.CURSOR)
    )
    return ValidationReport(
        target=Target.CURSOR.value,
        plugin_root=str(plugin_root),
        issues=tuple(issues),
    )
