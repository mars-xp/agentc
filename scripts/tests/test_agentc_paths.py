#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def test_rejects_parent_escape():
    from agentc.security.paths import resolve_in_project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        root.mkdir()
        path, err = resolve_in_project(root, "../secret")
        assert path is None
        assert err is not None and err.code == "AGC-P1-0003"


def test_rejects_absolute():
    from agentc.security.paths import resolve_in_project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        root.mkdir()
        path, err = resolve_in_project(root, "/Users/demo/x")
        assert path is None and err.code == "AGC-P1-0003"


def test_rejects_dotdot_in_parts():
    from agentc.security.paths import resolve_in_project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        (root / "skills" / "a").mkdir(parents=True)
        path, err = resolve_in_project(root, "skills/../skills/a")
        assert path is None
        assert err is not None and err.code == "AGC-P1-0003"


def test_rejects_symlink_escape():
    from agentc.security.paths import resolve_in_project

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "proj"
        outside = base / "secret"
        root.mkdir()
        outside.mkdir()
        link = root / "escape"
        os.symlink(outside, link)
        path, err = resolve_in_project(root, "escape")
        assert path is None
        assert err is not None and err.code == "AGC-P1-0003"


def test_accepts_nested_skill():
    from agentc.security.paths import resolve_in_project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        (root / "skills" / "a").mkdir(parents=True)
        path, err = resolve_in_project(root, "skills/a")
        assert err is None and path is not None
        assert path == (root / "skills" / "a").resolve()


def main() -> int:
    tests = (
        test_rejects_parent_escape,
        test_rejects_absolute,
        test_rejects_dotdot_in_parts,
        test_rejects_symlink_escape,
        test_accepts_nested_skill,
    )
    for test in tests:
        test()
        print("PASS", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
