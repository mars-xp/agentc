from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import CapabilityKind, CapabilityRef, CompilerIssue, Target

OVERRIDE_FILE = "packaging/platform-overrides.json"
SKILLS_JSON = "packaging/skills.json"
DEFAULT_TARGETS: tuple[Target, ...] = (Target.CURSOR, Target.CODEX, Target.CLAUDE_CODE)
_SUPPORTED_SCHEMA_VERSION = 1
_ROOT_FIELDS = frozenset(
    {"schema_version", "default_targets", "overrides", "command_bindings"}
)
_ENTRY_FIELDS = frozenset({"targets", "reason", "capability_id"})
_BINDING_FIELDS = frozenset({"codex_skill_dir", "reason"})

_VALID_HOSTS = {item.value: item for item in Target}


@dataclass(frozen=True)
class OverrideEntry:
    capability_id: str
    targets: tuple[Target, ...]
    reason: str


@dataclass(frozen=True)
class CommandBinding:
    capability_id: str
    codex_skill_dir: str
    reason: str


@dataclass(frozen=True)
class OverrideConfig:
    schema_version: int
    default_targets: tuple[Target, ...]
    overrides: dict[str, OverrideEntry]
    command_bindings: dict[str, CommandBinding] = field(default_factory=dict)


def _plugin_path(plugin_dir: Path, relative: str) -> Path:
    return plugin_dir.joinpath(*relative.split("/"))


def _parse_target(value: object, path: str) -> Target:
    if not isinstance(value, str):
        raise RuntimeError("{0}: target must be a string, got {1!r}".format(path, value))
    try:
        return Target(value)
    except ValueError as exc:
        raise RuntimeError("{0}: unknown target {1!r}".format(path, value)) from exc


def _parse_targets(raw: object, path: str) -> tuple[Target, ...]:
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("{0}: targets must be a non-empty list".format(path))
    parsed = tuple(_parse_target(item, path) for item in raw)
    return parsed


def _normalize_default_targets(raw: object, path: str) -> tuple[Target, ...]:
    parsed = _parse_targets(raw, path)
    if set(parsed) != set(DEFAULT_TARGETS) or len(parsed) != len(DEFAULT_TARGETS):
        raise RuntimeError(
            "{0}: default_targets must be exactly cursor, codex, and claude-code".format(
                path
            )
        )
    return DEFAULT_TARGETS


def _reject_unknown_fields(raw: dict, allowed: frozenset[str], path: str) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise RuntimeError(
            "{0}: unknown fields: {1}".format(path, ", ".join(unknown))
        )


def _parse_override_entry(key: str, raw: object, path: str) -> OverrideEntry:
    if not isinstance(raw, dict):
        raise RuntimeError("{0}: override {1!r} must be an object".format(path, key))
    _reject_unknown_fields(
        raw, _ENTRY_FIELDS, "{0} overrides[{1}]".format(path, key)
    )
    nested_id = raw.get("capability_id")
    if nested_id is not None and nested_id != key:
        raise RuntimeError(
            "{0}: override key {1!r} must equal capability_id {2!r}".format(
                path, key, nested_id
            )
        )
    targets = _parse_targets(raw.get("targets"), "{0} overrides[{1}].targets".format(path, key))
    reason_raw = raw.get("reason", "")
    if not isinstance(reason_raw, str):
        raise RuntimeError(
            "{0}: override {1!r} reason must be a string, got {2!r}".format(
                path, key, reason_raw
            )
        )
    reason = reason_raw.strip()
    if not reason:
        raise RuntimeError(
            "{0}: override {1!r} reason must be non-empty".format(path, key)
        )
    return OverrideEntry(capability_id=key, targets=targets, reason=reason)


def _parse_command_binding(key: str, raw: object, path: str) -> CommandBinding:
    if not isinstance(raw, dict):
        raise RuntimeError(
            "{0}: command binding {1!r} must be an object".format(path, key)
        )
    _reject_unknown_fields(
        raw, _BINDING_FIELDS, "{0} command_bindings[{1}]".format(path, key)
    )
    skill_raw = raw.get("codex_skill_dir")
    if not isinstance(skill_raw, str):
        raise RuntimeError(
            "{0}: command binding {1!r} codex_skill_dir must be a string, got {2!r}".format(
                path, key, skill_raw
            )
        )
    skill_dir = skill_raw.strip()
    if not skill_dir:
        raise RuntimeError(
            "{0}: command binding {1!r} codex_skill_dir must be non-empty".format(
                path, key
            )
        )
    reason_raw = raw.get("reason", "")
    if not isinstance(reason_raw, str):
        raise RuntimeError(
            "{0}: command binding {1!r} reason must be a string, got {2!r}".format(
                path, key, reason_raw
            )
        )
    reason = reason_raw.strip()
    if not reason:
        raise RuntimeError(
            "{0}: command binding {1!r} reason must be non-empty".format(path, key)
        )
    return CommandBinding(
        capability_id=key, codex_skill_dir=skill_dir, reason=reason
    )


def _parse_command_bindings(raw: object, path: str) -> dict[str, CommandBinding]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RuntimeError("{0}: command_bindings must be an object".format(path))
    bindings: dict[str, CommandBinding] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise RuntimeError("{0}: command_bindings keys must be strings".format(path))
        bindings[key] = _parse_command_binding(key, value, path)
    return bindings


def load_override_config(plugin_dir: Path) -> OverrideConfig:
    override_path = _plugin_path(plugin_dir, OVERRIDE_FILE)
    skills_path = _plugin_path(plugin_dir, SKILLS_JSON)
    has_override = override_path.is_file()
    has_skills = skills_path.is_file()
    if has_skills and has_override:
        raise RuntimeError(
            "both {0} and {1} exist; refuse dual source of truth".format(
                SKILLS_JSON, OVERRIDE_FILE
            )
        )
    if has_skills and not has_override:
        raise RuntimeError(
            "{0} exists but {1} is missing; migrate host routing to {1}".format(
                SKILLS_JSON, OVERRIDE_FILE
            )
        )
    if not has_override:
        return OverrideConfig(
            schema_version=1,
            default_targets=DEFAULT_TARGETS,
            overrides={},
            command_bindings={},
        )
    try:
        data = json.loads(override_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("{0}: invalid JSON: {1}".format(OVERRIDE_FILE, exc)) from exc
    if not isinstance(data, dict):
        raise RuntimeError("{0}: root must be an object".format(OVERRIDE_FILE))
    _reject_unknown_fields(data, _ROOT_FIELDS, OVERRIDE_FILE)
    schema_version = data.get("schema_version", _SUPPORTED_SCHEMA_VERSION)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise RuntimeError("{0}: schema_version must be an integer".format(OVERRIDE_FILE))
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        raise RuntimeError(
            "{0}: unsupported schema_version {1}; only {2} is accepted".format(
                OVERRIDE_FILE, schema_version, _SUPPORTED_SCHEMA_VERSION
            )
        )
    default_targets = _normalize_default_targets(
        data.get("default_targets"),
        "{0} default_targets".format(OVERRIDE_FILE),
    )
    raw_overrides = data.get("overrides", {})
    if raw_overrides is None:
        raw_overrides = {}
    if not isinstance(raw_overrides, dict):
        raise RuntimeError("{0}: overrides must be an object".format(OVERRIDE_FILE))
    overrides: dict[str, OverrideEntry] = {}
    for key, value in raw_overrides.items():
        if not isinstance(key, str):
            raise RuntimeError("{0}: override keys must be strings".format(OVERRIDE_FILE))
        overrides[key] = _parse_override_entry(key, value, OVERRIDE_FILE)
    command_bindings = _parse_command_bindings(
        data.get("command_bindings"), OVERRIDE_FILE
    )
    return OverrideConfig(
        schema_version=schema_version,
        default_targets=default_targets,
        overrides=overrides,
        command_bindings=command_bindings,
    )


def _targets_are_valid(targets: tuple[Target, ...]) -> bool:
    if not targets:
        return False
    for item in targets:
        if isinstance(item, Target):
            continue
        try:
            Target(item)
        except (TypeError, ValueError):
            return False
    return True


def validate_overrides(
    config: OverrideConfig,
    capabilities: tuple[CapabilityRef, ...],
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    if set(config.default_targets) != set(DEFAULT_TARGETS):
        issues.append(
            CompilerIssue(
                severity="block",
                code="override-default-targets-must-be-all-three",
                path=OVERRIDE_FILE,
                message="default_targets must be exactly cursor, codex, and claude-code",
            )
        )
    known_ids = {item.capability_id for item in capabilities}
    for capability_id, entry in config.overrides.items():
        if capability_id not in known_ids:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="override-unknown-capability",
                    path=OVERRIDE_FILE,
                    message="override {0} is not in scanned capabilities".format(
                        capability_id
                    ),
                )
            )
        if not entry.reason.strip():
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="override-reason-required",
                    path=OVERRIDE_FILE,
                    message="override {0} needs a non-empty reason".format(
                        capability_id
                    ),
                )
            )
        if not _targets_are_valid(entry.targets):
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="override-targets-invalid",
                    path=OVERRIDE_FILE,
                    message="override {0} needs a non-empty list of valid targets".format(
                        capability_id
                    ),
                )
            )
        elif set(entry.targets) == set(DEFAULT_TARGETS):
            issues.append(
                CompilerIssue(
                    severity="warn",
                    code="stale-override",
                    path=OVERRIDE_FILE,
                    message="override {0} repeats all default targets".format(
                        capability_id
                    ),
                )
            )
    capabilities_by_id = {item.capability_id: item for item in capabilities}
    for capability_id, binding in config.command_bindings.items():
        command = capabilities_by_id.get(capability_id)
        if command is None or command.kind is not CapabilityKind.COMMAND:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="command-binding-unknown-command",
                    path=OVERRIDE_FILE,
                    message="command binding {0} is not in scanned capabilities".format(
                        capability_id
                    ),
                )
            )
        skill_id = "skill:skills/{0}".format(binding.codex_skill_dir)
        skill = capabilities_by_id.get(skill_id)
        if skill is None or skill.kind is not CapabilityKind.SKILL:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="command-binding-unknown-skill",
                    path=OVERRIDE_FILE,
                    message=(
                        "command binding {0} skill directory {1!r} "
                        "is not in scanned capabilities"
                    ).format(capability_id, binding.codex_skill_dir),
                )
            )
            continue
        if Target.CODEX not in resolved_targets(config, skill):
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="command-binding-skill-not-on-codex",
                    path=OVERRIDE_FILE,
                    message=(
                        "command binding {0} skill {1!r} does not enter Codex"
                    ).format(capability_id, binding.codex_skill_dir),
                )
            )
    return tuple(issues)


def resolved_targets(config: OverrideConfig, capability: CapabilityRef) -> tuple[Target, ...]:
    entry = config.overrides.get(capability.capability_id)
    if entry is not None:
        return entry.targets
    return config.default_targets


def command_binding_for(
    config: OverrideConfig, capability_id: str
) -> CommandBinding | None:
    return config.command_bindings.get(capability_id)


def skill_dir_allowed(config: OverrideConfig, skill_dir_name: str, host: str) -> bool:
    target = _VALID_HOSTS.get(host)
    if target is None:
        raise ValueError(
            "host must be one of cursor, codex, claude-code, got {0!r}".format(host)
        )
    capability = CapabilityRef(
        capability_id="skill:skills/{0}".format(skill_dir_name),
        kind=CapabilityKind.SKILL,
        source_path="skills/{0}".format(skill_dir_name),
        source_dialect=None,
    )
    return target in resolved_targets(config, capability)
