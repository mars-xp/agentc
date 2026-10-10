"""Codex marketplace install: isolated --install-root or --current-user. Tests must patch Path.home before current-user install."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from plugin_compiler.model import CompilerIssue, Target
from plugin_compiler.names import InvalidPluginNameError, assert_plugin_name_for_targets
from plugin_compiler.paths import require_inside, require_safe_plugin_name
from plugin_compiler.validation import CommandResult
from plugin_targets.codex.manifest import CODEX_MANIFEST_RELATIVE
from plugin_targets.codex.validate import validate_codex_staged
from plugin_targets.codex.marketplace import (
    MarketplaceError,
    marketplace_registered_names,
    upsert_marketplace_plugin,
)
from plugin_targets.install_checks import read_manifest, result_ok, structure_issues
from plugin_targets.spi import InstallRequest, InstallResult

_CACHEBUSTER_PREFIX = "codex"


def default_cachebuster() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def sanitize_cachebuster(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    if not sanitized:
        raise ValueError("Cachebuster must contain at least one letter or digit.")
    return sanitized


def with_cachebuster(version: str, cachebuster: str) -> str:
    version_prefix = (version or "").split("+", 1)[0].strip() or "0.0.0"
    return "{0}+{1}.{2}".format(
        version_prefix, _CACHEBUSTER_PREFIX, sanitize_cachebuster(cachebuster)
    )


def _write_installed_version(tree: Path, version: str) -> None:
    path = tree / CODEX_MANIFEST_RELATIVE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = version
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _issue(code: str, message: str, path: str, severity: str = "block") -> CompilerIssue:
    return CompilerIssue(severity=severity, code=code, path=path, message=message)


def _from_validation_issues(issues: Sequence[object]) -> tuple[CompilerIssue, ...]:
    return tuple(
        CompilerIssue(
            severity=str(getattr(item, "severity")),
            code=str(getattr(item, "code")),
            path=str(getattr(item, "path")),
            message=str(getattr(item, "message")),
        )
        for item in issues
    )


def _identity(tree: Path) -> tuple[str, str]:
    manifest = read_manifest(tree, str(CODEX_MANIFEST_RELATIVE))
    name = str(manifest.get("name", "")).strip()
    version = str(manifest.get("version", "")).strip()
    if not name:
        raise RuntimeError("Codex plugin.json 缺少 name：{0}".format(tree))
    return name, version


def _resolve_plugin_dest(install_root: Path, name: str) -> Path:
    require_safe_plugin_name(name)
    assert_plugin_name_for_targets(name, (Target.CODEX,))
    root = install_root.expanduser()
    dest = root / "plugins" / name
    require_inside(root.resolve(), dest.resolve())
    return dest


def _list_header_version_index(header_line: str) -> int | None:
    parts = [part for part in header_line.strip().split() if part]
    upper = [part.upper() for part in parts]
    if "PLUGIN" in upper and "VERSION" in upper:
        return upper.index("VERSION")
    return None


def _parse_plugin_list_line(
    line: str,
    *,
    version_column: int | None = None,
) -> tuple[str, str, str, str] | None:
    """Parse one `codex plugin list` row: name@market, status, version."""
    stripped = line.strip()
    if not stripped:
        return None
    columns = [part for part in stripped.split("  ") if part.strip()]
    if len(columns) < 3:
        # Fallback: whitespace tokens — first is id, last is version, middle is status.
        tokens = stripped.split()
        if len(tokens) < 3 or "@" not in tokens[0]:
            return None
        identity = tokens[0]
        version = tokens[-1]
        status = " ".join(tokens[1:-1])
    else:
        identity = columns[0].strip()
        if version_column is not None and len(columns) > version_column:
            version = columns[version_column].strip()
            status = " ".join(
                part.strip() for part in columns[1:version_column]
            )
        elif len(columns) >= 4 and (
            columns[-1].strip().startswith("/")
            or "plugins/" in columns[-1]
        ):
            version = columns[-2].strip()
            status = " ".join(part.strip() for part in columns[1:-2])
        else:
            version = columns[-1].strip()
            status = " ".join(part.strip() for part in columns[1:-1])
    if "@" not in identity:
        return None
    name, marketplace = identity.split("@", 1)
    name = name.strip()
    marketplace = marketplace.strip()
    if not name or not marketplace:
        return None
    return name, marketplace, status, version


def _list_matches(
    name: str,
    marketplace: str,
    version: str,
    text: str,
    returncode: int,
) -> CompilerIssue | None:
    """Hard-gate `codex plugin list`. None means the row matched; else a block issue."""
    if returncode != 0:
        return _issue(
            "install-list-failed",
            "codex plugin list failed with returncode {0}".format(returncode),
            "",
        )
    expected_id = "{0}@{1}".format(name, marketplace)
    matched: tuple[str, str, str, str] | None = None
    version_column: int | None = None
    for raw_line in text.splitlines():
        if version_column is None:
            header_index = _list_header_version_index(raw_line)
            if header_index is not None:
                version_column = header_index
                continue
        parsed = _parse_plugin_list_line(raw_line, version_column=version_column)
        if parsed is None:
            continue
        row_name, row_market, _status, _version = parsed
        if row_name == name and row_market == marketplace:
            matched = parsed
            break
    if matched is None:
        return _issue(
            "install-plugin-not-listed",
            "codex plugin list missing exact row {0}".format(expected_id),
            "",
        )
    _row_name, _row_market, status, row_version = matched
    status_tokens = {
        token.strip().lower()
        for token in status.replace(",", " ").split()
        if token.strip()
    }
    if "installed" not in status_tokens or "enabled" not in status_tokens:
        return _issue(
            "install-plugin-not-enabled",
            "codex plugin list row {0} is not installed and enabled: {1}".format(
                expected_id, status
            ),
            "",
        )
    if version and row_version != version:
        return _issue(
            "install-version-mismatch",
            "codex plugin list version {0} != manifest {1}".format(row_version, version),
            "",
        )
    return None


def _run(
    command: Sequence[str],
    runner: object | None,
    isolated_home: Path | None = None,
) -> CommandResult:
    env = os.environ.copy()
    if isolated_home is not None:
        env["CODEX_HOME"] = str(isolated_home)
    if runner is not None:
        try:
            return runner(command, env=env)
        except TypeError:
            return runner(command)
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def install(request: InstallRequest) -> InstallResult:
    if request.current_user and request.install_root is not None:
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=(
                _issue(
                    "install-mode-conflict",
                    "Codex install accepts either --install-root or --current-user, not both",
                    "",
                ),
            ),
        )
    if not request.current_user and request.install_root is None:
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=(
                _issue(
                    "install-root-required",
                    "Codex install requires an isolated --install-root",
                    "",
                ),
            ),
        )
    source = request.source or request.published_root
    if source is None or not source.is_dir():
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=(
                _issue(
                    "install-source-missing",
                    "published Codex tree is required",
                    "" if source is None else str(source),
                ),
            ),
        )
    if request.current_user:
        install_root = Path.home() / ".codex-plugin-marketplaces" / "cursor-plugin-codex"
    else:
        assert request.install_root is not None
        install_root = request.install_root.expanduser()
    name, version = _identity(source)
    try:
        dest = _resolve_plugin_dest(install_root, name)
    except (ValueError, InvalidPluginNameError) as exc:
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=(
                _issue(
                    "install-unsafe-plugin-name",
                    str(exc),
                    name,
                ),
            ),
        )
    source_issues = tuple(structure_issues(source, str(CODEX_MANIFEST_RELATIVE)))
    if any(issue.severity == "block" for issue in source_issues):
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=source_issues,
        )
    reinstall = dest.exists()
    candidate = dest.parent / ".{0}.install-tmp".format(dest.name)
    if candidate.exists():
        shutil.rmtree(candidate)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, candidate)
    if reinstall:
        version = with_cachebuster(version, default_cachebuster())
        _write_installed_version(candidate, version)
    candidate_issues = tuple(structure_issues(candidate, str(CODEX_MANIFEST_RELATIVE)))
    if any(issue.severity == "block" for issue in candidate_issues):
        shutil.rmtree(candidate, ignore_errors=True)
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=candidate_issues,
        )
    report = validate_codex_staged(candidate, runner=request.runner)
    target_issues = _from_validation_issues(report.issues)
    if any(issue.severity == "block" for issue in target_issues):
        shutil.rmtree(candidate, ignore_errors=True)
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=candidate_issues + target_issues,
        )
    try:
        previous_names = marketplace_registered_names(install_root)
    except MarketplaceError as exc:
        shutil.rmtree(candidate, ignore_errors=True)
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=None,
            issues=(
                _issue(
                    "install-marketplace-invalid",
                    str(exc),
                    str(install_root / ".agents" / "plugins" / "marketplace.json"),
                ),
            ),
        )
    if dest.exists():
        shutil.rmtree(dest)
    candidate.rename(dest)
    try:
        upsert_marketplace_plugin(install_root, name)
    except MarketplaceError as exc:
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=str(dest),
            issues=(
                _issue(
                    "install-marketplace-invalid",
                    str(exc),
                    str(install_root / ".agents" / "plugins" / "marketplace.json"),
                ),
            ),
        )

    issues: list[CompilerIssue] = list(candidate_issues)
    issues.extend(
        issue for issue in target_issues if issue.severity != "block"
    )
    runner = request.runner
    if runner is None and shutil.which("codex") is None:
        issues.append(
            _issue(
                "install-cli-missing",
                "codex CLI not found; skipped plugin add and did not write default user config",
                str(install_root),
            )
        )
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=str(dest),
            issues=tuple(issues),
        )

    isolated_home: Path | None = None
    if not request.current_user:
        isolated_home = install_root / ".codex-home"
        if runner is None:
            isolated_home.mkdir(parents=True, exist_ok=True)
    market_add = _run(
        ("codex", "plugin", "marketplace", "add", str(install_root)),
        runner,
        isolated_home,
    )
    if market_add.returncode != 0:
        issues.append(
            _issue(
                "install-plugin-add-failed",
                "codex plugin marketplace add failed: {0}".format(
                    (market_add.stderr or market_add.stdout).strip()
                ),
                str(install_root),
            )
        )
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=str(dest),
            issues=tuple(issues),
        )
    payload = json.loads((install_root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    market_name = str(payload.get("name", "")).strip() or "cursor-plugin-codex"
    plugin_add = _run(
        ("codex", "plugin", "add", name, "--marketplace", market_name),
        runner,
        isolated_home,
    )
    if plugin_add.returncode != 0:
        issues.append(
            _issue(
                "install-plugin-add-failed",
                "codex plugin add failed: {0}".format(
                    (plugin_add.stderr or plugin_add.stdout).strip()
                ),
                str(dest),
            )
        )
        return InstallResult(
            ok=False,
            adapter_id="codex",
            installed_root=str(dest),
            issues=tuple(issues),
        )
    listed = _run(("codex", "plugin", "list"), runner, isolated_home)
    text = "{0}\n{1}".format(listed.stdout, listed.stderr)
    list_issue = _list_matches(name, market_name, version, text, listed.returncode)
    sibling_issues: list[CompilerIssue] = []
    if list_issue is None:
        for other in previous_names:
            if other == name:
                continue
            other_issue = _list_matches(
                other, market_name, "", text, listed.returncode
            )
            if other_issue is not None:
                sibling_issues.append(
                    _issue(
                        other_issue.code,
                        other_issue.message,
                        str(install_root / "plugins" / other),
                    )
                )
    if list_issue is not None:
        issues.append(
            _issue(list_issue.code, list_issue.message, str(dest))
        )
    elif sibling_issues:
        issues.extend(sibling_issues)
    else:
        issues.append(
            _issue(
                "install-list-ok",
                "codex plugin list saw {0} {1}".format(name, version).strip(),
                str(dest),
                severity="info",
            )
        )
        issues.append(
            _issue(
                "install-new-task-hint",
                "open a new Codex task to load the installed plugin content",
                str(dest),
                severity="info",
            )
        )
    return InstallResult(
        ok=result_ok(tuple(issues)),
        adapter_id="codex",
        installed_root=str(dest),
        issues=tuple(issues),
    )
