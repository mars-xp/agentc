"""Redact host-specific literals from portable text."""

from __future__ import annotations

import re

_GITHUB_TOKEN = re.compile(r"ghp_[A-Za-z0-9]{20,}")
_PLACEHOLDER = re.compile(r"\$\{[^}]+\}")
_POSIX_HOME = re.compile(r"/(?:Users|home)/[^/\s]+")
_WIN_HOME = re.compile(r"(?i)[a-z]:\\Users\\[^\\\s]+")
_WIN_HOME_FWD = re.compile(r"(?i)[a-z]:/Users/[^/\s]+")
_HOME_PATTERNS = (_POSIX_HOME, _WIN_HOME, _WIN_HOME_FWD)


def redact_portable_text(
    text: str, *, protect_placeholders: bool = True
) -> tuple[str, tuple[str, ...]]:
    placeholders: list[str] = []

    def _protect_placeholder(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x00PH{len(placeholders) - 1}\x00"

    if protect_placeholders:
        protected = _PLACEHOLDER.sub(_protect_placeholder, text)
    else:
        protected = text

    secret_found = False

    def _redact_secret(match: re.Match[str]) -> str:
        nonlocal secret_found
        secret_found = True
        return "<redacted-secret>"

    protected = _GITHUB_TOKEN.sub(_redact_secret, protected)

    home_found = False

    def _redact_home(match: re.Match[str]) -> str:
        nonlocal home_found
        home_found = True
        return "<redacted-home-path>"

    for pattern in _HOME_PATTERNS:
        protected = pattern.sub(_redact_home, protected)

    for index, placeholder in enumerate(placeholders):
        protected = protected.replace(f"\x00PH{index}\x00", placeholder)

    reasons: list[str] = []
    if secret_found:
        reasons.append("secret")
    if home_found:
        reasons.append("home-path")
    return protected, tuple(reasons)


def contains_secret_token(text: str) -> bool:
    """True when text contains a credential-style token, including inside ${...}."""
    _cleaned, reasons = redact_portable_text(text, protect_placeholders=False)
    return "secret" in reasons


def scrub_secret_text(text: str) -> str:
    """Replace credential-style tokens, including inside ${...}. Leave home paths."""
    cleaned, reasons = redact_portable_text(text, protect_placeholders=False)
    if "secret" not in reasons:
        return text
    return cleaned
