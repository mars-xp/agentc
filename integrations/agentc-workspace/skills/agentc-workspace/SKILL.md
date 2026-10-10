---
name: agentc-workspace
description: Compile one plugin directory with agentc and install the zip only when asked.
---

# AgentC workspace

Use `agentc` with `--format protocol` as the machine source of truth. Do not parse `--format text` output.

Do not assume the agentc command is installed with this skill.

## Confirm the CLI

```bash
agentc doctor --format protocol
```

Read `status` and `diagnostics` from the protocol envelope. If this command is missing, stop and ask the user to install the CLI. Do not continue.

## Choose the source and targets

The source root is the plugin directory the user named, not the application repository root.

The targets are whichever hosts the user named.

## Build

```bash
agentc workspace build <source> --targets <hosts> --format protocol
```

Parse only the protocol envelope and the report. Do not parse natural-language output.

The report is `workspace-build-report.json`. Its path is in the diagnostic `hint` or in `artifacts` when the build succeeds. The only fixed exclusion is the .git/ directory at the source root.

On failure, name the original paths from the diagnostics and the report. Ask the user to fix those files and retry. Do not install.

On success, show this round's ZIP paths and the SHA-256 in archives[].sha256 from workspace-build-report.json. That hash is not a field of the protocol envelope.

## Install or update this round

Install only when the user asked to install and named an install root. Use this round's ZIP.

Before install or sync, run status with the same --root and --kind.

```bash
agentc status --kind <host> --name <name> --root <dir> --format protocol
```

If the status diagnostics include message `unregistered`, run install.

```bash
agentc install <host> --from <zip> --root <dir> --yes --format protocol
```

If status does not report `unregistered` and the diagnostics do not contain AGC-P5-0008, the install is registered and has not drifted. Run sync.

```bash
agentc sync <host> --from <zip> --root <dir> --yes --format protocol
```

If the diagnostics contain AGC-P5-0008, stop. The installed files have drifted. Do not install or sync.

After install or sync succeeds, run status again with the same --root and --kind.

```bash
agentc status --kind <host> --name <name> --root <dir> --format protocol
```

If install or sync fails, show the diagnostics and run status with the same --root and --kind. Do not run restore.

A missing zip, a conflict, and any other failure before the destination is written leave the current files in place.

Run restore only when the user explicitly asks to undo a change this operation already wrote. Show the command and wait. Do not add --yes until the user confirms that restore.

```bash
agentc restore <host> --name <name> --root <dir> --format protocol
```

Do not create a side project. Do not call import. Do not install a partial result.
