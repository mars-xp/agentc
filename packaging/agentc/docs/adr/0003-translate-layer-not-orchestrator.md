# ADR 0003: Phase 2 translation layer, not orchestrator changes

**Status:** Accepted (Phase 2)  
**Date:** 2026-09-09

## Context

Phase 2 adds `agentc build` to compile portable `agent-project/v1` trees into host-specific artifacts. The internal monorepo already ships `plugin_targets` adapters and `plugin_compiler` orchestration used by existing plugin workflows. Phase 2 must reuse that investment without forking adapter internals or teaching the orchestrator about the new project schema.

## Decision

- Implement a translation layer in `src/agentc/build/` that maps Canonical IR plus on-disk skills into the existing `ParsedPlugin` / `CompileRequest` shapes adapters already accept.
- Call existing host adapters through `plugin_targets.registry.load_adapter`; Phase 2 does **not** change `plugin_compiler.orchestrator.compile` main path.
- Do **not** render the project into a temporary legacy plugin tree and feed that tree to the orchestrator. Artifacts stay under the project's own `build/` layout via `BuildLayout.from_repo(project_root)`.

## Consequences

- `agentc validate` remains source-layer only and still does not call `plugin_compiler`.
- `agentc build` can import adapters and compiler modules, but build logic stays in the translation layer plus per-host adapter calls.
- Future phases can extend translation and matrix policy without rewriting Phase 1 diagnostics or adapter SPI contracts.
