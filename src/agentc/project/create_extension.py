"""Create portable extension files and register them in agent-project.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from agentc.model.diagnostics import (
    CODE_INVALID_NAME,
    CODE_INVALID_SCHEMA,
    CODE_SKILL_EXISTS,
    Diagnostic,
)
from agentc.project.load import load_project_yaml, validate_schema

_PROJECT_YAML = "agent-project.yaml"
_EXTENSION_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TEMPLATES_ROOT = Path(__file__).resolve().parents[1] / "templates"


@dataclass(frozen=True)
class _ExtensionKind:
    label: str
    contents_key: str
    relative_dir: str
    template_path: Path


_KINDS = {
    "rule": _ExtensionKind(
        label="rule",
        contents_key="rules",
        relative_dir="extensions/rules",
        template_path=_TEMPLATES_ROOT / "rule" / "RULE.md",
    ),
    "command": _ExtensionKind(
        label="command",
        contents_key="commands",
        relative_dir="extensions/commands",
        template_path=_TEMPLATES_ROOT / "command" / "COMMAND.md",
    ),
    "agent": _ExtensionKind(
        label="agent",
        contents_key="agents",
        relative_dir="extensions/agents",
        template_path=_TEMPLATES_ROOT / "agent" / "AGENT.md",
    ),
}


def create_rule(project_root: Path, name: str) -> list[Diagnostic]:
    return _create_extension(project_root, name, _KINDS["rule"])


def create_command(project_root: Path, name: str) -> list[Diagnostic]:
    return _create_extension(project_root, name, _KINDS["command"])


def create_agent(project_root: Path, name: str) -> list[Diagnostic]:
    return _create_extension(project_root, name, _KINDS["agent"])


def _create_extension(
    project_root: Path, name: str, kind: _ExtensionKind
) -> list[Diagnostic]:
    yaml_path = project_root / _PROJECT_YAML
    if not yaml_path.is_file():
        return [
            Diagnostic(
                code=CODE_INVALID_SCHEMA,
                level="error",
                message=(
                    "--project must point at an agent-project/v1 project; "
                    "agent-project.yaml is missing."
                ),
                path=_PROJECT_YAML,
                hint="Run agentc init on that directory first.",
            )
        ]

    if _EXTENSION_NAME.fullmatch(name) is None:
        return [
            Diagnostic(
                code=CODE_INVALID_NAME,
                level="error",
                message=f"{kind.label.title()} name {name!r} is invalid.",
                path=name,
                hint=(
                    "Use lowercase letters, digits, and hyphens only "
                    "(for example: my-extension)."
                ),
            )
        ]

    rel_path = f"{kind.relative_dir}/{name}.md"
    file_path = project_root / rel_path
    if file_path.exists():
        return [
            Diagnostic(
                code=CODE_SKILL_EXISTS,
                level="error",
                message=f"{kind.label.title()} file {rel_path!r} already exists.",
                path=rel_path,
                hint=(
                    f"Choose a new {kind.label} name or remove the existing file."
                ),
            )
        ]

    data, project_issues = _load_valid_project(yaml_path)
    if project_issues:
        return project_issues

    template = kind.template_path.read_text(encoding="utf-8")
    body = template.replace("{{name}}", name)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(file_path, body)

    contents = data.setdefault("contents", {})
    if not isinstance(contents, dict):
        contents = {}
        data["contents"] = contents
    entries = contents.setdefault(kind.contents_key, [])
    if not isinstance(entries, list):
        entries = []
        contents[kind.contents_key] = entries
    entries.append(Path(rel_path).as_posix())
    _write_text(
        yaml_path,
        yaml.safe_dump(
            data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ),
    )

    return []


def _load_valid_project(yaml_path: Path) -> tuple[dict, list[Diagnostic]]:
    try:
        data = load_project_yaml(yaml_path)
    except yaml.YAMLError as exc:
        return {}, [_yaml_syntax_issue(exc)]
    except TypeError as exc:
        return {}, [
            Diagnostic(
                code=CODE_INVALID_SCHEMA,
                level="error",
                message=str(exc),
                path=_PROJECT_YAML,
                hint="Use an object with schemaVersion and project.",
            )
        ]

    issues = validate_schema(data)
    if any(issue.level == "error" for issue in issues):
        return {}, issues
    return data, []


def _yaml_syntax_issue(exc: yaml.YAMLError) -> Diagnostic:
    mark = getattr(exc, "problem_mark", None)
    where = ""
    if mark is not None:
        where = f" (line {mark.line + 1}, column {mark.column + 1})"
    return Diagnostic(
        code=CODE_INVALID_SCHEMA,
        level="error",
        message=f"agent-project.yaml is not valid YAML{where}.",
        path=_PROJECT_YAML,
        hint="Fix the YAML syntax reported at that position.",
    )


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
