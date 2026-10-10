"""Run a workspace build and publish its report and archives together."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from agentc.model.diagnostics import (
    CODE_WORKSPACE_COVERAGE,
    CODE_WORKSPACE_ENTRY,
    CODE_WORKSPACE_PUBLISH,
    CODE_WORKSPACE_SECRET,
    Diagnostic,
)
from agentc.security.redact import contains_secret_token, scrub_secret_text
from agentc.workspace.compile import compile_workspace
from agentc.workspace.inventory import (
    FIXED_EXCLUSIONS,
    inventory_source,
    output_overlap,
    recheck_inventory,
)
from agentc.workspace.report import (
    MARKER_NAME,
    REPORT_NAME,
    SCHEMA_VERSION,
    build_report,
    coverage_gaps,
    file_sha256,
    write_json_atomic,
)

_TARGETS = ("cursor", "codex", "claude-code")


@dataclass(frozen=True)
class WorkspaceRunResult:
    diagnostics: list[Diagnostic]
    exit_code: int
    artifacts: list[str]
    summary: str
    text_body: str | None


def _replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def default_output_dir(source: Path) -> Path:
    normalized = source.expanduser().resolve().as_posix()
    key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return Path.home() / ".agentc" / "workspace-builds" / key


def _target_diagnostics(targets: tuple[str, ...]) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    if not targets or any(part == "" for part in targets):
        diagnostics.append(
            Diagnostic(
                code=CODE_WORKSPACE_ENTRY,
                level="error",
                message="Empty target is not allowed.",
                path=".",
                hint="Pass cursor, codex, or claude-code.",
            )
        )
    seen: set[str] = set()
    for target in targets:
        if target == "":
            continue
        if target not in _TARGETS:
            diagnostics.append(
                Diagnostic(
                    code=CODE_WORKSPACE_ENTRY,
                    level="error",
                    message="Unknown target: {0}".format(target),
                    path=".",
                    hint="Use cursor, codex, or claude-code.",
                )
            )
            continue
        if target in seen:
            diagnostics.append(
                Diagnostic(
                    code=CODE_WORKSPACE_ENTRY,
                    level="error",
                    message="Duplicate target: {0}".format(target),
                    path=".",
                    hint="List each target once.",
                )
            )
        seen.add(target)
    return tuple(diagnostics)


def _read_marker(output: Path) -> dict | None:
    path = output / MARKER_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("managed"), list):
        return None
    return payload


def _safe_rel(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("/") or "\\" in value:
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return value


def _unmanaged_entries(output: Path) -> tuple[str, ...]:
    if not output.exists():
        return ()
    if not output.is_dir():
        return (output.name,)
    marker = _read_marker(output)
    children = list(output.iterdir())
    if marker is None:
        if not children:
            return ()
        return tuple(sorted(path.name for path in children))
    allowed = {MARKER_NAME}
    for item in marker["managed"]:
        if not isinstance(item, dict):
            continue
        rel = _safe_rel(item.get("path"))
        if rel is not None:
            allowed.add(rel)
    extras: list[str] = []
    for path in sorted(output.rglob("*")):
        rel = path.relative_to(output).as_posix()
        if path.is_dir():
            if any(item == rel or item.startswith(rel + "/") for item in allowed):
                continue
            extras.append(rel)
            continue
        if rel not in allowed:
            extras.append(rel)
    return tuple(extras)


def _output_block(source: Path, output: Path) -> Diagnostic | None:
    if output.exists() and not output.is_dir():
        return Diagnostic(
            code=CODE_WORKSPACE_PUBLISH,
            level="error",
            message="Output path is not a directory.",
            path=".",
            hint="Choose an empty directory for the workspace build output.",
        )
    overlap = output_overlap(source, output)
    if overlap is not None:
        return overlap
    extras = _unmanaged_entries(output)
    if extras:
        return Diagnostic(
            code=CODE_WORKSPACE_PUBLISH,
            level="error",
            message="Output directory has content AgentC does not manage: {0}".format(
                extras[0]
            ),
            path=".",
            hint="Use an empty output directory or one marked as an AgentC workspace build.",
        )
    return None


def _pointer(report_path: Path) -> Diagnostic:
    return Diagnostic(
        code=CODE_WORKSPACE_ENTRY,
        level="error",
        message="Workspace build failed before publish.",
        path=".",
        hint=report_path.as_posix(),
    )


def _has_error(diagnostics: list[Diagnostic]) -> bool:
    return any(item.level == "error" for item in diagnostics)


def _mark_archives_stale(output: Path, round_id: str) -> None:
    marker = _read_marker(output)
    managed: list[dict] = []
    if marker is not None:
        for item in marker["managed"]:
            if not isinstance(item, dict):
                continue
            updated = dict(item)
            if updated.get("role") == "archive" and not updated.get("stale"):
                updated["stale"] = True
            elif updated.get("role") == "report":
                updated["round"] = round_id
                updated["stale"] = False
                updated["path"] = REPORT_NAME
            managed.append(updated)
    if not any(item.get("role") == "report" for item in managed):
        managed.append(
            {
                "path": REPORT_NAME,
                "role": "report",
                "round": round_id,
                "stale": False,
                "target": None,
            }
        )
    write_json_atomic(
        output / MARKER_NAME,
        {
            "schemaVersion": SCHEMA_VERSION,
            "currentRound": round_id,
            "managed": managed,
        },
    )


def _write_report_file(path: Path, document: dict) -> Path:
    write_json_atomic(path, document)
    return path.resolve()


def _unused_swap_path(output: Path) -> Path:
    parent = output.parent
    for _ in range(8):
        candidate = parent / "{0}.agentc-swap-{1}".format(output.name, uuid.uuid4().hex)
        if not candidate.exists():
            return candidate
    raise OSError("Could not choose a unique workspace swap directory.")


def _swap_directory(prepared: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        _replace(prepared, output)
        return
    backup = _unused_swap_path(output)
    _replace(output, backup)
    try:
        _replace(prepared, output)
    except OSError:
        _replace(backup, output)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _publish_success(
    output: Path,
    *,
    round_id: str,
    pairs: list[tuple[str, Path]],
    document: dict,
) -> list[dict[str, str]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    prepared = Path(tempfile.mkdtemp(prefix="agentc-ws-stage-", dir=str(output.parent)))
    try:
        managed: list[dict] = []
        old = _read_marker(output) if output.is_dir() else None
        if old is not None:
            for item in old["managed"]:
                if not isinstance(item, dict) or item.get("role") != "archive":
                    continue
                rel = _safe_rel(item.get("path"))
                if rel is None:
                    continue
                src = output.joinpath(*rel.split("/"))
                if not src.is_file():
                    continue
                if item.get("stale") and rel.startswith("previous/"):
                    dest_rel = rel
                else:
                    dest_rel = "previous/{0}/{1}".format(item.get("round"), src.name)
                dest = prepared.joinpath(*dest_rel.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                moved = dict(item)
                moved["path"] = dest_rel
                moved["stale"] = True
                managed.append(moved)
        archives: list[dict[str, str]] = []
        for target, zip_path in pairs:
            name = Path(zip_path).name
            dest = prepared / name
            shutil.copy2(zip_path, dest)
            archives.append(
                {
                    "target": target,
                    "path": (output / name).resolve().as_posix(),
                    "sha256": file_sha256(dest),
                }
            )
            managed.append(
                {
                    "path": name,
                    "role": "archive",
                    "round": round_id,
                    "stale": False,
                    "target": target,
                }
            )
        document["archives"] = archives
        write_json_atomic(prepared / REPORT_NAME, document)
        managed.append(
            {
                "path": REPORT_NAME,
                "role": "report",
                "round": round_id,
                "stale": False,
                "target": None,
            }
        )
        write_json_atomic(
            prepared / MARKER_NAME,
            {
                "schemaVersion": SCHEMA_VERSION,
                "currentRound": round_id,
                "managed": managed,
            },
        )
        _swap_directory(prepared, output)
        return archives
    except Exception:
        if prepared.exists():
            shutil.rmtree(prepared, ignore_errors=True)
        raise


def _success_text(round_id: str, report_path: Path, archives: list[dict[str, str]]) -> str:
    lines = [
        "round: {0}\n".format(round_id),
        "report: {0}\n".format(report_path.as_posix()),
    ]
    for archive in archives:
        lines.append("archive: {0}\n".format(archive["path"]))
    return "".join(lines)


def _scrub_diagnostics(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    scrubbed: list[Diagnostic] = []
    for item in diagnostics:
        path = item.path
        scrubbed.append(
            Diagnostic(
                code=item.code,
                level=item.level,
                message=scrub_secret_text(item.message),
                path=scrub_secret_text(path) if path else path,
                hint=scrub_secret_text(item.hint),
            )
        )
    return scrubbed


def _store_failure(
    diagnostics: list[Diagnostic],
    *,
    output: Path,
    blocked: bool,
    source_root: str,
    source_kind: str | None,
    fingerprint: str,
    targets: tuple[str, ...],
    exclusions: tuple[str, ...],
    entries,
    outcomes,
    round_id: str,
) -> WorkspaceRunResult:
    diagnostics = _scrub_diagnostics(diagnostics)
    if blocked:
        report_path = (
            Path(tempfile.mkdtemp(prefix="agentc-ws-report-")) / REPORT_NAME
        ).resolve()
    else:
        report_path = (output / REPORT_NAME).resolve()
    diagnostics.append(_pointer(report_path))
    document = build_report(
        source_root=source_root,
        source_kind=source_kind,
        fingerprint=fingerprint,
        targets=targets,
        exclusions=exclusions,
        entries=entries,
        outcomes=outcomes,
        diagnostics=diagnostics,
        archives=[],
        round_id=round_id,
    )
    written = _write_report_file(report_path, document)
    if written.as_posix() != diagnostics[-1].hint:
        diagnostics[-1] = Diagnostic(
            code=diagnostics[-1].code,
            level=diagnostics[-1].level,
            message=diagnostics[-1].message,
            path=diagnostics[-1].path,
            hint=written.as_posix(),
        )
        document["diagnostics"][-1]["hint"] = written.as_posix()
        write_json_atomic(written, document)
    if not blocked:
        _mark_archives_stale(output, round_id)
    return WorkspaceRunResult(
        diagnostics=diagnostics,
        exit_code=1,
        artifacts=[],
        summary="workspace build failed",
        text_body=None,
    )


def _explicit_output_secret(output: Path) -> Diagnostic | None:
    candidates = [output.as_posix()]
    try:
        candidates.append(output.expanduser().resolve().as_posix())
    except OSError:
        pass
    text = next((item for item in candidates if contains_secret_token(item)), None)
    if text is None:
        return None
    return Diagnostic(
        code=CODE_WORKSPACE_SECRET,
        level="error",
        message="Suspected secret in the output path.",
        path=scrub_secret_text(text),
        hint="locator={0}".format(
            hashlib.sha256(text.encode("utf-8")).hexdigest()
        ),
    )


def run_workspace_build(
    *,
    source: Path,
    targets: tuple[str, ...],
    source_kind: str | None,
    output_dir: Path | None,
) -> WorkspaceRunResult:
    source = Path(source)
    output = Path(output_dir) if output_dir is not None else default_output_dir(source)
    round_id = uuid.uuid4().hex
    try:
        source_root = source.expanduser().resolve().as_posix()
    except OSError:
        source_root = source.as_posix()
    if output_dir is not None:
        output_secret = _explicit_output_secret(output)
        if output_secret is not None:
            return _store_failure(
                [output_secret],
                output=output,
                blocked=True,
                source_root=source_root,
                source_kind=source_kind,
                fingerprint="",
                targets=targets,
                exclusions=FIXED_EXCLUSIONS,
                entries=(),
                outcomes=(),
                round_id=round_id,
            )
    diagnostics = list(_target_diagnostics(targets))
    block = _output_block(source, output)
    blocked = block is not None
    if block is not None:
        diagnostics.append(block)
    target_failed = any(
        item.code == CODE_WORKSPACE_ENTRY
        and (
            item.message.startswith("Empty target")
            or item.message.startswith("Unknown target")
            or item.message.startswith("Duplicate target")
        )
        for item in diagnostics
    )
    inventory_entries = ()
    fingerprint = ""
    exclusions = FIXED_EXCLUSIONS
    outcomes = ()
    found_kind: str | None = None

    if not target_failed and not blocked:
        inventory, inventory_diags = inventory_source(source)
        diagnostics.extend(inventory_diags)
        if inventory is not None:
            inventory_entries = inventory.entries
            fingerprint = inventory.fingerprint
            with tempfile.TemporaryDirectory(prefix="agentc-ws-compile-") as tmp:
                stage = Path(tmp) / "stage"
                result = compile_workspace(source, targets, stage, source_kind=source_kind)
                changed = recheck_inventory(source, inventory)
                diagnostics.extend(result.diagnostics)
                diagnostics.extend(changed)
                outcomes = result.outcomes
                found_kind = result.source_kind
                exclusions = result.exclusions
                gaps = coverage_gaps(inventory.entries, outcomes, targets)
                diagnostics.extend(
                    Diagnostic(
                        code=CODE_WORKSPACE_COVERAGE,
                        level="error",
                        message="Report does not cover source entry: {0}".format(path),
                        path=path,
                        hint="Give every source entry a result for each requested target.",
                    )
                    for path in gaps
                )
                error_outcome = any(item.status == "error" for item in outcomes)
                ready = (
                    result.ok
                    and not changed
                    and not gaps
                    and not error_outcome
                    and len(result.zips) == len(targets)
                    and not _has_error(diagnostics)
                )
                if ready:
                    pairs = [
                        (target, Path(zip_path))
                        for target, zip_path in zip(targets, result.zips)
                    ]
                    document = build_report(
                        source_root=source_root,
                        source_kind=found_kind,
                        fingerprint=fingerprint,
                        targets=targets,
                        exclusions=exclusions,
                        entries=inventory_entries,
                        outcomes=outcomes,
                        diagnostics=(),
                        archives=[],
                        round_id=round_id,
                    )
                    try:
                        archives = _publish_success(
                            output,
                            round_id=round_id,
                            pairs=pairs,
                            document=document,
                        )
                    except OSError:
                        diagnostics.append(
                            Diagnostic(
                                code=CODE_WORKSPACE_PUBLISH,
                                level="error",
                                message="Workspace build publish failed.",
                                path=".",
                                hint="Free disk space and retry the workspace build.",
                            )
                        )
                    else:
                        report_path = (output / REPORT_NAME).resolve()
                        return WorkspaceRunResult(
                            diagnostics=[],
                            exit_code=0,
                            artifacts=[item["path"] for item in archives]
                            + [report_path.as_posix()],
                            summary="workspace build ok",
                            text_body=_success_text(round_id, report_path, archives),
                        )
    elif not target_failed:
        inventory, inventory_diags = inventory_source(source)
        diagnostics.extend(inventory_diags)
        if inventory is not None:
            inventory_entries = inventory.entries
            fingerprint = inventory.fingerprint

    if not _has_error(diagnostics):
        diagnostics.append(
            Diagnostic(
                code=CODE_WORKSPACE_ENTRY,
                level="error",
                message="Workspace build failed before publish.",
                path=".",
                hint="Fix the source directory and retry.",
            )
        )
    return _store_failure(
        diagnostics,
        output=output,
        blocked=blocked,
        source_root=source_root,
        source_kind=found_kind,
        fingerprint=fingerprint,
        targets=targets,
        exclusions=exclusions,
        entries=inventory_entries,
        outcomes=outcomes,
        round_id=round_id,
    )
