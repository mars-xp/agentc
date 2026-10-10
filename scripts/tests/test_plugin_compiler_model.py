#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.model import CapabilityKind, CapabilityRef


def test_capability_ref_serializes_stable_source_identity():
    ref = CapabilityRef(
        capability_id="rule:rules/python-only.mdc",
        kind=CapabilityKind.RULE,
        source_path="rules/python-only.mdc",
        source_dialect="cursor",
    )
    assert json.loads(json.dumps(ref.to_dict())) == {
        "id": "rule:rules/python-only.mdc",
        "kind": "rule",
        "source_path": "rules/python-only.mdc",
        "source_dialect": "cursor",
    }


def main() -> int:
    failures = []
    for key, value in sorted(globals().items()):
        if key.startswith("test_") and callable(value):
            try:
                value()
                print("PASS", key)
            except Exception as exc:
                failures.append((key, exc))
                print("FAIL", key, type(exc).__name__, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
