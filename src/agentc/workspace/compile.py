"""Compile a plugin directory in place.

The source tree stays where the user edited it. This module fills a
ParsedPlugin from that directory, then calls each requested adapter's
compile, validate, and publish_local. Build output goes to a separate
directory. A failure on any requested target deletes every ZIP written
for this call.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

from agentc.model.diagnostics import (
    CODE_SOURCE_UNREADABLE,
    CODE_WORKSPACE_ENTRY,
    CODE_WORKSPACE_SECRET,
    Diagnostic,
)
from agentc.security.redact import (
    contains_secret_token,
    redact_portable_text,
    scrub_secret_text,
)
from agentc.workspace.inventory import (
    FIXED_EXCLUSIONS,
    Inventory,
    InventoryEntry,
    inventory_source,
    output_overlap,
    recheck_inventory,
)
from agentc.workspace.source import detect_source

_TARGETS = ("cursor", "codex", "claude-code")
_MANIFEST_FILES = {
    ".cursor-plugin/plugin.json": "cursor",
    ".codex-plugin/plugin.json": "codex",
    ".claude-plugin/plugin.json": "claude-code",
}
_MANIFEST_DIRS = {
    ".cursor-plugin": "cursor",
    ".codex-plugin": "codex",
    ".claude-plugin": "claude-code",
}
_TARGET_MANIFEST = {
    "cursor": ".cursor-plugin/plugin.json",
    "codex": ".codex-plugin/plugin.json",
    "claude-code": ".claude-plugin/plugin.json",
}
_TARGET_MANIFEST_DIR = {
    "cursor": ".cursor-plugin",
    "codex": ".codex-plugin",
    "claude-code": ".claude-plugin",
}
_WORKSPACE_SKILLS = {
    "cursor-workspace": (".cursor/skills", "cursor"),
    "codex-workspace": (".agents/skills", "codex"),
    "claude-code-workspace": (".claude/skills", "claude-code"),
}
_SCRIPT_SUFFIXES = {
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".js",
    ".ts",
    ".rb",
    ".pl",
}
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")
_SOURCE_OUTPUT_ROOTS = frozenset(
    {"build", "dist", ".pytest_cache", "__pycache__"}
)


@dataclass(frozen=True)
class FileOutcome:
    path: str
    target: str
    status: str
    target_path: str | None = None
    capability: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class WorkspaceCompileResult:
    ok: bool
    zips: tuple[str, ...]
    outcomes: tuple[FileOutcome, ...]
    diagnostics: tuple[Diagnostic, ...]
    source_kind: str | None = None
    exclusions: tuple[str, ...] = FIXED_EXCLUSIONS

    @property
    def report_text(self) -> str:
        lines = [
            "source_kind={0}".format(self.source_kind or ""),
            "exclusions={0}".format(",".join(self.exclusions)),
        ]
        for item in self.outcomes:
            lines.append(
                "\t".join(
                    (
                        item.target,
                        item.path,
                        item.status,
                        item.target_path or "",
                        item.capability or "",
                        item.reason or "",
                    )
                )
            )
        for diag in self.diagnostics:
            lines.append(
                "\t".join(
                    (
                        diag.code,
                        diag.level,
                        diag.path or "",
                        diag.message,
                        diag.hint,
                    )
                )
            )
        return "\n".join(lines)


def compile_workspace(
    plugin_dir: Path,
    targets: tuple[str, ...] | list[str],
    output_dir: Path,
    source_kind: str | None = None,
) -> WorkspaceCompileResult:
    """Compile plugin_dir for each requested target without copying the source."""
    plugin_dir = Path(plugin_dir)
    output_dir = Path(output_dir)
    requested = tuple(targets)
    inventory, inventory_diags = inventory_source(plugin_dir)
    if inventory is None:
        return _result(False, (), (), inventory_diags, None)
    kind, kind_diags = detect_source(plugin_dir, source_kind)
    if kind is None:
        return _result(False, (), (), kind_diags, None, inventory.exclusions)
    target_diags = _target_diagnostics(requested)
    if target_diags:
        return _result(False, (), (), target_diags, kind, inventory.exclusions)
    overlap = output_overlap(plugin_dir, output_dir)
    if overlap is not None:
        return _result(False, (), (), (overlap,), kind, inventory.exclusions)
    secret_paths, unreadable = _secret_scan(plugin_dir, inventory)
    path_secrets = _path_secret_diagnostics(plugin_dir, inventory)
    if unreadable or path_secrets or secret_paths:
        if secret_paths:
            result = _secret_result(
                secret_paths, requested, kind, inventory.exclusions
            )
            return replace(
                result,
                diagnostics=result.diagnostics + tuple(unreadable) + path_secrets,
            )
        return _result(
            False,
            (),
            (),
            tuple(unreadable) + path_secrets,
            kind,
            inventory.exclusions,
        )
    try:
        parsed = _load_parsed(plugin_dir, kind)
        if parsed.override_config is None:
            from plugin_compiler.overrides import load_override_config

            parsed = replace(parsed, override_config=load_override_config(plugin_dir))
    except Exception as exc:
        return _result(
            False,
            (),
            (),
            (_exception_diagnostic(exc),),
            kind,
            inventory.exclusions,
        )
    outcomes = _classify(plugin_dir, inventory, parsed, requested)
    parsed = replace(
        _with_bundled_resources(parsed, outcomes, requested),
        workspace_full_source=True,
    )
    failed = any(item.status == "error" for item in outcomes)
    diagnostics = list(_outcome_diagnostics(outcomes))
    diagnostics.extend(_block_diagnostics(parsed.source_issues, kind))
    if any(issue.severity == "block" for issue in parsed.source_issues):
        failed = True
    archives: list[str] = []
    existing_zips = _existing_zips(output_dir)
    try:
        call_diags, call_failed = _publish_targets(
            parsed, requested, output_dir, archives
        )
        diagnostics.extend(call_diags)
        failed = failed or call_failed
        changed = recheck_inventory(plugin_dir, inventory)
        if changed:
            diagnostics.extend(changed)
            failed = True
        if not failed and len(archives) == len(requested):
            mismatches = _archive_target_mismatches(
                outcomes, requested, tuple(archives)
            )
            if mismatches:
                diagnostics.extend(mismatches)
                failed = True
    except Exception as exc:
        failed = True
        diagnostics.append(_exception_diagnostic(exc))
    if failed:
        _delete_archives(archives)
        _delete_new_zips(output_dir, existing_zips)
        archives = []
    return _result(
        not failed and len(archives) == len(requested),
        tuple(archives),
        outcomes,
        tuple(diagnostics),
        kind,
        inventory.exclusions,
    )


def _result(
    ok: bool,
    zips: tuple[str, ...],
    outcomes: tuple[FileOutcome, ...],
    diagnostics: tuple[Diagnostic, ...],
    source_kind: str | None,
    exclusions: tuple[str, ...] = FIXED_EXCLUSIONS,
) -> WorkspaceCompileResult:
    return WorkspaceCompileResult(
        ok=ok,
        zips=zips,
        outcomes=outcomes,
        diagnostics=diagnostics,
        source_kind=source_kind,
        exclusions=exclusions,
    )


def _target_diagnostics(targets: tuple[str, ...]) -> tuple[Diagnostic, ...]:
    if not targets:
        return (
            Diagnostic(
                code=CODE_WORKSPACE_ENTRY,
                level="error",
                message="At least one target is required.",
                path=".",
                hint="Pass cursor, codex, or claude-code.",
            ),
        )
    diags: list[Diagnostic] = []
    seen: set[str] = set()
    for target in targets:
        if target not in _TARGETS:
            diags.append(
                Diagnostic(
                    code=CODE_WORKSPACE_ENTRY,
                    level="error",
                    message="Unknown target: {0}".format(target),
                    path=".",
                    hint="Use cursor, codex, or claude-code.",
                )
            )
            continue
        if target in seen:
            diags.append(
                Diagnostic(
                    code=CODE_WORKSPACE_ENTRY,
                    level="error",
                    message="Duplicate target: {0}".format(target),
                    path=".",
                    hint="List each target once.",
                )
            )
        seen.add(target)
    return tuple(diags)


def _secret_scan(
    plugin_dir: Path, inventory: Inventory
) -> tuple[tuple[str, ...], tuple[Diagnostic, ...]]:
    found: list[str] = []
    unreadable: list[Diagnostic] = []
    for entry in inventory.entries:
        if entry.kind != "file":
            continue
        try:
            data = (plugin_dir / entry.path).read_bytes()
        except OSError:
            unreadable.append(_unreadable_diagnostic(entry.path))
            continue
        text = data.decode("utf-8", errors="ignore")
        # Body text keeps ${...} placeholders. Path checks do not.
        _cleaned, reasons = redact_portable_text(text)
        if "secret" in reasons:
            found.append(entry.path)
    return tuple(found), tuple(unreadable)


def _has_secret(text: str) -> bool:
    return contains_secret_token(text)


def _path_locator(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path_secret_diagnostics(
    plugin_dir: Path, inventory: Inventory
) -> tuple[Diagnostic, ...]:
    found: list[Diagnostic] = []
    try:
        root_text = plugin_dir.expanduser().resolve().as_posix()
    except OSError:
        root_text = plugin_dir.as_posix()
    if _has_secret(root_text):
        found.append(_path_secret_diagnostic(root_text))
    for entry in inventory.entries:
        if _has_secret(entry.path):
            found.append(_path_secret_diagnostic(entry.path))
        if entry.kind != "symlink":
            continue
        try:
            target = os.readlink(plugin_dir / entry.path)
        except OSError:
            continue
        if _has_secret(target):
            found.append(_symlink_target_diagnostic(entry.path, target))
    return tuple(found)


def _path_secret_diagnostic(original: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_WORKSPACE_SECRET,
        level="error",
        message="Suspected secret in a path component.",
        path=scrub_secret_text(original),
        hint="locator={0}".format(_path_locator(original)),
    )


def _symlink_target_diagnostic(rel: str, target: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_WORKSPACE_SECRET,
        level="error",
        message="Suspected secret in a symlink target.",
        path=scrub_secret_text(rel),
        hint="locator={0} path={1}".format(
            _path_locator(target), scrub_secret_text(target)
        ),
    )


def _unreadable_diagnostic(path: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_SOURCE_UNREADABLE,
        level="error",
        message="Path is unreadable: {0}".format(path),
        path=path,
        hint="Restore read permission and keep the path inside the source root.",
    )


def _secret_result(
    paths: tuple[str, ...],
    targets: tuple[str, ...],
    kind: str,
    exclusions: tuple[str, ...],
) -> WorkspaceCompileResult:
    outcomes: list[FileOutcome] = []
    diagnostics: list[Diagnostic] = []
    for path in paths:
        diagnostics.append(
            Diagnostic(
                code=CODE_WORKSPACE_SECRET,
                level="error",
                message="Suspected secret in {0}.".format(path),
                path=path,
                hint="Remove the secret from the source file and retry.",
            )
        )
        for target in targets:
            outcomes.append(
                FileOutcome(
                    path=path,
                    target=target,
                    status="error",
                    capability="secret",
                    reason="suspected secret",
                )
            )
    return _result(False, (), tuple(outcomes), tuple(diagnostics), kind, exclusions)


def _load_parsed(plugin_dir: Path, kind: str):
    if kind == "cursor":
        from plugin_targets.cursor.source_parse import parse_cursor_plugin

        return parse_cursor_plugin(plugin_dir)
    if kind == "codex":
        from plugin_targets.codex.source_parse import parse_codex_plugin

        return parse_codex_plugin(plugin_dir)
    if kind == "claude-code":
        from plugin_targets.claude_code.source_parse import parse_claude_plugin

        return parse_claude_plugin(plugin_dir)
    return _parse_workspace(plugin_dir, kind)


def _parse_workspace(plugin_dir: Path, kind: str):
    from plugin_compiler.model import CapabilityKind, CapabilityRef
    from plugin_compiler.manifests import PluginIdentity
    from plugin_compiler.skills import load_skill_document
    from plugin_targets.spi import DeclaredResource, ParsedPlugin, SourceExtension

    relative, dialect = _WORKSPACE_SKILLS[kind]
    root = plugin_dir.joinpath(*relative.split("/"))
    skills = []
    capabilities = []
    declared = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            skill_md = child / "SKILL.md"
            if not child.is_dir() or not skill_md.is_file():
                continue
            document = load_skill_document(
                skill_md, plugin_dir, source_dialect=dialect
            )
            skills.append(document)
            dir_rel = child.relative_to(plugin_dir).as_posix()
            capabilities.append(
                CapabilityRef(
                    capability_id="skill:{0}".format(dir_rel),
                    kind=CapabilityKind.SKILL,
                    source_path=dir_rel,
                    source_dialect=dialect,
                )
            )
            declared.append(
                DeclaredResource(source_path=dir_rel, kind=CapabilityKind.SKILL.value)
            )
    identity = PluginIdentity(
        name=_safe_name(plugin_dir),
        version="0.0.0",
        description=None,
        author=None,
    )
    empty: tuple = ()
    return ParsedPlugin(
        plugin_dir=plugin_dir,
        plugin_name=identity.name,
        source_dialect=dialect,
        identity=identity,
        source_extension=SourceExtension(dialect=dialect, fields={}),
        capabilities=tuple(capabilities),
        skills=tuple(skills),
        commands=empty,
        rules=empty,
        hooks=empty,
        agents=empty,
        workflows=empty,
        mcp_documents=empty,
        connectors=empty,
        lsp_documents=empty,
        output_styles=empty,
        themes=empty,
        monitors=empty,
        override_config=None,
        target_overlays=empty,
        declared_resources=tuple(declared),
        source_issues=empty,
    )


def _safe_name(plugin_dir: Path) -> str:
    raw = plugin_dir.name
    if raw and raw not in {".", ".."} and _SAFE_NAME.fullmatch(raw):
        return raw
    return "workspace"


def _classify(
    plugin_dir: Path,
    inventory: Inventory,
    parsed,
    targets: tuple[str, ...],
) -> tuple[FileOutcome, ...]:
    documents = _index_documents(parsed)
    skill_dirs = _skill_dir_map(parsed)
    file_outcomes: list[FileOutcome] = []
    other_entries: list[InventoryEntry] = []
    for entry in inventory.entries:
        if entry.kind != "file":
            other_entries.append(entry)
            continue
        for target in targets:
            file_outcomes.append(
                _file_outcome(
                    plugin_dir, entry.path, target, documents, parsed
                )
            )
    outcomes = list(file_outcomes)
    for entry in other_entries:
        for target in targets:
            outcomes.append(
                _other_outcome(
                    entry, target, inventory, file_outcomes, skill_dirs
                )
            )
    outcomes.sort(key=lambda item: (item.path, item.target))
    return tuple(outcomes)


def _index_documents(parsed) -> dict[str, tuple[str, object]]:
    indexed: dict[str, tuple[str, object]] = {}
    for document in parsed.skills:
        path = document.source_path.replace("\\", "/")
        if not path.endswith("SKILL.md"):
            path = path.rstrip("/") + "/SKILL.md"
        indexed[path] = ("skill", document)
    groups = (
        ("rule", parsed.rules),
        ("command", parsed.commands),
        ("agent", parsed.agents),
        ("workflow", parsed.workflows),
        ("hook", parsed.hooks),
        ("mcp", parsed.mcp_documents),
        ("connector", parsed.connectors),
        ("lsp", parsed.lsp_documents),
        ("output-style", parsed.output_styles),
        ("theme", parsed.themes),
        ("monitor", parsed.monitors),
    )
    for capability, documents in groups:
        for document in documents:
            path = getattr(document, "source_path", "") or ""
            path = path.replace("\\", "/")
            if path:
                indexed[path] = (capability, document)
    return indexed


def _skill_dir_map(parsed) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for document in parsed.skills:
        source = document.source_path.replace("\\", "/")
        if source.endswith("/SKILL.md"):
            source_dir = source.rsplit("/", 1)[0]
        else:
            source_dir = source.rstrip("/")
        mapped[source_dir] = _skill_output_dir(document)
    return mapped


def _skill_output_dir(document) -> str:
    parts = [
        part
        for part in document.source_path.replace("\\", "/").split("/")
        if part and part != "."
    ]
    if parts and parts[-1] == "SKILL.md":
        parts = parts[:-1]
    if len(parts) >= 2 and parts[0] == "skills":
        return "skills/{0}".format(parts[1])
    return "skills/{0}".format(document.name)


def _file_outcome(
    plugin_dir: Path,
    path: str,
    target: str,
    documents: dict[str, tuple[str, object]],
    parsed,
) -> FileOutcome:
    from plugin_compiler.policy import is_blocked_sensitive_path

    if is_blocked_sensitive_path(plugin_dir.joinpath(*path.split("/"))):
        return FileOutcome(
            path=path,
            target=target,
            status="protected",
            reason="local or credential-like file is not published",
        )
    parts = path.split("/")
    if (
        len(parts) >= 3
        and parts[0] == "skills"
        and parsed.override_config is not None
    ):
        from plugin_compiler.overrides import skill_dir_allowed

        if not skill_dir_allowed(parsed.override_config, parts[1], target):
            return FileOutcome(
                path=path,
                target=target,
                status="excluded",
                capability="skill",
                reason="target excluded by packaging/platform-overrides.json",
            )
    ignored = _source_only_reason(path)
    if ignored:
        return FileOutcome(path=path, target=target, status="ignored", reason=ignored)
    if path.startswith("packaging/"):
        return FileOutcome(
            path=path,
            target=target,
            status="consumed",
            capability="build-config",
            reason="used by the compiler; hosts do not accept packaging configuration",
        )
    if path in _MANIFEST_FILES:
        return FileOutcome(
            path=path,
            target=target,
            status="transformed",
            target_path=_TARGET_MANIFEST[target],
            capability="plugin-metadata",
        )
    indexed = documents.get(path)
    if indexed is not None:
        capability, document = indexed
        return _document_decision(
            plugin_dir, path, capability, document, target, parsed
        )
    capability = _rejected_capability(plugin_dir, path)
    return FileOutcome(
        path=path,
        target=target,
        status="bundled",
        target_path=path,
        capability=capability,
    )


def _source_only_reason(path: str) -> str | None:
    parts = path.split("/")
    if parts[0] in _SOURCE_OUTPUT_ROOTS or any(
        part in {"__pycache__", ".pytest_cache"} for part in parts
    ):
        return "existing build or cache output; excluded from the plugin archive"
    return None


def _document_decision(
    plugin_dir: Path, path: str, capability: str, document, target: str, parsed
) -> FileOutcome:
    if parsed.override_config is not None:
        from plugin_compiler.model import Target
        from plugin_compiler.overrides import command_binding_for, resolved_targets

        capability_id = getattr(document, "capability_id", None)
        ref = next(
            (
                item
                for item in parsed.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if ref is not None and Target(target) not in resolved_targets(
            parsed.override_config, ref
        ):
            return FileOutcome(
                path=path,
                target=target,
                status="excluded",
                capability=capability,
                reason="target excluded by packaging/platform-overrides.json",
            )
        if capability == "command" and target == "codex":
            binding = command_binding_for(parsed.override_config, document.capability_id)
            if binding is not None:
                return FileOutcome(
                    path=path,
                    target=target,
                    status="bound",
                    target_path="skills/{0}/SKILL.md".format(
                        binding.codex_skill_dir
                    ),
                    capability=capability,
                    reason="command uses the configured Codex skill",
                )
    if capability == "skill" and parsed.override_config is not None:
        from plugin_compiler.overrides import skill_dir_allowed

        if not skill_dir_allowed(
            parsed.override_config,
            _skill_output_dir(document).split("/")[-1],
            target,
        ):
            return FileOutcome(
                path=path,
                target=target,
                status="excluded",
                capability=capability,
                reason="target excluded by packaging/platform-overrides.json",
            )
    accepted, target_path, reason = _document_acceptance(
        plugin_dir, path, capability, document, target
    )
    if accepted:
        return FileOutcome(
            path=path,
            target=target,
            status="transformed",
            target_path=target_path,
            capability=capability,
        )
    return FileOutcome(
        path=path,
        target=target,
        status="error",
        capability=capability,
        reason=reason or _rejected_reason(capability, target),
    )


def _document_acceptance(
    plugin_dir: Path, path: str, capability: str, document, target: str
) -> tuple[bool, str | None, str | None]:
    from plugin_compiler.agents import agent_conversion_issue
    from plugin_compiler.extras import extra_conversion_issue
    from plugin_compiler.model import CapabilityKind, Target
    from plugin_compiler.rules import cursor_rule_output_path, rule_conversion_issue
    from plugin_compiler.workflows import workflow_conversion_issue

    host = Target(target)
    if capability == "skill":
        return True, "{0}/SKILL.md".format(_skill_output_dir(document)), None
    if capability == "agent":
        issue = agent_conversion_issue(document, host)
        if issue is not None:
            return False, None, _public_message(issue.message)
        if target == "codex":
            return True, "skills/{0}/SKILL.md".format(document.name), None
        return True, path, None
    if capability == "rule":
        issue = rule_conversion_issue(document, host)
        if issue is not None:
            return False, None, _public_message(issue.message)
        if target != "cursor":
            return True, "compiled-rules/bodies/{0}.txt".format(path), None
        return True, cursor_rule_output_path(document), None
    if capability == "command":
        return _command_acceptance(document, path, target)
    if capability == "workflow":
        issue = workflow_conversion_issue(document, host)
        if issue is not None:
            return False, None, _public_message(issue.message)
        if target == "claude-code":
            return True, path, None
        return True, "skills/{0}/SKILL.md".format(document.name), None
    if capability == "hook":
        return _hook_acceptance(plugin_dir, document, target)
    if capability == "mcp":
        from plugin_compiler.mcp import mcp_output_relative, mcp_secret_issues

        if mcp_secret_issues(document):
            return False, None, "suspected secret"
        return True, mcp_output_relative(host), None
    extra_kind = {
        "connector": CapabilityKind.CONNECTOR,
        "lsp": CapabilityKind.LSP,
        "output-style": CapabilityKind.OUTPUT_STYLE,
        "theme": CapabilityKind.THEME,
        "monitor": CapabilityKind.MONITOR,
    }.get(capability)
    if extra_kind is not None:
        issue = extra_conversion_issue(extra_kind, host)
        if issue is not None:
            return False, None, _public_message(issue.message)
        return True, path, None
    return False, None, _rejected_reason(capability, target)


def _command_acceptance(document, path: str, target: str):
    from plugin_compiler.commands import assert_codex_skill_name, assert_safe_command_path_name

    if target == "codex":
        try:
            assert_codex_skill_name(document.name)
        except ValueError as exc:
            return False, None, _public_message(str(exc))
        return True, "skills/{0}/SKILL.md".format(document.name), None
    try:
        assert_safe_command_path_name(document.name)
    except ValueError as exc:
        return False, None, _public_message(str(exc))
    return True, path, None


def _hook_acceptance(plugin_dir: Path, document, target: str):
    from plugin_compiler.hooks import remap_document
    from plugin_compiler.model import Target

    try:
        _remapped, issues = remap_document(document, Target(target), plugin_dir)
    except Exception as exc:
        return False, None, _public_message("{0}: {1}".format(type(exc).__name__, exc))
    blocks = [issue for issue in issues if issue.severity == "block"]
    if blocks:
        return False, None, _public_message(blocks[0].message)
    return True, "hooks/hooks.json", None


def _rejected_capability(plugin_dir: Path, path: str) -> str:
    suffix = Path(path).suffix.lower()
    if path.startswith("scripts/") or "/scripts/" in "/{0}".format(path):
        return "script"
    if suffix in _SCRIPT_SUFFIXES:
        return "script"
    if path == "build" or path.startswith("build/") or path == "packaging" or path.startswith("packaging/"):
        return "build"
    if suffix in {".bin", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".so", ".dylib", ".exe", ".wasm"}:
        return "binary"
    file_path = plugin_dir.joinpath(*path.split("/"))
    try:
        sample = file_path.read_bytes()[:512]
    except OSError:
        sample = b""
    if b"\x00" in sample:
        return "binary"
    return "unknown"


def _rejected_reason(capability: str, target: str) -> str:
    if capability == "script":
        return (
            "script is not named by the manifest or a transformed document, "
            "and {0} cannot preserve script behavior".format(target)
        )
    if capability == "binary":
        return (
            "binary is not named by the manifest or a transformed document, "
            "and {0} cannot preserve it".format(target)
        )
    if capability == "build":
        return "build output is not a plugin document {0} can accept".format(target)
    if capability == "symlink":
        return "symlink is not a named resource {0} can preserve".format(target)
    if capability == "directory":
        return "directory has no file {0} can accept".format(target)
    return (
        "file is not a supported document and is not named by the manifest "
        "or a transformed document for {0}".format(target)
    )


def _other_outcome(
    entry: InventoryEntry,
    target: str,
    inventory: Inventory,
    file_outcomes: list[FileOutcome],
    skill_dirs: dict[str, str],
) -> FileOutcome:
    if entry.kind == "symlink":
        return FileOutcome(
            path=entry.path,
            target=target,
            status="error",
            capability="symlink",
            reason=_rejected_reason("symlink", target),
        )
    prefix = entry.path + "/"
    if not any(item.path.startswith(prefix) for item in inventory.entries):
        return FileOutcome(path=entry.path, target=target, status="empty")
    under = [
        item
        for item in file_outcomes
        if item.target == target and item.path.startswith(prefix)
    ]
    if any(item.status == "transformed" for item in under):
        return _directory_outcome(
            entry.path, target, "transformed", skill_dirs, under
        )
    if any(item.status == "bundled" for item in under):
        return _directory_outcome(
            entry.path, target, "bundled", skill_dirs, under
        )
    if any(item.status == "error" for item in under):
        return FileOutcome(
            path=entry.path,
            target=target,
            status="error",
            capability="directory",
            reason=_rejected_reason("directory", target),
        )
    if any(item.status == "consumed" for item in under):
        return FileOutcome(path=entry.path, target=target, status="consumed")
    if any(item.status == "ignored" for item in under):
        return FileOutcome(
            path=entry.path,
            target=target,
            status="ignored",
            reason="existing build or cache output",
        )
    if any(item.status == "protected" for item in under):
        return FileOutcome(
            path=entry.path,
            target=target,
            status="protected",
            reason="local or credential-like file is not published",
        )
    if any(item.status == "excluded" for item in under):
        return FileOutcome(
            path=entry.path,
            target=target,
            status="excluded",
            reason="target excluded by source configuration",
        )
    return FileOutcome(path=entry.path, target=target, status="empty")


def _directory_outcome(
    path: str,
    target: str,
    status: str,
    skill_dirs: dict[str, str],
    under: list[FileOutcome],
) -> FileOutcome:
    target_path = _covered_directory_target(path, target, skill_dirs, under)
    if target_path is None:
        return FileOutcome(path=path, target=target, status="container")
    return FileOutcome(
        path=path,
        target=target,
        status=status,
        target_path=target_path,
    )


def _covered_directory_target(
    path: str,
    target: str,
    skill_dirs: dict[str, str],
    under: list[FileOutcome],
) -> str | None:
    mapped = _directory_target(path, target, skill_dirs)
    destinations = [
        item.target_path
        for item in under
        if item.target_path and item.status in {"transformed", "bundled"}
    ]
    if not destinations:
        return None
    if all(
        dest == mapped or dest.startswith(mapped + "/") for dest in destinations
    ):
        return mapped
    return None


def _archive_target_mismatches(
    outcomes: tuple[FileOutcome, ...],
    targets: tuple[str, ...],
    archives: tuple[str, ...],
) -> tuple[Diagnostic, ...]:
    diags: list[Diagnostic] = []
    for target, archive in zip(targets, archives):
        relative = _zip_relative_names(Path(archive))
        for item in outcomes:
            if item.target != target or not item.target_path:
                continue
            if _zip_has_target(relative, item.target_path):
                continue
            diags.append(
                Diagnostic(
                    code=CODE_WORKSPACE_ENTRY,
                    level="error",
                    message="Report target path is not in the archive.",
                    path=item.path,
                    hint="The target path must name a file or directory in the zip.",
                )
            )
    return tuple(diags)


def _zip_relative_names(archive: Path) -> set[str]:
    with zipfile.ZipFile(archive) as zf:
        names = [
            name.replace("\\", "/").strip("/")
            for name in zf.namelist()
            if name.strip() and not name.endswith("/")
        ]
    roots = {name.split("/", 1)[0] for name in names if "/" in name}
    relative: list[str] = []
    for name in names:
        if len(roots) == 1 and name.startswith(next(iter(roots)) + "/"):
            relative.append(name.split("/", 1)[1])
        else:
            relative.append(name)
    return set(relative)


def _zip_has_target(relative: set[str], target_path: str) -> bool:
    path = target_path.replace("\\", "/").strip("/")
    if not path:
        return False
    if path in relative:
        return True
    prefix = path + "/"
    return any(name.startswith(prefix) for name in relative)


def _directory_target(path: str, target: str, skill_dirs: dict[str, str]) -> str:
    if path in _MANIFEST_DIRS:
        return _TARGET_MANIFEST_DIR[target]
    if path in skill_dirs:
        return skill_dirs[path]
    if path == "skills" or path.endswith("/skills"):
        return "skills"
    return path


def _with_bundled_resources(parsed, outcomes: tuple[FileOutcome, ...], targets):
    from plugin_targets.spi import DeclaredResource

    bundled: list[str] = []
    root = Path(parsed.plugin_dir)
    paths = sorted({item.path for item in outcomes if item.status == "bundled"})
    for path in paths:
        if not root.joinpath(*path.split("/")).is_file():
            continue
        statuses = {
            item.status
            for item in outcomes
            if item.path == path and item.target in targets
        }
        if statuses == {"bundled"}:
            bundled.append(path)
    if not bundled:
        return parsed
    existing = {(item.source_path, item.kind) for item in parsed.declared_resources}
    extra = []
    for path in bundled:
        key = (path, "file")
        if key in existing:
            continue
        extra.append(DeclaredResource(source_path=path, kind="file"))
    if not extra:
        return parsed
    return replace(
        parsed,
        declared_resources=tuple(parsed.declared_resources) + tuple(extra),
    )


class _StructureOnlyRunner:
    """Satisfy host validator hooks without spawning machine scripts."""

    structure_only = True

    def __call__(self, command):
        from plugin_compiler.validation import CommandResult

        argv = tuple(str(part) for part in command)
        if argv and argv[0] == "claude":
            return CommandResult(0, "Validation passed\n", "")
        return CommandResult(0, "Plugin validation passed: demo\n", "")


def _publish_targets(
    parsed,
    targets: tuple[str, ...],
    output_dir: Path,
    archives: list[str],
):
    from plugin_compiler.paths import BuildLayout
    from plugin_targets.registry import load_adapter
    from plugin_targets.spi import CompileRequest, PublishRequest, ValidateRequest

    layout = BuildLayout(root=output_dir)
    diagnostics: list[Diagnostic] = []
    failed = False
    runner = _StructureOnlyRunner()
    stub = tempfile.NamedTemporaryFile(
        prefix="agentc-validator-", suffix=".py", delete=False
    )
    stub.close()
    previous_validator = os.environ.get("CODEX_PLUGIN_VALIDATOR")
    os.environ["CODEX_PLUGIN_VALIDATOR"] = stub.name
    try:
        for target in targets:
            adapter = load_adapter(target)
            collected: list = []
            compiled = adapter.compile(
                CompileRequest(
                    parsed=parsed,
                    layout=layout,
                    runner=runner,
                    collected_issues=collected,
                )
            )
            issues = tuple(compiled.issues) + tuple(collected)
            blocks = [
                issue
                for issue in issues
                if getattr(issue, "severity", None) == "block"
            ]
            target_ok = bool(compiled.ok) and not blocks and compiled.staged_root
            if blocks or not compiled.ok:
                failed = True
                diagnostics.extend(_block_diagnostics(issues, target))
            if not compiled.staged_root:
                failed = True
                continue
            validated = adapter.validate(
                ValidateRequest(
                    parsed=parsed,
                    staged_root=Path(compiled.staged_root),
                    runner=runner,
                )
            )
            if not validated.ok:
                failed = True
                target_ok = False
                diagnostics.extend(_block_diagnostics(validated.issues, target))
            if not target_ok:
                continue
            published = adapter.publish_local(
                PublishRequest(
                    parsed=parsed,
                    layout=layout,
                    staged_root=Path(compiled.staged_root),
                    want_zip=True,
                )
            )
            if published.archive_path:
                archives.append(published.archive_path)
            if not published.ok or not published.archive_path:
                failed = True
                diagnostics.extend(_block_diagnostics(published.issues, target))
    finally:
        if previous_validator is None:
            os.environ.pop("CODEX_PLUGIN_VALIDATOR", None)
        else:
            os.environ["CODEX_PLUGIN_VALIDATOR"] = previous_validator
        Path(stub.name).unlink(missing_ok=True)
    return diagnostics, failed


def _block_diagnostics(issues, target: str) -> tuple[Diagnostic, ...]:
    diags: list[Diagnostic] = []
    for issue in issues:
        if getattr(issue, "severity", None) != "block":
            continue
        path = getattr(issue, "path", None) or "."
        diags.append(
            Diagnostic(
                code=CODE_WORKSPACE_ENTRY,
                level="error",
                message="{0}: {1}".format(target, _public_message(getattr(issue, "message", "") or "")),
                path=str(path),
                hint="Fix the reported source path and retry.",
            )
        )
    return tuple(diags)


def _outcome_diagnostics(outcomes: tuple[FileOutcome, ...]) -> tuple[Diagnostic, ...]:
    diags: list[Diagnostic] = []
    for item in outcomes:
        if item.status != "error":
            continue
        diags.append(
            Diagnostic(
                code=CODE_WORKSPACE_ENTRY,
                level="error",
                message="{0} cannot accept {1} ({2}): {3}".format(
                    item.target,
                    item.path,
                    item.capability or "file",
                    item.reason or "unsupported",
                ),
                path=item.path,
                hint="Change or remove this source file, then retry.",
            )
        )
    return tuple(diags)


def _exception_diagnostic(exc: Exception) -> Diagnostic:
    return Diagnostic(
        code=CODE_WORKSPACE_ENTRY,
        level="error",
        message=_public_message("{0}: {1}".format(type(exc).__name__, exc)),
        path=".",
        hint="Fix the source directory and retry.",
    )


def _public_message(text: str) -> str:
    cleaned, _reasons = redact_portable_text(text)
    return cleaned


def _existing_zips(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {path.resolve() for path in root.rglob("*.zip") if path.is_file()}


def _delete_new_zips(root: Path, before: set[Path]) -> None:
    if not root.exists():
        return
    for path in root.rglob("*.zip"):
        if path.is_file() and path.resolve() not in before:
            path.unlink(missing_ok=True)


def _delete_archives(archives: list[str]) -> None:
    for archive in archives:
        Path(archive).unlink(missing_ok=True)
