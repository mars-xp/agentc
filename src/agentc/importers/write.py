"""Write imported Cursor plugin content into an agent-project tree."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

import yaml

from agentc.importers.pipeline import ImportPlan, OverlayAddFile
from agentc.importers.render import render_text_report
from agentc.model.diagnostics import Diagnostic
from agentc.project.init_project import init_project
from agentc.project.load import load_project_yaml
from agentc.security.redact import redact_portable_text
from agentc.validate.engine import validate_project

_PROJECT_YAML = "agent-project.yaml"
_IMPORT_REPORT = "import-report.md"


def materialize_plan(plan: ImportPlan, output_dir: Path) -> list[Diagnostic]:
    """init → copy → update yaml → validate_project. On error keep OUT, no import-report.md."""
    init_issues = init_project(
        output_dir,
        project_id=plan.project_id,
        name=plan.project_name,
        targets=plan.enabled_hosts if plan.enabled_hosts else (plan.enabled_host,),
        license_id=plan.license_id,
    )
    if any(issue.level == "error" for issue in init_issues):
        return init_issues

    redactions: list[tuple[str, str]] = []
    try:
        for item in plan.convertibles:
            dest_path = output_dir / item.dest_rel
            if item.kind == "skill":
                redactions.extend(
                    _copy_skill_tree(item.source_resolved, dest_path, output_dir)
                )
            else:
                redactions.extend(
                    _copy_text_or_bytes(item.source_resolved, dest_path, output_dir)
                )
        redactions.extend(_write_overlay_ops(plan, output_dir))
        _update_contents(output_dir / _PROJECT_YAML, plan)
    except OSError as exc:
        raise OSError(str(exc)) from exc

    validate_issues = validate_project(output_dir)
    if any(issue.level == "error" for issue in validate_issues):
        return validate_issues

    report = render_text_report(
        plan,
        title="# agentc import",
        redactions=tuple(redactions),
    )
    _write_text(output_dir / _IMPORT_REPORT, report)
    return validate_issues


def _write_overlay_ops(plan: ImportPlan, output_dir: Path) -> list[tuple[str, str]]:
    redactions: list[tuple[str, str]] = []
    by_host: dict[str, list[OverlayAddFile]] = defaultdict(list)
    for op in plan.overlay_ops:
        by_host[op.host].append(op)
    for host, ops in by_host.items():
        overlay_dir = output_dir / "overlays" / host
        payload = [
            {"op": "add-file", "from": op.from_rel, "to": op.to_rel} for op in ops
        ]
        overlay_dir.mkdir(parents=True, exist_ok=True)
        _write_text(
            overlay_dir / "import-merge.json",
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
        for op in ops:
            dest_file = overlay_dir / op.from_rel
            redactions.extend(
                _copy_text_or_bytes(op.source_resolved, dest_file, output_dir)
            )
    return redactions


def _copy_skill_tree(
    source_dir: Path, dest_dir: Path, project_root: Path
) -> list[tuple[str, str]]:
    redactions: list[tuple[str, str]] = []
    for source_file in sorted(source_dir.rglob("*")):
        if source_file.is_dir():
            continue
        relative = source_file.relative_to(source_dir)
        dest_file = dest_dir / relative
        redactions.extend(_copy_text_or_bytes(source_file, dest_file, project_root))
    return redactions


def _copy_text_or_bytes(
    source: Path, dest: Path, project_root: Path
) -> list[tuple[str, str]]:
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest, follow_symlinks=False)
        return []
    redacted, reasons = redact_portable_text(text)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _write_text(dest, redacted)
    if not reasons:
        return []
    rel = dest.relative_to(project_root).as_posix()
    return [(rel, reason) for reason in reasons]


def _update_contents(yaml_path: Path, plan: ImportPlan) -> None:
    data = load_project_yaml(yaml_path)
    contents = data.setdefault("contents", {})
    skills: list[str] = []
    rules: list[str] = []
    commands: list[str] = []
    agents: list[str] = []
    for item in plan.convertibles:
        if item.kind == "skill":
            skills.append(item.dest_rel)
        elif item.kind == "rule":
            rules.append(item.dest_rel)
        elif item.kind == "command":
            commands.append(item.dest_rel)
        elif item.kind == "agent":
            agents.append(item.dest_rel)
    contents["skills"] = skills
    contents["rules"] = rules
    contents["commands"] = commands
    contents["agents"] = agents
    _write_text(
        yaml_path,
        yaml.safe_dump(
            data,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ),
    )


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
