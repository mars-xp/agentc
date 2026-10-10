from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from plugin_compiler.compiled_rules import validate_compiled_rule_bundle
from plugin_compiler.model import CapabilityRef, Target
from plugin_compiler.overrides import OverrideConfig
from plugin_compiler.validation import (
    CommandRunner,
    ValidationIssue,
    ValidationReport,
    _command_diagnostic,
    _default_runner,
    _from_compiler_issue,
    _issue,
    structure_issues_for_host,
    validate_skill_discovery,
)
from plugin_targets.claude_code.manifest import (
    CLAUDE_MANIFEST_FIELDS,
    CLAUDE_MANIFEST_RELATIVE,
)

_CLAUDE_COMPONENT_FIELDS = frozenset(
    {
        "skills",
        "commands",
        "agents",
        "workflows",
        "hooks",
        "mcpServers",
        "lspServers",
        "outputStyles",
        "experimental",
    }
)
_PATH_STRING_OR_ARRAY = "path-string-or-array"
_PATH_OBJECT_OR_ARRAY = "path-object-or-array"
_CLAUDE_COMPONENT_SHAPES = {
    "skills": _PATH_STRING_OR_ARRAY,
    "commands": _PATH_STRING_OR_ARRAY,
    "agents": _PATH_STRING_OR_ARRAY,
    "workflows": _PATH_STRING_OR_ARRAY,
    "outputStyles": _PATH_STRING_OR_ARRAY,
    "hooks": _PATH_OBJECT_OR_ARRAY,
    "mcpServers": _PATH_OBJECT_OR_ARRAY,
    "lspServers": _PATH_OBJECT_OR_ARRAY,
}
_CLAUDE_UNSUPPORTED_ROOT = frozenset(
    {".cursor-plugin", ".codex-plugin", "CLAUDE.md"}
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def validate_claude_structure(plugin_root: Path) -> tuple[ValidationIssue, ...]:
    return structure_issues_for_host(
        plugin_root,
        Target.CLAUDE_CODE.value,
        CLAUDE_MANIFEST_RELATIVE,
        CLAUDE_MANIFEST_FIELDS,
        _CLAUDE_COMPONENT_FIELDS,
        _CLAUDE_COMPONENT_SHAPES,
        _CLAUDE_UNSUPPORTED_ROOT,
    )


def run_claude_validator(
    plugin_root: Path, runner: CommandRunner
) -> tuple[ValidationIssue, ...]:
    command = ("claude", "plugin", "validate", str(plugin_root))
    try:
        result = runner(command)
    except FileNotFoundError as exc:
        return (
            _issue(
                "validator-unavailable",
                "claude",
                "Claude Code executable was not found: {0}".format(exc),
            ),
        )
    except Exception as exc:
        return (
            _issue(
                "validator-invocation",
                "claude",
                "Claude validator invocation raised {0}: {1}".format(
                    type(exc).__name__, exc
                ),
            ),
        )
    diagnostic = _command_diagnostic(result)
    if result.returncode != 0:
        return (
            _issue(
                "validator-failed",
                "claude",
                "Claude validator exited with {0}\n{1}".format(
                    result.returncode, diagnostic
                ),
            ),
        )
    output = _ANSI_ESCAPE.sub("", "{0}\n{1}".format(result.stdout, result.stderr))
    if "Validation passed with warnings" in output:
        return (
            _issue(
                "validator-warning",
                "claude",
                "Claude validator passed with warnings\n{0}".format(diagnostic),
                severity="warn",
            ),
        )
    if "Validation passed" not in output:
        return (
            _issue(
                "validator-output",
                "claude",
                "Claude validator output could not be parsed\n{0}".format(diagnostic),
            ),
        )
    return ()


def validate_claude_staged(
    plugin_root: Path,
    runner: CommandRunner | None = None,
    plugin_dir: Path | None = None,
    config: OverrideConfig | None = None,
    capabilities: tuple[CapabilityRef, ...] | Sequence[CapabilityRef] | None = None,
) -> ValidationReport:
    plugin_root = Path(plugin_root)
    issues = list(validate_claude_structure(plugin_root))
    if plugin_dir is not None and config is not None and capabilities is not None:
        issues.extend(
            validate_skill_discovery(
                plugin_dir,
                Target.CLAUDE_CODE,
                plugin_root,
                config,
                capabilities,
            )
        )
    issues.extend(
        _from_compiler_issue(issue)
        for issue in validate_compiled_rule_bundle(plugin_root, Target.CLAUDE_CODE)
    )
    command_runner = runner or _default_runner
    issues.extend(run_claude_validator(plugin_root, command_runner))
    return ValidationReport(
        target=Target.CLAUDE_CODE.value,
        plugin_root=str(plugin_root),
        issues=tuple(issues),
    )
