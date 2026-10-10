# ADR 0010: Audit reuses Phase 0 scan; package writes sidecar SBOM

**Status:** Accepted (Phase 5 leftover security and packaging)
**Date:** 2026-09-10

## Context

Phase 0 already classifies text with blocking vs recording rules. Parent design asks for `agentc audit --for-public` plus release metadata (SHA-256, SBOM, reproducible archives). Embedding SBOM inside the zip would change zip bytes. Rewriting the scanner in `src/agentc/` would fork the rule table. Calling `copy_candidate` from `audit` would mix snapshot materialization with a read-only check.

## Decision

- Add a translation-layer façade in `src/agentc/audit/` that calls existing `opensource_phase0.scan` and `scan-rules.yaml`. Do not copy the scanner into `src/agentc/`. Do not change `copy_candidate` public behavior.
- `audit --for-public DIR` is read-only. Require both the flag and `DIR`. Do not default to cwd or home.
- After `package` writes each zip under `build/dist/`, write `<name>.zip.sha256` and `<name>.zip.cdx.json` beside it. Hand-write minimal CycloneDX JSON using the standard library.
- Pin zip member timestamps in `zip_plugin_tree` to 1980-01-01 so the same outputs produce the same bytes.

## Consequences

- Runtime `audit` still needs `PYTHONPATH=src:scripts` because the scanner lives under `scripts/`. `--help` with `PYTHONPATH=src` must not import it.
- Identical `package` runs become reproducible across hosts; callers of `zip_plugin_tree` inherit the pinned time.
- Legal sign-off, public GitHub, and PyPI stay out of this chapter.
