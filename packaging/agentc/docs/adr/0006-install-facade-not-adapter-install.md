# ADR 0006: Phase 5 Cursor install is a translation-layer façade

**Status:** Accepted (Phase 5)
**Date:** 2026-09-10

## Context

Phase 5 adds `agentc install cursor` so a Cursor plugin zip can be planned and, with `--yes`, written into a plugin install root. Existing `CursorAdapter.install` uses `rsync --delete` and would overwrite user files. Phase 5 must not call that path or change adapter internals.

## Decision

- Implement a translation-layer façade in `src/agentc/installer/` that extracts with `extract_zip_filtered`, compares trees, and writes only when the destination is missing.
- Identical destinations succeed without writing. Conflicts fail without writing. No `--force`.
- Do not call `CursorAdapter.install()`. Do not change host `adapter.py` files. Do not change `orchestrator.compile`.

## Consequences

- Install without `--yes` does not create the destination.
- No install-state file and no `install-report.md` in the plugin tree this slice.
- Later phases can add uninstall, status, other hosts, or wrap adapter install without rewriting this façade's fail-closed conflict rule.
