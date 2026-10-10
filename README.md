# AgentC (preview)

English | [简体中文](README.zh-CN.md)

AgentC is a cross-platform agent plugin compiler. This repository is a **preview**
of the portable agent-project CLI (`agentc`). It is **not** a 1.0 product release.

This tree does not include internal host plugins. Longer phase notes live in
[history.md](history.md).

## Quick start

Install the CLI, install the AgentC skill, compile from chat in the plugin directory, then install into a directory you name.

### Install the CLI

From the repository root:

```bash
python3 -m pip install .
agentc doctor --format protocol
```

`agentc doctor --format protocol` checks that the command is installed. The skill does not install the CLI.

### Install the AgentC skill

The skill source is one file, `integrations/agentc-workspace/skills/agentc-workspace/SKILL.md`. Package that project, then install the zip for the host you use. Cursor may omit `--root`. Codex and Claude Code require `--root`.

```bash
agentc build integrations/agentc-workspace
agentc package integrations/agentc-workspace
agentc install cursor --from integrations/agentc-workspace/build/dist/cursor/agentc-workspace.zip --yes --format protocol
agentc status --kind cursor --name agentc-workspace --format protocol
agentc install codex --from integrations/agentc-workspace/build/dist/codex/agentc-workspace.zip --root DIR --yes --format protocol
agentc status --kind codex --name agentc-workspace --root DIR --format protocol
agentc install claude-code --from integrations/agentc-workspace/build/dist/claude-code/agentc-workspace.zip --root DIR --yes --format protocol
agentc status --kind claude-code --name agentc-workspace --root DIR --format protocol
```

Codex and Claude Code installs are not visible from the default Cursor install root. When an install used `--root`, `status` passes that same `--root` and `--kind` for that single host. If that status says the plugin is already registered and does not report drift code AGC-P5-0008, update with `agentc sync` instead of running `install` again. A built zip is not proof the host loaded the skill.

### Compile in the plugin directory

The source is the plugin directory itself, not the root of an application repository. In chat, name that directory and the target hosts. The same command on the CLI:

```bash
agentc workspace build ./my-plugin --targets cursor,codex,claude-code --format protocol
```

Read the protocol envelope and `workspace-build-report.json`. Do not parse the text summary as the result.

The only fixed exclusion is the `.git/` directory at the source root.
Every other path is inventoried and reported for each target. Ordinary files, including scripts and assets, are bundled. `packaging/` is consumed as compiler configuration; previous `build/`, `dist/`, and cache files are reported as `ignored`. Local `.local` configuration is `protected`, and content explicitly disabled for a target is `excluded`.

### Install into a directory you name

When the build succeeds and you asked to install into a directory you named, query status with that same root before writing:

```bash
agentc status --kind codex --name NAME --root /chosen/codex/root --format protocol
```

If the protocol diagnostics include `AGC-P5-0020` (unregistered), install this round's zip:

```bash
agentc install codex --from /path/to/codex.zip --root /chosen/codex/root --yes --format protocol
```

If the protocol diagnostics include `AGC-P5-0019` (registered) and do not include drift code `AGC-P5-0008`, update with sync:

```bash
agentc sync codex --from /path/to/codex.zip --root /chosen/codex/root --yes --format protocol
```

If status reports AGC-P5-0008, stop. The installed files have drifted. Do not install or sync.

After install or sync, run status again with the same `--root` and `--kind`:

```bash
agentc status --kind codex --name NAME --root /chosen/codex/root --format protocol
```

### Fix the original directory and retry

When the build fails, the report names paths in the original plugin directory. Change those files there and run the same build again. Do not install a failed build. That round publishes no zip.

If install or sync fails, show the diagnostics and run status with the same `--root`. Do not run restore.

A missing zip, a destination conflict, and any other failure before the installer writes the destination leave the current files in place.

Run restore only when you already confirmed this operation changed the destination and you want the previous backup. Add `--yes` only after that command shows the backup you intend to put back:

```bash
agentc restore codex --name NAME --root /chosen/codex/root --format protocol
```

## Install and try the CLI

From the candidate repository root (after the allowlist copy), install the wheel layout locally:

```bash
python3 -m pip install .
agentc --help
agentc doctor
agentc doctor --dir ./my-agent
agentc matrix
agentc matrix --format json
```

After `pip install .`, the console script is `agentc`. `plugin_compiler` and `plugin_targets` are importable packages, not extra console scripts. `init`, `create`, `validate`, `build`, `package`, `audit`, `inspect`, `workspace build`, and `install` / `uninstall` / `status` / `sync` / `restore` all run as `agentc` without setting `PYTHONPATH`.

A git checkout that has not been installed still needs the `PYTHONPATH` environment variable set to `src:scripts` before running `python3 -m agentc`.

```bash
agentc build DIR [--profile HOST/ID]
agentc package DIR
agentc inspect DIR [--kind KIND]
agentc workspace build DIR --targets cursor,codex,claude-code
agentc install cursor --from plugin.zip --root DIR [--yes]
agentc install codex --from plugin.zip --root DIR [--yes]
agentc install claude-code --from plugin.zip --root DIR [--yes]
agentc install all \
  --cursor CURSOR.zip --codex CODEX.zip --claude-code CLAUDE.zip \
  [--cursor-root DIR] --codex-root DIR --claude-root DIR \
  [--yes] [--partial-ok]
agentc uninstall cursor --name NAME --root DIR [--yes]
agentc status [--kind HOST] [--name NAME] [--root DIR]
agentc sync cursor --from plugin.zip --root DIR [--yes]
agentc restore cursor --name NAME --root DIR [--yes]
```

Typical flow:

```bash
python3 -m pip install .
agentc init ./my-agent --id acme --name Acme --targets cursor
agentc create skill hello --project ./my-agent
agentc create rule house-style --project ./my-agent
agentc validate ./my-agent
agentc build ./my-agent
agentc build ./my-agent --profile cursor/2026.09
agentc package ./my-agent
agentc audit --for-public ./my-agent
```

`init` writes an example GitHub Actions workflow at `.github/workflows/ci.yml` (`validate` then `build`, Ubuntu / Windows / macOS, Python 3.10). That generated workflow must install `agentc` before invoking it; for now, install from a checkout or wheel built from this repository. This repository has a separate CI workflow for its own source tree.

## Advanced migration

`import` is an optional one-time migration into an `agent-project`. It is not part of the quick start, and `workspace build` does not call it. `import` still runs as `agentc` after the CLI is installed.

Import from an existing host tree (`--from` is required; there is no default home directory):

```bash
agentc inspect ./cursor-plugin
agentc inspect ./workspace --kind cursor-workspace
agentc import cursor --from ./cursor-plugin --output ./my-agent --yes
agentc import cursor-workspace --from ./workspace --output ./my-agent --yes
agentc import cursor-user --from ./cursor-user --output ./my-agent --yes
agentc import codex --from ./codex-plugin --output ./my-agent --yes
agentc import claude-code --from ./claude-plugin --output ./my-agent --yes
agentc import merge --cursor DIR --codex DIR --output OUT [--yes]
```

`inspect` is read-only. Omit `--kind` to auto-detect when fingerprints hit exactly one kind. `--kind` that does not match fingerprints fails with `AGC-P4-0007`. An omitted `--kind` that cannot uniquely identify the source fails with `AGC-P4-0008`; `inspect` does not guess. `import` without `--yes` does not create the output directory (dry-run preview). `import --yes` writes a source-level `agent-project` into a new or empty output directory and runs `validate_project`; it does not install into host config directories. Hook, MCP, host config, and unknown items appear in the text report, in `import-report.md` on successful import, and in `--format json` diagnostics, but not in `agent-project.yaml` `contents`.

Install, uninstall, status, sync, and restore (Cursor / Codex / Claude Code plugin zips):

```bash
agentc install cursor --from plugin.zip --root DIR [--yes]
agentc install codex --from plugin.zip --root DIR [--yes]
agentc install claude-code --from plugin.zip --root DIR [--yes]
agentc install all \
  --cursor CURSOR.zip --codex CODEX.zip --claude-code CLAUDE.zip \
  [--cursor-root DIR] --codex-root DIR --claude-root DIR \
  [--yes] [--partial-ok]
agentc uninstall cursor --name NAME --root DIR [--yes]
agentc uninstall codex --name NAME --root DIR [--yes]
agentc uninstall claude-code --name NAME --root DIR [--yes]
agentc status [--kind cursor|codex|claude-code] [--name NAME] [--root DIR]
agentc sync cursor --from plugin.zip --root DIR [--yes]
agentc sync codex --from plugin.zip --root DIR [--yes]
agentc sync claude-code --from plugin.zip --root DIR [--yes]
agentc restore cursor --name NAME --root DIR [--yes]
agentc restore codex --name NAME --root DIR [--yes]
agentc restore claude-code --name NAME --root DIR [--yes]
```

`install` without `--yes` prints a plan and does not create or modify the destination. `install --yes` writes only when the destination is missing, or succeeds without writing when the destination already matches the zip byte-for-byte. Conflicts (different content or extra files in the destination) fail without touching the destination. Cursor may omit `--root` to use `~/.cursor/plugins/local`; Codex and Claude Code require `--root`. Tests should pass an isolated `--root`. A successful new install writes a sidecar ledger at `--root/.agentc/installs/<host>/<name>.json` (ADR 0009), not inside the plugin tree. `install all` runs Cursor, then Codex, then Claude Code in one transaction: on default failure it rolls back hosts this command wrote (`AGC-P5-0016`); `--partial-ok` skips rollback and leaves successful hosts in place. Rollback that cannot uninstall a host because files were modified fails with `AGC-P5-0017` and names that host. `sync` and `uninstall --yes` snapshot registered files to `--root/.agentc/backups/<host>/<name>/` before replacing or deleting (ADR 0012). `uninstall` and `sync` refuse to guess without that ledger (`AGC-P5-0007`) and refuse when registered files were modified (`AGC-P5-0008`). `restore` undoes a successful `sync` or `uninstall`; after uninstall, `restore` rebuilds the destination and ledger from the backup. `status` is read-only: it reports drift as a warning, reports backup availability, and does not treat an on-disk plugin directory or backup-only slot as installed when no ledger exists. `sync --yes` overwrites unmodified registered files from the zip, creates new zip paths, deletes removed unmodified registered files, and leaves untracked extras. Install does not call `CursorAdapter.install()` and does not write `install-report.md` in the plugin tree.

Public audit (read-only; both `--for-public` and `DIR` are required):

```bash
agentc audit --for-public DIR
```

`audit` does not default `DIR` to the current working directory or a home directory. Missing `--for-public` is `AGC-P5-0009`; missing `DIR` is `AGC-P5-0010`; neither case scans or writes. An unreadable `DIR`, a path that escapes the given root, or a zip-slip member is `AGC-P5-0011`. Blocking Phase 0 hits (secrets, internal hosts) are `AGC-P5-0012` (`error`, exit 1). Recording-only hits are `AGC-P5-0013` (`warning`); with no errors the command exits 0. If `DIR` is an `agent-project`, `audit` also runs source-level `validate`. Existing `.zip` files under `DIR` (including `build/dist/`) are extracted to a temp directory and scanned the same way; zip scripts are not executed. `audit` does not write `DIR` and does not write `audit-report.md`. It reuses the packaged `agentc.audit` scanner and `public-scan-rules.yaml` rather than a second scanner (see ADR 0010). The candidate public copy does not ship internal `scan-rules.yaml` suffix lists.

`package` does not re-run `build`, does not call `publish_local`, and does not install into host config directories. After each successful zip under `build/dist/<host>/`, it writes sidecar files beside that zip (not inside it): `<name>.zip.sha256` (SHA-256 hex and filename) and `<name>.zip.cdx.json` (minimal CycloneDX JSON for that zip). Zip member timestamps are pinned to 1980-01-01 so the same `build/outputs/` tree packaged twice yields identical zip bytes and SHA-256 (see ADR 0010).

Codex adapter `validate` may invoke the host's local validator when present (`runner=None` passes through to the default subprocess runner). Codex `validate` may still block when the host `plugin.json` schema requires `author` or `interface`; that is a host validator requirement, not silent success.

**Capability limits (same words as `agentc matrix`):**

Legal levels are `native`, `supported`, `adapted`, `partial`, `unsupported`, `unknown`, and `host-only`. Only `supported` is a promise that the default template passes on that host. `partial` means AgentC still emits the files and the host may reject them.

- Cursor `skill`, `rule`, `command`, and `agent` are `supported`. `hook` and `mcp` are `unsupported`.
- Codex `skill` is `supported`. Codex `rule`, `command`, and `agent` are `partial`: AgentC still emits them, and the host `plugin.json` schema may reject the result because it requires `author` or `interface`. `partial` is not `supported`.
- Claude Code `skill` and `command` are `supported`. `agent` is `partial`: AgentC still emits agents, and the host validator may reject them (`agents: Invalid input`, `AGC-P2-0003`). `rule` is `partial` because the shipped `RULE.md` template uses `alwaysApply: false` with no `globs`, and `plugin_compiler` treats that scope as Cursor-only (`AGC-P2-0003`) until the rule sets `alwaysApply: true` or adds `globs`.
- `hook` and `mcp` are `unsupported` on Codex and Claude Code.
- A local host validator, when present, is an extra check. Codex `validate` may call it. A machine without that validator can still `build` and `package`. Missing the validator does not turn a `partial` capability into `supported`.

## Host profiles

Shipped default Host Profile ids are `cursor/2026.09`, `codex/2026.09`, and `claude-code/2026.09`. Those ids are not the CLI package `version` in `pyproject.toml`. Keep Host Profile id and CLI version as two separate numbers.

`agentc matrix` prints the shipped profile document. It does not execute host binaries and does not read a host `--version`.

`agentc doctor` runs `--version` on each host executable found on `PATH` (3-second timeout; failures emit `AGC-P1-0013` warning — see ADR 0015).

```bash
agentc matrix
agentc matrix --host cursor
agentc matrix --format json
agentc matrix --format protocol
```

`--format json` for `matrix` is a query-document object (`schemaVersion`, `defaults`, `profiles`). It is not a five-key diagnostic array. `validate`, `build`, and `doctor` `--format json` remain that five-key array (`code`, `level`, `message`, `path`, `hint`). `--format protocol` still uses the six-key envelope; a successful `matrix` protocol run has empty `artifacts`.

`partial` still emits files. It is not a promise that the host accepts them. The short list under **Capability limits** is the one to follow: only rows marked `supported` are that promise. `hook` and `mcp` stay `unsupported`.

Codex `author` / `interface` schema gaps and Claude Code agent validation (`agents: Invalid input`) are host-side limits on the `partial` rows. A `supported` row is the default template on that host. A `partial` row is not.

`build --profile` names a shipped id (repeatable; last write wins for that host). An unknown id is `AGC-P2-0010`. Corrupt profile YAML or `defaults.yaml` is `AGC-P2-0011`. Both are fatal for that `build` (any `portabilityPolicy`, exit 1). `package`, `validate`, and `doctor` do not take `--profile`.

If `init` fails its trailing source-level `validate`, the CLI exits non-zero and leaves the generated project tree on disk; Phase 1 does not roll back that directory.

## Output formats

All subcommands accept `--format text|json|protocol` (default `text`).

**`--format protocol`** prints one JSON object (envelope) to stdout with six keys:

| Key | Meaning |
| --- | --- |
| `protocolVersion` | Always `agentc-protocol/v1` |
| `status` | `ok`, `unconfirmed`, or `error` |
| `summary` | One-line human summary |
| `diagnostics` | Same five-key diagnostic objects as `--format json` |
| `artifacts` | POSIX paths written by this command, or `[]` |
| `confirmation` | `{required: bool, flag: string\|null}` — `flag` is `"--yes"` when confirmation is required |

Host Skills and automation should use `--format protocol` as the machine source of truth. Do not parse `--format text`.

**`--format json`** still prints a JSON **array** of five-key diagnostic objects (`code`, `level`, `message`, `path`, `hint`). It is **not** an envelope; do not add envelope keys to json output.

## Diagnostic codes (Phase 1 contract)

Every diagnostic carries `code`, `level`, `message`, `path`, and `hint`. `--format json` prints a JSON array of those objects; automation must read these codes instead of parsing prose.

| Code | Level | Meaning |
| --- | --- | --- |
| `AGC-P1-0001` | `error` | Destination is not empty, or exists and is not a directory |
| `AGC-P1-0002` | `error` | Unknown schema field (non `x-` prefixed) |
| `AGC-P1-0003` | `error` | Path escapes the project root, is absolute, or text contains a host home-directory literal |
| `AGC-P1-0004` | `error` | Invalid name (skill name, or unknown `--targets` value) |
| `AGC-P1-0005` | `error` | A path listed under `contents` does not exist |
| `AGC-P1-0006` | `error` | File is not valid UTF-8 |
| `AGC-P1-0007` | `error` | Invalid schema, including YAML syntax errors, a non-mapping document, and `create skill` against a directory that is not an agent project |
| `AGC-P1-0008` | `error` | Skill directory already exists |
| `AGC-P1-0009` | `error` | Running interpreter is older than Python 3.10, or the version cannot be read |
| `AGC-P1-0010` | `info` | Runtime or host executable found (`path` is that file) |
| `AGC-P1-0011` | `warning` | `cursor` / `codex` / `claude` not on `PATH` |
| `AGC-P1-0012` | `error` | `--dir` is unreadable, not a directory, or escapes the given path |
| `AGC-P1-0013` | `warning` | Host found on `PATH` but `--version` timed out, exited non-zero, or produced no readable text |

## Diagnostic codes (Phase 2 build contract)

| Code | Level | Meaning |
| --- | --- | --- |
| `AGC-P2-0001` | `error` | No enabled build targets |
| `AGC-P2-0002` | `error` | Translation from IR to adapter request failed |
| `AGC-P2-0003` | `error` | Host adapter compile/validate blocked |
| `AGC-P2-0004` | `error` | Capability matrix does not allow this host/capability |
| `AGC-P2-0005` | `error` | Build artifact escaped the project `build/` root |
| `AGC-P2-0005` | `notice` | Project `.gitignore` does not ignore `build/` |
| `AGC-P2-0006` | `error` | Overlay operations list is invalid (not an array, unknown `op`, missing fields, or illegal types) |
| `AGC-P2-0007` | `error` | Overlay `remove` has no non-empty `reason` |
| `AGC-P2-0008` | `error` | Overlay `from` / `to` path escaped, or `to` is an absolute path |
| `AGC-P2-0009` | `error` | Overlay `add-file` source does not exist |
| `AGC-P2-0010` | `error` | `--profile` id does not exist, or does not belong to that host |
| `AGC-P2-0011` | `error` | Host profile YAML or `defaults.yaml` is invalid |

`AGC-P2-0005` reuses one code with different levels: `error` means artifact escape; `notice` means `build/` is not ignored. Overlay codes `AGC-P2-0006` through `AGC-P2-0009` are always `error`. Profile codes `AGC-P2-0010` and `AGC-P2-0011` are always `error` and fatal for that `build` under every `portabilityPolicy`.

## Diagnostic codes (Phase 3 package contract)

| Code | Level | Meaning |
| --- | --- | --- |
| `AGC-P3-0001` | `error` or `warning` | Enabled host has no `build/outputs/<host>/` tree with files; run `build` first. Under `permissive`, missing output is a `warning`. |
| `AGC-P3-0002` | `error` | Package zip path escaped the project `build/` root |
| `AGC-P3-0003` | `error` | Package zip IO failure (disk, permissions, etc.) |

`AGC-P3-0002` and `AGC-P3-0003` are fatal: even `--policy compatible` exits non-zero when they appear.

Missing-output policy matches `build`:

| Policy | Missing output behavior |
| --- | --- |
| `strict` | Any missing host → exit 1; write **no** zips |
| `compatible` | Zip hosts that have outputs; missing hosts stay `error`; exit 0 |
| `permissive` | Missing hosts become `warning`; exit 0 |

## Diagnostic codes (Phase 4 inspect / import contract)

Codes `AGC-P4-0001` through `AGC-P4-0005` plus `AGC-P4-0007`, `AGC-P4-0008`, `AGC-P4-0009`, and `AGC-P4-0010` are `error`. `AGC-P4-0006` is informational only (`warning` or `notice`).

| Code | Level | Meaning |
| --- | --- | --- |
| `AGC-P4-0001` | `error` | Source is not a Cursor plugin (`import cursor` still uses this when `.cursor-plugin/plugin.json` is missing) |
| `AGC-P4-0002` | `error` | Source unreadable, or path escapes the source root |
| `AGC-P4-0003` | `error` | Output directory exists and is not empty |
| `AGC-P4-0004` | `error` | `import` without `--yes` (unconfirmed; no write) |
| `AGC-P4-0005` | `error` | Import write IO failure |
| `AGC-P4-0006` | `warning` or `notice` | Unresolved item (`warning`) or redaction note (`notice`) |
| `AGC-P4-0007` | `error` | `--kind` does not match the source fingerprints |
| `AGC-P4-0008` | `error` | Omitted `--kind` cannot uniquely identify the source |
| `AGC-P4-0009` | `error` | Import merge with fewer than two source directories |
| `AGC-P4-0010` | `error` | Merge destination path conflict (different bytes or kinds) |

Unresolved items (Hook, MCP, unknown files) and redaction notes use `AGC-P4-0006`. They appear in the text report, in `import-report.md` after a successful `import --yes`, and in the five-key `--format json` array. They do **not** appear in `agent-project.yaml` `contents`.

## Diagnostic codes (Phase 5 install and audit contract)

Codes `AGC-P5-0001` through `AGC-P5-0008` are `error` on `install` / `uninstall` / `sync` / `restore`. `status` reports registered-file drift as a `warning` and still exits 0. Codes `AGC-P5-0009` through `AGC-P5-0012` are `error` on `audit`. `AGC-P5-0013` is `warning` only.

| Code | Level | Meaning |
| --- | --- | --- |
| `AGC-P5-0001` | `error` | Not a usable plugin zip for this host (not exactly one top-level directory, or missing the host `plugin.json` / `name`) |
| `AGC-P5-0002` | `error` | Zip unreadable, path escape, or outbound symlink in the destination |
| `AGC-P5-0003` | `error` | Destination exists and does not match the zip (conflict; no write) |
| `AGC-P5-0004` | `error` | `install` / `install all` / `uninstall` / `sync` / `restore` without `--yes` (unconfirmed; no write) |
| `AGC-P5-0005` | `error` | Install / sync write IO failure (staging removed; destination unchanged) |
| `AGC-P5-0006` | `error` | Codex / Claude Code missing `--root` |
| `AGC-P5-0007` | `error` | No install ledger (`uninstall` / `sync` do not guess) |
| `AGC-P5-0008` | `error` | Registered files were modified (`uninstall` / `sync` / `restore` with ledger fail the whole operation) |
| `AGC-P5-0009` | `error` | `audit` without `--for-public` (no scan, no write) |
| `AGC-P5-0010` | `error` | `audit` without `DIR` (no scan, no write; does not default to cwd or home) |
| `AGC-P5-0011` | `error` | `DIR` unreadable, path escape, outbound symlink, or zip-slip member |
| `AGC-P5-0012` | `error` | Blocking Phase 0 scan hit (secrets, internal hosts); `DIR` unchanged |
| `AGC-P5-0013` | `warning` | Recording-only Phase 0 scan hit; exit 0 when there are no errors |
| `AGC-P5-0014` | `error` | `restore` without a backup slot |
| `AGC-P5-0015` | `error` | Backup manifest unreadable, file hash mismatch, or backup `dest` escapes `--root` |
| `AGC-P5-0016` | `error` | `install all` failed and rolled back hosts this command wrote |
| `AGC-P5-0017` | `error` | `install all` rollback could not uninstall at least one host (message names the host) |

`--format json` stdout remains a five-key diagnostic array (`code`, `level`, `message`, `path`, `hint`). It is not an envelope.

## Diagnostic codes (Phase 6 protocol contract)

| Code | Level | Meaning |
| --- | --- | --- |
| `AGC-P6-0001` | `error` | Protocol envelope could not be emitted as-is; `emit_protocol` repaired stdout to a valid error envelope containing this code |

## Platform overlays

There is no `agentc overlay` subcommand. Overlay files live at `overlays/<host>/*.json` (`cursor`, `codex`, `claude-code`). Each scanned file is a JSON **array of operations**, not a field object and not `{ "operations": [...] }`. `.gitkeep` and non-`.json` files are ignored. An empty array is legal (no operations). Missing overlay JSON is also legal.

```json
[
  {"op": "set", "field": "description", "value": "Shared host skills."},
  {"op": "set", "field": "interface", "value": {"displayName": "Host Skills"}},
  {"op": "remove", "field": "author", "reason": "Codex overlay merge rejects author here"},
  {"op": "add-file", "from": "extra/icon.png", "to": "assets/icon.png"}
]
```

| `op` | Required | Behavior |
| --- | --- | --- |
| `set` | `field` (string, top-level host-manifest key), `value` | Write that top-level key on the host manifest |
| `remove` | `field`, `reason` (non-empty string) | Delete that top-level key from the host manifest |
| `add-file` | `from`, `to` (POSIX relative paths) | Copy a file |

`from` is relative to that host overlay directory `overlays/<host>/`. After resolve it must stay inside that directory; `..` and outbound symlinks fail. The source must already exist. `to` is relative to that host output root; it must not start with `/` and must not be absolute. After resolve it must stay inside that host output tree. `set` / `remove` only touch top-level keys of that host wrapper manifest.

- `validate` only checks overlay files. Codes `AGC-P2-0006` through `AGC-P2-0009` are `error`, the command exits 1, and nothing is written under `build/`.
- `build` is the only apply point: after source overlay checks pass, it applies `set` / `remove` / `add-file` onto host outputs. Overlay errors fail the whole build with exit 1 and do not apply.
- `package` only zips already-applied `build/outputs/<host>/` into `build/dist/`. It does not re-run `build`, does not call adapters, and does not apply overlays. A packaged zip contains overlay results only because `build` already ran.

Successful operations appear in the existing build reports (`report.md` overlay section and `diagnostics.json` `overlay` list). They are not extra `info` diagnostics. `--format json` still prints the five-key diagnostic array only.

## AgentC workspace skill

The public quick start uses one skill, `integrations/agentc-workspace/`. The same `SKILL.md` is packaged for Cursor, Codex, and Claude Code. Host manifests and directory layouts can differ. The skill calls `agentc doctor`, `agentc workspace build`, `agentc install`, `agentc status`, and `agentc restore` with `--format protocol`. It does not call `import`.

## Host Skills

The older tree `integrations/host-skills/` remains (one `agent-project`, four Skills: create, migrate, build, publish-prep). It is not the quick start. Package with `build` and `package` for Cursor, Codex, and Claude Code. Skill bodies call `agentc … --format protocol` only.

Per-host overlay files under `overlays/<host>/` are operation arrays (`set` / `remove` / `add-file`). `set` of `name` / `version` / `author` still lifts onto shared `PluginIdentity` and is stripped from `target_overlays` so Codex/Claude adapters do not reject the merge. `validate` checks those arrays; `build` applies them; `package` only packs the already-applied outputs.

## ADR summary

The public product tree lives at `src/agentc/` and is copied into a candidate via the allowlist. Do not hand-edit a generated candidate directory as the source of truth.

Phase-by-phase notes are in [history.md](history.md).
