---
name: publish-prep
description: Package agent-project zips and install to the local Cursor host.
---

# Publish prep

Use `agentc` with `--format protocol` as the machine source of truth. Do not parse `--format text` output.

## Package

```bash
agentc package <dir> --format protocol
```

## Install to Cursor

Writes to disk require `--yes`:

```bash
agentc install cursor --from <zip> --yes --format protocol
```

Optional install root:

```bash
agentc install cursor --from <zip> --root <dir> --yes --format protocol
```

Read `status`, `diagnostics`, and `artifacts` from the protocol envelope only.
