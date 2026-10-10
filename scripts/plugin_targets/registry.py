from __future__ import annotations

import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    module_path: str
    enabled: bool = True


_DEFAULT: tuple[AdapterSpec, ...] = (
    AdapterSpec("cursor", "plugin_targets.cursor.adapter", True),
    AdapterSpec("codex", "plugin_targets.codex.adapter", True),
    AdapterSpec("claude-code", "plugin_targets.claude_code.adapter", True),
)

_REGISTRY: tuple[AdapterSpec, ...] = _DEFAULT


def registered_specs() -> tuple[AdapterSpec, ...]:
    return _REGISTRY


def enabled_ids() -> tuple[str, ...]:
    return tuple(spec.adapter_id for spec in _REGISTRY if spec.enabled)


def spec_for(adapter_id: str) -> AdapterSpec:
    for spec in _REGISTRY:
        if spec.adapter_id == adapter_id:
            return spec
    raise RuntimeError("unknown adapter id: {0}".format(adapter_id))


def replace_registry_for_tests(specs: tuple[AdapterSpec, ...]) -> None:
    global _REGISTRY
    _REGISTRY = specs


def load_adapter(adapter_id: str) -> object:
    spec = spec_for(adapter_id)
    if not spec.enabled:
        raise RuntimeError("adapter disabled: {0}".format(adapter_id))
    try:
        module = importlib.import_module(spec.module_path)
    except Exception as exc:
        raise RuntimeError(
            "failed to load adapter {0} from {1}: {2}".format(
                adapter_id, spec.module_path, exc
            )
        ) from exc
    factory = getattr(module, "get_adapter", None)
    if not callable(factory):
        raise RuntimeError(
            "adapter module {0} has no get_adapter()".format(spec.module_path)
        )
    return factory()
