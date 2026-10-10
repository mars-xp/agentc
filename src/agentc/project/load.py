"""Read agent-project.yaml and validate agent-project/v1 by hand."""

from __future__ import annotations

import re
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agentc.model.diagnostics import (
    CODE_INVALID_SCHEMA,
    CODE_UNKNOWN_FIELD,
    Diagnostic,
)
from agentc.model.ir import PORTABILITY_POLICIES, CanonicalProject

SCHEMA_VERSION = "agent-project/v1"

TOP_LEVEL_KEYS = frozenset(
    {
        "schemaVersion",
        "project",
        "targets",
        "contents",
        "defaults",
        "requirements",
        "security",
    }
)
PROJECT_KEYS = frozenset({"id", "name", "version", "description", "license"})
TARGET_HOST_KEYS = frozenset({"cursor", "codex", "claude-code"})
TARGET_VALUE_KEYS = frozenset({"enabled"})
CONTENTS_ORDER = ("skills", "rules", "commands", "agents", "hooks", "mcp")
CONTENTS_KEYS = frozenset(CONTENTS_ORDER)
DEFAULTS_KEYS = frozenset({"scopes", "conflictPolicy", "portabilityPolicy"})
REQUIREMENTS_KEYS = frozenset({"executables", "environment"})
ENVIRONMENT_KEYS = frozenset({"optional", "required"})
SECURITY_KEYS = frozenset(
    {"secretPolicy", "allowNetwork", "forbidAbsolutePaths"}
)

_EXECUTABLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_URLISH = re.compile(r"(?i)[a-z][a-z0-9+.-]*://")


def load_project_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise TypeError(
            "agent-project.yaml must be a mapping, got "
            + type(payload).__name__
        )
    return payload


def validate_schema(data: dict) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    if not isinstance(data, dict):
        issues.append(
            _schema_issue(
                "Document must be a mapping.",
                "/",
                "Use an object with schemaVersion and project.",
            )
        )
        return issues

    issues.extend(_unknown_fields(data, TOP_LEVEL_KEYS, ""))

    version = data.get("schemaVersion")
    if version != SCHEMA_VERSION:
        issues.append(
            _schema_issue(
                "schemaVersion must be 'agent-project/v1'.",
                "/schemaVersion",
                "Set schemaVersion to agent-project/v1.",
            )
        )

    project = data.get("project")
    if not isinstance(project, dict):
        issues.append(
            _schema_issue(
                "project must be an object with id, name, and version.",
                "/project",
                "Add a project object with string id, name, and version.",
            )
        )
    else:
        issues.extend(_unknown_fields(project, PROJECT_KEYS, "/project"))
        for field in ("id", "name", "version"):
            value = project.get(field)
            if not isinstance(value, str) or not value:
                issues.append(
                    _schema_issue(
                        f"project.{field} must be a non-empty string.",
                        f"/project/{field}",
                        f"Set project.{field} to a non-empty string.",
                    )
                )

    targets = data.get("targets")
    if targets is not None:
        issues.extend(_validate_targets(targets))

    contents = data.get("contents")
    if contents is not None:
        issues.extend(_validate_mapping_object(contents, CONTENTS_KEYS, "/contents"))
        if isinstance(contents, dict):
            for list_key in CONTENTS_ORDER:
                if list_key in contents:
                    issues.extend(
                        _validate_string_list(
                            contents.get(list_key),
                            f"/contents/{list_key}",
                            names_only=False,
                        )
                    )

    defaults = data.get("defaults")
    if defaults is not None:
        issues.extend(_validate_mapping_object(defaults, DEFAULTS_KEYS, "/defaults"))

    requirements = data.get("requirements")
    if requirements is not None:
        issues.extend(_validate_requirements(requirements))

    security = data.get("security")
    if security is not None:
        issues.extend(_validate_mapping_object(security, SECURITY_KEYS, "/security"))

    return issues


def to_ir(data: dict) -> CanonicalProject:
    """Build a frozen CanonicalProject. Does not mutate ``data``."""
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError("project must be an object with id, name, and version.")
    project_id = project.get("id")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project.id must be a non-empty string.")
    if not isinstance(name, str) or not name:
        raise ValueError("project.name must be a non-empty string.")
    if not isinstance(version, str) or not version:
        raise ValueError("project.version must be a non-empty string.")

    return CanonicalProject(
        project_id=project_id,
        name=name,
        version=version,
        targets=_ir_targets(data.get("targets")),
        skill_paths=_ir_skill_paths(data.get("contents")),
        rule_paths=_ir_rule_paths(data.get("contents")),
        command_paths=_ir_command_paths(data.get("contents")),
        agent_paths=_ir_agent_paths(data.get("contents")),
        content_paths=_ir_content_paths(data.get("contents")),
        portability_policy=_ir_portability_policy(data.get("defaults")),
        scopes=_ir_scopes(data.get("defaults")),
        requirement_names=_ir_requirement_names(data.get("requirements")),
        forbid_absolute_paths=_ir_forbid_absolute_paths(data.get("security")),
    )


def _ir_targets(targets: Any) -> MappingProxyType[str, bool]:
    mapping: dict[str, bool] = {}
    if not isinstance(targets, dict):
        return MappingProxyType(mapping)
    for host, spec in targets.items():
        if not isinstance(host, str) or host.startswith("x-"):
            continue
        if isinstance(spec, dict) and isinstance(spec.get("enabled"), bool):
            mapping[host] = spec["enabled"]
    return MappingProxyType(mapping)


def _ir_skill_paths(contents: Any) -> tuple[str, ...]:
    if not isinstance(contents, dict):
        return ()
    return _ir_path_list(contents.get("skills"))


def _ir_rule_paths(contents: Any) -> tuple[str, ...]:
    if not isinstance(contents, dict):
        return ()
    return _ir_path_list(contents.get("rules"))


def _ir_command_paths(contents: Any) -> tuple[str, ...]:
    if not isinstance(contents, dict):
        return ()
    return _ir_path_list(contents.get("commands"))


def _ir_agent_paths(contents: Any) -> tuple[str, ...]:
    if not isinstance(contents, dict):
        return ()
    return _ir_path_list(contents.get("agents"))


def _ir_content_paths(contents: Any) -> tuple[str, ...]:
    """Every path listed under contents, in schema order, without duplicates."""
    if not isinstance(contents, dict):
        return ()
    paths: list[str] = []
    seen: set[str] = set()
    for key in CONTENTS_ORDER:
        for relposix in _ir_path_list(contents.get(key)):
            if relposix in seen:
                continue
            seen.add(relposix)
            paths.append(relposix)
    return tuple(paths)


def _ir_path_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        Path(item).as_posix() for item in value if isinstance(item, str) and item
    )


def _ir_portability_policy(defaults: Any) -> str:
    if not isinstance(defaults, dict) or "portabilityPolicy" not in defaults:
        return "strict"
    policy = defaults.get("portabilityPolicy")
    if policy not in PORTABILITY_POLICIES:
        raise ValueError(
            "defaults.portabilityPolicy must be strict, compatible, or permissive."
        )
    return str(policy)


def _ir_scopes(defaults: Any) -> tuple[str, ...]:
    if not isinstance(defaults, dict):
        return ()
    scopes = defaults.get("scopes")
    if not isinstance(scopes, list):
        return ()
    return tuple(item for item in scopes if isinstance(item, str) and item)


def _ir_requirement_names(requirements: Any) -> tuple[str, ...]:
    if not isinstance(requirements, dict):
        return ()
    names: list[str] = []
    executables = requirements.get("executables")
    if isinstance(executables, list):
        names.extend(_name_only_items(executables, kind="executable"))
    environment = requirements.get("environment")
    if isinstance(environment, dict):
        for field in ("optional", "required"):
            values = environment.get(field)
            if isinstance(values, list):
                names.extend(_name_only_items(values, kind="environment"))
    return tuple(names)


def _name_only_items(values: list, *, kind: str) -> list[str]:
    names: list[str] = []
    for item in values:
        if isinstance(item, str) and item and not _is_credential_shaped(item, kind=kind):
            names.append(item)
    return names


def _ir_forbid_absolute_paths(security: Any) -> bool:
    if not isinstance(security, dict) or "forbidAbsolutePaths" not in security:
        return True
    flag = security.get("forbidAbsolutePaths")
    return flag if isinstance(flag, bool) else True


def _validate_targets(targets: Any) -> list[Diagnostic]:
    issues = _validate_mapping_object(targets, TARGET_HOST_KEYS, "/targets")
    if not isinstance(targets, dict):
        return issues
    for host, spec in targets.items():
        if not isinstance(host, str) or host.startswith("x-"):
            continue
        loc = f"/targets/{host}"
        if not isinstance(spec, dict):
            issues.append(
                _schema_issue(
                    f"targets.{host} must be an object with enabled: bool.",
                    loc,
                    "Use {enabled: true} or {enabled: false}.",
                )
            )
            continue
        issues.extend(_unknown_fields(spec, TARGET_VALUE_KEYS, loc))
        enabled = spec.get("enabled")
        if not isinstance(enabled, bool):
            issues.append(
                _schema_issue(
                    f"targets.{host}.enabled must be a boolean.",
                    f"{loc}/enabled",
                    "Set enabled to true or false.",
                )
            )
    return issues


def _validate_requirements(requirements: Any) -> list[Diagnostic]:
    issues = _validate_mapping_object(
        requirements, REQUIREMENTS_KEYS, "/requirements"
    )
    if not isinstance(requirements, dict):
        return issues

    if "executables" in requirements:
        issues.extend(
            _validate_string_list(
                requirements.get("executables"),
                "/requirements/executables",
                names_only=True,
                kind="executable",
            )
        )

    environment = requirements.get("environment")
    if environment is not None:
        issues.extend(
            _validate_mapping_object(
                environment, ENVIRONMENT_KEYS, "/requirements/environment"
            )
        )
        if isinstance(environment, dict):
            for field in ("optional", "required"):
                if field not in environment:
                    continue
                issues.extend(
                    _validate_string_list(
                        environment.get(field),
                        f"/requirements/environment/{field}",
                        names_only=True,
                        kind="environment",
                    )
                )
    return issues


def _validate_mapping_object(
    value: Any, allowed: frozenset[str], loc: str
) -> list[Diagnostic]:
    if not isinstance(value, dict):
        return [
            _schema_issue(
                f"{loc.lstrip('/') or 'value'} must be an object.",
                loc or "/",
                "Replace this value with a mapping of allowed keys.",
            )
        ]
    return _unknown_fields(value, allowed, loc)


def _unknown_fields(
    obj: dict, allowed: frozenset[str], loc: str
) -> list[Diagnostic]:
    issues: list[Diagnostic] = []
    for key in obj:
        if not isinstance(key, str):
            issues.append(
                _schema_issue(
                    "Object keys must be strings.",
                    loc or "/",
                    "Use string field names.",
                )
            )
            continue
        if _is_extension_key(key):
            continue
        if key not in allowed:
            path = f"{loc}/{key}" if loc else f"/{key}"
            issues.append(
                Diagnostic(
                    code=CODE_UNKNOWN_FIELD,
                    level="error",
                    message=f"Unknown field {key!r}.",
                    path=path,
                    hint=(
                        "Remove the field, or prefix an experimental "
                        "extension with x-."
                    ),
                )
            )
    return issues


def _validate_string_list(
    value: Any,
    loc: str,
    *,
    names_only: bool,
    kind: str = "item",
) -> list[Diagnostic]:
    if not isinstance(value, list):
        return [
            _schema_issue(
                f"{loc.lstrip('/')} must be a list of strings.",
                loc,
                "Use a YAML list of names only.",
            )
        ]
    issues: list[Diagnostic] = []
    for index, item in enumerate(value):
        item_loc = f"{loc}/{index}"
        if not isinstance(item, str) or not item:
            issues.append(
                _schema_issue(
                    "List entries must be non-empty strings.",
                    item_loc,
                    "Use names only, not objects or empty values.",
                )
            )
            continue
        if names_only and _is_credential_shaped(item, kind=kind):
            issues.append(
                _schema_issue(
                    "Requirements may only list names, not values or credentials.",
                    item_loc,
                    "Declare a name such as git or GITHUB_TOKEN; do not embed '=', URLs, or paths.",
                )
            )
    return issues


def _is_credential_shaped(value: str, *, kind: str) -> bool:
    if "=" in value or _URLISH.search(value):
        return True
    if kind == "executable":
        return _EXECUTABLE_NAME.fullmatch(value) is None
    return _ENV_NAME.fullmatch(value) is None


def _is_extension_key(key: str) -> bool:
    return key.startswith("x-")


def _schema_issue(message: str, path: str, hint: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_INVALID_SCHEMA,
        level="error",
        message=message,
        path=path,
        hint=hint,
    )
