# -*- coding: utf-8 -*-
"""platform-overrides.json 加载、按宿主过滤技能目录与打包审计。

Codex 审计发现写死的 Cursor 路径时应修改技能文档；只有技能目标本身就是
管理 Cursor 产品时，才应在 packaging/platform-overrides.json 中登记为 Cursor-only。
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import re
from pathlib import Path
from typing import NamedTuple

from plugin_targets.registry import enabled_ids


def _layout_extras():
    """Load the layout loader by file so this module can finish defining AuditIssue.

    Importing plugin_compiler here would re-enter this module through audits.py.
    The candidate also copies this file to the repository root, next to ``src/``.
    """
    here = Path(__file__).resolve().parent
    options = (
        here / "plugin_compiler" / "layout_extras.py",
        here / "scripts" / "plugin_compiler" / "layout_extras.py",
    )
    path = next((item for item in options if item.is_file()), None)
    if path is None:
        raise ImportError("missing layout loader: {0}".format(options[0]))
    spec = importlib.util.spec_from_file_location("agentc_layout_extras", path)
    if spec is None or spec.loader is None:
        raise ImportError("missing layout loader: {0}".format(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LAYOUT = _layout_extras()

DEFAULT_HOSTS: tuple[str, ...] = ("cursor", "codex", "claude-code")


class AuditIssue(NamedTuple):
    severity: str
    path: str
    line: int
    message: str


def _skill_dir_names(plugin_dir: Path) -> set[str]:
    skills_root = plugin_dir / "skills"
    if not skills_root.is_dir():
        return set()
    return {
        child.name
        for child in skills_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    }


def validate_skills_config(plugin_dir: Path) -> None:
    """非法 JSON、双真源、未迁移 skills.json → RuntimeError。"""
    from plugin_compiler.overrides import load_override_config

    load_override_config(plugin_dir)


def skill_hosts(plugin_dir: Path, skill_dir_name: str) -> tuple[str, ...]:
    """未登记 → default_targets；已登记 → 该项 resolved_targets 的 .value。"""
    from plugin_compiler.model import CapabilityKind, CapabilityRef
    from plugin_compiler.overrides import load_override_config, resolved_targets

    config = load_override_config(plugin_dir)
    capability = CapabilityRef(
        capability_id="skill:skills/{0}".format(skill_dir_name),
        kind=CapabilityKind.SKILL,
        source_path="skills/{0}".format(skill_dir_name),
        source_dialect=None,
    )
    return tuple(target.value for target in resolved_targets(config, capability))


def should_include_skill(plugin_dir: Path, skill_dir_name: str, host: str) -> bool:
    from plugin_compiler.overrides import load_override_config, skill_dir_allowed

    return skill_dir_allowed(load_override_config(plugin_dir), skill_dir_name, host)


def allowed_skill_dirs(plugin_dir: Path, host: str) -> list[str]:
    """plugin_dir/skills 下含 SKILL.md 的子目录名，按 host 过滤后排序。"""
    from plugin_compiler.overrides import load_override_config, skill_dir_allowed

    skills_root = plugin_dir / "skills"
    if not skills_root.is_dir():
        return []
    names = sorted(
        child.name
        for child in skills_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )
    config = load_override_config(plugin_dir)
    return [name for name in names if skill_dir_allowed(config, name, host)]


KEBAB_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _split_yaml_frontmatter(text: str, skill_md: Path) -> str:
    if not text.startswith("---"):
        raise RuntimeError("{0}: missing YAML frontmatter".format(skill_md))
    end = text.find("\n---", 3)
    if end == -1:
        raise RuntimeError("{0}: incomplete YAML frontmatter".format(skill_md))
    return text[3:end].strip("\n")


def _parse_frontmatter_fields(block: str) -> dict[str, str]:
    name: str | None = None
    description: str | None = None
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("name:"):
            name = line[5:].strip()
            index += 1
            continue
        if line.startswith("description:"):
            rest = line[12:].strip()
            if rest in (">-", ">", "|"):
                parts: list[str] = []
                index += 1
                while index < len(lines) and (
                    not lines[index] or lines[index][0] in " \t"
                ):
                    stripped = lines[index].strip()
                    if stripped:
                        parts.append(stripped)
                    index += 1
                description = (
                    " ".join(parts) if rest in (">-", ">") else "\n".join(parts)
                )
                continue
            if rest:
                description = rest
            index += 1
            continue
        index += 1
    return {"name": name or "", "description": description or ""}


def parse_skill_frontmatter(skill_md: Path) -> dict:
    """读开头 --- 块，返回 name/description 字符串；缺块或空 name/description → raise RuntimeError。"""
    block = _split_yaml_frontmatter(
        skill_md.read_text(encoding="utf-8"), skill_md
    )
    fields = _parse_frontmatter_fields(block)
    if not fields["name"]:
        raise RuntimeError("{0}: empty or missing name in frontmatter".format(skill_md))
    if not fields["description"]:
        raise RuntimeError(
            "{0}: empty or missing description in frontmatter".format(skill_md)
        )
    return fields


def audit_skill_frontmatter_names(plugin_dir: Path, host: str) -> list[AuditIssue]:
    """对 allowed_skill_dirs(plugin_dir, host) 检查 name 匹配 KEBAB_SKILL_NAME。
    失败 → block。Cursor 包与 Codex 包都跑（Cursor-only 技能也要改名）。"""
    issues: list[AuditIssue] = []
    skills_root = plugin_dir / "skills"
    for skill_dir_name in allowed_skill_dirs(plugin_dir, host):
        skill_md = skills_root / skill_dir_name / "SKILL.md"
        rel = skill_md.relative_to(plugin_dir).as_posix()
        try:
            fields = parse_skill_frontmatter(skill_md)
        except RuntimeError as exc:
            issues.append(
                AuditIssue(
                    severity="block",
                    path=rel,
                    line=1,
                    message=str(exc),
                )
            )
            continue
        name = fields["name"]
        if KEBAB_SKILL_NAME.match(name):
            continue
        line_no = 2
        try:
            for idx, line in enumerate(
                skill_md.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.startswith("name:"):
                    line_no = idx
                    break
        except UnicodeDecodeError:
            pass
        issues.append(
            AuditIssue(
                severity="block",
                path=rel,
                line=line_no,
                message="skill name {0!r} is not kebab-case".format(name),
            )
        )
    return issues


COMMAND_PREFIX = re.compile(
    r"(?im)^[ \t]*(?:python3?|bash|sh|\"\$VPY\"|\$VPY|cat|source)\b"
)
FORBIDDEN_CURSOR_SKILLS = re.compile(r"\.cursor/skills/")
FORBIDDEN_HOME_SMART_PLUGIN = re.compile(r"/home/someone/")
FORBIDDEN_SOLE_CURSOR_INSTALL = re.compile(
    r"~/\.cursor/plugins/local/[^/\s]+/skills/"
)
FORBIDDEN_CWD_RELATIVE_SKILLS = re.compile(
    r"(?im)(?:python3?)\s+skills[/\\]"
)
_FENCE_OPEN = re.compile(r"^([ \t]*)```([^`\n]*)$")
_FENCE_LINE_CMD = re.compile(
    r"(?im)^[ \t]*(?:python3?|bash|sh|\"\$VPY\"|\$VPY)\b"
)
_PLUGIN_SKILL_CURSOR_PREFIX = _LAYOUT.extra_cursor_skill_prefix()
_LOCAL_SKILL_COMMAND = _LAYOUT.extra_local_skill_command()
_RUN_LABEL = re.compile(r"运行[:：]")


def _is_command_line(line: str, in_fence: bool) -> bool:
    if COMMAND_PREFIX.search(line):
        return True
    return in_fence and bool(_FENCE_LINE_CMD.search(line))


def _points_at_plugin_skill(line: str, skill_dirs: set[str]) -> bool:
    if (
        _PLUGIN_SKILL_CURSOR_PREFIX is not None
        and _PLUGIN_SKILL_CURSOR_PREFIX.search(line)
    ):
        return True
    normalized = line.replace("\\", "/")
    for name in skill_dirs:
        if ".cursor/skills/" + name in normalized:
            return True
    return False


def audit_skill_command_paths(plugin_dir: Path) -> list[AuditIssue]:
    """扫描 skills/*/SKILL.md。

    规则：
    1. 任意出现 `.cursor/skills/` 且该行是命令行（COMMAND_PREFIX 命中，或围栏内以 python/bash/$VPY 开头）→ block。
    2. 任意出现 `/home/someone/` → block（含产出路径示例）。
    3. 把 `~/.cursor/plugins/local/<插件>/skills/` 写成唯一入口（命令行或「运行：」后的路径）→ block。
    允许：散文里提到工程根下的 `.cursor/skills/`，前提是该行不是命令、且路径不指向本插件技能目录。
    指向本插件：按当前插件自己的技能目录名判断。本仓额外的目录前缀不在公开树里。
    4. 命令行匹配 `python3? skills/` 或 `python3? skills\\` 且无 `<插件根>` → block。
    5. 命令行若命中本仓额外的本地技能路径，也 block。公开树没有这条额外路径。
    """
    issues: list[AuditIssue] = []
    skills_root = plugin_dir / "skills"
    if not skills_root.is_dir():
        return issues

    skill_dirs = _skill_dir_names(plugin_dir)
    for skill_dir in sorted(child for child in skills_root.iterdir() if child.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            lines = skill_md.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = skill_md.relative_to(plugin_dir).as_posix()
        in_fence = False
        for line_number, line in enumerate(lines, start=1):
            fence_match = _FENCE_OPEN.match(line)
            if fence_match is not None:
                in_fence = not in_fence
                continue
            is_command = _is_command_line(line, in_fence)
            if FORBIDDEN_HOME_SMART_PLUGIN.search(line):
                issues.append(
                    AuditIssue(
                        severity="block",
                        path=rel,
                        line=line_number,
                        message="hardcoded /home/someone/.../agent-plugins path",
                    )
                )
                continue
            if FORBIDDEN_CURSOR_SKILLS.search(line):
                if is_command or _points_at_plugin_skill(line, skill_dirs):
                    issues.append(
                        AuditIssue(
                            severity="block",
                            path=rel,
                            line=line_number,
                            message="hardcoded .cursor/skills path",
                        )
                    )
                    continue
            if FORBIDDEN_SOLE_CURSOR_INSTALL.search(line) and (
                is_command or _RUN_LABEL.search(line)
            ):
                issues.append(
                    AuditIssue(
                        severity="block",
                        path=rel,
                        line=line_number,
                        message="Cursor install path used as sole skill entry",
                    )
                )
                continue
            if is_command and FORBIDDEN_CWD_RELATIVE_SKILLS.search(line) and (
                "<插件根>" not in line
            ):
                issues.append(
                    AuditIssue(
                        severity="block",
                        path=rel,
                        line=line_number,
                        message="cwd-relative python skills/ command",
                    )
                )
                continue
            if (
                is_command
                and _LOCAL_SKILL_COMMAND is not None
                and _LOCAL_SKILL_COMMAND.search(line.replace("\\", "/"))
            ):
                issues.append(
                    AuditIssue(
                        severity="block",
                        path=rel,
                        line=line_number,
                        message="local skill path used as command entry",
                    )
                )
    return issues


def _load_codex_host_audits():
    return importlib.import_module(
        "plugin_targets.codex.packaging"
    ).collect_codex_host_audits


_HOST_EXTRA_AUDITS = {
    "codex": _load_codex_host_audits,
}


def _load_codex_pack_skip():
    return importlib.import_module(
        "plugin_targets.codex.packaging"
    ).should_skip_codex_pack_path


_HOST_PACK_SKIP = {
    "codex": _load_codex_pack_skip,
}


def collect_audits(plugin_dir: Path, host: str) -> list[AuditIssue]:
    """汇总指定宿主的所有打包审计结果。"""
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
    issues.extend(audit_skill_frontmatter_names(plugin_dir, host))
    extra = _HOST_EXTRA_AUDITS.get(host)
    if extra is not None:
        issues.extend(extra()(plugin_dir))
    return issues


def blocking_audits(plugin_dir: Path, host: str) -> list[AuditIssue]:
    return [
        issue
        for issue in collect_audits(plugin_dir, host)
        if issue.severity == "block"
    ]


def _parsed_plugin(plugin_dir: Path):
    from dataclasses import replace

    from plugin_compiler.overrides import load_override_config
    from plugin_targets.source import resolve_source_adapter

    plugin_dir = plugin_dir.resolve()
    config = load_override_config(plugin_dir)
    return replace(
        resolve_source_adapter(plugin_dir, "auto").parse(plugin_dir),
        override_config=config,
    )


def collect_claude_code_audits(
    plugin_dir: Path,
    parsed=None,
) -> list[AuditIssue]:
    """Claude Code audits over the files that would enter the Claude Code pack."""
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
    from plugin_targets.claude_code.packaging import collect_claude_code_host_audits

    if parsed is None:
        parsed = _parsed_plugin(plugin_dir)
    issues = audit_skill_command_paths(plugin_dir)
    issues.extend(audit_skill_frontmatter_names(plugin_dir, "claude-code"))
    issues.extend(collect_claude_code_host_audits(parsed))
    return issues


PACK_EXCLUDE_DIR_NAMES = {
    ".git",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".superpowers",
    "outputs",
    "packaging",
    ".codex-plugin",
} | set(_LAYOUT.extra_output_dir_names())
PACK_EXCLUDE_SUFFIXES = {".pyc", ".zip", ".gz", ".tgz", ".xz", ".bz2", ".7z"}
PACK_EXCLUDE_ROOT_FILE_NAMES = {"runtime-host.json"}


def format_blocking_audits(issues: list[AuditIssue]) -> str:
    return "\n".join(
        "{0}:{1}: {2}".format(issue.path, issue.line, issue.message)
        for issue in issues
    )


def _is_credential_file(path: Path) -> bool:
    name = path.name
    return name.endswith(".local") or name.endswith(".local.env")


def _pack_should_skip(
    path: Path, root: Path, host: str, allowed_skills: set[str]
) -> bool:
    relative = path.relative_to(root)
    parts = relative.parts
    if parts[:2] == ("docs", "superpowers"):
        return True
    if len(parts) == 1 and relative.name in PACK_EXCLUDE_ROOT_FILE_NAMES:
        return True
    if path.suffix in PACK_EXCLUDE_SUFFIXES:
        return True
    if _is_credential_file(path):
        return True
    for part in parts:
        if part in PACK_EXCLUDE_DIR_NAMES:
            return True
    skipper = _HOST_PACK_SKIP.get(host)
    if skipper is not None and skipper()(parts, relative.name):
        return True
    if parts and parts[0] == "skills" and len(parts) >= 2:
        if parts[1] not in allowed_skills:
            return True
    return False


def iter_pack_files(plugin_dir: Path, host: str) -> list[Path]:
    """返回应打进该宿主包的源文件绝对路径。"""
    if host not in enabled_ids():
        raise RuntimeError("illegal pack host: {0}".format(host))
    plugin_dir = plugin_dir.resolve()
    allowed = set(allowed_skill_dirs(plugin_dir, host))
    collected: list[Path] = []
    for path in plugin_dir.rglob("*"):
        if not path.is_file():
            continue
        if _pack_should_skip(path, plugin_dir, host, allowed):
            continue
        collected.append(path)
    return sorted(collected)


def write_runtime_host(dest_plugin_root: Path, host: str) -> None:
    """写入 dest_plugin_root/runtime-host.json → {"host": host}"""
    if host not in enabled_ids():
        raise RuntimeError("illegal runtime host: {0}".format(host))
    dest_plugin_root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"host": host}, ensure_ascii=True) + "\n"
    (dest_plugin_root / "runtime-host.json").write_text(payload, encoding="utf-8")


def default_staging_root(repo_root: Path) -> Path:
    raise RuntimeError(
        "default_staging_root is deprecated; delete --staging-root and read "
        "build/intermediates/codex/"
    )
