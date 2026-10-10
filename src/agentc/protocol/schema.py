"""Hand-written validation for agentc-protocol/v1 envelopes."""

from __future__ import annotations

PROTOCOL_VERSION = "agentc-protocol/v1"

_REQUIRED_KEYS = (
    "protocolVersion",
    "status",
    "summary",
    "diagnostics",
    "artifacts",
    "confirmation",
)
_DIAGNOSTIC_KEYS = ("code", "level", "message", "path", "hint")
_VALID_STATUS = frozenset({"ok", "unconfirmed", "error"})


def validate_envelope(obj: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["envelope must be an object"]

    extra = set(obj) - set(_REQUIRED_KEYS)
    if extra:
        errors.append("unexpected keys: " + ", ".join(sorted(extra)))

    missing = [key for key in _REQUIRED_KEYS if key not in obj]
    if missing:
        errors.append("missing keys: " + ", ".join(missing))
        return errors

    version = obj["protocolVersion"]
    if version != PROTOCOL_VERSION:
        errors.append(
            f"protocolVersion must be {PROTOCOL_VERSION!r}, got {version!r}"
        )

    status = obj["status"]
    if status not in _VALID_STATUS:
        errors.append(
            "status must be one of ok, unconfirmed, error, got " + repr(status)
        )

    if not isinstance(obj["summary"], str):
        errors.append("summary must be a string")

    diagnostics = obj["diagnostics"]
    if not isinstance(diagnostics, list):
        errors.append("diagnostics must be an array")
    else:
        for index, item in enumerate(diagnostics):
            prefix = f"diagnostics[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be an object")
                continue
            item_extra = set(item) - set(_DIAGNOSTIC_KEYS)
            if item_extra:
                errors.append(
                    f"{prefix} unexpected keys: " + ", ".join(sorted(item_extra))
                )
            item_missing = [key for key in _DIAGNOSTIC_KEYS if key not in item]
            if item_missing:
                errors.append(
                    f"{prefix} missing keys: " + ", ".join(item_missing)
                )
                continue
            for key in ("code", "level", "message", "hint"):
                if not isinstance(item[key], str):
                    errors.append(f"{prefix}.{key} must be a string")
            path = item["path"]
            if path is not None and not isinstance(path, str):
                errors.append(f"{prefix}.path must be a string or null")

    artifacts = obj["artifacts"]
    if not isinstance(artifacts, list):
        errors.append("artifacts must be an array")
    else:
        for index, item in enumerate(artifacts):
            if not isinstance(item, str):
                errors.append(f"artifacts[{index}] must be a string")

    confirmation = obj["confirmation"]
    if not isinstance(confirmation, dict):
        errors.append("confirmation must be an object")
    else:
        if set(confirmation) != {"required", "flag"}:
            errors.append("confirmation must have only required and flag")
        if not isinstance(confirmation.get("required"), bool):
            errors.append("confirmation.required must be a boolean")
        flag = confirmation.get("flag")
        if flag is not None and not isinstance(flag, str):
            errors.append("confirmation.flag must be a string or null")

    return errors
