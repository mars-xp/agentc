"""Public-audit translation: flag, path, and Phase 0 source-tree scan."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from agentc.audit.engine import is_blocking_rule, scan_tree
from plugin_compiler.zip_tree import extract_zip_filtered

from agentc.audit.files import list_text_files
from agentc.model.diagnostics import (
    CODE_AUDIT_BLOCKING,
    CODE_AUDIT_DIR,
    CODE_AUDIT_FLAG,
    CODE_AUDIT_RECORDING,
    CODE_AUDIT_UNREADABLE,
    Diagnostic,
)
from agentc.validate.engine import validate_project


def _flag_missing() -> Diagnostic:
    return Diagnostic(
        code=CODE_AUDIT_FLAG,
        level="error",
        message="missing --for-public flag",
        path=None,
        hint="Re-run with --for-public DIR.",
    )


def _dir_missing() -> Diagnostic:
    return Diagnostic(
        code=CODE_AUDIT_DIR,
        level="error",
        message="missing audit directory",
        path=None,
        hint="Pass an explicit DIR; cwd and home are not used.",
    )


def _unreadable(path: Path, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_AUDIT_UNREADABLE,
        level="error",
        message=message,
        path=path.as_posix(),
        hint="Pass a readable directory that stays inside the given path.",
    )


def _escapes_given_root(dir_path: Path) -> bool:
    if ".." not in dir_path.parts:
        return False
    idx = dir_path.parts.index("..")
    prefix = Path(*dir_path.parts[:idx]) if idx > 0 else Path(".")
    try:
        dir_path.resolve().relative_to(prefix.resolve())
    except (ValueError, OSError):
        return True
    return False


def _map_finding(finding) -> Diagnostic:
    if is_blocking_rule(finding.rule):
        return Diagnostic(
            code=CODE_AUDIT_BLOCKING,
            level="error",
            message=f"blocking scan rule {finding.rule}",
            path=finding.path,
            hint="Remove secrets and internal hosts before public audit.",
        )
    return Diagnostic(
        code=CODE_AUDIT_RECORDING,
        level="warning",
        message=f"recording scan rule {finding.rule}",
        path=finding.path,
        hint="Review recorded findings before public release.",
    )


def _scan_listed(root: Path, listed: list[str]) -> list[Diagnostic]:
    return [_map_finding(finding) for finding in scan_tree(root, listed)]


def _audit_zip(root: Path, zip_path: Path) -> list[Diagnostic]:
    try:
        zip_path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return [_unreadable(zip_path, "audit zip path escapes the given directory")]
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                extract_zip_filtered(zf, staging)
            listed = list_text_files(staging)
            if isinstance(listed, Diagnostic):
                return [listed]
            return _scan_listed(staging, listed)
        except (ValueError, zipfile.BadZipFile, OSError, RuntimeError):
            return [_unreadable(zip_path, "audit zip is unreadable")]


def audit_directory(dir_path: Path | None, *, for_public: bool) -> list[Diagnostic]:
    if not for_public:
        return [_flag_missing()]
    if dir_path is None:
        return [_dir_missing()]
    if _escapes_given_root(dir_path):
        return [_unreadable(dir_path, "audit directory path escapes the given root")]
    try:
        root = dir_path.resolve()
    except OSError:
        return [_unreadable(dir_path, "audit directory is unreadable")]
    if not root.is_dir():
        return [_unreadable(dir_path, "audit directory is unreadable")]

    listed = list_text_files(root)
    if isinstance(listed, Diagnostic):
        return [listed]

    diagnostics = _scan_listed(root, listed)
    if (root / "agent-project.yaml").is_file():
        diagnostics.extend(validate_project(root))
    for zip_path in sorted(root.rglob("*.zip")):
        if zip_path.is_file():
            diagnostics.extend(_audit_zip(root, zip_path))
    return diagnostics
