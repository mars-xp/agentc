"""Package existing build outputs into distributable zip archives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from agentc.build.plan import BuildPlan
from agentc.model.diagnostics import (
    CODE_MISSING_OUTPUT,
    CODE_PACKAGE_ESCAPE,
    CODE_PACKAGE_IO,
    Diagnostic,
)
from agentc.project.load import load_project_yaml, to_ir
from agentc.validate.engine import validate_project
from plugin_compiler.paths import BuildLayout, require_inside
from plugin_compiler.zip_tree import zip_plugin_tree

_FATAL_EXIT_CODES = frozenset(
    {
        CODE_PACKAGE_ESCAPE,
        CODE_PACKAGE_IO,
    }
)


def exit_code_for_package(
    diagnostics: list[Diagnostic],
    policy: str,
    *,
    source_failed: bool,
) -> int:
    if source_failed:
        return 1
    for diagnostic in diagnostics:
        if diagnostic.level != "error":
            continue
        if diagnostic.code in _FATAL_EXIT_CODES:
            return 1
    if policy == "strict":
        return 1 if any(d.level == "error" for d in diagnostics) else 0
    return 0


def _require_safe_component(name: str, label: str) -> None:
    if not name or name in (".", ".."):
        raise ValueError("{0} must be a non-empty path component".format(label))
    if "/" in name or "\\" in name:
        raise ValueError("{0} must not contain path separators".format(label))
    if ".." in Path(name).parts:
        raise ValueError("{0} must not contain '..'".format(label))


def write_zip_sidecars(zip_path: Path) -> None:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha_path = zip_path.with_name(zip_path.name + ".sha256")
    cdx_path = zip_path.with_name(zip_path.name + ".cdx.json")
    sha_path.write_text("{0}  {1}\n".format(digest, zip_path.name), encoding="utf-8")
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [
            {
                "type": "file",
                "name": zip_path.name,
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
        ],
    }
    cdx_path.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )


def _zip_destination(build_root: Path, host: str, plugin_name: str) -> Path:
    _require_safe_component(host, "host")
    _require_safe_component(plugin_name, "plugin_name")
    dest = build_root / "dist" / host / "{0}.zip".format(plugin_name)
    require_inside(build_root, dest)
    return dest


def _output_has_files(output_dir: Path) -> bool:
    if not output_dir.is_dir():
        return False
    return any(path.is_file() for path in output_dir.rglob("*"))


def _missing_output_issue(host: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_MISSING_OUTPUT,
        level="error",
        message=(
            "No build output for {0}; run agentc build first."
        ).format(host),
        path="/targets/{0}".format(host),
        hint="Run agentc build for this host before packaging.",
    )


def _package_escape_issue(host: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_PACKAGE_ESCAPE,
        level="error",
        message=message,
        path="/targets/{0}".format(host),
        hint=message,
    )


def _package_io_issue(host: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_PACKAGE_IO,
        level="error",
        message=message,
        path="/targets/{0}".format(host),
        hint=message,
    )


def _apply_permissive_missing_downgrades(
    diagnostics: list[Diagnostic],
    policy: str,
) -> list[Diagnostic]:
    if policy != "permissive":
        return diagnostics
    downgraded: list[Diagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.level == "error" and diagnostic.code == CODE_MISSING_OUTPUT:
            downgraded.append(replace(diagnostic, level="warning"))
        else:
            downgraded.append(diagnostic)
    return downgraded


def package_project(
    project_root: Path,
    *,
    policy: str | None = None,
) -> tuple[list[Diagnostic], bool, str]:
    source = validate_project(project_root, policy=policy)
    if any(issue.level == "error" for issue in source):
        return (source, True, policy or "strict")

    root = project_root.resolve()
    ir = to_ir(load_project_yaml(root / "agent-project.yaml"))
    plan = BuildPlan.from_ir(root, ir, policy)
    plugin_name = ir.project_id
    layout = BuildLayout.from_repo(root)
    build_root = layout.root

    diagnostics: list[Diagnostic] = []
    hosts_to_zip: list[str] = []

    for host in plan.enabled_hosts:
        try:
            output_dir = layout.output(host, plugin_name)
        except ValueError as exc:
            diagnostics.append(_package_escape_issue(host, str(exc)))
            continue
        if _output_has_files(output_dir):
            hosts_to_zip.append(host)
        else:
            diagnostics.append(_missing_output_issue(host))

    diagnostics = _apply_permissive_missing_downgrades(diagnostics, plan.policy)

    strict_block = plan.policy == "strict" and any(
        d.code == CODE_MISSING_OUTPUT and d.level == "error" for d in diagnostics
    )
    if strict_block:
        return (diagnostics, False, plan.policy)

    for host in hosts_to_zip:
        try:
            dest_zip = _zip_destination(build_root, host, plugin_name)
        except ValueError as exc:
            diagnostics.append(_package_escape_issue(host, str(exc)))
            continue

        try:
            output_dir = layout.output(host, plugin_name)
        except ValueError as exc:
            diagnostics.append(_package_escape_issue(host, str(exc)))
            continue
        try:
            zip_plugin_tree(output_dir, plugin_name, dest_zip)
        except ValueError as exc:
            diagnostics.append(_package_escape_issue(host, str(exc)))
            continue
        except OSError as exc:
            diagnostics.append(_package_io_issue(host, str(exc)))
            continue
        try:
            write_zip_sidecars(dest_zip)
        except OSError as exc:
            diagnostics.append(_package_io_issue(host, str(exc)))

    return (diagnostics, False, plan.policy)
