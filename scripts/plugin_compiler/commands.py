from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .model import CompilerIssue, Target
from .skills import SkillDocument, render_skill_markdown

_SKILL_REF = re.compile(r"(?<![A-Za-z0-9_])skills/([A-Za-z0-9][A-Za-z0-9._-]*)")
_FRONTMATTER_FIELD = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
_SAFE_COMMAND_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_CODEX_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class CommandDocument:
    capability_id: str
    source_path: str
    name: str
    body: str
    referenced_skill_dir: str | None
    frontmatter_fields: tuple[tuple[str, str], ...] = ()
    frontmatter_text: str = ""


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def is_bin_passthrough_source(source_path: str) -> bool:
    """True when the capability lives under ``bin/`` and must be copied as bytes."""
    parts = [part for part in source_path.replace("\\", "/").split("/") if part]
    return bool(parts) and parts[0] == "bin"


def _name_from_source_path(source_path: str) -> str:
    posix = source_path.replace("\\", "/")
    filename = posix.rsplit("/", 1)[-1]
    if filename.lower().endswith(".md"):
        return filename[:-3]
    return filename


def _split_optional_frontmatter(
    text: str, source_path: str
) -> tuple[str, tuple[tuple[str, str], ...], str, str]:
    if not text.startswith("---"):
        return "", (), text, ""
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
    fields: list[tuple[str, str]] = []
    name = ""
    for line in block.splitlines():
        match = _FRONTMATTER_FIELD.match(line)
        if not match:
            continue
        key = match.group(1)
        value = match.group(2).strip()
        fields.append((key, value))
        if key == "name":
            name = _strip_quotes(value)
    return name, tuple(fields), body, frontmatter_text


def _referenced_skill_dir(text: str, source_path: str) -> str | None:
    found: list[str] = []
    seen: set[str] = set()
    for match in _SKILL_REF.finditer(text):
        dirname = match.group(1)
        if dirname in seen:
            continue
        seen.add(dirname)
        found.append(dirname)
    if not found:
        return None
    if len(found) > 1:
        raise RuntimeError(
            "{0}: multiple skill references: {1}".format(
                source_path, ", ".join(found)
            )
        )
    return found[0]


def assert_safe_command_path_name(name: str) -> None:
    if not name or name in (".", ".."):
        raise ValueError("command name must be a non-empty path component")
    if Path(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise ValueError("command name must not be absolute")
    if PureWindowsPath(name).drive:
        raise ValueError("command name must not be drive-qualified")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("command name must not contain path separators")
    if not _SAFE_COMMAND_NAME.fullmatch(name):
        raise ValueError("command name {0!r} is not a safe path segment".format(name))


def assert_codex_skill_name(name: str) -> None:
    if not _CODEX_SKILL_NAME.fullmatch(name):
        raise ValueError(
            "command name {0!r} is not kebab-case; provide an explicit legal name".format(
                name
            )
        )


def parse_command_markdown(text: str, source_path: str) -> CommandDocument:
    frontmatter_name, fields, body, frontmatter_text = _split_optional_frontmatter(
        text, source_path
    )
    name = frontmatter_name or _name_from_source_path(source_path)
    return CommandDocument(
        capability_id="command:{0}".format(source_path),
        source_path=source_path,
        name=name,
        body=body,
        referenced_skill_dir=_referenced_skill_dir(body, source_path),
        frontmatter_fields=fields,
        frontmatter_text=frontmatter_text,
    )


def _source_path_for(path: Path, plugin_dir: Path) -> str:
    try:
        return path.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(path).replace("\\", "/")).as_posix()


def load_command_document(path: Path, plugin_dir: Path) -> CommandDocument:
    text = path.read_text(encoding="utf-8")
    return parse_command_markdown(text, _source_path_for(path, plugin_dir))


def render_native_command(document: CommandDocument) -> str:
    if not document.frontmatter_text:
        return document.body
    header = document.frontmatter_text
    if not header.endswith("\n"):
        header += "\n"
    return header + document.body


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


def render_codex_skill_from_command(document: CommandDocument) -> str:
    skill = SkillDocument(
        name=document.name,
        description=_description_from_body(document.body, document.name),
        extra_fields=(),
        body=document.body,
        source_path=document.source_path,
    )
    text, _issues = render_skill_markdown(skill, Target.CODEX)
    return text


def command_codex_skill_conflict(
    document: CommandDocument,
    existing_skill_names: set[str],
) -> CompilerIssue | None:
    if document.name not in existing_skill_names:
        return None
    return CompilerIssue(
        severity="block",
        code="command-skill-name-conflict",
        path=document.source_path,
        message="command name {0!r} conflicts with existing skill name".format(
            document.name
        ),
    )
