# -*- coding: utf-8 -*-
"""Resolve which host adapter owns a plugin source directory."""
from __future__ import annotations

from pathlib import Path

from plugin_targets.registry import enabled_ids, load_adapter, registered_specs, spec_for

SOURCE_FORMATS = ("auto", "cursor", "codex", "claude-code")
_SOURCE_MANIFESTS = {
    "cursor": ".cursor-plugin/plugin.json",
    "codex": ".codex-plugin/plugin.json",
    "claude-code": ".claude-plugin/plugin.json",
}


def _has_source_manifest(plugin_dir: Path, adapter_id: str) -> bool:
    relative = _SOURCE_MANIFESTS.get(adapter_id)
    if relative is None:
        return False
    return (plugin_dir / relative).is_file()


class SourceResolutionError(RuntimeError):
    """zero match / ambiguous / format mismatch; str(exc) 给人看。"""


def resolve_source_adapter(
    plugin_dir: Path,
    source_format: str = "auto",
) -> object:
    """返回已 load 的宿主 adapter。source_format: auto|cursor|codex|claude-code。"""
    plugin_dir = Path(plugin_dir)
    if source_format == "auto":
        matches: list[object] = []
        for adapter_id in enabled_ids():
            try:
                adapter = load_adapter(adapter_id)
            except RuntimeError:
                # 坏掉的 enabled adapter 不得拖垮其它宿主的 auto 识别
                continue
            detect = getattr(adapter, "detect", None)
            if callable(detect) and detect(plugin_dir):
                matches.append(adapter)
        disabled_native = [
            spec.adapter_id
            for spec in registered_specs()
            if not spec.enabled and _has_source_manifest(plugin_dir, spec.adapter_id)
        ]
        if disabled_native:
            matches = [
                item
                for item in matches
                if _has_source_manifest(plugin_dir, getattr(item, "id", ""))
            ]
        if not matches:
            raise SourceResolutionError(
                "找不到插件：目录下没有已启用宿主认领的来源清单：{0}".format(
                    plugin_dir
                )
            )
        if len(matches) >= 2:
            manifest_matches = [
                item
                for item in matches
                if _has_source_manifest(plugin_dir, getattr(item, "id", ""))
            ]
            if len(manifest_matches) == 1:
                return manifest_matches[0]
            ids = ", ".join(getattr(item, "id", "?") for item in matches)
            raise SourceResolutionError(
                "多个来源清单同时命中（{0}）：{1}".format(ids, plugin_dir)
            )
        return matches[0]

    if source_format not in SOURCE_FORMATS:
        raise SourceResolutionError(
            "未知 --source-format：{0}".format(source_format)
        )

    try:
        spec = spec_for(source_format)
    except RuntimeError as exc:
        raise SourceResolutionError(str(exc)) from exc
    if not spec.enabled:
        raise SourceResolutionError(
            "adapter disabled: {0}".format(source_format)
        )

    adapter = load_adapter(source_format)
    detect = getattr(adapter, "detect", None)
    if not callable(detect) or not detect(plugin_dir):
        raise SourceResolutionError(
            "--source-format 与源码不符：要求 {0}，但目录未命中该来源清单：{1}".format(
                source_format, plugin_dir
            )
        )
    return adapter
