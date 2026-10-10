from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from plugin_compiler.compiled_rules import validate_compiled_rule_bundle
from plugin_compiler.model import CapabilityRef, Target
from plugin_compiler.overrides import OverrideConfig
from plugin_compiler.validation import (
    CommandRunner,
    ValidationIssue,
    ValidationReport,
    _command_diagnostic,
    _default_runner,
    _from_compiler_issue,
    _is_structure_only_runner,
    _issue,
    structure_issues_for_host,
    validate_skill_discovery,
)
from plugin_targets.codex.manifest import CODEX_MANIFEST_FIELDS, CODEX_MANIFEST_RELATIVE

_CODEX_COMPONENT_FIELDS = frozenset({"skills", "mcpServers", "apps", "hooks"})
_PATH_STRING = "path-string"
_PATH_OR_OBJECT = "path-or-object"
_CODEX_COMPONENT_SHAPES = {
    "skills": _PATH_STRING,
    "apps": _PATH_STRING,
    "hooks": _PATH_STRING,
    "mcpServers": _PATH_OR_OBJECT,
}
_CODEX_UNSUPPORTED_ROOT = frozenset(
    {".cursor-plugin", ".claude-plugin", "rules", "commands"}
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RECORDED_VALIDATOR_PATH = re.compile(r"recorded-path:\s*`([^`]+)`")
_RECORDED_VALIDATOR_SHA256 = re.compile(r"recorded-sha256:\s*`([0-9a-fA-F]{64})`")
_DEFAULT_IDENTITY_README = (
    Path(__file__).resolve().parents[2] / "plugin_compiler" / "README.md"
)
_YAML_INSTALL_HINT = (
    "official Codex validator needs PyYAML; install with: "
    "python3 -m pip install -r requirements/plugin-validator.txt"
)


def _default_codex_probe_paths(environ: Mapping[str, str]) -> tuple[Path, ...]:
    home = Path(environ.get("HOME", str(Path.home()))).expanduser()
    codex_home = Path(environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
    relative = Path("skills/.system/plugin-creator/scripts/validate_plugin.py")
    return (
        codex_home / relative,
        codex_home / "skills/plugin-creator/scripts/validate_plugin.py",
    )


def resolve_codex_validator(
    environ: Mapping[str, str] | None = None,
    probe_paths: Sequence[Path] | None = None,
) -> Path | None:
    values = os.environ if environ is None else environ
    explicit = values.get("CODEX_PLUGIN_VALIDATOR")
    if explicit:
        return Path(explicit).expanduser()
    candidates = (
        tuple(probe_paths)
        if probe_paths is not None
        else _default_codex_probe_paths(values)
    )
    return next((path for path in candidates if path.is_file()), None)


def _codex_yaml_available() -> bool:
    return importlib.util.find_spec("yaml") is not None


def recorded_codex_validator_identity(
    readme: Path | None = None,
) -> tuple[str | None, str | None]:
    path = _DEFAULT_IDENTITY_README if readme is None else readme
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    path_match = _RECORDED_VALIDATOR_PATH.search(text)
    digest_match = _RECORDED_VALIDATOR_SHA256.search(text)
    return (
        path_match.group(1) if path_match else None,
        digest_match.group(1).lower() if digest_match else None,
    )


def _codex_validator_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _emit_validator_identity_notice(
    stream: TextIO,
    recorded_path: str | None,
    recorded_digest: str | None,
    actual_path: Path,
    actual_digest: str,
) -> None:
    stream.write("契约需要重新确认\n")
    stream.write(
        "recorded: {0} sha256={1}\n".format(recorded_path, recorded_digest)
    )
    stream.write(
        "actual: {0} sha256={1}\n".format(actual_path, actual_digest)
    )
    stream.flush()


def _maybe_emit_validator_identity_notice(
    validator: Path,
    identity_readme: Path | None,
    identity_stream: TextIO | None,
) -> None:
    recorded_path, recorded_digest = recorded_codex_validator_identity(
        identity_readme
    )
    try:
        actual_digest = _codex_validator_digest(validator)
    except OSError:
        actual_digest = ""
    if recorded_path == str(validator) and recorded_digest == actual_digest:
        return
    stream = sys.stderr if identity_stream is None else identity_stream
    _emit_validator_identity_notice(
        stream,
        recorded_path,
        recorded_digest,
        validator,
        actual_digest,
    )


def run_codex_validator(
    plugin_root: Path,
    runner: CommandRunner,
    *,
    environ: Mapping[str, str] | None = None,
    probe_paths: Sequence[Path] | None = None,
    identity_readme: Path | None = None,
    identity_stream: TextIO | None = None,
) -> tuple[ValidationIssue, ...]:
    validator = resolve_codex_validator(environ, probe_paths)
    if validator is None or not validator.is_file():
        path = str(validator) if validator is not None else "validate_plugin.py"
        return (
            _issue(
                "validator-unavailable",
                path,
                "official Codex plugin validator script was not found",
            ),
        )
    if not _is_structure_only_runner(runner) and not _codex_yaml_available():
        return (
            _issue(
                "validator-dependency",
                str(validator),
                _YAML_INSTALL_HINT,
            ),
        )
    if not (
        _is_structure_only_runner(runner)
        and identity_readme is None
        and identity_stream is None
    ):
        _maybe_emit_validator_identity_notice(
            validator, identity_readme, identity_stream
        )
    command = (sys.executable, str(validator), str(plugin_root))
    try:
        result = runner(command)
    except FileNotFoundError as exc:
        return (
            _issue(
                "validator-unavailable",
                str(validator),
                "unable to start Codex validator: {0}".format(exc),
            ),
        )
    except Exception as exc:
        return (
            _issue(
                "validator-invocation",
                str(validator),
                "Codex validator invocation raised {0}: {1}".format(
                    type(exc).__name__, exc
                ),
            ),
        )
    if result.returncode != 0:
        output = "{0}\n{1}".format(result.stdout, result.stderr)
        code = (
            "validator-dependency"
            if "ModuleNotFoundError" in output or "No module named" in output
            else "validator-failed"
        )
        return (
            _issue(
                code,
                str(validator),
                "Codex validator exited with {0}\n{1}".format(
                    result.returncode, _command_diagnostic(result)
                ),
            ),
        )
    stdout = _ANSI_ESCAPE.sub("", result.stdout)
    success_lines = [
        line
        for line in stdout.splitlines()
        if line.startswith("Plugin validation passed:")
    ]
    if not success_lines:
        return (
            _issue(
                "validator-output",
                str(validator),
                "Codex validator output could not be parsed\n{0}".format(
                    _command_diagnostic(result)
                ),
            ),
        )
    nonempty_lines = [line for line in stdout.splitlines() if line.strip()]
    if len(nonempty_lines) != 1 or result.stderr:
        return (
            _issue(
                "validator-warning",
                str(validator),
                "Codex validator passed with extra diagnostics\n{0}".format(
                    _command_diagnostic(result)
                ),
                severity="warn",
            ),
        )
    return ()


def validate_codex_structure(plugin_root: Path) -> tuple[ValidationIssue, ...]:
    return structure_issues_for_host(
        plugin_root,
        Target.CODEX.value,
        CODEX_MANIFEST_RELATIVE,
        CODEX_MANIFEST_FIELDS,
        _CODEX_COMPONENT_FIELDS,
        _CODEX_COMPONENT_SHAPES,
        _CODEX_UNSUPPORTED_ROOT,
    )


def validate_codex_staged(
    plugin_root: Path,
    runner: CommandRunner | None = None,
    plugin_dir: Path | None = None,
    config: OverrideConfig | None = None,
    capabilities: tuple[CapabilityRef, ...] | Sequence[CapabilityRef] | None = None,
) -> ValidationReport:
    plugin_root = Path(plugin_root)
    issues = list(validate_codex_structure(plugin_root))
    if plugin_dir is not None and config is not None and capabilities is not None:
        issues.extend(
            validate_skill_discovery(
                plugin_dir,
                Target.CODEX,
                plugin_root,
                config,
                capabilities,
            )
        )
    issues.extend(
        _from_compiler_issue(issue)
        for issue in validate_compiled_rule_bundle(plugin_root, Target.CODEX)
    )
    command_runner = runner or _default_runner
    issues.extend(run_codex_validator(plugin_root, command_runner))
    return ValidationReport(
        target=Target.CODEX.value,
        plugin_root=str(plugin_root),
        issues=tuple(issues),
    )
