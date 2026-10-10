from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .model import CompilerIssue, Target


_ROOT_RULE_SPECS = {
    "AGENTS.md": (
        "rule:AGENTS.md",
        "Imported root AGENTS.md rules",
        "rules/imported-root-agents.mdc",
    ),
    "CLAUDE.md": (
        "rule:CLAUDE.md",
        "Imported root CLAUDE.md rules",
        "rules/imported-root-claude.mdc",
    ),
}


@dataclass(frozen=True)
class RuleDocument:
    capability_id: str
    source_path: str
    description: str
    always_apply: bool
    globs: tuple[str, ...]
    body: str


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_rule_frontmatter(block: str) -> tuple[str, bool, tuple[str, ...]]:
    description = ""
    always_apply = False
    globs: list[str] = []
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
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
        if line.startswith("alwaysApply:"):
            always_apply = line[12:].strip().lower() == "true"
            index += 1
            continue
        if line.startswith("globs:"):
            rest = line[6:].strip()
            if rest:
                globs.append(_strip_quotes(rest))
                index += 1
                continue
            index += 1
            while index < len(lines):
                stripped = lines[index].strip()
                if not stripped:
                    index += 1
                    continue
                if stripped.startswith("- "):
                    globs.append(_strip_quotes(stripped[2:].strip()))
                    index += 1
                    continue
                break
            continue
        index += 1
    return description, always_apply, tuple(globs)


def parse_rule_markdown(text: str, source_path: str) -> RuleDocument:
    if not text.startswith("---"):
        raise RuntimeError("{0}: missing YAML frontmatter".format(source_path))
    end = text.find("\n---", 3)
    if end == -1:
        raise RuntimeError("{0}: incomplete YAML frontmatter".format(source_path))
    block = text[3:end].strip("\n")
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    description, always_apply, globs = _parse_rule_frontmatter(block)
    return RuleDocument(
        capability_id="rule:{0}".format(source_path),
        source_path=source_path,
        description=description,
        always_apply=always_apply,
        globs=globs,
        body=body,
    )


def _source_path_for(rule_path: Path, plugin_dir: Path) -> str:
    try:
        return rule_path.resolve().relative_to(plugin_dir.resolve()).as_posix()
    except ValueError:
        return Path(str(rule_path).replace("\\", "/")).as_posix()


def load_rule_document(path: Path, plugin_dir: Path) -> RuleDocument:
    text = path.read_text(encoding="utf-8")
    return parse_rule_markdown(text, _source_path_for(path, plugin_dir))


def is_root_rule_source(source_path: str) -> bool:
    return source_path.replace("\\", "/") in _ROOT_RULE_SPECS


def cursor_output_relative(source_path: str) -> str:
    posix = source_path.replace("\\", "/")
    spec = _ROOT_RULE_SPECS.get(posix)
    if spec is None:
        return posix
    return spec[2]


def cursor_rule_output_path(document: RuleDocument) -> str:
    return cursor_output_relative(document.source_path)


def _body_without_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    return body


def load_root_rule_document(path: Path, plugin_dir: Path) -> RuleDocument:
    source_path = _source_path_for(path, plugin_dir)
    spec = _ROOT_RULE_SPECS.get(source_path.replace("\\", "/"))
    if spec is None:
        raise RuntimeError("{0}: not a mapped root rule".format(source_path))
    capability_id, description, _cursor_output = spec
    text = path.read_text(encoding="utf-8")
    return RuleDocument(
        capability_id=capability_id,
        source_path=source_path,
        description=description,
        always_apply=True,
        globs=(),
        body=_body_without_frontmatter(text),
    )


def _render_description(description: str) -> list[str]:
    if "\n" in description:
        lines = ["description: |"]
        for part in description.split("\n"):
            lines.append("  {0}".format(part))
        return lines
    if len(description) > 80 or ":" in description:
        return ["description: >-", "  {0}".format(description)]
    return ["description: {0}".format(description)]


def _render_globs(globs: tuple[str, ...]) -> list[str]:
    if not globs:
        return []
    if len(globs) == 1:
        return ['globs: "{0}"'.format(globs[0])]
    lines = ["globs:"]
    for item in globs:
        lines.append('  - "{0}"'.format(item))
    return lines


def render_cursor_mdc(document: RuleDocument) -> str:
    lines = ["---"]
    lines.extend(_render_description(document.description))
    lines.append(
        "alwaysApply: {0}".format("true" if document.always_apply else "false")
    )
    lines.extend(_render_globs(document.globs))
    lines.append("---")
    return "\n".join(lines) + "\n" + document.body


def rule_scope_supported(document: RuleDocument, target: Target) -> bool:
    if document.always_apply or document.globs:
        return True
    return target is Target.CURSOR


def rule_conversion_issue(
    document: RuleDocument, target: Target
) -> CompilerIssue | None:
    if rule_scope_supported(document, target):
        return None
    return CompilerIssue(
        severity="block",
        code="rule-scope-not-representable",
        path=document.source_path,
        message=(
            "rule scope without alwaysApply or globs is not representable "
            "on target {0}".format(target.value)
        ),
    )
