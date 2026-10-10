"""Read-only probes for Python runtime and host CLIs on PATH."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentc.model.diagnostics import (
    CODE_DOCTOR_DIR,
    CODE_DOCTOR_FOUND,
    CODE_DOCTOR_HOST_MISSING,
    CODE_DOCTOR_PYTHON,
    CODE_DOCTOR_VERSION,
    Diagnostic,
)

HOST_COMMANDS = ("cursor", "codex", "claude")
MIN_PYTHON = (3, 10)
VERSION_TEXT_MAX = 120
DEFAULT_TIMEOUT_SECONDS = 3.0

VERSION_HINT = "Doctor continues; this host can remain unused."


@dataclass(frozen=True)
class HostVersionResult:
    timed_out: bool
    returncode: int | None
    stdout: bytes
    stderr: bytes


RunHostVersion = Callable[[list[str], float], HostVersionResult]


def _resolve_path(raw: str) -> str:
    try:
        return Path(raw).resolve().as_posix()
    except OSError:
        return raw


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


def _dir_error(path: Path, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_DOCTOR_DIR,
        level="error",
        message=message,
        path=path.as_posix(),
        hint="Pass an explicit readable project directory.",
    )


def format_version_text(stdout: bytes, stderr: bytes) -> str | None:
    def first_nonempty_line(data: bytes) -> str | None:
        if not data:
            return None
        text = data.decode("utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None

    try:
        line = first_nonempty_line(stdout)
        if line is None:
            line = first_nonempty_line(stderr)
    except UnicodeDecodeError:
        return None

    if line is None:
        return None

    if len(line) > VERSION_TEXT_MAX:
        return line[:VERSION_TEXT_MAX] + "…"
    return line


def _default_run_host_version(
    argv: list[str], timeout_seconds: float
) -> HostVersionResult:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return HostVersionResult(True, None, b"", b"")
    except OSError:
        return HostVersionResult(False, None, b"", b"")
    return HostVersionResult(
        False, completed.returncode, completed.stdout, completed.stderr
    )


def _probe_host_version(
    name: str,
    resolved_path: str,
    runner: RunHostVersion,
    timeout_seconds: float,
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    base_message = f"Host CLI {name} found on PATH."
    result = runner([resolved_path, "--version"], timeout_seconds)

    if result.timed_out:
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_FOUND,
                level="info",
                message=base_message,
                path=resolved_path,
                hint="",
            )
        )
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_VERSION,
                level="warning",
                message=f"Host CLI {name} --version timed out after {timeout_seconds}s.",
                path=resolved_path,
                hint=VERSION_HINT,
            )
        )
        return diags

    if result.returncode is not None and result.returncode != 0:
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_FOUND,
                level="info",
                message=base_message,
                path=resolved_path,
                hint="",
            )
        )
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_VERSION,
                level="warning",
                message=f"Host CLI {name} --version exited {result.returncode}.",
                path=resolved_path,
                hint=VERSION_HINT,
            )
        )
        return diags

    version_text = format_version_text(result.stdout, result.stderr)
    if version_text is None:
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_FOUND,
                level="info",
                message=base_message,
                path=resolved_path,
                hint="",
            )
        )
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_VERSION,
                level="warning",
                message=f"Host CLI {name} --version produced no readable version text.",
                path=resolved_path,
                hint=VERSION_HINT,
            )
        )
        return diags

    diags.append(
        Diagnostic(
            code=CODE_DOCTOR_FOUND,
            level="info",
            message=f"{base_message} {version_text}",
            path=resolved_path,
            hint="",
        )
    )
    return diags


def run_doctor(
    project_dir: Path | None = None,
    *,
    version_info: tuple[int, ...] | None = None,
    executable: str | None = None,
    which: Callable[[str], str | None] | None = None,
    run_host_version: RunHostVersion | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[Diagnostic]:
    vi = version_info if version_info is not None else sys.version_info
    exe = executable if executable is not None else sys.executable
    which_fn = which if which is not None else shutil.which
    runner = (
        run_host_version if run_host_version is not None else _default_run_host_version
    )

    diags: list[Diagnostic] = []

    exe_path = _resolve_path(exe)
    if len(vi) < 2 or vi[:2] < MIN_PYTHON:
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_PYTHON,
                level="error",
                message="Python version is below 3.10 or unreadable.",
                path=exe_path,
                hint="Use Python 3.10 or newer to run agentc.",
            )
        )
    else:
        diags.append(
            Diagnostic(
                code=CODE_DOCTOR_FOUND,
                level="info",
                message="Python interpreter meets minimum version.",
                path=exe_path,
                hint="",
            )
        )

    for name in HOST_COMMANDS:
        found = which_fn(name)
        if found:
            resolved = _resolve_path(found)
            diags.extend(_probe_host_version(name, resolved, runner, timeout_seconds))
        else:
            diags.append(
                Diagnostic(
                    code=CODE_DOCTOR_HOST_MISSING,
                    level="warning",
                    message=f"Host CLI {name} not found on PATH.",
                    path=name,
                    hint="Install the host CLI or ignore if unused.",
                )
            )

    if project_dir is not None:
        if _escapes_given_root(project_dir):
            diags.append(
                _dir_error(project_dir, "doctor directory path escapes the given root")
            )
            return diags
        if project_dir.is_symlink():
            try:
                resolved = project_dir.resolve()
                if not resolved.is_relative_to(project_dir.parent.resolve()):
                    diags.append(
                        _dir_error(
                            project_dir,
                            "doctor directory path escapes the given root",
                        )
                    )
                    return diags
            except OSError:
                diags.append(_dir_error(project_dir, "doctor directory is unreadable"))
                return diags
        try:
            root = project_dir.resolve()
        except OSError:
            diags.append(_dir_error(project_dir, "doctor directory is unreadable"))
            return diags
        if not root.is_dir():
            diags.append(_dir_error(project_dir, "doctor directory is unreadable"))
            return diags
        from agentc.validate.engine import validate_project

        diags.extend(validate_project(root))

    return diags
