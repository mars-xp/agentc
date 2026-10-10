"""Cursor overlay install. Tests must pass an isolated install_root."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from plugin_compiler.model import CompilerIssue, Target
from plugin_compiler.names import InvalidPluginNameError, assert_plugin_name_for_targets
from plugin_compiler.paths import require_inside, require_safe_plugin_name
from plugin_compiler.zip_tree import extract_zip_filtered, zip_member_destination, zip_plugin_tree
from plugin_compiler.layout_extras import extra_output_dir_names
from plugin_targets.cursor.manifest import CURSOR_MANIFEST_RELATIVE
from plugin_targets.install_checks import result_ok, structure_issues
from plugin_targets.spi import InstallRequest, InstallResult

DEFAULT_INSTALL_ROOT = Path.home() / ".cursor" / "plugins" / "local"
RSYNC_EXCLUDES = (
    "outputs/",
    ".deps/",
    "*/.deps/",
    "*/config/*.local",
    "*/config/*.local.env",
) + tuple("{0}/".format(name) for name in sorted(extra_output_dir_names()))


def _issue(code: str, message: str, path: str) -> CompilerIssue:
    return CompilerIssue(severity="block", code=code, path=path, message=message)


def _raw_cursor_name(plugin_dir: Path) -> str:
    manifest = plugin_dir / CURSOR_MANIFEST_RELATIVE
    if not manifest.is_file():
        raise RuntimeError("缺少插件清单：{0}".format(manifest))
    name = str(json.loads(manifest.read_text(encoding="utf-8")).get("name", "")).strip()
    if not name:
        raise RuntimeError("plugin.json 缺少 name 字段：{0}".format(plugin_dir))
    return name


def _resolve_plugin_dest(install_root: Path, name: str) -> Path:
    require_safe_plugin_name(name)
    assert_plugin_name_for_targets(name, (Target.CURSOR,))
    root = install_root.expanduser()
    dest = root / name
    require_inside(root.resolve(), dest.resolve())
    return dest


def _read_cursor_name(plugin_dir: Path) -> str:
    name = _raw_cursor_name(plugin_dir)
    assert_plugin_name_for_targets(name, (Target.CURSOR,))
    return name


def _is_excluded(relative: Path) -> bool:
    parts = tuple(
        part for part in relative.as_posix().replace("\\", "/").split("/") if part
    )
    if ".deps" in parts:
        return True
    if len(parts) >= 2 and parts[-2] == "config" and parts[-1].endswith(".local"):
        return True
    if len(parts) >= 2 and parts[-2] == "config" and parts[-1].endswith(".local.env"):
        return True
    skipped = {"outputs"} | set(extra_output_dir_names())
    return any(part in skipped for part in parts)


def _mirror_install(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    source_files = {
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and not _is_excluded(path.relative_to(source))
    }
    for relative in sorted(source_files):
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, dest)

    for path in sorted(target.rglob("*"), reverse=True):
        relative = path.relative_to(target)
        if _is_excluded(relative):
            continue
        if path.is_file() and relative not in source_files:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            if not _is_excluded(relative):
                path.rmdir()


def install_from_zip(zip_path: Path, install_root: Path | None = None) -> Path:
    """把发布包覆盖安装到安装根，返回安装后的插件目录。"""
    zip_path = zip_path.resolve()
    root = (install_root or DEFAULT_INSTALL_ROOT).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    use_rsync = shutil.which("rsync") is not None

    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        with zipfile.ZipFile(zip_path) as zf:
            extract_zip_filtered(zf, staging_path)
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0o7777
                extracted = zip_member_destination(staging_path, info.filename)
                if mode and extracted.is_file():
                    try:
                        extracted.chmod(mode)
                    except OSError:
                        pass
        entries = list(Path(staging).iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise RuntimeError("发布包顶层应只有一个插件目录：{0}".format(zip_path))
        source = entries[0]
        try:
            name = _read_cursor_name(source)
            target = _resolve_plugin_dest(root, name)
        except (ValueError, InvalidPluginNameError) as exc:
            raise RuntimeError(str(exc)) from exc
        target.mkdir(parents=True, exist_ok=True)
        if use_rsync:
            command = ["rsync", "-a", "--delete"]
            for pattern in RSYNC_EXCLUDES:
                command.extend(["--exclude", pattern])
            command.extend(["{0}/".format(source), "{0}/".format(target)])
            subprocess.run(command, check=True)
        else:
            _mirror_install(source, target)
    return target


def _name_from_zip(zip_path: Path) -> str:
    with tempfile.TemporaryDirectory() as staging:
        with zipfile.ZipFile(zip_path) as zf:
            extract_zip_filtered(zf, Path(staging))
        entries = list(Path(staging).iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise RuntimeError("发布包顶层应只有一个插件目录：{0}".format(zip_path))
        return _read_cursor_name(entries[0])


def install(request: InstallRequest) -> InstallResult:
    source = request.source or request.published_root
    if source is None:
        issues = (
            CompilerIssue(
                severity="block",
                code="install-source-missing",
                path="",
                message="published_root or source is required",
            ),
        )
        return InstallResult(
            ok=False,
            adapter_id="cursor",
            installed_root=None,
            issues=issues,
        )
    install_root = (request.install_root or DEFAULT_INSTALL_ROOT).expanduser()
    try:
        if source.is_file() and source.suffix.lower() == ".zip":
            name = _name_from_zip(source)
            _resolve_plugin_dest(install_root, name)
            installed = install_from_zip(source, install_root=request.install_root)
        else:
            name = _read_cursor_name(source)
            _resolve_plugin_dest(install_root, name)
            with tempfile.TemporaryDirectory() as tmp:
                zip_path = Path(tmp) / "{0}.zip".format(name)
                zip_plugin_tree(source, name, zip_path)
                installed = install_from_zip(zip_path, install_root=request.install_root)
    except (ValueError, InvalidPluginNameError) as exc:
        return InstallResult(
            ok=False,
            adapter_id="cursor",
            installed_root=None,
            issues=(
                _issue(
                    "install-unsafe-plugin-name",
                    str(exc),
                    "",
                ),
            ),
        )
    issues = structure_issues(installed, str(CURSOR_MANIFEST_RELATIVE))
    return InstallResult(
        ok=result_ok(issues),
        adapter_id="cursor",
        installed_root=str(installed),
        issues=issues,
    )
