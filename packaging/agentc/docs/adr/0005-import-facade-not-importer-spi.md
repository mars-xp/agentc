# ADR 0005: Phase 4 Cursor plugin import is a translation-layer façade

**Status:** Accepted (Phase 4)
**Date:** 2026-09-10

## Context

Phase 4 adds `agentc inspect` and `agentc import cursor` so a Cursor plugin tree can be read and, with `--yes`, written as a portable `agent-project`. The parent design describes a full Importer SPI. Phase 4 must reuse `scan_plugin` and existing document layout without teaching adapters or the orchestrator about import, and without shipping detect/scan/plan/convert/validate as a public SPI.

## Decision

- Implement a translation-layer façade in `src/agentc/importers/` that shares one scan → classify → plan pipeline between `inspect` and `import`.
- Write a source-level `agent-project` on `import --yes`; do not install into host config directories.
- Do not land a full Importer SPI. Do not change host `adapter.py` files. Do not change `orchestrator.compile`.

## Consequences

- `inspect` stays read-only. `import` without `--yes` does not create the output directory.
- Hook / MCP / unknown items stay in the text report and `import-report.md`, not in `contents`.
- Later phases can add workspace/user/Codex/Claude Code importers or a real SPI without rewriting Adapter internals.
