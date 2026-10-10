"""Load optional local layout names.

The public export ships this loader only. ``private_layout`` may add directory
and script names used by this repository's own plugins. That module is not
part of the public tree. When it is absent, the public compiler keeps only
the generic names (``outputs``, root ``build`` / ``dist``, and so on).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path


def _private():
    path = Path(__file__).resolve().parent / "private_layout.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("agentc_private_layout", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extra_output_dir_names() -> frozenset[str]:
    module = _private()
    if module is None:
        return frozenset()
    return frozenset(module.OUTPUT_DIR_NAMES)


def extra_root_script_names() -> frozenset[str]:
    module = _private()
    if module is None:
        return frozenset()
    return frozenset(module.ROOT_SCRIPT_NAMES)


def extra_hook_import_modules() -> tuple[str, ...]:
    module = _private()
    if module is None:
        return ()
    return tuple(module.HOOK_IMPORT_MODULES)


def _optional_pattern(attr: str) -> re.Pattern[str] | None:
    module = _private()
    if module is None:
        return None
    text = getattr(module, attr, "")
    if not text:
        return None
    return re.compile(text)


def extra_cursor_skill_prefix() -> re.Pattern[str] | None:
    return _optional_pattern("CURSOR_SKILL_PREFIX")


def extra_local_skill_command() -> re.Pattern[str] | None:
    return _optional_pattern("LOCAL_SKILL_COMMAND")
