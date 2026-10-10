from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .commands import assert_codex_skill_name
from .model import CompilerIssue, Target
from .skills import SkillDocument, document_frontmatter_fields, render_skill_markdown

_SKILL_FRONTMATTER_KEYS = frozenset({"name", "description"})
_ISOLATION_KEYS = frozenset(
    {
        "tools",
        "disallowedTools",
        "permissions",
        "permission",
        "isolation",
        "model",
        "memory",
        "mcp",
        "schedule",
        "cron",
        "on",
    }
)


@dataclass(frozen=True)
class WorkflowDocument:
    capability_id: str
    source_path: str
    name: str
    body: str
    frontmatter_fields: tuple[tuple[str, str], ...]
    convertible_to_skill: bool


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _name_from_source_path(source_path: str) -> str:
    posix = source_path.replace("\\", "/")
    filename = posix.rsplit("/", 1)[-1]
    if filename.lower().endswith(".md"):
        return filename[:-3]
    return filename


def _split_optional_frontmatter(
    text: str, source_path: str
) -> tuple[str, tuple[tuple[str, str], ...], str]:
    if not text.startswith("---"):
        return "", (), text
    end = text.find("\n---", 3)
    if end == -1:
        raise RuntimeError("{0}: incomplete YAML frontmatter".format(source_path))
    block = text[3:end].strip("\n")
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    name, fields = document_frontmatter_fields(block)
    return name, fields, body


def _convertible_to_skill(fields: tuple[tuple[str, str], ...]) -> bool:
    keys = {key for key, _ in fields}
    if keys & _ISOLATION_KEYS:
        return False
    return keys <= _SKILL_FRONTMATTER_KEYS


def parse_workflow_markdown(text: str, source_path: str) -> WorkflowDocument:
    frontmatter_name, fields, body = _split_optional_frontmatter(text, source_path)
    name = frontmatter_name or _name_from_source_path(source_path)
    return WorkflowDocument(
        capability_id="workflow:{0}".format(source_path),
        source_path=source_path,
        name=name,
        body=body,
        frontmatter_fields=fields,
        convertible_to_skill=_convertible_to_skill(fields),
    )


def _source_path_for(path: Path, plugin_dir: Path) -> str:
    try:
        return path.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(path).replace("\\", "/")).as_posix()


def load_workflow_document(path: Path, plugin_dir: Path) -> WorkflowDocument:
    text = path.read_text(encoding="utf-8")
    return parse_workflow_markdown(text, _source_path_for(path, plugin_dir))


def workflow_native_relative(document: WorkflowDocument) -> str:
    return document.source_path.replace("\\", "/")


def workflow_conversion_issue(
    document: WorkflowDocument, target: Target
) -> CompilerIssue | None:
    if target is Target.CLAUDE_CODE:
        return None
    if document.convertible_to_skill:
        return None
    return CompilerIssue(
        severity="block",
        code="undeclared-target-unsupported",
        path=document.source_path,
        message=(
            "workflow {0!r} is not convertible to a skill on {1}; "
            "{1} compilation fails; fix via the authoring/repair program; "
            "to declare a host gap use packaging/platform-overrides.json "
            "with a human-confirmed no-equivalent reason"
        ).format(document.name, target.value),
    )


def _description_from_fields(fields: tuple[tuple[str, str], ...]) -> str:
    for key, value in fields:
        if key == "description":
            return _strip_quotes(value)
    return ""


def _description_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            return heading or fallback
        return stripped
    return fallback


def render_workflow_as_skill(document: WorkflowDocument) -> str:
    if not document.convertible_to_skill:
        raise ValueError(
            "{0}: workflow is not convertible to a skill".format(document.source_path)
        )
    assert_codex_skill_name(document.name)
    description = _description_from_fields(document.frontmatter_fields)
    if not description:
        description = _description_from_body(document.body, document.name)
    skill = SkillDocument(
        name=document.name,
        description=description,
        extra_fields=(),
        body=document.body,
        source_path=document.source_path,
    )
    text, _issues = render_skill_markdown(skill, Target.CODEX)
    return text
