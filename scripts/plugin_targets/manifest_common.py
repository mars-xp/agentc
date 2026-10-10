# -*- coding: utf-8 -*-
"""Shared manifest overlay merge, conflict, and dropped-field warnings."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from plugin_compiler.model import CompilerIssue
from plugin_targets.spi import ParsedPlugin


def source_overlay_conflict_issue(
    *,
    target_id: str,
    key: str,
    manifest_relative: str,
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="source-overlay-conflict",
        path=manifest_relative,
        message="source-overlay-conflict: {0} key {1!r} differs".format(
            target_id, key
        ),
    )


def _apply_remove_keys(result: dict, parsed: ParsedPlugin, target_id: str) -> None:
    for overlay in parsed.target_overlays:
        if overlay.target_id != target_id:
            continue
        for key in getattr(overlay, "remove_keys", ()):
            result.pop(key, None)


def apply_overlay_or_extension(
    result: dict,
    parsed: ParsedPlugin,
    target_id: str,
    *,
    whitelist: frozenset[str],
    component_fields: frozenset[str],
    manifest_relative: str,
    validate_overlay: Callable[[dict], None] | None = None,
    restrict_overlay_to_whitelist: bool = False,
) -> tuple[CompilerIssue, ...]:
    """Merge native extension + overlays into result.

    Same-host: native bag first; overlay keys that differ → block issue (no raise).
    Cross-host: apply matching overlay fields.
    """
    issues: list[CompilerIssue] = []

    def _merge_same_host(fields: dict) -> None:
        for key, value in fields.items():
            if key in whitelist and key not in component_fields:
                result[key] = value

    if parsed.source_extension.dialect == target_id:
        _merge_same_host(parsed.source_extension.fields)
        for overlay in parsed.target_overlays:
            if overlay.target_id != target_id:
                continue
            if validate_overlay is not None:
                validate_overlay(overlay.fields)
            for key, value in overlay.fields.items():
                if key in parsed.source_extension.fields:
                    if parsed.source_extension.fields[key] != value:
                        issues.append(
                            source_overlay_conflict_issue(
                                target_id=target_id,
                                key=key,
                                manifest_relative=manifest_relative,
                            )
                        )
                    continue
                if restrict_overlay_to_whitelist:
                    if key in whitelist and key not in component_fields:
                        result[key] = value
                else:
                    result[key] = value
        _apply_remove_keys(result, parsed, target_id)
        return tuple(issues)

    for overlay in parsed.target_overlays:
        if overlay.target_id != target_id:
            continue
        if validate_overlay is not None:
            validate_overlay(overlay.fields)
        result.update(overlay.fields)
    _apply_remove_keys(result, parsed, target_id)
    return tuple(issues)


def dropped_manifest_field_issues(
    parsed: ParsedPlugin,
    *,
    target_id: str,
    whitelist: frozenset[str],
    manifest_relative: str | Path,
    written_keys: set[str] | frozenset[str] | None = None,
    translated_keys: frozenset[str] | set[str] | None = None,
    replaced_keys: frozenset[str] | set[str] | None = None,
    functional_keys: frozenset[str] | set[str] | None = None,
) -> tuple[CompilerIssue, ...]:
    """Warn for source-extension keys not on the target whitelist / not written."""
    path = (
        manifest_relative
        if isinstance(manifest_relative, str)
        else Path(manifest_relative).as_posix()
    )
    kept = set(whitelist) if written_keys is None else set(written_keys)
    translated = set(translated_keys or ())
    replaced = set(replaced_keys or ())
    functional = set(functional_keys or ())
    issues: list[CompilerIssue] = []
    for key in sorted(parsed.source_extension.fields):
        if key in kept:
            continue
        if key in translated:
            issues.append(
                CompilerIssue(
                    severity="warn",
                    code="translated-manifest-declaration",
                    path=path,
                    message="translated manifest declaration {0!r} for target {1}".format(
                        key, target_id
                    ),
                )
            )
            continue
        if key in replaced:
            issues.append(
                CompilerIssue(
                    severity="warn",
                    code="replaced-by-target-overlay",
                    path=path,
                    message="replaced by target overlay {0!r} for target {1}".format(
                        key, target_id
                    ),
                )
            )
            continue
        if key in functional:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="dropped-functional-manifest-field",
                    path=path,
                    message="dropped functional manifest field {0!r} for target {1}".format(
                        key, target_id
                    ),
                )
            )
            continue
        issues.append(
            CompilerIssue(
                severity="warn",
                code="dropped-manifest-field",
                path=path,
                message="dropped manifest field {0!r} for target {1}".format(
                    key, target_id
                ),
            )
        )
    return tuple(issues)
