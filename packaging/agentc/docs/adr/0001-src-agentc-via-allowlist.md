# ADR 0001: Public product tree at `src/agentc/` via allowlist

**Status:** Accepted (Phase 1)  
**Date:** 2026-09-09

## Context

AgentC is a new public product CLI inside the internal `agent-plugins` monorepo. Phase 0 already materializes a portable candidate repository from an explicit allowlist. Phase 1 adds `init`, `create skill`, and source-level `validate` without changing existing `plugin_compiler` production code.

## Decision

- Author the public product package at `src/agentc/` in the internal repository.
- Include `src/agentc/**`, `schemas/**`, and `packaging/agentc/**` in `public-allowlist.yaml`.
- Copy the candidate with `opensource_phase0.git_candidate`; do not hand-edit `build/agentc-public-candidate/` as source of truth.
- Rename `packaging/agentc/pyproject.toml` to the candidate repository root `pyproject.toml` during materialization.

## Consequences

- Internal development uses `PYTHONPATH=src python3 -m agentc`.
- The monorepo root stays free of a top-level `pyproject.toml`, avoiding mislabeling the whole plugin workspace as one Python package.
- Candidate snapshots stay reproducible from the allowlist and packaging overlays.
