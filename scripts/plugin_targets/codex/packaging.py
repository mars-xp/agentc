from __future__ import annotations

import shutil
from pathlib import Path

from plugin_compiler.cursor_runtime_needles import audit_cursor_runtime_in_files
from plugin_compiler.layout_extras import extra_root_script_names
from plugin_packaging import (
    AuditIssue,
    allowed_skill_dirs,
    audit_skill_command_paths,
    audit_skill_frontmatter_names,
    format_blocking_audits,
    validate_skills_config,
)

CODEX_CURSOR_ONLY_TOP_DIRS = {".cursor-plugin", "rules", "commands", "docs"}
CODEX_CURSOR_ONLY_ROOT_FILE_NAMES = {"README.md", "使用说明.md"}
CODEX_CURSOR_ONLY_ROOT_SCRIPT_NAMES = extra_root_script_names()


def should_skip_codex_pack_path(parts: tuple[str, ...], name: str) -> bool:
    if parts and parts[0] in CODEX_CURSOR_ONLY_TOP_DIRS:
        return True
    if len(parts) >= 2 and parts[0] == "scripts" and parts[1] == "tests":
        return True
    if len(parts) == 1 and name in CODEX_CURSOR_ONLY_ROOT_FILE_NAMES:
        return True
    return (
        len(parts) == 2
        and parts[0] == "scripts"
        and parts[1] in CODEX_CURSOR_ONLY_ROOT_SCRIPT_NAMES
    )


def audit_kiwi_sp_reverse_refs(plugin_dir: Path, host: str) -> list[AuditIssue]:
    """检查指定宿主可见技能是否反向引用 kiwi-sp。"""
    if host not in ("codex", "claude-code"):
        return []

    issues: list[AuditIssue] = []
    skills_root = plugin_dir / "skills"
    for skill_name in allowed_skill_dirs(plugin_dir, host):
        if skill_name.lower() == "kiwi-sp":
            continue
        skill_dir = skills_root / skill_name
        for path in sorted(item for item in skill_dir.rglob("*") if item.is_file()):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if "kiwi-sp" in line.lower():
                    issues.append(
                        AuditIssue(
                            severity="block",
                            path=path.relative_to(plugin_dir).as_posix(),
                            line=line_number,
                            message="Codex-visible skill must not reference kiwi-sp",
                        )
                    )
    return issues


def audit_packed_cursor_runtime(plugin_dir: Path, host: str) -> list[AuditIssue]:
    """检查将进入指定宿主包的文件中的 Cursor 运行时/布局依赖。"""
    from plugin_packaging import iter_pack_files

    plugin_dir = plugin_dir.resolve()
    return audit_cursor_runtime_in_files(
        plugin_dir, iter_pack_files(plugin_dir, host)
    )


def audit_codex_cursor_runtime(plugin_dir: Path) -> list[AuditIssue]:
    """检查全部 Codex 打包文件中的 Cursor 依赖。"""
    return audit_packed_cursor_runtime(plugin_dir, "codex")


def collect_codex_host_audits(plugin_dir: Path) -> list[AuditIssue]:
    issues = audit_kiwi_sp_reverse_refs(plugin_dir, "codex")
    issues.extend(audit_codex_cursor_runtime(plugin_dir))
    return issues


def collect_codex_audits(plugin_dir: Path) -> list[AuditIssue]:
    try:
        validate_skills_config(plugin_dir)
    except RuntimeError as exc:
        from plugin_compiler.overrides import OVERRIDE_FILE

        return [
            AuditIssue(
                severity="block",
                path=OVERRIDE_FILE,
                line=0,
                message=str(exc),
            )
        ]
    issues = audit_skill_command_paths(plugin_dir)
    issues.extend(audit_skill_frontmatter_names(plugin_dir, "codex"))
    issues.extend(collect_codex_host_audits(plugin_dir))
    return issues


def blocking_codex_audits(plugin_dir: Path) -> list[AuditIssue]:
    return [
        issue
        for issue in collect_codex_audits(plugin_dir)
        if issue.severity == "block"
    ]


def write_codex_plugin_tree(plugin_dir: Path, dest: Path) -> None:
    """Thin wrap of the Codex compile write path; not a second tree writer."""
    from dataclasses import replace
    from tempfile import TemporaryDirectory

    from plugin_compiler.overrides import load_override_config
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.codex.stage import stage_codex
    from plugin_targets.source import SourceResolutionError, resolve_source_adapter

    plugin_dir = plugin_dir.resolve()
    issues = blocking_codex_audits(plugin_dir)
    if issues:
        raise RuntimeError(format_blocking_audits(issues))
    try:
        adapter = resolve_source_adapter(plugin_dir, "auto")
    except SourceResolutionError:
        # Legacy Cursor-shaped trees may ship multiple host manifests; prefer Cursor.
        if (plugin_dir / ".cursor-plugin" / "plugin.json").is_file():
            adapter = resolve_source_adapter(plugin_dir, "cursor")
        elif (plugin_dir / ".codex-plugin" / "plugin.json").is_file():
            adapter = resolve_source_adapter(plugin_dir, "codex")
        elif (plugin_dir / ".claude-plugin" / "plugin.json").is_file():
            adapter = resolve_source_adapter(plugin_dir, "claude-code")
        else:
            raise
    parsed = replace(
        adapter.parse(plugin_dir),
        override_config=load_override_config(plugin_dir),
    )
    with TemporaryDirectory() as tmp:
        staged = stage_codex(parsed, BuildLayout(Path(tmp) / "build"))
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(staged, dest)
