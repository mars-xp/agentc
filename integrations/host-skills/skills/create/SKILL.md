---
name: create
description: Create portable skills, rules, commands, and agents with agentc.
---

# Create

Use `agentc` with `--format protocol` as the machine source of truth. Do not parse `--format text` output.

## Create a skill

```bash
agentc create skill <name> --project <dir> --format protocol
```

## Create a rule

```bash
agentc create rule <name> --project <dir> --format protocol
```

## Create a command

```bash
agentc create command <name> --project <dir> --format protocol
```

## Create an agent

```bash
agentc create agent <name> --project <dir> --format protocol
```

Read `status`, `diagnostics`, and `artifacts` from the protocol envelope only.
