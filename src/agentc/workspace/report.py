"""Workspace build report documents.

The report lists inventory rows, per-target results, and archive hashes.
It does not copy source file bodies.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agentc.model.diagnostics import Diagnostic
from agentc.security.redact import scrub_secret_text

SCHEMA_VERSION = "agentc-workspace-build/v1"
REPORT_NAME = "workspace-build-report.json"
MARKER_NAME = ".agentc-workspace.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diagnostic_dict(diagnostic: Diagnostic) -> dict[str, str | None]:
    path = diagnostic.path
    return {
        "code": diagnostic.code,
        "level": diagnostic.level,
        "message": scrub_secret_text(diagnostic.message),
        "path": scrub_secret_text(path) if path else path,
        "hint": scrub_secret_text(diagnostic.hint),
    }


def coverage_gaps(entries, outcomes, targets: tuple[str, ...]) -> tuple[str, ...]:
    """Return inventory paths that lack a result for every requested target."""
    present: dict[str, set[str]] = {}
    for item in outcomes:
        present.setdefault(item.path, set()).add(item.target)
    missing: list[str] = []
    for entry in entries:
        got = present.get(entry.path, set())
        if any(target not in got for target in targets):
            missing.append(entry.path)
    return tuple(missing)


def build_report(
    *,
    source_root: str,
    source_kind: str | None,
    fingerprint: str,
    targets: tuple[str, ...],
    exclusions: tuple[str, ...],
    entries,
    outcomes,
    diagnostics: tuple[Diagnostic, ...] | list[Diagnostic],
    archives: list[dict[str, str]],
    round_id: str,
) -> dict:
    by_path: dict[str, dict[str, object]] = {}
    for item in outcomes:
        by_path.setdefault(item.path, {})[item.target] = item
    rows = []
    for entry in entries:
        target_rows = []
        for target in targets:
            item = by_path.get(entry.path, {}).get(target)
            if item is None:
                continue
            target_rows.append(
                {
                    "target": target,
                    "status": item.status,
                    "targetPath": item.target_path,
                }
            )
        rows.append(
            {
                "path": scrub_secret_text(entry.path),
                "kind": entry.kind,
                "digest": entry.digest,
                "targets": target_rows,
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sourceRoot": scrub_secret_text(source_root),
        "sourceKind": source_kind,
        "inputFingerprint": fingerprint,
        "targets": list(targets),
        "exclusions": list(exclusions),
        "entries": rows,
        "diagnostics": [diagnostic_dict(item) for item in diagnostics],
        "archives": list(archives),
        "roundId": round_id,
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(blob, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
