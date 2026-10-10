# ADR 0004: Phase 3 package zips outputs, not publish

**Status:** Accepted (Phase 3)  
**Date:** 2026-09-09

## Context

Phase 3 adds `agentc package` to turn successful per-host build trees into distributable zip archives. The internal monorepo already ships `plugin_compiler.zip_tree.zip_plugin_tree` and host adapters with `publish` / `install` entry points used by existing plugin workflows. Phase 3 must reuse zip creation without forking adapter internals or teaching the SPI about project-level packaging.

## Decision

- `agentc package` only zips **existing** `build/outputs/<host>/` trees; it does **not** re-run `build` or call host adapters.
- Call `plugin_compiler.zip_tree.zip_plugin_tree(tree, archive_root_name, dest_zip)` to write archives under `build/dist/<host>/<project_id>.zip`.
- Do **not** call `publish_local`, Adapter `publish`, or Adapter `install`.
- Do **not** add a `package()` method to the Adapter SPI.

## Consequences

- `agentc package` can import `plugin_compiler` zip helpers, but packaging logic stays in `src/agentc/build/package.py`.
- Missing outputs are reported with `AGC-P3-0001` and follow the same strict / compatible / permissive policy as build blockers.
- Path escape and IO failures (`AGC-P3-0002`, `AGC-P3-0003`) remain fatal even under `compatible`.
- Future phases can add install or marketplace flows without rewriting Phase 3 zip semantics.
