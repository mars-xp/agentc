"""Claude Code --plugin-dir install. No persistent marketplace."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from plugin_compiler.model import CompilerIssue, Target
from plugin_compiler.names import InvalidPluginNameError, assert_plugin_name_for_targets
from plugin_compiler.paths import require_inside, require_safe_plugin_name
from plugin_compiler.zip_tree import extract_zip_filtered
from plugin_targets.claude_code.manifest import CLAUDE_MANIFEST_RELATIVE
from plugin_targets.install_checks import read_manifest, result_ok, structure_issues
from plugin_targets.spi import InstallRequest, InstallResult


def _issue(code: str, message: str, path: str) -> CompilerIssue:
    return CompilerIssue(severity="block", code=code, path=path, message=message)


def _identity_from_tree(tree: Path) -> str:
    manifest = read_manifest(tree, str(CLAUDE_MANIFEST_RELATIVE))
    name = str(manifest.get("name", "")).strip()
    if not name:
        raise RuntimeError("Claude plugin.json 缺少 name：{0}".format(tree))
    return name


def _resolve_plugin_dest(install_root: Path, name: str) -> Path:
    require_safe_plugin_name(name)
    assert_plugin_name_for_targets(name, (Target.CLAUDE_CODE,))
    root = install_root.expanduser()
    dest = root / name
    require_inside(root.resolve(), dest.resolve())
    return dest


def _copy_tree(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)


def _extract_zip(zip_path: Path, install_root: Path) -> Path:
    with tempfile.TemporaryDirectory() as staging:
        with zipfile.ZipFile(zip_path) as zf:
            extract_zip_filtered(zf, Path(staging))
        entries = [path for path in Path(staging).iterdir() if path.name]
        if len(entries) != 1 or not entries[0].is_dir():
            raise RuntimeError("发布包顶层应只有一个插件目录：{0}".format(zip_path))
        source = entries[0]
        name = _identity_from_tree(source)
        target = _resolve_plugin_dest(install_root, name)
        _copy_tree(source, target)
        return target


def install(request: InstallRequest) -> InstallResult:
    if request.install_root is None:
        return InstallResult(
            ok=False,
            adapter_id="claude-code",
            installed_root=None,
            issues=(
                _issue(
                    "install-root-required",
                    "Claude Code install requires an isolated --install-root",
                    "",
                ),
            ),
        )
    source = request.source or request.published_root
    if source is None:
        return InstallResult(
            ok=False,
            adapter_id="claude-code",
            installed_root=None,
            issues=(
                _issue(
                    "install-source-missing",
                    "published Claude tree or ZIP is required",
                    "",
                ),
            ),
        )
    install_root = request.install_root.expanduser()
    try:
        if source.is_file() and source.suffix.lower() == ".zip":
            dest = _extract_zip(source, install_root)
        else:
            name = _identity_from_tree(source)
            dest = _resolve_plugin_dest(install_root, name)
            _copy_tree(source, dest)
    except (ValueError, InvalidPluginNameError) as exc:
        return InstallResult(
            ok=False,
            adapter_id="claude-code",
            installed_root=None,
            issues=(
                _issue(
                    "install-unsafe-plugin-name",
                    str(exc),
                    "",
                ),
            ),
        )
    issues = structure_issues(dest, str(CLAUDE_MANIFEST_RELATIVE))
    argv = ("claude", "--plugin-dir", str(dest))
    return InstallResult(
        ok=result_ok(issues),
        adapter_id="claude-code",
        installed_root=str(dest),
        issues=issues,
        suggested_argv=argv,
    )
