"""Wrap diagnostics and command metadata into agentc-protocol/v1 envelopes."""

from __future__ import annotations

import json
import sys

from agentc.model.diagnostics import CODE_PROTOCOL_EMIT, Diagnostic
from agentc.protocol.schema import PROTOCOL_VERSION, validate_envelope

__all__ = [
    "PROTOCOL_VERSION",
    "emit_protocol",
    "status_for",
    "wrap",
]


def _diagnostic_to_dict(d: Diagnostic) -> dict[str, str | None]:
    return {
        "code": d.code,
        "level": d.level,
        "message": d.message,
        "path": d.path,
        "hint": d.hint,
    }


def status_for(*, exit_code: int, confirmation_required: bool) -> str:
    if confirmation_required:
        return "unconfirmed"
    if exit_code == 0:
        return "ok"
    return "error"


def wrap(
    *,
    diagnostics: list[Diagnostic],
    exit_code: int,
    artifacts: list[str],
    confirmation_required: bool,
    summary: str,
) -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "status": status_for(
            exit_code=exit_code, confirmation_required=confirmation_required
        ),
        "summary": summary,
        "diagnostics": [_diagnostic_to_dict(d) for d in diagnostics],
        "artifacts": list(artifacts),
        "confirmation": {
            "required": confirmation_required,
            "flag": "--yes" if confirmation_required else None,
        },
    }


def emit_protocol(obj: dict) -> None:
    errors = validate_envelope(obj)
    if not errors:
        print(json.dumps(obj, ensure_ascii=False))
        return

    reason = "; ".join(errors)
    fallback_diag = Diagnostic(
        CODE_PROTOCOL_EMIT,
        "error",
        "protocol envelope validation failed",
        None,
        reason,
    )
    fallback = wrap(
        diagnostics=[fallback_diag],
        exit_code=1,
        artifacts=[],
        confirmation_required=False,
        summary="protocol envelope validation failed",
    )
    fallback_errors = validate_envelope(fallback)
    if not fallback_errors:
        print(json.dumps(fallback, ensure_ascii=False))
        return

    diag_dict = _diagnostic_to_dict(fallback_diag)
    print(json.dumps([diag_dict], ensure_ascii=False))
    print(f"{CODE_PROTOCOL_EMIT}: {reason}", file=sys.stderr)
