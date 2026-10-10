#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.policy import classify_pack_filename


def test_local_json_and_fixed_secrets_are_blocked():
    for name in (
        "feishu_assignees.local.json",
        "excludes.local.json",
        "config.local",
        ".env",
        ".env.production",
        "credentials.json",
        "tokens.json",
        "app.secret",
        "app.secret.json",
        "private.key",
        "private.template.pem",
    ):
        assert classify_pack_filename(name) == "block", name


def test_templates_are_allowed_except_cert_suffixes():
    for name in (
        "credentials.local.template.json",
        ".env.example",
        "notes.template",
        "pms-oauth.local.env.example",
    ):
        assert classify_pack_filename(name) == "allow", name


def main() -> int:
    failures = []
    for key, value in sorted(globals().items()):
        if key.startswith("test_") and callable(value):
            try:
                value()
                print("PASS", key)
            except Exception as exc:
                failures.append((key, exc))
                print("FAIL", key, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
