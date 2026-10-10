"""Human-readable install plans."""

from __future__ import annotations

from agentc.installer.pipeline import InstallPlan


def _section_lines(items: tuple[str, ...]) -> list[str]:
    if not items:
        return ["(none)"]
    return [f"- {item}" for item in items]


def render_install_text(plan: InstallPlan) -> str:
    lines = [
        "# agentc install",
        f"zip: {plan.zip_path.as_posix()}",
        f"root: {plan.install_root.as_posix()}",
        f"plugin: {plan.plugin_name}",
        f"dest: {plan.dest.as_posix()}",
        "create:",
    ]
    lines.extend(_section_lines(plan.create))
    lines.append("identical:")
    lines.extend(_section_lines(plan.identical))
    lines.append("conflict:")
    lines.extend(_section_lines(plan.conflicts))
    return "\n".join(lines) + "\n"
