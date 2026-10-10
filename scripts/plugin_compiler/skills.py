from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugin_targets.source_paths import DeclaredPathError, assert_skill_md_contained

from .contracts import skill_frontmatter_fields
from .model import CompilerIssue, Target

_EXTRA_FIELD = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")

ExtraFieldValue = str | list | dict | bool | int | None


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _require_yaml():
    try:
        import yaml
    except ImportError as exc:
        error = RuntimeError(
            "PyYAML is required; python3 -m pip install -r "
            "requirements/plugin-validator.txt"
        )
        error.compiler_code = "compiler-dependency"
        raise error from exc
    return yaml


def _load_frontmatter_mapping(block: str, source_path: str) -> dict:
    yaml = _require_yaml()
    try:
        payload = yaml.safe_load(block) if block.strip() else {}
    except Exception as exc:
        error = RuntimeError(
            "{0}: invalid YAML frontmatter".format(source_path)
        )
        error.compiler_code = "invalid-frontmatter-yaml"
        raise error from exc
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        error = RuntimeError(
            "{0}: invalid YAML frontmatter".format(source_path)
        )
        error.compiler_code = "invalid-frontmatter-yaml"
        raise error
    return payload


def _scalar_field(value: object) -> str:
    if isinstance(value, str):
        return _strip_quotes(value)
    if value is None:
        return ""
    return str(value)


@dataclass(frozen=True)
class SkillDocument:
    name: str
    description: str
    extra_fields: tuple[tuple[str, ExtraFieldValue], ...]
    body: str
    source_path: str
    source_dialect: str | None = None
    frontmatter_text: str = ""


def _parse_frontmatter_fields(
    block: str,
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    name = ""
    description = ""
    extra: list[tuple[str, str]] = []
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("name:"):
            name = _strip_quotes(line[5:].strip())
            index += 1
            continue
        if line.startswith("description:"):
            rest = line[12:].strip()
            if rest in (">-", ">", "|"):
                parts: list[str] = []
                index += 1
                while index < len(lines) and (
                    not lines[index] or lines[index][0] in " \t"
                ):
                    stripped = lines[index].strip()
                    if rest == "|":
                        parts.append(stripped)
                    elif stripped:
                        parts.append(stripped)
                    index += 1
                description = (
                    " ".join(parts) if rest in (">-", ">") else "\n".join(parts)
                )
                continue
            if rest:
                description = _strip_quotes(rest)
            index += 1
            continue
        match = _EXTRA_FIELD.match(line)
        if match:
            extra.append((match.group(1), match.group(2).strip()))
        index += 1
    return name, description, tuple(extra)


def document_frontmatter_fields(
    block: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    name, description, extra = _parse_frontmatter_fields(block)
    fields: list[tuple[str, str]] = []
    if name:
        fields.append(("name", name))
    if description:
        fields.append(("description", description))
    fields.extend(extra)
    return name, tuple(fields)


def parse_skill_markdown(
    text: str, source_path: str, source_dialect: str | None = None
) -> SkillDocument:
    if not text.startswith("---"):
        raise RuntimeError("{0}: missing YAML frontmatter".format(source_path))
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
    name = _scalar_field(payload.get("name"))
    description = _scalar_field(payload.get("description"))
    if not name:
        raise RuntimeError(
            "{0}: empty or missing name in frontmatter".format(source_path)
        )
    if not description:
        raise RuntimeError(
            "{0}: empty or missing description in frontmatter".format(source_path)
        )
    extra_fields: list[tuple[str, ExtraFieldValue]] = []
    for key, value in payload.items():
        if key in ("name", "description"):
            continue
        extra_fields.append((key, value))
    return SkillDocument(
        name=name,
        description=description,
        extra_fields=tuple(extra_fields),
        body=body,
        source_path=source_path,
        source_dialect=source_dialect,
        frontmatter_text=frontmatter_text,
    )


def render_skill_markdown(
    document: SkillDocument, target: Target
) -> tuple[str, tuple[CompilerIssue, ...]]:
    if document.frontmatter_text:
        preserve = document.source_dialect == target.value
        if not preserve and document.source_dialect == "agent-project":
            allowed = skill_frontmatter_fields(target.value)
            preserve = all(key in allowed for key, _ in document.extra_fields)
        if preserve:
            header = document.frontmatter_text
            if not header.endswith("\n"):
                header += "\n"
            return header + document.body, ()

    allowed = skill_frontmatter_fields(target.value)
    payload: dict[str, Any] = {}
    if document.name:
        payload["name"] = document.name
    if document.description:
        payload["description"] = document.description
    issues: list[CompilerIssue] = []
    for key, value in document.extra_fields:
        if key in allowed:
            payload[key] = value
            continue
        issues.append(
            CompilerIssue(
                severity="warn",
                code="dropped-frontmatter-field",
                path=document.source_path,
                message="dropped frontmatter field {0!r} for target {1}".format(
                    key, target.value
                ),
            )
        )
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
    return text, tuple(issues)


def _source_path_for(skill_md: Path, plugin_dir: Path) -> str:
    try:
        return skill_md.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(skill_md).replace("\\", "/")).as_posix()


def load_skill_document(
    skill_md: Path, plugin_dir: Path, source_dialect: str | None = None
) -> SkillDocument:
    try:
        assert_skill_md_contained(skill_md, plugin_dir)
    except DeclaredPathError as exc:
        raise RuntimeError(exc.message) from exc
    text = skill_md.read_text(encoding="utf-8")
    return parse_skill_markdown(
        text, _source_path_for(skill_md, plugin_dir), source_dialect=source_dialect
    )
