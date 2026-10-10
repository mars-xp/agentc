---
name: migrate
description: Inspect and import existing Cursor plugins into agent-project.
---

# Migrate

Use `agentc` with `--format protocol` as the machine source of truth. Do not parse `--format text` output.

## Inspect a plugin directory

```bash
agentc inspect <dir> --format protocol
```

## Import from Cursor

```bash
agentc import cursor --from <dir> --output <out> --format protocol
```

Writes to disk require `--yes`:

```bash
agentc import cursor --from <dir> --output <out> --yes --format protocol
```

Read `status`, `diagnostics`, and `artifacts` from the protocol envelope only.
