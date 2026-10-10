# ADR 0002: Phase 1 `validate` without `plugin_compiler`

**Status:** Accepted (Phase 1)  
**Date:** 2026-09-09

## Context

The internal tree already ships `scripts/plugin_compiler/` and host adapters under `scripts/plugin_targets/`. Phase 1 must deliver portable project validation (schema, paths, security defaults) for new `agent-project/v1` trees without coupling to three-host adapter wrapping or marketplace wiring.

## Decision

- Implement Phase 1 `agentc validate` in `src/agentc/validate/` against the canonical IR only.
- Do **not** call `plugin_compiler`, `plugin_targets`, or `release.py` from Phase 1 validate.
- Defer adapter/importer packaging, build plans, and host-specific artifact checks to Phase 2.

## Consequences

- `validate` stays offline, does not execute user scripts, and does not require internal plugin trees.
- Phase 0 portable tests for `plugin_compiler` remain unchanged; new coverage lives in `scripts/tests/test_agentc_*.py`.
- Phase 2 can wrap existing compiler code behind adapters without rewriting Phase 1 diagnostics.
