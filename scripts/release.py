#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包并覆盖安装 agent-plugins 仓库下的 Cursor / Codex / Claude Code 插件。

用法：
    python3 scripts/release.py targets                       # 列出适配器 id 与启用状态
    python3 scripts/release.py <plugin-dir-name>            # 编译 Cursor 正式产物
    python3 scripts/release.py build <plugin-dir-name>    # 与无子命令相同
    python3 scripts/release.py install <plugin> --target cursor|codex|claude-code --install-root <dir>
    python3 scripts/release.py <plugin-dir-name> --install  # 仅 Cursor，等于 install --target cursor
    python3 scripts/release.py <plugin-dir-name> --target all --validate-only
    python3 scripts/release.py <plugin-dir-name> --target claude-code

版本号纪律（给 Agent / 维护者）：
    --install 只覆盖安装，不要求也不应顺手改 plugin.json 的 version。
    只有用户明确说「发版 / 发布 / release」时才升 .cursor-plugin/plugin.json 的 version。
"""
from __future__ import annotations

import sys
import importlib.util


def _bootstrap_require_python_310():
    """Load runtime.py by file so Python 3.9 never parses plugin_compiler/__init__.py."""
    runtime_path = __file__.replace("\\", "/")
    if runtime_path.endswith("release.py"):
        runtime_path = runtime_path[: -len("release.py")] + "plugin_compiler/runtime.py"
    spec = importlib.util.spec_from_file_location(
        "_plugin_compiler_runtime_gate",
        runtime_path,
    )
    if spec is None or spec.loader is None:
        sys.stderr.write("无法加载 Python 版本门禁：{0}\n".format(runtime_path))
        raise SystemExit(1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.require_python_310()


_bootstrap_require_python_310()

import argparse
import json
import re
import shutil
from pathlib import Path

import plugin_packaging
from plugin_compiler import BuildLayout, compile_plugin
from plugin_compiler.layout_extras import extra_output_dir_names
from plugin_compiler.zip_tree import zip_plugin_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
# 测试可注入 CommandRunner，避免依赖本机官方校验 CLI。
COMPILE_RUNNER = None

# plugin.json 的 name 必须是 kebab-case（小写字母、数字、连字符，不以连字符开头或结尾）
KEBAB_CASE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# 目录名级排除：运行产出、版本控制、打包产物、缓存
EXCLUDE_DIR_NAMES = {
    ".git",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".superpowers",
    "outputs",
    "packaging",
    ".codex-plugin",
} | set(extra_output_dir_names())
# 文件名后缀级排除：字节码与体积归档（如 92MB 的 nativefier release 包）
EXCLUDE_SUFFIXES = {".pyc", ".zip", ".gz", ".tgz", ".xz", ".bz2", ".7z"}
# 插件根目录级文件排除：源码侧生成物，不应进入 Cursor 包
EXCLUDE_ROOT_FILE_NAMES = {"runtime-host.json"}

DEFAULT_INSTALL_ROOT = Path.home() / ".cursor" / "plugins" / "local"


def read_manifest(plugin_dir: Path) -> dict:
    manifest = plugin_dir / ".cursor-plugin" / "plugin.json"
    if not manifest.is_file():
        raise RuntimeError("缺少插件清单：{0}".format(manifest))
    return json.loads(manifest.read_text(encoding="utf-8"))


def plugin_name(plugin_dir: Path) -> str:
    name = str(read_manifest(plugin_dir).get("name", "")).strip()
    if not name:
        raise RuntimeError("plugin.json 缺少 name 字段：{0}".format(plugin_dir))
    if not KEBAB_CASE_NAME.match(name):
        raise RuntimeError(
            "plugin.json 的 name 必须是 kebab-case（小写字母、数字、连字符，不以连字符开头或结尾）：{0}".format(name)
        )
    return name


def plugin_version(plugin_dir: Path) -> str:
    return str(read_manifest(plugin_dir).get("version", "0.0.0"))


def should_skip(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if relative.parts[:2] == ("docs", "superpowers"):
        return True
    if len(relative.parts) == 1 and relative.name in EXCLUDE_ROOT_FILE_NAMES:
        return True
    for part in relative.parts:
        if part in EXCLUDE_DIR_NAMES:
            return True
    return path.suffix in EXCLUDE_SUFFIXES


def _zip_plugin_tree(tree: Path, archive_root_name: str, dest_zip: Path) -> Path:
    """把已通过校验的插件树打成顶层目录为 manifest name 的 zip。"""
    return zip_plugin_tree(tree, archive_root_name, dest_zip)


def _compile_failure_message(result, report_dir: Path) -> str:
    if result.published_roots:
        return "编译失败；已保留成功发布的目标。报告：{0}".format(report_dir)
    return "编译失败；本轮没有新的正式包。报告：{0}".format(report_dir)


def pack(plugin_dir: Path, target: str = "cursor") -> Path:
    """Cursor 兼容入口：只编译 Cursor，zip 写到 build/outputs/cursor/。"""
    if target != "cursor":
        raise RuntimeError("unsupported pack target: {0}".format(target))
    plugin_dir = plugin_dir.resolve()
    name = plugin_name(plugin_dir)
    result = compile_plugin(
        plugin_dir=plugin_dir,
        repo_root=REPO_ROOT,
        targets=("cursor",),
        runner=COMPILE_RUNNER,
        source_format="cursor",
    )
    if not result.ok:
        raise RuntimeError(
            _compile_failure_message(
                result,
                BuildLayout.from_repo(REPO_ROOT).report_dir(result.plugin_name),
            )
        )
    published = Path(result.published_roots["cursor"])
    out_dir = BuildLayout.from_repo(REPO_ROOT).output("cursor", name).parent
    out = out_dir / "{0}-{1}.zip".format(name, plugin_version(plugin_dir))
    return _zip_plugin_tree(published, name, out)


def stage_codex(plugin_dir: Path, staging_root: Path) -> Path:
    """在 staging_root 下写入 plugins/<name>/ 与更新 marketplace.json。"""
    upsert_marketplace_plugin = importlib.import_module(
        "plugin_targets.codex.marketplace"
    ).upsert_marketplace_plugin

    plugin_dir = plugin_dir.resolve()
    name = plugin_name(plugin_dir)
    dest = staging_root / "plugins" / name
    result = compile_plugin(
        plugin_dir=plugin_dir,
        repo_root=REPO_ROOT,
        targets=("codex",),
        validate_only=False,
        runner=COMPILE_RUNNER,
    )
    if not result.ok:
        raise RuntimeError(
            _compile_failure_message(
                result,
                BuildLayout.from_repo(REPO_ROOT).report_dir(result.plugin_name),
            )
        )
    published = Path(result.published_roots["codex"])
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(published, dest)
    upsert_marketplace_plugin(staging_root, name)
    return dest


def install(zip_path: Path, install_root: Path | None = None) -> Path:
    """把发布包覆盖安装到安装根，返回安装后的插件目录。

    覆盖语义：安装态严格等于发布包内容（源码中已删除的文件会被删除），
    但各 skill 的产出目录原地保留，不被清空。
    """
    module = importlib.import_module("plugin_targets.cursor.install")
    return module.install_from_zip(zip_path, install_root=install_root)


def _resolve_targets(args: argparse.Namespace) -> tuple[str, ...]:
    from plugin_targets.registry import enabled_ids, spec_for

    if args.target == "all":
        ids = enabled_ids()
        if not ids:
            raise RuntimeError("no enabled adapters")
        return ids
    spec = spec_for(args.target)
    if not spec.enabled:
        raise RuntimeError("adapter disabled: {0}".format(args.target))
    return (spec.adapter_id,)


def _cmd_targets() -> int:
    from plugin_targets.registry import registered_specs

    for spec in registered_specs():
        state = "enabled" if spec.enabled else "disabled"
        print("{0}\t{1}".format(spec.adapter_id, state))
    return 0


def _flag_value(argv: list[str], flag: str) -> str | None:
    prefix = flag + "="
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(prefix):
            return item[len(prefix) :]
    return None


def _cmd_install(argv: list[str]) -> int:
    from plugin_targets.registry import load_adapter, registered_specs, spec_for
    from plugin_targets.source import SOURCE_FORMATS, SourceResolutionError, resolve_source_adapter
    from plugin_targets.spi import InstallRequest

    parser = argparse.ArgumentParser(description="覆盖安装 Cursor / Codex / Claude Code 插件")
    parser.add_argument("plugin", help="仓库内的插件目录名，例如 example-plugin")
    parser.add_argument(
        "--target",
        choices=tuple(spec.adapter_id for spec in registered_specs()),
        default="cursor",
        help="安装宿主，默认 cursor",
    )
    parser.add_argument(
        "--source-format",
        choices=SOURCE_FORMATS,
        default="auto",
        help="源码清单格式：auto|cursor|codex|claude-code，默认 auto",
    )
    parser.add_argument(
        "--install-root", default=None, help="覆盖默认安装根（调试与测试用）"
    )
    parser.add_argument(
        "--current-user",
        action="store_true",
        help="Codex：装到当前用户 marketplace，不覆盖 CODEX_HOME",
    )
    args = parser.parse_args(argv)

    plugin_dir = REPO_ROOT / args.plugin
    try:
        resolve_source_adapter(plugin_dir, args.source_format)
    except SourceResolutionError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        spec = spec_for(args.target)
        if not spec.enabled:
            raise RuntimeError("adapter disabled: {0}".format(args.target))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        message = str(exc)
        if message.startswith("adapter disabled") or message.startswith(
            "unknown adapter"
        ):
            return 2
        return 1

    if args.current_user and args.install_root:
        print(
            "--current-user 与 --install-root 不能同时使用（install-mode-conflict）",
            file=sys.stderr,
        )
        return 2
    if args.current_user and args.target != "codex":
        print(
            "--current-user 仅支持 --target codex",
            file=sys.stderr,
        )
        return 2
    if args.target == "codex" and not args.current_user and not args.install_root:
        print(
            "codex 安装必须传 --install-root 或 --current-user",
            file=sys.stderr,
        )
        return 2
    if args.target == "claude-code" and not args.install_root:
        print(
            "claude-code 安装必须传 --install-root（隔离根，避免写默认用户配置）",
            file=sys.stderr,
        )
        return 2

    layout = BuildLayout.from_repo(REPO_ROOT)

    try:
        result = compile_plugin(
            plugin_dir=plugin_dir,
            repo_root=REPO_ROOT,
            targets=(args.target,),
            runner=COMPILE_RUNNER,
            source_format=args.source_format,
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    report_dir = layout.report_dir(result.plugin_name)
    if not result.ok:
        print(_compile_failure_message(result, report_dir), file=sys.stderr)
        return 1
    published = Path(result.published_roots[args.target])
    print("已发布 {0}：{1}".format(args.target, published))

    install_root = Path(args.install_root).expanduser() if args.install_root else None
    try:
        adapter = load_adapter(args.target)
        install_result = adapter.install(
            InstallRequest(
                published_root=published,
                install_root=install_root,
                runner=COMPILE_RUNNER,
                current_user=args.current_user,
            )
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    if install_result.suggested_argv:
        print("建议启动：{0}".format(" ".join(install_result.suggested_argv)))
    if not install_result.ok:
        for issue in install_result.issues:
            if issue.severity == "block":
                print("{0}: {1}".format(issue.code, issue.message), file=sys.stderr)
        return 1
    print("已覆盖安装：{0}".format(install_result.installed_root))
    if args.target == "cursor":
        print(
            "提示：skill 清单是会话级快照，请【新建会话】后验证；Reload Window 不足。"
        )
    return 0


def _cmd_build(argv: list[str]) -> int:
    from plugin_targets.registry import registered_specs
    from plugin_targets.source import SOURCE_FORMATS, SourceResolutionError, resolve_source_adapter

    parser = argparse.ArgumentParser(
        description="编译 Cursor / Codex / Claude Code 插件（不改宿主配置）"
    )
    parser.add_argument("plugin", help="仓库内的插件目录名，例如 example-plugin")
    parser.add_argument(
        "--target",
        choices=tuple(spec.adapter_id for spec in registered_specs()) + ("all",),
        default="cursor",
        help="发布宿主，默认 cursor",
    )
    parser.add_argument(
        "--source-format",
        choices=SOURCE_FORMATS,
        default="auto",
        help="源码清单格式：auto|cursor|codex|claude-code，默认 auto",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验并写报告，不写 build/outputs/",
    )
    parser.add_argument(
        "--allow-overlap",
        action="store_true",
        help="将语义重叠（门 B）降为警告并继续暂存；路径冲突仍失败",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="兼容别名，不再额外编译 Codex。",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        dest="codex_zip",
        help="从已发布的 Codex 插件树生成辅助 zip，写入 build/outputs/codex/",
    )
    parser.add_argument(
        "--install-root", default=None, help="build 忽略此参数；安装请用 install 子命令"
    )
    parser.add_argument(
        "--staging-root",
        default=None,
        help="已停用：与全部产物只写仓库根 build/ 冲突",
    )
    args = parser.parse_args(argv)
    _ = args.install_root  # accepted for compatibility; build must not install

    if args.staging_root is not None:
        print(
            "--staging-root 已停用：全部产物只写仓库根 build/。"
            "请删除该参数，Codex 中间产物见 build/intermediates/codex/。",
            file=sys.stderr,
        )
        return 2

    plugin_dir = REPO_ROOT / args.plugin
    try:
        resolve_source_adapter(plugin_dir, args.source_format)
    except SourceResolutionError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        targets = _resolve_targets(args)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        message = str(exc)
        if message.startswith("adapter disabled") or message.startswith(
            "unknown adapter"
        ):
            return 2
        return 1

    if args.codex_zip and "codex" not in targets:
        print(
            "--zip 需要 Codex 目标（使用 --target codex / all）",
            file=sys.stderr,
        )
        return 2

    try:
        result = compile_plugin(
            plugin_dir=plugin_dir,
            repo_root=REPO_ROOT,
            targets=targets,
            validate_only=args.validate_only,
            runner=COMPILE_RUNNER,
            allow_overlap=args.allow_overlap,
            want_zip=bool(args.codex_zip),
            source_format=args.source_format,
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    layout = BuildLayout.from_repo(REPO_ROOT)
    report_dir = layout.report_dir(result.plugin_name)
    if not result.ok:
        print(_compile_failure_message(result, report_dir), file=sys.stderr)
        return 1

    if args.validate_only:
        print("校验报告：{0}".format(report_dir))
        return 0

    try:
        for adapter_id in targets:
            published = result.published_roots.get(adapter_id)
            if published:
                print("已发布 {0}：{1}".format(adapter_id, published))
        if args.codex_zip:
            archive_text = result.archives.get("codex")
            if not archive_text:
                raise RuntimeError("Codex zip was requested but not produced")
            archive = Path(archive_text)
            if not archive.is_file():
                raise RuntimeError(
                    "Codex zip was requested but not produced: {0}".format(archive)
                )
            print("已打包 Codex zip（辅助归档）：{0}".format(archive))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "targets":
        return _cmd_targets()
    if argv and argv[0] == "build":
        if "--install" in argv[1:]:
            print(
                "build 不改宿主配置；请使用：python3 scripts/release.py install "
                "<plugin> --target … --install-root <dir>",
                file=sys.stderr,
            )
            return 2
        return _cmd_build(argv[1:])
    if argv and argv[0] == "install":
        return _cmd_install(argv[1:])
    if "--install" in argv:
        target = _flag_value(argv, "--target") or "cursor"
        if target != "cursor":
            print(
                "请改用子命令：python3 scripts/release.py install <plugin> "
                "--target {0} --install-root <dir>".format(target),
                file=sys.stderr,
            )
            return 2
        filtered = [item for item in argv if item != "--install"]
        return _cmd_install(filtered)
    return _cmd_build(argv)


if __name__ == "__main__":
    raise SystemExit(main())

