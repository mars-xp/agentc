# -*- coding: utf-8 -*-
"""Host-neutral packaging/ overlay loading for parse (not compile)."""
from __future__ import annotations

import json
from pathlib import Path

from plugin_targets.spi import TargetOverlay

# packaging/<file> → target_id. Parse loads these into the model; compile must not.
PACKAGING_OVERLAY_FILES: tuple[tuple[str, str], ...] = (
    ("cursor", "packaging/cursor-plugin.json"),
    ("codex", "packaging/codex-plugin.json"),
    ("claude-code", "packaging/claude-plugin.json"),
)


def _read_json_object(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("missing plugin manifest: {0}".format(path)) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("{0}: invalid JSON: {1}".format(path, exc)) from exc
    if not isinstance(raw, dict):
        raise RuntimeError("{0}: root must be an object".format(path))
    return raw


def _load_packaging_overlay(path: Path, *, target_id: str) -> TargetOverlay | None:
    if not path.is_file():
        return None
    overlay = _read_json_object(path)
    if "description" in overlay and not isinstance(overlay["description"], str):
        raise RuntimeError("{0}: description must be a string".format(path))
    if target_id == "codex" and "interface" in overlay and not isinstance(
        overlay["interface"], dict
    ):
        raise RuntimeError("{0}: interface must be an object".format(path))
    return TargetOverlay(target_id=target_id, fields=dict(overlay))


def load_target_overlays(
    plugin_dir: Path,
    *,
    skip_dialect: str | None = None,
) -> tuple[TargetOverlay, ...]:
    """Load packaging/*.json overlays for hosts other than skip_dialect."""
    plugin_dir = Path(plugin_dir)
    overlays: list[TargetOverlay] = []
    for target_id, relative in PACKAGING_OVERLAY_FILES:
        if skip_dialect is not None and target_id == skip_dialect:
            continue
        loaded = _load_packaging_overlay(plugin_dir / relative, target_id=target_id)
        if loaded is not None:
            overlays.append(loaded)
    return tuple(overlays)


def load_target_overlays_for_parse(
    plugin_dir: Path,
    *,
    source_dialect: str,
    extension_fields: dict,
) -> tuple[TargetOverlay, ...]:
    """Load other-host overlays and own-dialect packaging when the file exists.

    Conflict detection stays in apply_overlay_or_extension. extension_fields is
    kept for call-site compatibility.
    """
    del extension_fields
    overlays = list(
        load_target_overlays(plugin_dir, skip_dialect=source_dialect)
    )
    own_relative = None
    for target_id, relative in PACKAGING_OVERLAY_FILES:
        if target_id == source_dialect:
            own_relative = relative
            break
    if own_relative is None:
        return tuple(overlays)
    own = _load_packaging_overlay(
        Path(plugin_dir) / own_relative, target_id=source_dialect
    )
    if own is not None:
        overlays.append(own)
    return tuple(overlays)
