"""Initialize a new agent-project/v1 directory tree."""

from __future__ import annotations

from pathlib import Path

import yaml

from agentc.model.diagnostics import (
    CODE_INVALID_NAME,
    CODE_NONEMPTY_DEST,
    Diagnostic,
)
from agentc.validate.engine import validate_project

_HOSTS = ("cursor", "codex", "claude-code")
_TARGET_TOKENS = frozenset({"all", *_HOSTS})

_DIRECTORIES = (
    "skills",
    "extensions/rules",
    "extensions/commands",
    "extensions/agents",
    "extensions/hooks",
    "extensions/mcp",
    "overlays/cursor",
    "overlays/codex",
    "overlays/claude-code",
    "tests",
)

_CI_TEMPLATE = (
    Path(__file__).resolve().parent / "templates" / "github-workflows-ci.yml"
)
_README_CI = (
    "This project includes an example GitHub Actions workflow at "
    "`.github/workflows/ci.yml`.\n"
    "The workflow installs AgentC from the public GitHub source before validation "
    "and build.\n"
)

_APACHE_2_LICENSE_PATH = Path(__file__).resolve().parent / "templates" / "LICENSE-2.0.txt"


def init_project(
    dest: Path,
    *,
    project_id: str,
    name: str,
    targets: tuple[str, ...],
    license_id: str = "Apache-2.0",
) -> list[Diagnostic]:
    unknown = [token for token in targets if token not in _TARGET_TOKENS]
    if unknown or not targets:
        listed = ", ".join(repr(token) for token in unknown) or "(empty)"
        return [
            Diagnostic(
                code=CODE_INVALID_NAME,
                level="error",
                message=f"Unknown --targets value: {listed}.",
                path="/targets",
                hint="Use cursor, codex, claude-code, or all (comma-separated).",
            )
        ]

    if dest.exists() and not dest.is_dir():
        return [
            Diagnostic(
                code=CODE_NONEMPTY_DEST,
                level="error",
                message="Destination already exists and is not a directory.",
                path=dest.name,
                hint="Choose a new directory path or remove the existing file.",
            )
        ]

    if dest.exists() and any(dest.iterdir()):
        return [
            Diagnostic(
                code=CODE_NONEMPTY_DEST,
                level="error",
                message="Destination directory is not empty.",
                path=dest.name,
                hint="Choose a new directory or remove existing files.",
            )
        ]

    dest.mkdir(parents=True, exist_ok=True)

    for relative in _DIRECTORIES:
        (dest / relative).mkdir(parents=True, exist_ok=True)

    yaml_text = _render_agent_project_yaml(
        project_id=project_id,
        name=name,
        license_id=license_id,
        targets=targets,
    )
    _write_text(dest / "agent-project.yaml", yaml_text)
    _write_text(
        dest / "README.md",
        f"# {name}\n\nPortable agent project initialized with agentc.\n\n{_README_CI}",
    )
    workflow_dir = dest / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    _write_text(
        dest / ".github" / "workflows" / "ci.yml",
        _CI_TEMPLATE.read_text(encoding="utf-8"),
    )
    _write_text(dest / "LICENSE", _license_text(license_id))
    _write_text(dest / ".gitignore", "build/\n")

    return validate_project(dest)


def _render_agent_project_yaml(
    *,
    project_id: str,
    name: str,
    license_id: str,
    targets: tuple[str, ...],
) -> str:
    enabled = _resolve_targets(targets)
    data = {
        "schemaVersion": "agent-project/v1",
        "project": {
            "id": project_id,
            "name": name,
            "version": "0.1.0",
            "license": license_id,
        },
        "targets": {host: {"enabled": enabled[host]} for host in _HOSTS},
        "contents": {
            "skills": [],
            "rules": [],
            "commands": [],
            "agents": [],
            "hooks": [],
            "mcp": [],
        },
        "defaults": {
            "scopes": ["workspace"],
            "conflictPolicy": "fail",
            "portabilityPolicy": "strict",
        },
        "requirements": {
            "executables": [],
            "environment": {"optional": []},
        },
        "security": {"forbidAbsolutePaths": True},
    }
    return yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _resolve_targets(targets: tuple[str, ...]) -> dict[str, bool]:
    if "all" in targets:
        return {host: True for host in _HOSTS}
    selected = set(targets)
    return {host: host in selected for host in _HOSTS}


def _license_text(license_id: str) -> str:
    if license_id == "Apache-2.0":
        return _APACHE_2_LICENSE_PATH.read_text(encoding="utf-8")
    return f"Copyright AgentC contributors\n\nSPDX-License-Identifier: {license_id}\n"


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
