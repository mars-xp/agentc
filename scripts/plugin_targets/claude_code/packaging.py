from __future__ import annotations

from pathlib import Path

from plugin_compiler.cursor_runtime_needles import audit_cursor_runtime_in_files
from plugin_packaging import AuditIssue, allowed_skill_dirs
from plugin_targets.claude_code.stage import iter_claude_stage_files
from plugin_targets.spi import ParsedPlugin


def _audit_kiwi_sp_reverse_refs_in_files(
    plugin_dir: Path,
    files: tuple[Path, ...],
    host: str,
) -> list[AuditIssue]:
    if host not in ("codex", "claude-code"):
        return []

    plugin_dir = plugin_dir.resolve()
    allowed = set(allowed_skill_dirs(plugin_dir, host))
    issues: list[AuditIssue] = []
    for path in files:
        relative = path.relative_to(plugin_dir)
        parts = relative.parts
        if len(parts) < 2 or parts[0] != "skills":
            continue
        skill_name = parts[1]
        if skill_name.lower() == "kiwi-sp" or skill_name not in allowed:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = relative.as_posix()
        for line_number, line in enumerate(lines, start=1):
            if "kiwi-sp" in line.lower():
                issues.append(
                    AuditIssue(
                        severity="block",
                        path=rel,
                        line=line_number,
                        message="Codex-visible skill must not reference kiwi-sp",
                    )
                )
    return issues


def iter_claude_host_audit_files(parsed: ParsedPlugin) -> tuple[Path, ...]:
    """Same file set as Claude staging; audits must not scan outside this."""
    return iter_claude_stage_files(parsed)


def collect_claude_code_host_audits(parsed: ParsedPlugin) -> list[AuditIssue]:
    """Audit Claude Code pack files using the same paths as staging."""
    plugin_dir = parsed.plugin_dir.resolve()
    staged_files = iter_claude_host_audit_files(parsed)
    issues = _audit_kiwi_sp_reverse_refs_in_files(
        plugin_dir, staged_files, "claude-code"
    )
    issues.extend(audit_cursor_runtime_in_files(plugin_dir, staged_files))
    return issues
