"""Create a portable skill directory and register it in agent-project.yaml."""

from __future__ import annotations

import re
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
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1] / "templates" / "skill" / "SKILL.md"
)
_SUBDIRS = ("scripts", "references", "assets")


def create_skill(project_root: Path, name: str) -> list[Diagnostic]:
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

    if _SKILL_NAME.fullmatch(name) is None:
        return [
            Diagnostic(
                code=CODE_INVALID_NAME,
                level="error",
                message=f"Skill name {name!r} is invalid.",
                path=name,
                hint=(
                    "Use lowercase letters, digits, and hyphens only "
                    "(for example: my-skill)."
                ),
            )
        ]

    skill_rel = f"skills/{name}"
    skill_dir = project_root / skill_rel
    if skill_dir.exists():
        return [
            Diagnostic(
                code=CODE_SKILL_EXISTS,
                level="error",
                message=f"Skill directory {skill_rel!r} already exists.",
                path=skill_rel,
                hint="Choose a new skill name or remove the existing directory.",
            )
        ]

    data, project_issues = _load_valid_project(yaml_path)
    if project_issues:
        return project_issues

    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    skill_md = template.replace("{{name}}", name)

    skill_dir.mkdir(parents=True, exist_ok=False)
    for subdir in _SUBDIRS:
        (skill_dir / subdir).mkdir(parents=True, exist_ok=True)
    _write_text(skill_dir / "SKILL.md", skill_md)
    contents = data.setdefault("contents", {})
    if not isinstance(contents, dict):
        contents = {}
        data["contents"] = contents
    skills = contents.setdefault("skills", [])
    if not isinstance(skills, list):
        skills = []
        contents["skills"] = skills
    skills.append(skill_rel)
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
