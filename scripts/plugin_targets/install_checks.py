"""Structure-level install checks. Trust / new-session / hooks are not gates."""
from __future__ import annotations

import json
from pathlib import Path

from plugin_compiler.model import CompilerIssue


def _issue(
    code: str,
    message: str,
    path: str,
    severity: str = "info",
) -> CompilerIssue:
    return CompilerIssue(severity=severity, code=code, path=path, message=message)


def _skills_dir(tree: Path, spec: object) -> Path | None:
    if spec is None:
        candidate = tree / "skills"
        return candidate if candidate.is_dir() else None
    if isinstance(spec, str):
        relative = spec.strip().replace("\\", "/").lstrip("./")
        if not relative:
            return None
        return tree.joinpath(*relative.split("/"))
    return None


def read_manifest(tree: Path, manifest_relative: str) -> dict:
    path = tree / Path(manifest_relative)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("{0}: root must be an object".format(path))
    return raw


def structure_issues(tree: Path, manifest_relative: str) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    tree_path = str(tree)
    if not tree.is_dir():
        return (
            _issue(
                "install-tree-missing",
                "installed tree is missing",
                tree_path,
                severity="block",
            ),
        )
    issues.append(_issue("install-tree-ok", "installed tree exists", tree_path))
    try:
        manifest = read_manifest(tree, manifest_relative)
        name = str(manifest.get("name", "")).strip()
        if not name:
            raise ValueError("plugin name is empty")
    except Exception as exc:
        issues.append(
            _issue(
                "install-manifest-invalid",
                str(exc),
                str(tree / manifest_relative),
                severity="block",
            )
        )
        return tuple(issues)
    issues.append(
        _issue(
            "install-manifest-ok",
            "manifest is valid",
            str(tree / manifest_relative),
        )
    )
    skills_dir = _skills_dir(tree, manifest.get("skills"))
    if skills_dir is None:
        issues.append(
            _issue(
                "install-skills-ok",
                "no skills declared",
                tree_path,
            )
        )
        return tuple(issues)
    found = sorted(
        path
        for path in skills_dir.glob("*/SKILL.md")
        if path.is_file()
    )
    if not found:
        issues.append(
            _issue(
                "install-skill-missing",
                "declared skills were not found",
                str(skills_dir),
                severity="block",
            )
        )
        return tuple(issues)
    issues.append(
        _issue(
            "install-skills-ok",
            "declared skills found: {0}".format(
                ", ".join(path.parent.name for path in found)
            ),
            str(skills_dir),
        )
    )
    return tuple(issues)


def result_ok(issues: tuple[CompilerIssue, ...]) -> bool:
    return not any(issue.severity == "block" for issue in issues)
