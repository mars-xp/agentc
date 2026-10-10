#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.text import write_text_utf8_lf


def test_write_text_utf8_lf_does_not_use_path_write_text_newline():
    signature = inspect.signature(Path.write_text)
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "out.txt"
        write_text_utf8_lf(path, "alpha\nbeta\n")
        assert path.read_bytes() == b"alpha\nbeta\n"
    if "newline" not in signature.parameters:
        # Python 3.9: the helper must not have called Path.write_text(newline=...).
        return


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
