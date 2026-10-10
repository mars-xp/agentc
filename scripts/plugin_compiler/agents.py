from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commands import assert_codex_skill_name
from .model import CompilerIssue, Target
from .skills import (
    SkillDocument,
    _load_frontmatter_mapping,
    _require_yaml,
    _scalar_field,
    render_skill_markdown,
)

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

AgentFieldValue = str | list | dict | bool | int | None


@dataclass(frozen=True)
class AgentDocument:
    capability_id: str
    source_path: str
    name: str
    body: str
    frontmatter_fields: tuple[tuple[str, AgentFieldValue], ...]
    convertible_to_skill: bool
    source_dialect: str | None = None
    frontmatter_text: str = ""


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
) -> tuple[str, str, tuple[tuple[str, AgentFieldValue], ...]]:
    if not text.startswith("---"):
        return "", text, ()
    end = text.find("\n---", 3)
    if end == -1:
        raise RuntimeError("{0}: incomplete YAML frontmatter".format(source_path))
    close_end = end + 4
    if close_end < len(text) and text[close_end] == "\n":
        close_end += 1
    frontmatter_text = text[:close_end]
    block = text[3:end].strip("\n")
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    payload = _load_frontmatter_mapping(block, source_path)
    fields = tuple((key, value) for key, value in payload.items())
    return frontmatter_text, body, fields


def _convertible_to_skill(
    fields: tuple[tuple[str, AgentFieldValue], ...],
) -> bool:
    keys = {key for key, _ in fields}
    if keys & _ISOLATION_KEYS:
        return False
    return keys <= _SKILL_FRONTMATTER_KEYS


def parse_agent_markdown(
    text: str, source_path: str, source_dialect: str | None = None
) -> AgentDocument:
    frontmatter_text, body, fields = _split_optional_frontmatter(text, source_path)
    name = _scalar_field(dict(fields).get("name")) or _name_from_source_path(
        source_path
    )
    return AgentDocument(
        capability_id="agent:{0}".format(source_path),
        source_path=source_path,
        name=name,
        body=body,
        frontmatter_fields=fields,
        convertible_to_skill=_convertible_to_skill(fields),
        source_dialect=source_dialect,
        frontmatter_text=frontmatter_text,
    )


def _source_path_for(path: Path, plugin_dir: Path) -> str:
    try:
        return path.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(path).replace("\\", "/")).as_posix()


def load_agent_document(
    path: Path, plugin_dir: Path, source_dialect: str | None = None
) -> AgentDocument:
    text = path.read_text(encoding="utf-8")
    return parse_agent_markdown(
        text, _source_path_for(path, plugin_dir), source_dialect=source_dialect
    )


def agent_native_relative(document: AgentDocument) -> str:
    return document.source_path.replace("\\", "/")


def agent_conversion_issue(
    document: AgentDocument, target: Target
) -> CompilerIssue | None:
    if target is not Target.CODEX:
        return None
    if document.convertible_to_skill:
        return None
    return CompilerIssue(
        severity="block",
        code="undeclared-target-unsupported",
        path=document.source_path,
        message=(
            "agent {0!r} is not convertible to a skill on {1}; "
            "{1} compilation fails; fix via the authoring/repair program; "
            "to declare a host gap use packaging/platform-overrides.json "
            "with a human-confirmed no-equivalent reason"
        ).format(document.name, target.value),
    )


def agent_skill_conflict(
    document: AgentDocument, skill_ids: set[str]
) -> CompilerIssue | None:
    if document.name not in skill_ids:
        return None
    return CompilerIssue(
        severity="block",
        code="generated-skill-conflict",
        path=document.source_path,
        message="generated skill name {0!r} conflicts with existing skill".format(
            document.name
        ),
    )


def _description_from_fields(
    fields: tuple[tuple[str, AgentFieldValue], ...],
) -> str:
    for key, value in fields:
        if key == "description":
            return _scalar_field(value)
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


def render_agent_markdown(
    document: AgentDocument, target: Target
) -> tuple[str, tuple[CompilerIssue, ...]]:
    if not document.frontmatter_text:
        return document.body, ()
    if (
        document.source_dialect == target.value
        and document.frontmatter_text
    ):
        header = document.frontmatter_text
        if not header.endswith("\n"):
            header += "\n"
        return header + document.body, ()
    payload: dict[str, Any] = dict(document.frontmatter_fields)
    dumped = _require_yaml().safe_dump(
        payload,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    if dumped is None:
        dumped = ""
    dumped = dumped.lstrip("\n")
    text = "---\n" + dumped
    if not dumped.endswith("\n"):
        text += "\n"
    text += "---\n" + document.body
    return text, ()


# FUTURE: If Codex grows a native agents/ component, delete this conversion
# and stage agents via render_agent_markdown same-host passthrough instead.
def render_agent_as_skill(document: AgentDocument) -> str:
    if not document.convertible_to_skill:
        raise ValueError(
            "{0}: agent is not convertible to a skill".format(document.source_path)
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
