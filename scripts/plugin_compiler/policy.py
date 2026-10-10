from __future__ import annotations

from pathlib import Path

HIGH_RISK_NAMES = frozenset(
    {
        ".env",
        "credential.json",
        "credentials.json",
        "secret.json",
        "secrets.json",
        "token.json",
        "tokens.json",
    }
)
CERT_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})


def _lower_name(name: str) -> str:
    return name.lower()


def _has_template_marker(name: str) -> bool:
    lower = _lower_name(name)
    if lower.endswith(".example") or lower.endswith(".template"):
        return True
    parts = lower.split(".")
    return "example" in parts or "template" in parts


def _is_local_config(name: str) -> bool:
    lower = _lower_name(name)
    if lower.endswith(".local"):
        return True
    parts = lower.split(".")
    try:
        index = parts.index("local")
    except ValueError:
        return False
    return index < len(parts) - 1 or lower.endswith(".local")


def _is_secret_marker(name: str) -> bool:
    lower = _lower_name(name)
    if lower.endswith(".secret"):
        return True
    parts = lower.split(".")
    return "secret" in parts and not _has_template_marker(name)


def classify_pack_filename(name: str) -> str:
    lower = _lower_name(name)
    suffix = Path(lower).suffix
    if suffix in CERT_SUFFIXES:
        return "block"
    if lower in HIGH_RISK_NAMES:
        return "block"
    if _has_template_marker(name) and lower not in HIGH_RISK_NAMES:
        return "allow"
    if lower.startswith(".env."):
        return "block"
    if _is_local_config(name) or _is_secret_marker(name):
        return "block"
    return "allow"


def is_blocked_sensitive_path(path: Path) -> bool:
    return classify_pack_filename(path.name) == "block"
