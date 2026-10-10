"""Validate agent-project.yaml and referenced paths without plugin_compiler."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from agentc.model.diagnostics import (
    CODE_INVALID_SCHEMA,
    CODE_INVALID_UTF8,
    CODE_MISSING_PATH,
    CODE_PATH_SECURITY,
    Diagnostic,
)
from agentc.model.ir import PORTABILITY_POLICIES, CanonicalProject
from agentc.overlay.ops import check_overlays
from agentc.project.load import load_project_yaml, to_ir, validate_schema
from agentc.security.home_paths import contains_home_directory_literal
from agentc.security.paths import resolve_in_project

_PROJECT_YAML = "agent-project.yaml"


def validate_project(
    project_root: Path, policy: str | None = None
) -> list[Diagnostic]:
    root = project_root.resolve()
    yaml_path = root / _PROJECT_YAML
    if not yaml_path.is_file():
        return [
            Diagnostic(
                code=CODE_INVALID_SCHEMA,
                level="error",
                message="agent-project.yaml is missing.",
                path=f"/{_PROJECT_YAML}",
                hint="Run agentc init to create a project tree.",
            )
        ]

    try:
        yaml_text = yaml_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [_invalid_utf8_issue(root, yaml_path)]

    try:
        data = load_project_yaml(yaml_path)
    except yaml.YAMLError as exc:
        return [_yaml_syntax_issue(exc)]
    except TypeError as exc:
        return [
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
        return issues

    try:
        ir = to_ir(data)
    except ValueError as exc:
        return [
            Diagnostic(
                code=CODE_INVALID_SCHEMA,
                level="error",
                message=str(exc),
                path="/",
                hint="Fix agent-project.yaml to match the v1 schema.",
            )
        ]

    if policy is not None:
        if policy not in PORTABILITY_POLICIES:
            return [
                Diagnostic(
                    code=CODE_INVALID_SCHEMA,
                    level="error",
                    message=(
                        "portabilityPolicy must be strict, compatible, or permissive."
                    ),
                    path="/defaults/portabilityPolicy",
                    hint="Use --policy strict, compatible, or permissive.",
                )
            ]
        ir = replace(ir, portability_policy=policy)

    return _validate_ir(root, yaml_text, ir)


def _validate_ir(
    root: Path,
    yaml_text: str,
    ir: CanonicalProject,
) -> list[Diagnostic]:
    """Check every contents path; SKILL.md body scan stays skills-only."""
    issues: list[Diagnostic] = []

    if ir.forbid_absolute_paths:
        issues.extend(_home_dir_issues(yaml_text, _PROJECT_YAML))

    skill_paths = frozenset(ir.skill_paths)
    for content_path in ir.content_paths:
        resolved, err = resolve_in_project(root, content_path)
        if err is not None:
            issues.append(err)
            continue
        if resolved is None or not resolved.exists():
            issues.append(_missing_path_issue(content_path))
            continue
        if content_path not in skill_paths:
            continue

        skill_md = resolved / "SKILL.md"
        if skill_md.is_file():
            if ir.forbid_absolute_paths:
                try:
                    skill_text = skill_md.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    issues.append(_invalid_utf8_issue(root, skill_md))
                    continue
                issues.extend(
                    _home_dir_issues(skill_text, _relative_to_root(root, skill_md))
                )

    issues.extend(check_overlays(root))
    return issues


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _home_dir_issues(text: str, relpath: str) -> list[Diagnostic]:
    if not contains_home_directory_literal(text):
        return []
    return [
        Diagnostic(
            code=CODE_PATH_SECURITY,
            level="error",
            message="Text must not contain host home-directory paths.",
            path=relpath,
            hint=(
                "Remove /Users/, /home/, or Windows C:\\Users\\ literals; "
                "use project-relative paths."
            ),
        )
    ]


def _missing_path_issue(relpath: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_MISSING_PATH,
        level="error",
        message=f"Listed path {relpath!r} does not exist.",
        path=relpath,
        hint="Create the path or remove it from contents.",
    )


def _invalid_utf8_issue(root: Path, path: Path) -> Diagnostic:
    return Diagnostic(
        code=CODE_INVALID_UTF8,
        level="error",
        message=f"{path.name} is not valid UTF-8.",
        path=_relative_to_root(root, path),
        hint="Save the file as UTF-8 with LF line endings.",
    )


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
