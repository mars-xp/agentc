# ADR 0009: Remaining Phase 5 install lifecycle stays a translation-layer façade

**Status:** Accepted (Phase 5 leftover install)
**Date:** 2026-09-10

## Context

Phase 5 first slice shipped `agentc install cursor` without an install-state file and without calling `CursorAdapter.install()` (ADR 0006). The leftover chapter adds Codex / Claude Code zip install, uninstall, `status`, and `sync`. Product FR-303 / FR-305 require recording installed files and refusing guessed deletes. Existing host `Adapter.install()` paths register marketplaces or `rsync --delete`, which would overwrite user files.

## Decision

- Keep the façade in `src/agentc/installer/`. All three hosts extract a zip into `--root/<name>` and write a ledger at `--root/.agentc/installs/<host>/<name>.json`, outside the plugin tree.
- Uninstall, `status`, and `sync` read that ledger only. No ledger means fail closed. Do not infer ownership from directory names.
- `sync` replaces only files whose current hash still matches the ledger. User-modified registered files fail the whole operation. Untracked extra files are left in place.
- Do not call `Adapter.install()`. Do not change host `adapter.py` files. Do not change `orchestrator.compile`.
- Cursor may omit `--root` (existing default). Codex and Claude Code require `--root`.

## Consequences

- `install cursor` conflict rule is unchanged: different existing content fails, no `--force`.
- Identical destinations with no ledger still succeed without writing dest and without backfilling a ledger.
- Marketplace registration and SBOM remain later work.
