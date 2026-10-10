#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import io
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.runtime import python_version_supported, require_python_310

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
RELEASE_PY = SCRIPTS_DIR / "release.py"


def test_python_310_gate():
    assert python_version_supported((3, 9, 0)) is False
    assert python_version_supported((3, 10, 0)) is True


def test_release_py_imports_plugin_compiler_after_version_check():
    source = RELEASE_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    check_line = None
    compiler_line = None
    packaging_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "require_python_310":
                check_line = node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "plugin_packaging":
                    packaging_line = node.lineno
                if alias.name == "plugin_compiler":
                    compiler_line = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "plugin_compiler":
                compiler_line = node.lineno
    assert check_line is not None
    assert compiler_line is not None
    assert packaging_line is not None
    assert check_line < compiler_line
    assert check_line < packaging_line


def test_require_python_310_exits_with_chinese_readme_on_39():
    fake_err = io.StringIO()
    with patch.object(sys, "version_info", (3, 9, 0, "final", 0)):
        with patch.object(sys, "stderr", fake_err):
            try:
                require_python_310()
            except SystemExit as exc:
                assert exc.code not in (0, None)
            else:
                raise AssertionError("expected SystemExit")
    text = fake_err.getvalue()
    assert "Python 3.10" in text
    assert "README" in text


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
