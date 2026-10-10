# ADR 0012: Pre-write backup lives beside the install ledger

**Status:** Accepted (Phase 5 leftover backup)
**Date:** 2026-09-12

## Context

Phase 5 install writes zip trees under `--root/<name>` and records ownership at `--root/.agentc/installs/<host>/<name>.json` (ADR 0006, ADR 0009). `install` still refuses to overwrite a conflicting destination. `sync` and `uninstall` do replace or delete registered files. Product FR-302 requires a recoverable copy before those writes, and a restore path after a successful mutation. Putting `.bak` files inside the plugin tree would mix backup with installed files and blur uninstall boundaries. Calling `Adapter.install()` would reopen the façade decision.

## Decision

- Keep the façade in `src/agentc/installer/`. Do not call `Adapter.install()`. Do not change host `adapter.py` files. Do not change `orchestrator.compile`.
- Store the latest backup at `--root/.agentc/backups/<host>/<name>/`: `manifest.json` plus a `files/` tree of currently registered paths. One slot per host + plugin name.
- Snapshot the full ledger file set before `sync` replaces or deletes any registered file, and before `uninstall` deletes registered files. `install` does not snapshot. Unregistered extra files are not copied.
- Add `agentc restore <host> --name NAME [--root DIR] [--yes]`. Restore can undo a successful `sync` or `uninstall`. User-modified registered files fail closed. No `--force`.
- While a current ledger exists, restore swaps: the current registered tree becomes the new latest backup after the previous backup is written back. After uninstall (no ledger), restore rebuilds dest + ledger from the backup and leaves the backup slot unchanged.

## Consequences

- `install` conflict rule is unchanged: different existing content fails, destination untouched.
- Add-only `sync` (create zip-only paths, no replace or delete) does not refresh the backup slot.
- Mid-write failure rolls dest back from the backup just taken; the new ledger is not committed.
- Three-host install transactions and `--partial-ok` stay out of this chapter.
- JSON five-key arrays and protocol six-key envelopes stay unchanged.
