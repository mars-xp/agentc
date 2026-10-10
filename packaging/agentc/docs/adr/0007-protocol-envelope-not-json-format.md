# ADR 0007: Phase 6 host protocol is a CLI stdout envelope

**Status:** Accepted (Phase 6)
**Date:** 2026-09-10

## Context

Phase 6 adds `--format protocol` so host Skills and automation can read a stable JSON envelope from CLI stdout. Existing `--format json` prints a five-key diagnostic array. Host Skill sources live in `integrations/host-skills/` as one `agent-project` built with existing adapters. Phase 6 must not change that array contract, reimplement the compiler inside plugins, or add client version negotiation.

## Decision

- Implement protocol assembly in `src/agentc/protocol/`; stdout is one JSON object with six keys (`protocolVersion`, `status`, `summary`, `diagnostics`, `artifacts`, `confirmation`).
- Leave `--format json` as the five-key diagnostic array; do not merge envelope fields into json output or describe json as an envelope.
- Ship host Skills from one `agent-project` at `integrations/host-skills/`, packaged with existing `build` / `package` and adapters.
- Do not implement a compiler inside host plugin trees. Do not change `orchestrator.compile` or host `adapter.py` files.
- When envelope assembly fails, `emit_protocol` repairs invalid objects to a valid error envelope with `AGC-P6-0001`.

## Consequences

- Host Skills call `agentc … --format protocol` only; they must not parse `--format text` or treat `--format json` as an envelope.
- `PYTHONPATH=src python3 -m agentc --help` still works without `scripts/` on `PYTHONPATH`.
- Later phases can add client version negotiation or new envelope versions without breaking the Phase 6 json contract.
- Overlay identity keys (`name`, `version`, `author`) in `overlays/<host>/` JSON apply to all hosts: translate lifts them onto shared `PluginIdentity` and strips them from per-host overlay fields before adapter merge.
