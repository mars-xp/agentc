# ADR 0013: install all composes the existing install façade

**Status:** Accepted (Phase 5 leftover multi-host transaction)
**Date:** 2026-09-12

## Context

Single-host `agentc install cursor|codex|claude-code` already writes one zip into `--root/<name>` with a sidecar ledger (ADR 0006, ADR 0009). Product FR-302 still asks for a default three-host transaction and `--partial-ok`. Calling `Adapter.install()` or inventing a second write path would fork conflict rules and undo the façade decision. Pre-write backup (ADR 0012) applies to `sync` / `uninstall`, not to a new install of a missing destination.

## Decision

- Add `agentc install all` as a sibling of the existing `install <host>` subcommands.
- Require three zips (`--cursor`, `--codex`, `--claude-code`). Require `--codex-root` and `--claude-root`. `--cursor-root` may be omitted (existing Cursor default).
- Call existing `install_zip` in a fixed order: Cursor, then Codex, then Claude Code. On default failure, call existing `uninstall_plugin` for hosts this command actually wrote. `--partial-ok` skips that rollback.
- Do not call `Adapter.install()`. Do not change host `adapter.py` files. Do not change `orchestrator.compile`. Do not add `--force`.

## Consequences

- Single-host install, uninstall, sync, restore, and backup contracts stay unchanged.
- A rollback that hits user-modified files cannot finish; the command fails with `AGC-P5-0017` and names the host left behind.
- JSON five-key arrays and protocol six-key envelopes stay unchanged.
- Public GitHub / PyPI stay out of this chapter.
