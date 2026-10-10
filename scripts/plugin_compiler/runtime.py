# -*- coding: utf-8 -*-
"""Python 3.10+ gate. This file must stay parseable on Python 3.9."""
import sys


def python_version_supported(info):
    return tuple(info[:2]) >= (3, 10)


def require_python_310():
    if python_version_supported(sys.version_info):
        return
    readme = _root_readme_path()
    sys.stderr.write(
        "当前 Python 版本过低。本编译器和 scripts/release.py 需要 Python 3.10 或更高版本；"
        "3.9 及以下不受理。\n说明见：{0}\n".format(readme)
    )
    raise SystemExit(1)


def _root_readme_path():
    filename = __file__.replace("\\", "/")
    suffix = "/scripts/plugin_compiler/runtime.py"
    if filename.endswith(suffix):
        return filename[: -len(suffix)] + "/README.md"
    if filename.endswith("/runtime.py"):
        return filename[: -len("/runtime.py")] + "/README.md"
    return "README.md"
