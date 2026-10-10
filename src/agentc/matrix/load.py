"""Load agentc host-profile YAML into a capability matrix."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agentc.model.diagnostics import (
    CODE_PROFILE_INVALID,
    CODE_PROFILE_UNKNOWN,
    Diagnostic,
)

PROFILE_SCHEMA = "agentc-host-profile/v1"
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
PROFILE_HOSTS = ("cursor", "codex", "claude-code")
SUCCESS_LEVELS = frozenset(
    {"native", "supported", "adapted", "partial", "host-only"}
)
CAPABILITY_LEVELS = frozenset(
    {
        "native",
        "supported",
        "adapted",
        "partial",
        "unsupported",
        "unknown",
        "host-only",
    }
)

_REQUIRED_PROFILE_STRINGS = ("id", "host", "hostVersions", "lastVerified")


@dataclass(frozen=True)
class CapabilityMatrix:
    hosts: Mapping[str, Mapping[str, str]]

    def support(self, host_id: str, capability: str) -> str:
        host_caps = self.hosts.get(host_id)
        if host_caps is None:
            return "unsupported"
        level = host_caps.get(capability)
        if level is None:
            return "unsupported"
        return level

    def allows_success(self, host_id: str, capability: str) -> bool:
        return self.support(host_id, capability) in SUCCESS_LEVELS


def load_matrix(
    path: Path | None = None,
    host_ids: Mapping[str, str] | None = None,
) -> CapabilityMatrix:
    """`path` is a profiles directory (contains defaults.yaml). None → shipped defaults."""
    root = _profiles_root(path)
    selected = dict(_load_defaults(root) if host_ids is None else host_ids)
    hosts: dict[str, Mapping[str, str]] = {}
    for host, profile_id in selected.items():
        if not isinstance(host, str) or not isinstance(profile_id, str):
            raise ValueError("host profile ids must be strings")
        profile = _load_profile(root, profile_id)
        if profile["host"] != host:
            raise ValueError(
                "profile {0} host {1!r} does not match {2!r}".format(
                    profile_id, profile["host"], host
                )
            )
        capabilities = {
            cap: level for cap, level in profile["capabilities"].items()
        }
        hosts[host] = MappingProxyType(capabilities)
    return CapabilityMatrix(hosts=MappingProxyType(hosts))


def load_profile_document(
    profiles_dir: Path | None = None, host: str | None = None
) -> dict[str, Any]:
    """Document for `agentc matrix --format json`."""
    root = _profiles_root(profiles_dir)
    defaults = _load_defaults(root)
    profiles: list[dict[str, Any]] = []
    for host_id, profile_id in defaults.items():
        if host is not None and host_id != host:
            continue
        profiles.append(_load_profile(root, profile_id))
    return {
        "schemaVersion": PROFILE_SCHEMA,
        "defaults": dict(defaults),
        "profiles": profiles,
    }


def resolve_profile_ids(
    overrides: tuple[str, ...],
    profiles_dir: Path | None = None,
) -> tuple[dict[str, str], list[Diagnostic]]:
    """host → id after applying `--profile` tokens (`cursor/2026.09`)."""
    root = _profiles_root(profiles_dir)
    issues: list[Diagnostic] = []
    try:
        selected = dict(_load_defaults(root))
    except (OSError, ValueError) as exc:
        issues.append(
            _invalid_issue(
                (root / "defaults.yaml").as_posix(),
                str(exc),
            )
        )
        return {}, issues

    malformed: list[str] = []
    for token in overrides:
        host = _host_from_id(token)
        if host is None:
            malformed.append(token)
            continue
        selected[host] = token

    for token in malformed:
        issues.append(
            _unknown_issue(
                token,
                None,
                "Profile id must look like host/id.",
            )
        )

    for host, profile_id in selected.items():
        issues.extend(_validate_selected_id(root, host, profile_id))
    return selected, issues


def _profiles_root(path: Path | None) -> Path:
    return DEFAULT_PROFILES_DIR if path is None else path


def _host_from_id(token: str) -> str | None:
    if "/" not in token:
        return None
    host, _rest = token.split("/", 1)
    if host == "":
        return None
    return host


def _profile_file(root: Path, profile_id: str) -> Path:
    return root / "{0}.yaml".format(profile_id.replace("/", "-"))


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            "cannot read {0}: {1}".format(path.as_posix(), exc)
        ) from exc
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(
            "invalid YAML in {0}: {1}".format(path.as_posix(), exc)
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("{0} must be a mapping".format(path.as_posix()))
    return payload


def _load_defaults(root: Path) -> dict[str, str]:
    payload = _read_mapping(root / "defaults.yaml")
    if payload.get("schemaVersion") != PROFILE_SCHEMA:
        raise ValueError("unsupported host profile schema")
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise ValueError("defaults must be a mapping")
    result: dict[str, str] = {}
    for host, profile_id in defaults.items():
        if not isinstance(host, str) or not isinstance(profile_id, str) or not profile_id:
            raise ValueError("defaults entries must be host to profile id strings")
        result[host] = profile_id
    return result


def _load_profile(root: Path, profile_id: str) -> dict[str, Any]:
    path = _profile_file(root, profile_id)
    payload = _read_mapping(path)
    reason = _profile_invalid_reason(payload)
    if reason is not None:
        raise ValueError("{0}: {1}".format(path.as_posix(), reason))
    if payload["id"] != profile_id:
        raise ValueError(
            "{0}: profile id {1!r} does not match {2!r}".format(
                path.as_posix(), payload["id"], profile_id
            )
        )
    return payload


def _profile_invalid_reason(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return "profile must be a mapping"
    for key in _REQUIRED_PROFILE_STRINGS:
        value = payload.get(key)
        if not isinstance(value, str) or value == "":
            return "profile field {0} must be a non-empty string".format(key)
    limits = payload.get("limits")
    if not isinstance(limits, list) or not all(
        isinstance(item, str) for item in limits
    ):
        return "profile limits must be a list of strings"
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        return "profile capabilities must be a mapping"
    for capability, level in capabilities.items():
        if not isinstance(capability, str) or not isinstance(level, str):
            return "capability keys and levels must be strings"
        if level not in CAPABILITY_LEVELS:
            return "illegal capability level {0!r}".format(level)
    return None


def _validate_selected_id(
    root: Path, host: str, profile_id: str
) -> list[Diagnostic]:
    token_host = _host_from_id(profile_id)
    if token_host is None:
        return [
            _unknown_issue(
                profile_id,
                host,
                "Profile id must look like host/id.",
            )
        ]
    if host not in PROFILE_HOSTS:
        return [
            _unknown_issue(
                profile_id,
                host,
                "Unknown host {0!r} in profile id.".format(host),
            )
        ]
    if token_host != host:
        return [
            _unknown_issue(
                profile_id,
                host,
                "Profile id host does not match {0!r}.".format(host),
            )
        ]
    path = _profile_file(root, profile_id)
    if not path.is_file():
        return [
            _unknown_issue(
                profile_id,
                host,
                "No shipped profile file for this id.",
            )
        ]
    try:
        payload = _read_mapping(path)
    except (OSError, ValueError) as exc:
        return [_invalid_issue(path.as_posix(), str(exc))]
    reason = _profile_invalid_reason(payload)
    if reason is not None:
        return [_invalid_issue(path.as_posix(), reason)]
    yaml_host = payload.get("host")
    if yaml_host != token_host:
        return [
            _unknown_issue(
                profile_id,
                host,
                "Profile id host does not match YAML host {0!r}.".format(
                    yaml_host
                ),
            )
        ]
    if payload.get("id") != profile_id:
        return [
            _invalid_issue(
                path.as_posix(),
                "profile id {0!r} does not match {1!r}".format(
                    payload.get("id"), profile_id
                ),
            )
        ]
    return []


def _unknown_issue(profile_id: str, host: str | None, hint: str) -> Diagnostic:
    path = "/targets/{0}".format(host) if host is not None else profile_id
    return Diagnostic(
        code=CODE_PROFILE_UNKNOWN,
        level="error",
        message="Unknown host profile {0!r}.".format(profile_id),
        path=path,
        hint=hint,
    )


def _invalid_issue(path: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_PROFILE_INVALID,
        level="error",
        message=message,
        path=path,
        hint="Fix the host profile YAML (required fields and legal capability levels).",
    )
