---
name: build
description: Validate and build agent-project sources for all enabled hosts.
---

# Build

Use `agentc` with `--format protocol` as the machine source of truth. Do not parse `--format text` output.

## Validate

```bash
agentc validate <dir> --format protocol
```

## Build

```bash
agentc build <dir> --format protocol
```

Read `status`, `diagnostics`, and `artifacts` from the protocol envelope only.
