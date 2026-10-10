from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .model import Target


def require_safe_plugin_name(plugin_name: str) -> str:
    """Reject plugin names that could escape a single build/ path component."""
    if not plugin_name or plugin_name in (".", ".."):
        raise ValueError("plugin_name must be a non-empty path component")
    if Path(plugin_name).is_absolute() or PureWindowsPath(plugin_name).is_absolute():
        raise ValueError("plugin_name must not be absolute")
    if PureWindowsPath(plugin_name).drive:
        raise ValueError("plugin_name must not be drive-qualified")
    if "/" in plugin_name or "\\" in plugin_name:
        raise ValueError("plugin_name must not contain path separators")
    return plugin_name


def require_inside(root: Path, candidate: Path) -> Path:
    """Return *candidate* resolved, if and only if it stays under *root*."""
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    resolved_candidate.relative_to(resolved_root)
    return resolved_candidate


@dataclass(frozen=True)
class BuildLayout:
    root: Path

    @classmethod
    def from_repo(cls, repo_root: Path) -> "BuildLayout":
        return cls(root=repo_root.resolve() / "build")

    def intermediate(self, adapter_id: str, plugin_name: str) -> Path:
        safe_name = require_safe_plugin_name(plugin_name)
        return self.root / "intermediates" / adapter_id / safe_name

    def output(self, adapter_id: str, plugin_name: str) -> Path:
        safe_name = require_safe_plugin_name(plugin_name)
        return self.root / "outputs" / adapter_id / safe_name

    def report_dir(self, plugin_name: str) -> Path:
        safe_name = require_safe_plugin_name(plugin_name)
        return self.root / "reports" / safe_name

    def temporary(self, plugin_name: str) -> Path:
        safe_name = require_safe_plugin_name(plugin_name)
        return self.root / "tmp" / safe_name


def clear_plugin_build(layout: BuildLayout, plugin_name: str) -> None:
    from plugin_targets.registry import enabled_ids

    require_safe_plugin_name(plugin_name)
    for adapter_id in enabled_ids():
        shutil.rmtree(layout.intermediate(adapter_id, plugin_name), ignore_errors=True)
        shutil.rmtree(layout.output(adapter_id, plugin_name), ignore_errors=True)
    shutil.rmtree(layout.report_dir(plugin_name), ignore_errors=True)
    shutil.rmtree(layout.temporary(plugin_name), ignore_errors=True)


def clear_target_intermediate(
    layout: BuildLayout, target: Target, plugin_name: str
) -> None:
    shutil.rmtree(layout.intermediate(target, plugin_name), ignore_errors=True)


def clear_target_output(
    layout: BuildLayout, target: Target, plugin_name: str
) -> None:
    shutil.rmtree(layout.output(target, plugin_name), ignore_errors=True)


def publish_directory(source: Path, destination: Path) -> None:
    """Publish *source* into *destination* with rollback-safe directory replacement.

    When *destination* already exists, it is renamed aside before the pending
    copy is promoted. If promotion fails, the previous contents are restored.
    A very short window may exist where *destination* is missing; callers accept
    this trade-off. Pending scratch directories are removed on failure.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.parent / (
        ".{0}.pending-{1}".format(destination.name, uuid.uuid4().hex)
    )
    old = destination.parent / (
        ".{0}.old-{1}".format(destination.name, uuid.uuid4().hex)
    )
    try:
        shutil.copytree(source, pending)
        if destination.exists():
            os.replace(destination, old)
        try:
            os.replace(pending, destination)
        except BaseException:
            if old.exists() and not destination.exists():
                os.replace(old, destination)
            raise
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        if old.exists() and not destination.exists():
            os.replace(old, destination)
        shutil.rmtree(old, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
