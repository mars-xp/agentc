# ADR 0015: doctor reports host `--version` without rewriting profiles

**Status:** Accepted (Phase 1 leftover host version probe)
**Date:** 2026-09-13

## Context

`agentc doctor` already reports whether `cursor` / `codex` / `claude` exist on `PATH` (`AGC-P1-0010` / `AGC-P1-0011`) without executing them (Phase 1 doctor). Packaged host profiles store `hostVersions: "declared-snapshot"` and `lastVerified: "2026.09"`; those strings are not a semver range. Product FR-001 still asks doctor to show the installed host version. Executing hosts from `matrix`, rewriting packaged YAML, or failing the process when the text disagrees with `lastVerified` would mix a query document, a shipped snapshot, and a local probe.

## Decision

- Keep the probe on `agentc doctor`. Do not change `matrix`. Do not add a new command. Do not add a flag that skips `--version`.
- After `which` hits, run `[resolved_path, "--version"]` with a 3s timeout and an injectable runner. Do not use `shell=True`.
- On success, append truncated version text to the existing `AGC-P1-0010` `message`. JSON stays five keys.
- On timeout, non-zero exit, or unreadable output, keep `AGC-P1-0010` and add `AGC-P1-0013` warning. Do not reuse `AGC-P1-0011`. Do not force exit code 1 for `0013` alone.
- Do not read or rewrite packaged profile YAML. Do not compare the probe text with `lastVerified` / `declared-snapshot`.

## Consequences

- `doctor` executes three host CLIs with `--version` when they are on `PATH`. Tests must inject fake commands and must not launch the user's real Cursor / Codex / Claude.
- Compatibility for `build` remains `build --profile` plus the shipped matrix, not doctor exit codes.
- Public GitHub / PyPI stay out of this chapter.
