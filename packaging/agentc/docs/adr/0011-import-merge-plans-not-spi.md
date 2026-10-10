# ADR 0011: import merge composes existing ImportPlans

**Status:** Accepted (Phase 4 leftover merge)
**Date:** 2026-09-11

## Context

Single-source import already scans host trees into one `ImportPlan` without an Importer SPI (ADR 0005, ADR 0008). Parent design §6.3 asks for `agentc import merge` across host directories. Landing detect/scan/plan/convert as a public SPI, or calling `Adapter.parse()`, would couple merge to adapter internals and reopen the façade decision.

## Decision

- Add `import merge` as a sibling of the existing `import <kind>` subcommands.
- For each provided host directory, call existing `build_plan`. Compose one `ImportPlan` by portable destination path. Reuse existing write-out.
- Do not call `Adapter.parse()`. Do not change host `adapter.py` files. Do not change `orchestrator.compile`. Do not land an Importer SPI.
- Source flags do not default to a home directory. Require at least two sources.

## Consequences

- `--cursor` stays a plugin tree. Workspace uses `--cursor-workspace`.
- Same destination with identical redacted bytes keeps one copy. Same destination with different bytes or kinds fails closed.
- Format-normalization merge and overlay / `host-only` stay out of this chapter.
- ADR 0008's leftover note that `import merge` is a later chapter is covered by this decision.
