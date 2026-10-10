from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, Sequence

from .model import CapabilityKind, CapabilityRef, CompilerIssue, Target
from .overrides import OverrideConfig, resolved_targets
from .policy import is_blocked_sensitive_path
from .skills import parse_skill_markdown


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> CommandResult:
        ...


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    target: str
    plugin_root: str
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(item.severity == "block" for item in self.issues)


_PATH_STRING = "path-string"
_PATH_STRING_OR_ARRAY = "path-string-or-array"
_PATH_OR_OBJECT = "path-or-object"
_PATH_OBJECT_OR_ARRAY = "path-object-or-array"


def _issue(code: str, path: str, message: str, severity: str = "block") -> ValidationIssue:
    return ValidationIssue(severity=severity, code=code, path=path, message=message)


def _default_runner(command: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _read_json_object(
    path: Path,
    *,
    missing_code: str,
    json_code: str,
    root_code: str,
    label: str,
) -> tuple[dict | None, tuple[ValidationIssue, ...]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, (_issue(missing_code, path.as_posix(), "missing {0}".format(label)),)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, (
            _issue(
                json_code,
                path.as_posix(),
                "{0} is not readable JSON: {1}".format(label, exc),
            ),
        )
    if not isinstance(raw, dict):
        return None, (
            _issue(root_code, path.as_posix(), "{0} root must be an object".format(label)),
        )
    return raw, ()


def _relative_display(path: Path, plugin_root: Path) -> str:
    try:
        return path.relative_to(plugin_root).as_posix()
    except ValueError:
        return str(path)


def _tree_issues(
    plugin_root: Path, host: str, unsupported_root_components: frozenset[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if plugin_root.is_symlink():
        return [
            _issue(
                "symlink-component",
                str(plugin_root),
                "staged plugin root must not be a symbolic link",
            )
        ]
    if not plugin_root.is_dir():
        return [
            _issue(
                "missing-plugin-root",
                str(plugin_root),
                "staged plugin root is not a directory",
            )
        ]

    for current_root, dirnames, filenames in os.walk(plugin_root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        current = Path(current_root)
        entries = [current / name for name in dirnames + filenames]
        for path in entries:
            relative = _relative_display(path, plugin_root)
            if path.is_symlink():
                issues.append(
                    _issue(
                        "symlink-component",
                        relative,
                        "staged plugin must not contain symbolic links",
                    )
                )
            if is_blocked_sensitive_path(path):
                issues.append(
                    _issue(
                        "credential-file",
                        relative,
                        "staged plugin contains a credential-like file",
                    )
                )

    if (plugin_root / "packaging").exists():
        issues.append(
            _issue(
                "source-packaging",
                "packaging",
                "source packaging configuration must not be shipped",
            )
        )
    for name in sorted(unsupported_root_components):
        if (plugin_root / name).exists():
            issues.append(
                _issue(
                    "unsupported-component",
                    name,
                    "{0} target must not contain {1}".format(host, name),
                )
            )
    return issues


def _path_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _component_path_values(
    field: str, value: object, shapes: dict[str, str]
) -> tuple[list[str], bool]:
    if field == "experimental":
        if not isinstance(value, dict):
            return [], False
        paths: list[str] = []
        valid = True
        for nested_field in ("themes", "monitors"):
            if nested_field not in value:
                continue
            nested_value = value[nested_field]
            paths.extend(_path_strings(nested_value))
            valid = valid and (
                isinstance(nested_value, str)
                or (
                    isinstance(nested_value, list)
                    and all(isinstance(item, str) for item in nested_value)
                )
            )
        return paths, valid
    shape = shapes[field]
    if shape == _PATH_STRING:
        return _path_strings(value), isinstance(value, str)
    if shape == _PATH_STRING_OR_ARRAY:
        return _path_strings(value), (
            isinstance(value, str)
            or (
                isinstance(value, list)
                and all(isinstance(item, str) for item in value)
            )
        )
    if shape == _PATH_OR_OBJECT:
        return _path_strings(value), isinstance(value, (str, dict))
    if shape == _PATH_OBJECT_OR_ARRAY:
        return _path_strings(value), (
            isinstance(value, (str, dict))
            or (
                isinstance(value, list)
                and all(isinstance(item, (str, dict)) for item in value)
            )
        )
    raise AssertionError("unsupported component shape: {0}".format(shape))


def _unsafe_relative_path(raw_path: str) -> tuple[PurePosixPath | None, str | None]:
    if not raw_path.strip():
        return None, "path must be a non-empty string"
    normalized = raw_path.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or PureWindowsPath(raw_path).is_absolute():
        return None, "absolute component paths are not allowed"
    if ".." in candidate.parts:
        return None, "parent path segments are not allowed"
    parts = tuple(part for part in candidate.parts if part not in ("", "."))
    if not parts:
        return None, "component path must name a file or directory"
    return PurePosixPath(*parts), None


def _validate_component_path(
    plugin_root: Path, field: str, raw_path: str
) -> list[ValidationIssue]:
    candidate, error = _unsafe_relative_path(raw_path)
    if error is not None or candidate is None:
        return [
            _issue(
                "unsafe-component-path",
                field,
                "{0}: {1!r}".format(error, raw_path),
            )
        ]

    current = plugin_root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            return [
                _issue(
                    "symlink-component",
                    candidate.as_posix(),
                    "declared component path contains a symbolic link",
                )
            ]

    root_resolved = plugin_root.resolve()
    resolved = (plugin_root / candidate.as_posix()).resolve()
    if not resolved.is_relative_to(root_resolved):
        return [
            _issue(
                "unsafe-component-path",
                candidate.as_posix(),
                "declared component resolves outside the staged plugin",
            )
        ]
    if not resolved.exists():
        return [
            _issue(
                "missing-component",
                candidate.as_posix(),
                "manifest field {0} declares a missing component".format(field),
            )
        ]
    return []


def structure_issues_for_host(
    plugin_root: Path,
    host: str,
    manifest_relative: Path,
    manifest_fields: frozenset[str],
    component_fields: frozenset[str],
    component_shapes: dict[str, str],
    unsupported_root_components: frozenset[str],
) -> tuple[ValidationIssue, ...]:
    if plugin_root.is_symlink():
        return tuple(_tree_issues(plugin_root, host, unsupported_root_components))
    issues = _tree_issues(plugin_root, host, unsupported_root_components)
    manifest_path = plugin_root / manifest_relative
    manifest, read_issues = _read_json_object(
        manifest_path,
        missing_code="missing-manifest",
        json_code="invalid-manifest-json",
        root_code="invalid-manifest-root",
        label="{0} plugin manifest".format(host),
    )
    issues.extend(read_issues)
    if manifest is not None:
        unknown = sorted(set(manifest).difference(manifest_fields))
        for field in unknown:
            issues.append(
                _issue(
                    "unknown-manifest-field",
                    manifest_relative.as_posix(),
                    "{0} manifest field is not allowlisted: {1}".format(
                        host, field
                    ),
                )
            )
        name = manifest.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.append(
                _issue(
                    "invalid-manifest-name",
                    manifest_relative.as_posix(),
                    "manifest name must be a non-empty string",
                )
            )
        for field in sorted(component_fields.intersection(manifest)):
            paths, valid_shape = _component_path_values(
                field, manifest[field], component_shapes
            )
            if not valid_shape:
                issues.append(
                    _issue(
                        "invalid-component-shape",
                        manifest_relative.as_posix(),
                        "manifest field {0} has an unsupported path shape".format(field),
                    )
                )
            for raw_path in paths:
                issues.extend(_validate_component_path(plugin_root, field, raw_path))

    runtime_path = plugin_root / "runtime-host.json"
    runtime, runtime_issues = _read_json_object(
        runtime_path,
        missing_code="missing-runtime-host",
        json_code="invalid-runtime-host-json",
        root_code="invalid-runtime-host-root",
        label="runtime-host.json",
    )
    issues.extend(runtime_issues)
    if runtime is not None and runtime != {"host": host}:
        issues.append(
            _issue(
                "runtime-host-mismatch",
                "runtime-host.json",
                "runtime host must be exactly {0!r}".format(host),
            )
        )
    return tuple(issues)


def _is_structure_only_runner(runner: CommandRunner) -> bool:
    return getattr(runner, "structure_only", False) is True


def _command_diagnostic(result: CommandResult) -> str:
    return "stdout:\n{0}\nstderr:\n{1}".format(result.stdout, result.stderr)


def discover_staged_skills(staged_root: Path) -> tuple[str, ...]:
    skills_root = Path(staged_root) / "skills"
    if not skills_root.is_dir():
        return ()
    names = [
        child.name
        for child in skills_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    return tuple(sorted(names))


def _skill_dir_name(capability: CapabilityRef) -> str:
    source = capability.source_path.replace("\\", "/").rstrip("/")
    return source.rsplit("/", 1)[-1]


def validate_skill_discovery(
    plugin_dir: Path,
    target: Target,
    staged_root: Path,
    config: OverrideConfig,
    capabilities: tuple[CapabilityRef, ...] | Sequence[CapabilityRef],
) -> tuple[ValidationIssue, ...]:
    # plugin_dir is the source plugin; discovery reads staged_root only.
    discovered = set(discover_staged_skills(staged_root))
    issues: list[ValidationIssue] = []
    for capability in capabilities:
        if capability.kind is not CapabilityKind.SKILL:
            continue
        name = _skill_dir_name(capability)
        found = name in discovered
        if target not in resolved_targets(config, capability):
            if found:
                issues.append(
                    _issue(
                        "unexpected-skill-artifact",
                        capability.source_path,
                        "{0} target has unexpected skill artifact for {1}".format(
                            target.value, capability.capability_id
                        ),
                    )
                )
            continue
        skill_md = Path(staged_root) / "skills" / name / "SKILL.md"
        relative = "{0}/SKILL.md".format(capability.source_path)
        if not found:
            issues.append(
                _issue(
                    "skill-not-discoverable",
                    capability.source_path,
                    "{0} target cannot discover skill {1}".format(
                        target.value, capability.capability_id
                    ),
                )
            )
            continue
        try:
            document = parse_skill_markdown(
                skill_md.read_text(encoding="utf-8"),
                relative,
            )
        except (OSError, UnicodeError, RuntimeError) as exc:
            issues.append(
                _issue(
                    "skill-not-discoverable",
                    capability.source_path,
                    "{0} target cannot parse skill {1}: {2}".format(
                        target.value, capability.capability_id, exc
                    ),
                )
            )
            continue
        if not document.name.strip() or not document.description.strip():
            issues.append(
                _issue(
                    "skill-not-discoverable",
                    capability.source_path,
                    "{0} target skill {1} needs a non-empty name and description".format(
                        target.value, capability.capability_id
                    ),
                )
            )
    return tuple(issues)


def _from_compiler_issue(issue: CompilerIssue) -> ValidationIssue:
    return ValidationIssue(
        severity=issue.severity,
        code=issue.code,
        path=issue.path,
        message=issue.message,
    )
