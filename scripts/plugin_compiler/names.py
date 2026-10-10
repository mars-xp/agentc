from __future__ import annotations

import re

from .model import Target
from .paths import require_safe_plugin_name

_CURSOR_PLUGIN_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_KEBAB_PLUGIN_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class InvalidPluginNameError(Exception):
    """Path-safe plugin name that is not allowed for the requested targets."""


def plugin_name_allowed(name: str, targets: tuple[Target, ...]) -> bool:
    require_safe_plugin_name(name)
    needs_kebab = any(target is not Target.CURSOR for target in targets)
    if needs_kebab:
        return bool(_KEBAB_PLUGIN_NAME.fullmatch(name))
    return bool(_CURSOR_PLUGIN_NAME.fullmatch(name))


def assert_plugin_name_for_targets(name: str, targets: tuple[Target, ...]) -> None:
    if not plugin_name_allowed(name, targets):
        raise InvalidPluginNameError(
            "plugin name {0!r} is not allowed for the requested targets".format(name)
        )
