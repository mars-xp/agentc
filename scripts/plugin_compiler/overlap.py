from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from plugin_targets.internal_refs import embedded_plugin_relatives

from .agents import AgentDocument
from .commands import CommandDocument
from .hooks import HookDocument
from .model import CapabilityKind, CapabilityRef, CompilerIssue
from .rules import RuleDocument
from .skills import load_skill_document
from .workflows import WorkflowDocument

_CROSS_FORM = frozenset(
    {
        frozenset({"command", "skill"}),
        frozenset({"agent", "skill"}),
        frozenset({"workflow", "skill"}),
        frozenset({"hook", "rule"}),
    }
)
_SKIP_KINDS = frozenset({"mcp", "theme"})


@dataclass(frozen=True)
class TextUnit:
    kind: str
    path: str
    name: str
    body: str


def _posix_join(plugin_dir: Path, relative: str) -> Path:
    parts = tuple(
        part for part in relative.replace("\\", "/").split("/") if part
    )
    return plugin_dir.joinpath(*parts) if parts else plugin_dir


def _hook_body(plugin_dir: Path, document: HookDocument) -> str:
    parts = [handler.command for handler in document.handlers]
    seen: set[str] = set()
    for handler in document.handlers:
        for relpath in embedded_plugin_relatives(handler.command):
            if relpath in seen:
                continue
            seen.add(relpath)
            path = _posix_join(plugin_dir, relpath)
            try:
                parts.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return "\n".join(parts)


def units_from_documents(
    plugin_dir: Path,
    capabilities: tuple[CapabilityRef, ...],
    commands: tuple[CommandDocument, ...] = (),
    rules: tuple[RuleDocument, ...] = (),
    agents: tuple[AgentDocument, ...] = (),
    workflows: tuple[WorkflowDocument, ...] = (),
    hooks: tuple[HookDocument, ...] = (),
) -> tuple[TextUnit, ...]:
    units: list[TextUnit] = []
    for capability in capabilities:
        if capability.kind is not CapabilityKind.SKILL:
            continue
        skill_md = _posix_join(plugin_dir, capability.source_path) / "SKILL.md"
        try:
            document = load_skill_document(skill_md, plugin_dir)
        except (OSError, RuntimeError, UnicodeDecodeError):
            continue
        units.append(
            TextUnit("skill", document.source_path, document.name, document.body)
        )
    for document in commands:
        units.append(
            TextUnit("command", document.source_path, document.name, document.body)
        )
    for document in rules:
        units.append(
            TextUnit(
                "rule",
                document.source_path,
                document.description,
                document.body,
            )
        )
    for document in agents:
        units.append(
            TextUnit("agent", document.source_path, document.name, document.body)
        )
    for document in workflows:
        units.append(
            TextUnit(
                "workflow",
                document.source_path,
                document.name,
                document.body,
            )
        )
    for document in hooks:
        units.append(
            TextUnit(
                "hook",
                document.source_path,
                document.source_path,
                _hook_body(plugin_dir, document),
            )
        )
    return tuple(units)


def normalize_name(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1]
    return text.casefold()


def normalize_body_lines(text: str) -> tuple[str, ...]:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped.casefold())
    return tuple(lines)


def _append_body_overlap_issues(
    issues: list[CompilerIssue],
    left: TextUnit,
    right: TextUnit,
    *,
    duplicate_code: str,
    line_overlap_code: str,
) -> None:
    if left.path <= right.path:
        smaller, larger = left, right
    else:
        smaller, larger = right, left

    left_lines = normalize_body_lines(left.body)
    right_lines = normalize_body_lines(right.body)
    if left_lines == right_lines:
        issues.append(
            CompilerIssue(
                severity="block",
                code=duplicate_code,
                path=smaller.path,
                message="content duplicates {0}".format(larger.path),
            )
        )
        return

    if len(left_lines) <= len(right_lines):
        shorter_lines, longer_lines = left_lines, right_lines
    else:
        shorter_lines, longer_lines = right_lines, left_lines
    shorter_count = len(shorter_lines)
    if shorter_count >= 5:
        overlap_count = len(set(shorter_lines) & set(longer_lines))
        if overlap_count * 100 >= 80 * shorter_count:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code=line_overlap_code,
                    path=smaller.path,
                    message="lines overlap {0}".format(larger.path),
                )
            )


def find_overlap_issues(units: tuple[TextUnit, ...]) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    for index in range(len(units)):
        for other in range(index + 1, len(units)):
            left = units[index]
            right = units[other]
            if left.kind in _SKIP_KINDS or right.kind in _SKIP_KINDS:
                continue
            if left.path <= right.path:
                smaller, larger = left, right
            else:
                smaller, larger = right, left

            if left.kind == right.kind:
                _append_body_overlap_issues(
                    issues,
                    left,
                    right,
                    duplicate_code="content-duplicate",
                    line_overlap_code="semantic-line-overlap",
                )
                if (
                    left.name.strip()
                    and right.name.strip()
                    and normalize_name(left.name) == normalize_name(right.name)
                ):
                    issues.append(
                        CompilerIssue(
                            severity="block",
                            code="semantic-name-overlap",
                            path=smaller.path,
                            message="name overlaps {0}".format(larger.path),
                        )
                    )
            elif frozenset({left.kind, right.kind}) in _CROSS_FORM:
                _append_body_overlap_issues(
                    issues,
                    left,
                    right,
                    duplicate_code="cross-form-overlap",
                    line_overlap_code="cross-form-overlap",
                )
    return tuple(issues)
