# ADR 0016: `init` writes a project GitHub Actions example

**Status:** Accepted (`init` leftover GitHub Actions example)
**Date:** 2026-09-13

## Context

Parent design Phase 3 asks for a GitHub Actions example. Phase 1 `init` deferred `.github/` so it would not ship an unimplemented pipeline. Phase 0 already has a **compiler-snapshot** `ci.yml` that runs `scripts/tests/test_*.py`; that file is the wrong layout for a generated agent-project. Public GitHub / PyPI are still closed, so a generated workflow cannot install `agentc` from a public index.

## Decision

- `agentc init` always writes `.github/workflows/ci.yml` into the **new project**. No new flag.
- The workflow is one job with `ubuntu-latest` / `windows-latest` / `macos-latest`, Python 3.10, `agentc validate .` then `agentc build .`.
- Header comment: install `agentc` yourself; no public install source yet; the file will not go green on public GitHub by default. Do not vendor compiler sources. Do not `pip install agentc` in the YAML.
- Do not run `install` / `doctor` / `package`. Do not publish. Do not pin action SHAs in this chapter.
- Only new `init` trees. No backfill command. `doctor` does not mention this file.
- Do not copy the Phase 0 compiler `ci.yml`. Do not change this repository's own `.github/`.

## Consequences

- `test_init_writes_schema_and_no_github` must assert the workflow exists and is a project CI example.
- Existing projects are unchanged until someone copies the example by hand.
- Public runners will fail to find `agentc` until a later public install source exists; that is expected for this chapter.
