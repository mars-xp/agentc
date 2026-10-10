# ADR 0008: Remaining Phase 4 host imports stay a translation-layer façade

**Status:** Accepted (Phase 4 leftover hosts)
**Date:** 2026-09-10

## Context

Phase 4 first slice shipped Cursor plugin `inspect` and `import cursor` without an Importer SPI (ADR 0005). The leftover chapter adds Cursor workspace, Cursor user, Codex, and Claude Code single-source import, plus optional `inspect --kind` with fail-closed auto-detect. The parent design still describes a full Importer SPI (`detect` / `scan` / `plan` / `convert`). Calling host `Adapter.parse()` would couple import to adapter internals.

## Decision

- Keep the façade in `src/agentc/importers/`. Codex and Claude Code reuse `scan_plugin` for `.codex-plugin` / `.claude-plugin` trees. Workspace and user trees get a read-only scan that still produces the same `ImportPlan`.
- Auto-detect is a mutually exclusive fingerprint table on the directory root, not a public `Importer.detect()` SPI. Ambiguous or unknown sources fail; do not guess.
- Do not call `Adapter.parse()`. Do not change host `adapter.py` files. Do not change `orchestrator.compile`.
- `--from` remains required. Do not default to a home directory.

## Consequences

- `import cursor` stays plugin-only. Other kinds are explicit subcommands.
- Hook / MCP / host config / marketplace / unknown items stay unresolved, not `contents`.
- `import merge` remains a later chapter.
