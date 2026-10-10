"""Human-readable inspect reports for import plans."""

from __future__ import annotations

from agentc.importers.pipeline import ImportPlan


def _section_lines(items: list[str]) -> list[str]:
    if not items:
        return ["(none)"]
    return items


def render_text_report(
    plan: ImportPlan,
    *,
    title: str = "# agentc inspect",
    redactions: tuple[tuple[str, str], ...] | None = None,
) -> str:
    lines = [
        title,
        f"source: {plan.source_root.as_posix()}",
        f"plugin: {plan.project_id} ({plan.version})",
        "converted:",
    ]
    converted = [
        f"- {item.kind} {item.source_rel} -> {item.dest_rel}"
        for item in plan.convertibles
    ]
    lines.extend(_section_lines(converted))
    lines.append("overlay:")
    overlay = [
        f"- {op.host} add-file {op.from_rel} -> {op.to_rel}"
        for op in plan.overlay_ops
    ]
    lines.extend(_section_lines(overlay))
    lines.append("unresolved:")
    unresolved = [
        f"- {item.source_rel} ({item.reason})" for item in plan.unresolved
    ]
    lines.extend(_section_lines(unresolved))
    lines.append("redacted:")
    redaction_items = plan.redactions if redactions is None else redactions
    redacted = [
        f"- {source_rel} ({reason})" for source_rel, reason in redaction_items
    ]
    lines.extend(_section_lines(redacted))
    return "\n".join(lines) + "\n"
