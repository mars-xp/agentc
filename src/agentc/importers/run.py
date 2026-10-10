"""Import/inspect entry points for Cursor plugin sources."""

from __future__ import annotations

from pathlib import Path

from agentc.importers.detect import resolve_kind
from agentc.importers.merge import MERGE_SOURCE_ORDER, compose_plans
from agentc.importers.pipeline import (
    ImportPlan,
    build_plan,
    kind_error_diagnostic,
    overlay_note_diagnostics,
)
from agentc.importers.render import render_text_report
from agentc.importers.write import materialize_plan
from agentc.model.diagnostics import (
    CODE_IMPORT_IO,
    CODE_MERGE_SOURCES,
    CODE_OUTPUT_NONEMPTY,
    CODE_UNCONFIRMED,
    Diagnostic,
)


def inspect_source(
    source_dir: Path, kind: str | None = None
) -> tuple[list[Diagnostic], ImportPlan | None, str]:
    """Resolve kind then plan. Explicit --kind cursor on a non-plugin tree is 0007."""
    source_root = source_dir.resolve()
    resolved, code = resolve_kind(source_root, kind)
    if resolved is None:
        return [kind_error_diagnostic(code, kind, source_root)], None, ""
    plan, diagnostics = build_plan(source_dir, kind=resolved)
    if plan is None:
        return diagnostics, None, ""
    return diagnostics, plan, render_text_report(plan)


def inspect_plugin(source_dir: Path) -> tuple[list[Diagnostic], ImportPlan | None, str]:
    """Plugin-only inspect. Missing .cursor-plugin/plugin.json → AGC-P4-0001."""
    plan, diagnostics = build_plan(source_dir)
    if plan is None:
        return diagnostics, None, ""
    return diagnostics, plan, render_text_report(plan)


def import_source(
    source_dir: Path,
    output_dir: Path,
    *,
    kind: str,
    yes: bool,
) -> tuple[list[Diagnostic], str]:
    """Return (diagnostics, text_report). Without --yes, output_dir is not created."""
    plan, diagnostics = build_plan(source_dir, kind=kind)
    if plan is None:
        return diagnostics, ""

    report = render_text_report(plan)

    if not yes:
        diagnostics = [
            *diagnostics,
            Diagnostic(
                code=CODE_UNCONFIRMED,
                level="error",
                message=(
                    "Import not confirmed; no files were written to the output directory."
                ),
                path=None,
                hint="Re-run with --yes to write the imported project.",
            ),
        ]
        return diagnostics, report

    blocked = _output_nonempty_diagnostic(output_dir)
    if blocked is not None:
        return [*diagnostics, blocked], report

    try:
        materialize_issues = materialize_plan(plan, output_dir)
    except OSError as exc:
        return [
            *diagnostics,
            Diagnostic(
                code=CODE_IMPORT_IO,
                level="error",
                message=f"Import I/O failed: {exc}",
                path=None,
                hint="Check permissions and disk space, then retry.",
            ),
        ], report

    all_diagnostics = [*diagnostics, *materialize_issues]
    if any(issue.level == "error" for issue in materialize_issues):
        return all_diagnostics, report

    report = (output_dir / "import-report.md").read_text(encoding="utf-8")
    return all_diagnostics, report


def import_cursor_plugin(
    source_dir: Path,
    output_dir: Path,
    *,
    yes: bool,
) -> tuple[list[Diagnostic], str]:
    """Return (diagnostics, text_report). Without --yes, output_dir is not created."""
    return import_source(source_dir, output_dir, kind="cursor", yes=yes)


def import_merge(
    *,
    cursor: Path | None,
    cursor_workspace: Path | None,
    cursor_user: Path | None,
    codex: Path | None,
    claude_code: Path | None,
    output_dir: Path,
    yes: bool,
) -> tuple[list[Diagnostic], str]:
    """Merge two or more host trees. Without --yes, output_dir is not created."""
    sources = {
        "cursor": cursor,
        "cursor-workspace": cursor_workspace,
        "cursor-user": cursor_user,
        "codex": codex,
        "claude-code": claude_code,
    }
    provided = [
        (kind, sources[kind])
        for kind in MERGE_SOURCE_ORDER
        if sources[kind] is not None
    ]
    if len(provided) < 2:
        return [
            Diagnostic(
                code=CODE_MERGE_SOURCES,
                level="error",
                message="Import merge requires at least two source directories.",
                path=None,
                hint=(
                    "Pass two or more of --cursor, --cursor-workspace, "
                    "--cursor-user, --codex, --claude-code."
                ),
            )
        ], ""

    sourced: list[tuple[str, ImportPlan]] = []
    diagnostics: list[Diagnostic] = []
    failed = False
    for kind, path in provided:
        plan, diags = build_plan(path, kind=kind)
        diagnostics.extend(diags)
        if plan is None:
            failed = True
        else:
            sourced.append((kind, plan))
    if failed:
        return diagnostics, ""

    merged, merge_diags = compose_plans(tuple(sourced))
    if merged is None:
        return merge_diags, ""

    diagnostics = [*diagnostics, *merge_diags, *overlay_note_diagnostics(merged)]
    report = render_text_report(merged, title="# agentc import")

    if not yes:
        diagnostics = [
            *diagnostics,
            Diagnostic(
                code=CODE_UNCONFIRMED,
                level="error",
                message=(
                    "Import not confirmed; no files were written to the output directory."
                ),
                path=None,
                hint="Re-run with --yes to write the imported project.",
            ),
        ]
        return diagnostics, report

    blocked = _output_nonempty_diagnostic(output_dir)
    if blocked is not None:
        return [*diagnostics, blocked], report

    try:
        materialize_issues = materialize_plan(merged, output_dir)
    except OSError as exc:
        return [
            *diagnostics,
            Diagnostic(
                code=CODE_IMPORT_IO,
                level="error",
                message=f"Import I/O failed: {exc}",
                path=None,
                hint="Check permissions and disk space, then retry.",
            ),
        ], report

    all_diagnostics = [*diagnostics, *materialize_issues]
    if any(issue.level == "error" for issue in materialize_issues):
        return all_diagnostics, report

    report = (output_dir / "import-report.md").read_text(encoding="utf-8")
    return all_diagnostics, report


def _output_nonempty_diagnostic(output_dir: Path) -> Diagnostic | None:
    if not output_dir.exists():
        return None
    if not output_dir.is_dir():
        return Diagnostic(
            code=CODE_OUTPUT_NONEMPTY,
            level="error",
            message="Output path exists and is not a directory.",
            path=output_dir.name,
            hint="Choose an empty output directory or remove existing files.",
        )
    if any(output_dir.iterdir()):
        return Diagnostic(
            code=CODE_OUTPUT_NONEMPTY,
            level="error",
            message="Output directory is not empty.",
            path=output_dir.name,
            hint="Choose an empty output directory or remove existing files.",
        )
    return None
