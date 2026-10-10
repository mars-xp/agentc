"""Write build diagnostics to build/reports/ files."""

from __future__ import annotations

import json
from pathlib import Path

from agentc.model.diagnostics import Diagnostic
from agentc.overlay.ops import OverlayOp
from plugin_compiler.paths import BuildLayout, require_inside


def _diagnostic_to_dict(d: Diagnostic) -> dict[str, str | None]:
    return {
        "code": d.code,
        "level": d.level,
        "message": d.message,
        "path": d.path,
        "hint": d.hint,
    }


def _host_from_path(path: str | None) -> str | None:
    if not path:
        return None
    prefix = "/targets/"
    if path.startswith(prefix):
        rest = path[len(prefix) :]
        if rest and "/" not in rest:
            return rest
    return None


def _overlay_op_to_dict(op: OverlayOp) -> dict[str, str | None]:
    return {
        "host": op.host,
        "op": op.op,
        "field": op.field,
        "from": op.from_rel,
        "to": op.to_rel,
    }


def _render_overlay_section(overlay_ops: tuple[OverlayOp, ...]) -> list[str]:
    lines: list[str] = ["## overlay", ""]
    for op in overlay_ops:
        if op.op == "add-file":
            lines.append(
                "- {0}: add-file from={1} to={2}".format(
                    op.host, op.from_rel, op.to_rel
                )
            )
        elif op.op == "remove":
            lines.append("- {0}: remove field={1}".format(op.host, op.field))
        else:
            lines.append("- {0}: set field={1}".format(op.host, op.field))
    lines.append("")
    return lines


def _render_report_markdown(
    diagnostics: list[Diagnostic],
    overlay_ops: tuple[OverlayOp, ...] = (),
) -> str:
    lines: list[str] = ["# Build report", ""]
    general: list[Diagnostic] = []
    by_host: dict[str, list[Diagnostic]] = {}
    host_order: list[str] = []
    for diagnostic in diagnostics:
        host = _host_from_path(diagnostic.path)
        if host is None:
            general.append(diagnostic)
            continue
        if host not in by_host:
            by_host[host] = []
            host_order.append(host)
        by_host[host].append(diagnostic)

    if general:
        lines.append("## Global")
        lines.append("")
        for diagnostic in general:
            lines.extend(_format_report_entry(diagnostic))
        lines.append("")

    for host in host_order:
        lines.append("## host: {0}".format(host))
        lines.append("")
        for diagnostic in by_host[host]:
            lines.extend(_format_report_entry(diagnostic))
        lines.append("")

    lines.extend(_render_overlay_section(overlay_ops))
    return "\n".join(lines).rstrip() + "\n"


def _format_report_entry(diagnostic: Diagnostic) -> list[str]:
    return [
        "- **{0}** `{1}`: {2}".format(
            diagnostic.level,
            diagnostic.code,
            diagnostic.message,
        )
    ]


def write_build_reports(
    project_root: Path,
    diagnostics: list[Diagnostic],
    overlay_ops: tuple[OverlayOp, ...] = (),
) -> None:
    layout = BuildLayout.from_repo(project_root)
    reports_dir = layout.root / "reports"
    require_inside(layout.root, reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    diagnostics_path = reports_dir / "diagnostics.json"
    require_inside(layout.root, diagnostics_path)
    diagnostics_path.write_text(
        json.dumps(
            {
                "diagnostics": [_diagnostic_to_dict(d) for d in diagnostics],
                "overlay": [_overlay_op_to_dict(op) for op in overlay_ops],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    report_path = reports_dir / "report.md"
    require_inside(layout.root, report_path)
    report_path.write_text(
        _render_report_markdown(diagnostics, overlay_ops),
        encoding="utf-8",
        newline="\n",
    )
