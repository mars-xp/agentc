# ADR 0014: import merge emits host overlay add-file

**Status:** Accepted (Phase 4 leftover overlay emission)
**Date:** 2026-09-12

## Context

`import merge` already composes host directories into one `agent-project` by portable destination path (ADR 0011). Same path with different bytes fails closed (`AGC-P4-0010`). One-host Skill / Rule / Command / Agent still landed in the generic tree (`skills/` and `extensions/`). Product FR-109 still asks those one-host items to enter overlay or be marked `host-only`. Adding a schema field, calling `Adapter.parse()`, or changing the overlay apply engine would reopen façade and overlay-contract decisions (ADR 0005, ADR 0011, Phase 2 overlay).

## Decision

- After a successful compose, partition convertibles by host. `cursor` / `cursor-workspace` / `cursor-user` count as Cursor.
- One-host Skill / Rule / Command / Agent leave the generic tree and become `add-file` ops in `overlays/<host>/import-merge.json`, with source files beside that JSON. `from` stays inside that host overlay directory. `from` and `to` use the same POSIX relative path as today's portable `dest_rel` (skill directories split to one op per file).
- Do not add a `host-only` field to schema. Do not emit `set` / `remove` / `add-dir`. Do not put Hook / MCP / unknown into overlay.
- Do not change single-source `import` commands. Do not call `Adapter.parse()`. Do not change host `adapter.py` files. Do not change `orchestrator.compile`. Do not change the overlay apply engine.
- No new CLI flag. Dry-run without `--yes` prints the overlay plan and does not create OUT.

## Consequences

- Same-path different-bytes still fails with `AGC-P4-0010` and writes nothing, including overlay.
- Items present on two or more hosts with identical redacted bytes stay in the generic tree.
- `validate` / `build` consume the generated JSON through the existing overlay scanner.
- ADR 0011's leftover that overlay / `host-only` stay out of merge is covered by this decision.
- Public GitHub / PyPI stay out of this chapter.
