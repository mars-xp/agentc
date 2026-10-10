from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePath

from .agents import (
    AgentDocument,
    agent_conversion_issue,
    agent_native_relative,
    load_agent_document,
    render_agent_markdown,
)
from .commands import (
    CommandDocument,
    assert_safe_command_path_name,
    is_bin_passthrough_source,
    load_command_document,
    render_native_command,
)
from .extras import extra_conversion_issue, extra_output_relative, merge_lsp_documents, native_targets
from .hooks import (
    HookDocument,
    load_hook_document,
    merge_hook_payloads,
    remap_document,
    rule_loader_missing_issues,
)
from .manifests import is_plugin_manifest_source
from .mcp import (
    mcp_output_relative,
    mcp_secret_issues,
    merge_mcp_documents,
    payload_secret_issues,
    render_mcp_json,
    rewrite_mcp_document,
)
from .model import CapabilityKind, CapabilityRef, CompilerIssue, Target
from .overrides import (
    OverrideConfig,
    load_override_config,
    resolved_targets,
    skill_dir_allowed,
)
from .paths import require_inside
from .policy import is_blocked_sensitive_path
from .rules import (
    RuleDocument,
    cursor_output_relative,
    is_root_rule_source,
    rule_conversion_issue,
)
from .skills import render_skill_markdown
from .text import write_text_utf8_lf
from .layout_extras import extra_output_dir_names
from .workflows import (
    load_workflow_document,
    render_workflow_as_skill,
    workflow_conversion_issue,
)


_GENERATED_MANIFEST_DIRS = frozenset(
    {".cursor-plugin", ".codex-plugin", ".claude-plugin"}
)
_EXCLUDED_DIR_NAMES_ANYWHERE = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".superpowers",
        "__pycache__",
        "outputs",
    }
) | extra_output_dir_names()
_EXCLUDED_ROOT_DIR_NAMES = frozenset(
    {
        "build",
        "dist",
        "packaging",
    }
)
_EXCLUDED_SUFFIXES = frozenset(
    {".pyc", ".zip", ".gz", ".tgz", ".xz", ".bz2", ".7z"}
)
_GENERATED_FILE_NAMES = frozenset({"runtime-host.json"})
_REPLACED_TOP_DIRS = frozenset(
    {
        "rules",
        "commands",
        "agents",
        "workflows",
        "hooks",
        "output-styles",
        "themes",
        "monitors",
    }
)
_REPLACED_ROOT_FILES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "mcp.json",
        ".mcp.json",
        ".app.json",
        ".lsp.json",
    }
)
_EXTRAS_KINDS = frozenset(
    {
        CapabilityKind.CONNECTOR,
        CapabilityKind.LSP,
        CapabilityKind.OUTPUT_STYLE,
        CapabilityKind.THEME,
        CapabilityKind.MONITOR,
    }
)

_MANIFEST_PATHS = {
    Target.CURSOR: Path(".cursor-plugin/plugin.json"),
}


def _normalized_parts(path: PurePath) -> tuple[str, ...]:
    """Return POSIX-like path segments even for Windows-shaped relative paths."""
    return tuple(part for part in str(path).replace("\\", "/").split("/") if part)


def _require_target(target: Target) -> Target:
    if not isinstance(target, Target):
        raise ValueError("unsupported target: {0!r}".format(target))
    return target


def _has_symlink_component(path: Path, plugin_dir: Path) -> bool:
    current = path
    while current != plugin_dir:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _shared_should_skip(
    path: Path,
    plugin_dir: Path,
    config: OverrideConfig,
    host: str,
) -> bool:
    relative = path.relative_to(plugin_dir)
    parts = _normalized_parts(relative)
    if not parts:
        return True
    if parts[:2] == ("docs", "superpowers"):
        return True
    if parts[0] in _EXCLUDED_ROOT_DIR_NAMES:
        return True
    if any(
        part in _EXCLUDED_DIR_NAMES_ANYWHERE or part in _GENERATED_MANIFEST_DIRS
        for part in parts[:-1]
    ):
        return True
    if path.suffix.lower() in _EXCLUDED_SUFFIXES:
        return True
    if path.name in _GENERATED_FILE_NAMES or is_blocked_sensitive_path(path):
        return True
    if parts[0] == "skills" and len(parts) >= 2:
        if not skill_dir_allowed(config, parts[1], host):
            return True
    return False


_COMPONENT_DEFAULT_DIRS = {
    CapabilityKind.RULE.value: "rules",
    CapabilityKind.COMMAND.value: "commands",
    CapabilityKind.AGENT.value: "agents",
    CapabilityKind.OUTPUT_STYLE.value: "output-styles",
}


def copy_stage_files(
    plugin_dir: Path,
    destination: Path,
    sources: tuple[Path, ...],
    *,
    skill_source_roots: frozenset[str] | set[str] | None = None,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> None:
    """Copy retained source files into a staging tree, skipping replaced docs.

    Skill trees whose source root is not already skills/<name>/ are remapped
    into skills/<name>/ so non-default roots (e.g. custom-skills/) do not appear
    beside the generated skills/ output. Rules, commands, agents, and
    output-styles from a non-default declared directory are remapped into
    rules/, commands/, agents/, and output-styles/.
    """
    roots = frozenset(skill_source_roots or ())
    component_roots = component_source_roots or {}
    for source in sources:
        relative = source.relative_to(plugin_dir)
        remapped = _remap_nondefault_skill_relative(relative, roots)
        if remapped is False:
            continue
        if remapped is None:
            remapped = _remap_nondefault_component_relative(
                relative, component_roots
            )
        if remapped is False:
            continue
        if _is_replaced_source_document(relative):
            continue
        output_relative = remapped if remapped is not None else relative
        output = require_inside(destination, destination / output_relative)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)


def _posix_plugin_relative(raw: str) -> str:
    text = (raw or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _add_stage_relative(seen: set[str], relatives: list[str], raw: str) -> None:
    text = _posix_plugin_relative(raw)
    if not text or text in seen:
        return
    seen.add(text)
    relatives.append(text)
    parts = text.split("/")
    if len(parts) >= 2 and parts[-1] == "SKILL.md":
        parent = "/".join(parts[:-1])
        if parent and parent not in seen:
            seen.add(parent)
            relatives.append(parent)
    if (
        len(parts) >= 2
        and parts[0] in _REPLACED_TOP_DIRS
        and parts[0] != "hooks"
    ):
        parent = "/".join(parts[:-1])
        if parent and parent not in seen and parent != ".":
            seen.add(parent)
            relatives.append(parent)


def _files_for_declared_path(plugin_dir: Path, relative: str) -> tuple[Path, ...]:
    parts = tuple(part for part in relative.split("/") if part and part != ".")
    if not parts or any(part == ".." for part in parts):
        return ()
    resolved = plugin_dir.joinpath(*parts)
    if resolved.is_file():
        return (resolved,)
    if not resolved.is_dir():
        return ()
    # Expand only a declared path (directory or file), never the whole plugin root.
    return tuple(sorted(path for path in resolved.rglob("*") if path.is_file()))


def iter_parsed_stage_files(
    parsed,
    config: OverrideConfig,
    should_skip,
) -> tuple[Path, ...]:
    """Collect stage sources from declared_resources + document source_path only."""
    plugin_dir = Path(parsed.plugin_dir).resolve()
    relatives: list[str] = []
    seen_rel: set[str] = set()
    replaced_docs: set[str] = set()
    for resource in getattr(parsed, "declared_resources", ()) or ():
        _add_stage_relative(seen_rel, relatives, resource.source_path)
    for documents in (
        parsed.skills,
        parsed.commands,
        parsed.rules,
        parsed.hooks,
        parsed.agents,
        parsed.workflows,
        parsed.mcp_documents,
        parsed.connectors,
        parsed.lsp_documents,
        parsed.output_styles,
        parsed.themes,
        parsed.monitors,
    ):
        for document in documents:
            source_path = _posix_plugin_relative(
                getattr(document, "source_path", "") or ""
            )
            if source_path:
                replaced_docs.add(source_path)
            _add_stage_relative(seen_rel, relatives, document.source_path)

    collected: list[Path] = []
    seen_files: set[Path] = set()
    for relative in relatives:
        for path in _files_for_declared_path(plugin_dir, relative):
            if path in seen_files:
                continue
            posix = _posix_plugin_relative(str(path.relative_to(plugin_dir)))
            full_source = getattr(parsed, "workspace_full_source", False)
            if full_source and posix in {
                ".cursor-plugin/plugin.json",
                ".codex-plugin/plugin.json",
                ".claude-plugin/plugin.json",
            }:
                continue
            if full_source and posix.startswith("packaging/"):
                continue
            if full_source:
                if is_blocked_sensitive_path(path):
                    continue
                parts = posix.split("/")
                if parts[0] in {
                    "build", "dist", ".pytest_cache", "__pycache__"
                } or any(part in {"__pycache__", ".pytest_cache"} for part in parts):
                    continue
                if (
                    parts[0] == "skills"
                    and len(parts) >= 2
                    and should_skip(path, plugin_dir, config)
                ):
                    continue
            if posix in replaced_docs:
                continue
            if _has_symlink_component(path, plugin_dir):
                continue
            if not full_source and should_skip(path, plugin_dir, config):
                continue
            seen_files.add(path)
            collected.append(path)
    return tuple(sorted(collected))


def write_runtime_host(destination: Path, target: Target) -> Path:
    target = _require_target(target)
    output = require_inside(destination, destination / "runtime-host.json")
    write_text_utf8_lf(
        output, json.dumps({"host": target.value}, ensure_ascii=True) + "\n"
    )
    return output


def _write_contained(destination: Path, candidate: Path, text: str) -> Path:
    output = require_inside(destination, candidate)
    write_text_utf8_lf(output, text)
    return output


def _illegal_command_name_issue(
    document: CommandDocument,
    target: Target,
    code: str,
    reason: str,
) -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code=code,
        path=document.source_path,
        message=(
            "{0} target {1}: illegal name {2!r}: {3}".format(
                document.capability_id,
                target.value,
                document.name,
                reason,
            )
        ),
    )


def _is_source_skill_document(relative: Path) -> bool:
    """True for any .../<skill-dir>/SKILL.md (not only top-level skills/)."""
    parts = _normalized_parts(relative)
    return len(parts) >= 2 and parts[-1] == "SKILL.md"


def _skill_source_roots_from_parsed(parsed) -> frozenset[str]:
    """Posix dirs for SkillDocument.source_path trees (remapped to skills/ on write)."""
    roots: set[str] = set()
    for document in getattr(parsed, "skills", ()) or ():
        text = _posix_plugin_relative(getattr(document, "source_path", "") or "")
        if not text:
            continue
        parts = [part for part in text.split("/") if part and part != "."]
        if parts and parts[-1] == "SKILL.md":
            parts = parts[:-1]
        if parts:
            roots.add("/".join(parts))
    return frozenset(roots)


def _under_skill_source_root(
    relative: Path, skill_source_roots: frozenset[str]
) -> bool:
    if not skill_source_roots:
        return False
    rel = "/".join(_normalized_parts(relative))
    if not rel:
        return False
    for root in skill_source_roots:
        if rel == root or rel.startswith(root + "/"):
            return True
    return False


def _remap_nondefault_skill_relative(
    relative: Path, skill_source_roots: frozenset[str]
) -> Path | None | bool:
    """Remap files under non-skills/ skill roots into skills/<name>/.

    Returns:
      False — skip (SKILL.md / bare skill root; generated writer owns them)
      Path — copy to this relative destination
      None — not under a non-default skill root; use normal copy rules
    """
    if not skill_source_roots:
        return None
    rel = "/".join(_normalized_parts(relative))
    if not rel:
        return None
    for root in skill_source_roots:
        root_parts = [part for part in root.split("/") if part]
        if len(root_parts) >= 2 and root_parts[0] == "skills":
            continue
        if rel != root and not rel.startswith(root + "/"):
            continue
        dir_name = root_parts[-1] if root_parts else ""
        if not dir_name:
            return False
        suffix = rel[len(root) :].lstrip("/")
        if not suffix or suffix == "SKILL.md":
            return False
        return Path("skills") / dir_name / Path(suffix)
    return None


def _component_source_roots_from_parsed(parsed) -> dict[str, frozenset[str]]:
    """Declared directory (or file) roots for remappable component kinds."""
    grouped: dict[str, set[str]] = {
        kind: set() for kind in _COMPONENT_DEFAULT_DIRS
    }
    for resource in getattr(parsed, "declared_resources", ()) or ():
        kind = resource.kind
        if kind not in grouped:
            continue
        posix = _posix_plugin_relative(resource.source_path)
        if posix and not is_bin_passthrough_source(posix):
            grouped[kind].add(posix)
    for kind, documents in (
        (CapabilityKind.RULE.value, getattr(parsed, "rules", ()) or ()),
        (CapabilityKind.COMMAND.value, getattr(parsed, "commands", ()) or ()),
        (CapabilityKind.AGENT.value, getattr(parsed, "agents", ()) or ()),
        (
            CapabilityKind.OUTPUT_STYLE.value,
            getattr(parsed, "output_styles", ()) or (),
        ),
    ):
        for document in documents:
            posix = _posix_plugin_relative(
                getattr(document, "source_path", "") or ""
            )
            if posix and not is_bin_passthrough_source(posix):
                grouped[kind].add(posix)
    result: dict[str, frozenset[str]] = {}
    for kind, paths in grouped.items():
        default = _COMPONENT_DEFAULT_DIRS[kind]
        dir_roots = set()
        for path in paths:
            if path == default or path.startswith(default + "/"):
                continue
            if any(
                other != path and other.startswith(path + "/") for other in paths
            ):
                dir_roots.add(path)
        candidates = set(dir_roots)
        for path in paths:
            if path == default or path.startswith(default + "/"):
                continue
            if path in dir_roots:
                continue
            if any(path.startswith(root + "/") for root in dir_roots):
                continue
            candidates.add(path)
        minimal = set()
        for root in candidates:
            if any(
                root.startswith(other + "/")
                for other in candidates
                if other != root
            ):
                continue
            minimal.add(root)
        result[kind] = frozenset(minimal)
    return result


def _kind_component_roots(
    component_source_roots: dict[str, frozenset[str]] | None, kind: str
) -> frozenset[str] | None:
    """None means the caller omitted roots (writers must fail closed)."""
    if component_source_roots is None:
        return None
    return component_source_roots.get(kind, frozenset())


def _owning_component_kind(
    posix: str, roots_by_kind: dict[str, frozenset[str]]
) -> str | None:
    """Kind whose custom root is the longest matching prefix of posix."""
    posix = _posix_plugin_relative(posix)
    if not posix:
        return None
    best_kind = None
    best_len = -1
    for kind, roots in roots_by_kind.items():
        default = _COMPONENT_DEFAULT_DIRS.get(kind)
        if not default:
            continue
        for root in roots:
            root_parts = [part for part in root.split("/") if part]
            if not root_parts or root_parts[0] == default:
                continue
            if posix != root and not posix.startswith(root + "/"):
                continue
            if len(root) > best_len:
                best_len = len(root)
                best_kind = kind
    return best_kind


def _source_owned_by_kind(
    source_path: str,
    kind: str,
    roots_by_kind: dict[str, frozenset[str]] | None,
) -> bool:
    """False when a longer other-kind custom root owns this path."""
    if not roots_by_kind:
        return True
    owner = _owning_component_kind(source_path, roots_by_kind)
    return owner is None or owner == kind


def _remap_under_declared_roots(
    posix: str, default: str, roots: frozenset[str] | set[str]
) -> str | None:
    """Map posix onto default/ using suffix under a matching declared root."""
    for root in sorted(roots, key=len, reverse=True):
        root_parts = [part for part in root.split("/") if part]
        if not root_parts or root_parts[0] == default:
            continue
        if posix != root and not posix.startswith(root + "/"):
            continue
        suffix = posix[len(root) :].lstrip("/")
        if not suffix:
            return "{0}/{1}".format(default, root_parts[-1])
        return "{0}/{1}".format(default, suffix)
    return None


def _component_output_relative(
    source_path: str,
    kind: str,
    roots: frozenset[str] | set[str] | None,
) -> str:
    """Map a source relative path onto the target default directory for kind.

    ``roots is None`` is fail-closed: do not guess the first path segment.
    Pass an empty frozenset when there are no custom roots.
    """
    default = _COMPONENT_DEFAULT_DIRS.get(kind)
    posix = _posix_plugin_relative(source_path)
    if not default or not posix:
        return posix
    parts = [part for part in posix.split("/") if part]
    if parts and parts[0] == "bin":
        return posix
    if parts[0] == default:
        return posix
    if roots is None:
        raise ValueError(
            "declared {0} roots required to remap {1}".format(kind, posix)
        )
    remapped = _remap_under_declared_roots(posix, default, roots)
    if remapped is not None:
        return remapped
    return posix


def _remap_nondefault_component_relative(
    relative: Path, roots_by_kind: dict[str, frozenset[str]] | None
) -> Path | None | bool:
    """Remap files under custom component roots into the target default dir.

    Uses that file's owning kind only (longest matching custom root across
    kinds). Same-kind overlap still prefers the longest prefix.

    Returns:
      False — skip (declared directory itself; generated writer owns documents)
      Path — copy to this relative destination
      None — not under a remappable custom root; use normal copy rules
    """
    if not roots_by_kind:
        return None
    rel = "/".join(_normalized_parts(relative))
    if not rel:
        return None
    if is_bin_passthrough_source(rel):
        return None
    owner = _owning_component_kind(rel, roots_by_kind)
    if owner is None:
        return None
    default = _COMPONENT_DEFAULT_DIRS.get(owner)
    if not default:
        return None
    roots = roots_by_kind.get(owner, frozenset())
    remapped = _remap_under_declared_roots(rel, default, roots)
    if remapped is None:
        return None
    if rel in roots:
        name = Path(rel).name
        if "." not in name:
            return False
    return Path(remapped)


def _rule_output_relative(
    source_path: str, roots: frozenset[str] | set[str] | None
) -> str:
    if is_root_rule_source(source_path):
        return cursor_output_relative(source_path)
    return _component_output_relative(
        source_path, CapabilityKind.RULE.value, roots
    )


def _is_replaced_source_document(relative: Path) -> bool:
    if _is_source_skill_document(relative):
        return True
    parts = _normalized_parts(relative)
    if not parts:
        return False
    name = parts[-1]
    if name.endswith(".mdc"):
        return True
    if len(parts) == 1 and name in _REPLACED_ROOT_FILES:
        return True
    if parts and parts[0] == "hooks":
        return True
    return False


def _relative_from_source_path(source_path: str) -> Path:
    parts = _normalized_parts(PurePath(source_path))
    if not parts:
        raise ValueError("empty source path")
    return Path(*parts)


def _capability_ref(
    kind: CapabilityKind, capability_id: str, source_path: str
) -> CapabilityRef:
    return CapabilityRef(
        capability_id=capability_id,
        kind=kind,
        source_path=source_path,
        source_dialect=None,
    )


def _iter_source_files(plugin_dir: Path, top: str) -> tuple[Path, ...]:
    root = plugin_dir / top
    if not root.is_dir():
        return ()
    collected = []
    for path in sorted(root.rglob("*")):
        if _has_symlink_component(path, plugin_dir):
            continue
        if path.is_file():
            collected.append(path)
    return tuple(collected)


def _skill_dir_name(document) -> str:
    parts = _normalized_parts(PurePath(document.source_path))
    if len(parts) >= 2 and parts[0] == "skills":
        return parts[1]
    return document.name


def _existing_skill_names(skills, destination: Path) -> set[str]:
    names: set[str] = set()
    for document in skills:
        names.add(document.name)
        names.add(_skill_dir_name(document))
    root = destination / "skills"
    if not root.is_dir():
        return names
    for child in root.iterdir():
        if child.is_dir():
            names.add(child.name)
    return names


def _write_generated_skills(
    skills,
    target: Target,
    destination: Path,
    config: OverrideConfig,
    plugin_dir: Path,
) -> tuple[CompilerIssue, ...]:
    host = target.value
    issues: list[CompilerIssue] = []
    for document in skills:
        dir_name = _skill_dir_name(document)
        if not skill_dir_allowed(config, dir_name, host):
            continue
        parts = _normalized_parts(PurePath(document.source_path))
        source = plugin_dir.joinpath(*parts) if parts else plugin_dir
        if source.is_dir():
            source = source / "SKILL.md"
        if _has_symlink_component(source, plugin_dir):
            continue
        text, render_issues = render_skill_markdown(document, target)
        issues.extend(render_issues)
        output = destination / "skills" / dir_name / "SKILL.md"
        _write_contained(destination, output, text)
    return tuple(issues)


def _root_rule_files(plugin_dir: Path) -> tuple[Path, ...]:
    collected = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = plugin_dir / name
        if _has_symlink_component(path, plugin_dir):
            continue
        if path.is_file():
            collected.append(path)
    return tuple(collected)


def _write_generated_rules(
    rules: tuple[RuleDocument, ...],
    target: Target,
    config: OverrideConfig,
) -> tuple[tuple[CompilerIssue, ...], tuple[RuleDocument, ...]]:
    issues: list[CompilerIssue] = []
    compiled: list[RuleDocument] = []
    for document in rules:
        capability = _capability_ref(
            CapabilityKind.RULE, document.capability_id, document.source_path
        )
        if target not in resolved_targets(config, capability):
            continue
        conversion = rule_conversion_issue(document, target)
        if conversion is not None:
            issues.append(conversion)
            continue
        compiled.append(document)
    return tuple(issues), tuple(compiled)


def _write_generated_commands(
    commands: tuple[CommandDocument, ...],
    target: Target,
    destination: Path,
    config: OverrideConfig,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    command_roots = _kind_component_roots(
        component_source_roots, CapabilityKind.COMMAND.value
    )
    for document in commands:
        capability = _capability_ref(
            CapabilityKind.COMMAND, document.capability_id, document.source_path
        )
        if target not in resolved_targets(config, capability):
            continue
        if not _source_owned_by_kind(
            document.source_path,
            CapabilityKind.COMMAND.value,
            component_source_roots,
        ):
            continue
        if is_bin_passthrough_source(document.source_path):
            continue
        try:
            assert_safe_command_path_name(document.name)
        except ValueError as exc:
            issues.append(
                _illegal_command_name_issue(
                    document, target, "illegal-command-name", str(exc)
                )
            )
            continue
        try:
            relative = _component_output_relative(
                document.source_path,
                CapabilityKind.COMMAND.value,
                command_roots,
            )
        except ValueError as exc:
            issues.append(
                _illegal_command_name_issue(
                    document, target, "write-outside-destination", str(exc)
                )
            )
            continue
        output = destination / _relative_from_source_path(relative)
        try:
            _write_contained(destination, output, render_native_command(document))
        except ValueError as exc:
            issues.append(
                _illegal_command_name_issue(
                    document, target, "write-outside-destination", str(exc)
                )
            )
    return tuple(issues)


def _posix_relative(source_path: str) -> str:
    return "/".join(_normalized_parts(PurePath(source_path)))


def _claim_output(
    claimed: dict[str, str], relative: str, capability_id: str
) -> CompilerIssue | None:
    posix = _posix_relative(relative)
    previous = claimed.get(posix)
    if previous is not None and previous != capability_id:
        return CompilerIssue(
            severity="block",
            code="duplicate-output-path",
            path=posix,
            message="capabilities {0} and {1} both write {2}".format(
                previous, capability_id, posix
            ),
        )
    claimed[posix] = capability_id
    return None


def _with_source_path(issue: CompilerIssue, source_path: str) -> CompilerIssue:
    if issue.path == source_path:
        return issue
    return CompilerIssue(
        severity=issue.severity,
        code=issue.code,
        path=source_path,
        message=issue.message,
    )


def _render_skill_failure_issue(
    source_path: str, target: Target, exc: ValueError
) -> CompilerIssue:
    text = str(exc)
    code = (
        "illegal-codex-skill-name"
        if "kebab-case" in text
        else "undeclared-target-unsupported"
    )
    return CompilerIssue(
        severity="block",
        code=code,
        path=source_path,
        message="{0} target {1}: {2}".format(source_path, target.value, text),
    )


def _frontmatter_markdown(document) -> str:
    fields = getattr(document, "frontmatter_fields", ())
    body = document.body
    if not fields:
        return body
    lines = ["---"]
    for key, value in fields:
        lines.append("{0}: {1}".format(key, value))
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _hook_script_candidates(document: HookDocument) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for item in document.script_relpaths:
        posix = item.replace("\\", "/")
        if posix in seen:
            continue
        seen.add(posix)
        found.append(posix)
    return tuple(found)


def _reserved_rule_loader_issue() -> CompilerIssue:
    return CompilerIssue(
        severity="block",
        code="reserved-hook-path",
        path="hooks/rule_loader.py",
        message="hooks/rule_loader.py is reserved for the generated rule loader",
    )


def _user_rule_loader_present(plugin_dir: Path) -> bool:
    path = plugin_dir / "hooks" / "rule_loader.py"
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return False


def _emits_generated_rule_loader(compiled_rules: tuple) -> bool:
    return bool(compiled_rules)


def _copy_user_hook_scripts(
    plugin_dir: Path, destination: Path, document: HookDocument
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    for relpath in _hook_script_candidates(document):
        if relpath == "hooks/rule_loader.py":
            continue
        source = plugin_dir / relpath
        dest = destination / relpath
        try:
            require_inside(plugin_dir, source)
            require_inside(destination, dest)
        except ValueError:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="hook-script-escape",
                    path=relpath,
                    message=(
                        "hook script {0!r} escapes the plugin or staging root".format(
                            relpath
                        )
                    ),
                )
            )
            continue
        if _has_symlink_component(source, plugin_dir) or source.is_symlink():
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="hook-script-symlink",
                    path=relpath,
                    message="hook script {0!r} must not be a symlink".format(relpath),
                )
            )
            continue
        if not source.is_file():
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="missing-hook-script",
                    path=relpath,
                    message="hook script {0!r} is missing from the plugin source".format(
                        relpath
                    ),
                )
            )
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    return tuple(issues)


def _hooks_json_capability() -> CapabilityRef:
    return _capability_ref(
        CapabilityKind.HOOK, "hook:hooks/hooks.json", "hooks/hooks.json"
    )


def _hook_presence_targets(
    config: OverrideConfig, capability: CapabilityRef
) -> tuple[Target, ...]:
    """User hook scripts follow hooks/hooks.json targets; rule_loader does not."""
    parts = _normalized_parts(PurePath(capability.source_path))
    if (
        parts
        and parts[0] == "hooks"
        and parts[-1] not in ("hooks.json", "rule_loader.py")
    ):
        return resolved_targets(config, _hooks_json_capability())
    return resolved_targets(config, capability)


def _write_user_hooks(
    plugin_dir: Path,
    target: Target,
    destination: Path,
    compiled_rules: tuple,
    config: OverrideConfig,
    hooks: tuple = (),
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    emits_loader = _emits_generated_rule_loader(compiled_rules)
    if emits_loader and _user_rule_loader_present(plugin_dir):
        issues.append(_reserved_rule_loader_issue())
    users: list[HookDocument] = []
    for document in hooks:
        user_path = plugin_dir.joinpath(
            *_normalized_parts(PurePath(document.source_path))
        )
        if _has_symlink_component(user_path, plugin_dir):
            continue
        capability = _capability_ref(
            CapabilityKind.HOOK, document.capability_id, document.source_path
        )
        if target not in resolved_targets(config, capability):
            continue
        if any(
            item.replace("\\", "/") == "hooks/rule_loader.py"
            for item in _hook_script_candidates(document)
        ) and not any(item.code == "reserved-hook-path" for item in issues):
            issues.append(_reserved_rule_loader_issue())
        issues.extend(_copy_user_hook_scripts(plugin_dir, destination, document))
        users.append(document)
    compiled_present = bool(compiled_rules)
    generated = None
    generated_path = destination / "hooks" / "hooks.json"
    if compiled_present and generated_path.is_file():
        generated = json.loads(generated_path.read_text(encoding="utf-8"))
    if not users:
        if compiled_present and generated is not None:
            issues.extend(
                rule_loader_missing_issues(
                    generated, target, compiled_rules_present=True
                )
            )
        return tuple(issues)
    merged = generated
    for user in users:
        remapped, remap_issues = remap_document(user, target, plugin_dir)
        issues.extend(remap_issues)
        merged = merge_hook_payloads(merged, remapped)
    _write_contained(
        destination,
        generated_path,
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
    )
    issues.extend(
        rule_loader_missing_issues(
            merged, target, compiled_rules_present=compiled_present
        )
    )
    return tuple(issues)


def _generated_skill_conflict(
    source_path: str, name: str, existing_names: set[str]
) -> CompilerIssue | None:
    if name not in existing_names:
        return None
    return CompilerIssue(
        severity="block",
        code="generated-skill-conflict",
        path=source_path,
        message="generated skill name {0!r} conflicts with existing skill".format(
            name
        ),
    )


def _write_converted_skill(
    document,
    target: Target,
    destination: Path,
    claimed: dict[str, str],
    existing_names: set[str],
    render,
) -> tuple[CompilerIssue, ...]:
    conflict = _generated_skill_conflict(
        document.source_path, document.name, existing_names
    )
    if conflict is not None:
        return (conflict,)
    relative = "skills/{0}/SKILL.md".format(document.name)
    claimed_issue = _claim_output(claimed, relative, document.capability_id)
    if claimed_issue is not None:
        return (claimed_issue,)
    try:
        text = render(document)
        output = destination / "skills" / document.name / "SKILL.md"
        _write_contained(destination, output, text)
    except ValueError as exc:
        return (_render_skill_failure_issue(document.source_path, target, exc),)
    existing_names.add(document.name)
    return ()


def _write_native_markdown(
    document,
    relative: str,
    destination: Path,
    claimed: dict[str, str],
    target: Target,
) -> tuple[CompilerIssue, ...]:
    claimed_issue = _claim_output(claimed, relative, document.capability_id)
    if claimed_issue is not None:
        return (claimed_issue,)
    output = destination / _relative_from_source_path(relative)
    if isinstance(document, AgentDocument):
        text, render_issues = render_agent_markdown(document, target)
        issues: list[CompilerIssue] = list(render_issues)
    else:
        text = _frontmatter_markdown(document)
        issues = []
    try:
        _write_contained(destination, output, text)
    except ValueError as exc:
        return (
            CompilerIssue(
                severity="block",
                code="write-outside-destination",
                path=document.source_path,
                message=str(exc),
            ),
        )
    return tuple(issues)


def _write_generated_agents(
    agents,
    target: Target,
    destination: Path,
    config: OverrideConfig,
    claimed: dict[str, str],
    existing_names: set[str],
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    agent_roots = _kind_component_roots(
        component_source_roots, CapabilityKind.AGENT.value
    )
    for document in agents:
        capability = _capability_ref(
            CapabilityKind.AGENT, document.capability_id, document.source_path
        )
        if target not in resolved_targets(config, capability):
            continue
        if not _source_owned_by_kind(
            document.source_path,
            CapabilityKind.AGENT.value,
            component_source_roots,
        ):
            continue
        conversion = agent_conversion_issue(document, target)
        if conversion is not None:
            issues.append(conversion)
            continue
        try:
            relative = _component_output_relative(
                agent_native_relative(document),
                CapabilityKind.AGENT.value,
                agent_roots,
            )
        except ValueError as exc:
            issues.append(
                CompilerIssue(
                    severity="block",
                    code="write-outside-destination",
                    path=document.source_path,
                    message=str(exc),
                )
            )
            continue
        issues.extend(
            _write_native_markdown(
                document,
                relative,
                destination,
                claimed,
                target,
            )
        )
    return tuple(issues)


def _write_generated_workflows(
    workflows,
    target: Target,
    destination: Path,
    config: OverrideConfig,
    claimed: dict[str, str],
    existing_names: set[str],
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    for document in workflows:
        capability = _capability_ref(
            CapabilityKind.WORKFLOW, document.capability_id, document.source_path
        )
        if target not in resolved_targets(config, capability):
            continue
        conversion = workflow_conversion_issue(document, target)
        if conversion is not None:
            issues.append(conversion)
            continue
        issues.extend(
            _write_converted_skill(
                document,
                target,
                destination,
                claimed,
                existing_names,
                render_workflow_as_skill,
            )
        )
    return tuple(issues)


def _documents_for_target(documents, kind, target: Target, config: OverrideConfig):
    kept = []
    for document in documents:
        capability = _capability_ref(
            kind, document.capability_id, document.source_path
        )
        if target in resolved_targets(config, capability):
            kept.append(document)
    return tuple(kept)


def _write_generated_mcp(
    mcp_documents,
    target: Target,
    destination: Path,
    config: OverrideConfig,
    claimed: dict[str, str],
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    try:
        mcp_documents = merge_mcp_documents(
            _documents_for_target(
                mcp_documents, CapabilityKind.MCP, target, config
            )
        )
    except RuntimeError as exc:
        return (
            CompilerIssue(
                severity="block",
                code="sidecar-merge-collision",
                path=mcp_output_relative(target),
                message=str(exc),
            ),
        )
    for document in mcp_documents:
        capability = _capability_ref(
            CapabilityKind.MCP, document.capability_id, document.source_path
        )
        if target not in resolved_targets(config, capability):
            continue
        secret_issues = mcp_secret_issues(document)
        issues.extend(secret_issues)
        if secret_issues:
            continue
        rewritten, rewrite_issues = rewrite_mcp_document(document, target)
        issues.extend(rewrite_issues)
        if rewrite_issues:
            continue
        relative = mcp_output_relative(target)
        claimed_issue = _claim_output(claimed, relative, document.capability_id)
        if claimed_issue is not None:
            issues.append(claimed_issue)
            continue
        output = destination / relative
        _write_contained(destination, output, render_mcp_json(rewritten, target))
    return tuple(issues)


def _stage_extra_document(
    document,
    kind: CapabilityKind,
    target: Target,
    destination: Path,
    config: OverrideConfig,
    claimed: dict[str, str],
    text: str,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    capability = _capability_ref(kind, document.capability_id, document.source_path)
    if target not in resolved_targets(config, capability):
        return ()
    if kind.value in _COMPONENT_DEFAULT_DIRS and not _source_owned_by_kind(
        document.source_path, kind.value, component_source_roots
    ):
        return ()
    conversion = extra_conversion_issue(kind, target)
    if conversion is not None:
        return (_with_source_path(conversion, document.source_path),)
    relative = extra_output_relative(kind, document.source_path)
    if kind is CapabilityKind.OUTPUT_STYLE:
        try:
            relative = _component_output_relative(
                relative,
                CapabilityKind.OUTPUT_STYLE.value,
                _kind_component_roots(
                    component_source_roots, CapabilityKind.OUTPUT_STYLE.value
                ),
            )
        except ValueError as exc:
            return (
                CompilerIssue(
                    severity="block",
                    code="write-outside-destination",
                    path=document.source_path,
                    message=str(exc),
                ),
            )
    claimed_issue = _claim_output(claimed, relative, document.capability_id)
    if claimed_issue is not None:
        return (claimed_issue,)
    output = destination / _relative_from_source_path(relative)
    try:
        _write_contained(destination, output, text)
    except ValueError as exc:
        return (
            CompilerIssue(
                severity="block",
                code="write-outside-destination",
                path=document.source_path,
                message=str(exc),
            ),
        )
    return ()


def _json_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _output_style_text(document) -> str:
    lines = ["---", "name: {0}".format(document.name)]
    for key, value in document.extra_fields:
        lines.append("{0}: {1}".format(key, value))
    lines.append("---")
    return "\n".join(lines) + "\n" + document.body


def _write_generated_extras(
    connectors,
    lsp_documents,
    output_styles,
    themes,
    monitors,
    target: Target,
    destination: Path,
    config: OverrideConfig,
    claimed: dict[str, str],
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    issues: list[CompilerIssue] = []
    for document in connectors:
        secret_issues = payload_secret_issues(
            document.payload, document.source_path
        )
        issues.extend(secret_issues)
        if secret_issues:
            continue
        issues.extend(
            _stage_extra_document(
                document,
                CapabilityKind.CONNECTOR,
                target,
                destination,
                config,
                claimed,
                _json_text(document.payload),
                component_source_roots,
            )
        )
    try:
        lsp_documents = merge_lsp_documents(
            _documents_for_target(
                lsp_documents, CapabilityKind.LSP, target, config
            )
        )
    except RuntimeError as exc:
        issues.append(
            CompilerIssue(
                severity="block",
                code="sidecar-merge-collision",
                path=".lsp.json",
                message=str(exc),
            )
        )
        lsp_documents = ()
    for document in lsp_documents:
        secret_issues = payload_secret_issues(
            document.payload, document.source_path
        )
        issues.extend(secret_issues)
        if secret_issues:
            continue
        issues.extend(
            _stage_extra_document(
                document,
                CapabilityKind.LSP,
                target,
                destination,
                config,
                claimed,
                _json_text(document.payload),
                component_source_roots,
            )
        )
    for document in output_styles:
        issues.extend(
            _stage_extra_document(
                document,
                CapabilityKind.OUTPUT_STYLE,
                target,
                destination,
                config,
                claimed,
                _output_style_text(document),
                component_source_roots,
            )
        )
    for document in themes:
        issues.extend(
            _stage_extra_document(
                document,
                CapabilityKind.THEME,
                target,
                destination,
                config,
                claimed,
                _json_text(document.payload),
                component_source_roots,
            )
        )
    for document in monitors:
        issues.extend(
            _stage_extra_document(
                document,
                CapabilityKind.MONITOR,
                target,
                destination,
                config,
                claimed,
                _json_text(document.payload),
                component_source_roots,
            )
        )
    return tuple(issues)


def _remapped_skill_artifact(capability: CapabilityRef) -> Path:
    """Skills are always written under skills/<dir>/SKILL.md after remap."""
    parts = _normalized_parts(PurePath(capability.source_path))
    if parts and parts[-1] == "SKILL.md":
        parts = parts[:-1]
    if not parts:
        return Path("skills") / "SKILL.md"
    if len(parts) >= 2 and parts[0] == "skills":
        dir_name = parts[1]
    else:
        dir_name = parts[-1]
    return Path("skills") / dir_name / "SKILL.md"


def _artifact_path(capability: CapabilityRef, target: Target) -> Path | None:
    if capability.kind is CapabilityKind.PLUGIN_METADATA:
        return _MANIFEST_PATHS[target]
    if capability.kind is CapabilityKind.SKILL:
        return _remapped_skill_artifact(capability)
    parts = _normalized_parts(PurePath(capability.source_path))
    return Path(*parts) if parts else None


def _compiled_rule_present(staged_root: Path, capability_id: str) -> bool:
    index_path = staged_root / "compiled-rules" / "index.json"
    if not index_path.is_file():
        return False
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return False
    for entry in rules:
        if not isinstance(entry, dict) or entry.get("id") != capability_id:
            continue
        body = entry.get("body")
        if not isinstance(body, str) or not body:
            return False
        return (staged_root / "compiled-rules" / body).is_file()
    return False


def _load_command_for_check(
    capability: CapabilityRef,
    staged_root: Path,
    plugin_dir: Path | None,
) -> CommandDocument | None:
    roots = [item for item in (plugin_dir, staged_root) if item is not None]
    relative = _relative_from_source_path(capability.source_path)
    for root in roots:
        path = root / relative
        if not path.is_file():
            continue
        try:
            return load_command_document(path, root)
        except (OSError, UnicodeError, RuntimeError, ValueError):
            return None
    return None


def _load_agent_for_check(capability, staged_root, plugin_dir):
    return _load_typed_for_check(
        load_agent_document, capability, staged_root, plugin_dir
    )


def _command_artifact_present(
    capability: CapabilityRef,
    target: Target,
    staged_root: Path,
    plugin_dir: Path | None,
    capabilities: tuple[CapabilityRef, ...],
    config: OverrideConfig | None = None,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> bool:
    roots = _kind_component_roots(
        component_source_roots if component_source_roots is not None else {},
        CapabilityKind.COMMAND.value,
    )
    relatives = [
        _component_output_relative(
            capability.source_path, CapabilityKind.COMMAND.value, roots
        ),
        capability.source_path,
    ]
    for relative in relatives:
        if (staged_root / _relative_from_source_path(relative)).is_file():
            return True
    return False


def _command_unexpected_present(
    capability: CapabilityRef,
    target: Target,
    staged_root: Path,
    plugin_dir: Path | None,
    capabilities: tuple[CapabilityRef, ...],
    config: OverrideConfig | None = None,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> bool:
    return _command_artifact_present(
        capability,
        target,
        staged_root,
        plugin_dir,
        capabilities,
        config,
        component_source_roots,
    )


def _rule_artifact_present(
    capability: CapabilityRef,
    target: Target,
    staged_root: Path,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> bool:
    del target
    roots = _kind_component_roots(
        component_source_roots if component_source_roots is not None else {},
        CapabilityKind.RULE.value,
    )
    for relative in (
        _rule_output_relative(capability.source_path, roots),
        cursor_output_relative(capability.source_path),
        capability.source_path,
    ):
        if (staged_root / _relative_from_source_path(relative)).is_file():
            return True
    return False


def _load_typed_for_check(loader, capability, staged_root, plugin_dir):
    roots = [item for item in (plugin_dir, staged_root) if item is not None]
    relative = _relative_from_source_path(capability.source_path)
    for root in roots:
        path = root / relative
        if not path.is_file():
            continue
        try:
            return loader(path, root)
        except (OSError, UnicodeError, RuntimeError, ValueError):
            return None
    return None


def _hook_artifact_present(capability: CapabilityRef, staged_root: Path) -> bool:
    parts = _normalized_parts(PurePath(capability.source_path))
    if not parts:
        return False
    posix = "/".join(parts)
    sidecar = staged_root / "hooks" / "hooks.json"
    if is_plugin_manifest_source(posix) or posix == "hooks.json" or posix.endswith(
        ".json"
    ):
        return sidecar.is_file()
    if parts[0] != "hooks":
        return False
    native = staged_root.joinpath(*parts)
    if native.is_file():
        return True
    return parts[-1] == "hooks.json" and sidecar.is_file()


def _hook_unexpected_present(
    capability: CapabilityRef,
    staged_root: Path,
    plugin_dir: Path | None,
) -> bool:
    """User-hook exclusion must not treat the generated rule-loader skeleton as leak."""
    parts = _normalized_parts(PurePath(capability.source_path))
    if not parts or parts[0] != "hooks":
        return False
    posix = "/".join(parts)
    if posix == "hooks/rule_loader.py":
        return _hook_artifact_present(capability, staged_root)
    if posix != "hooks/hooks.json":
        return _hook_artifact_present(capability, staged_root)
    if not (staged_root / "hooks" / "hooks.json").is_file():
        return False
    if plugin_dir is None:
        return False
    user_path = plugin_dir / "hooks" / "hooks.json"
    if not user_path.is_file() or _has_symlink_component(user_path, plugin_dir):
        return False
    try:
        user = load_hook_document(
            user_path, plugin_dir, source_host=capability.source_dialect
        )
    except (OSError, UnicodeError, RuntimeError, ValueError):
        return False
    for relpath in _hook_script_candidates(user):
        if relpath == "hooks/rule_loader.py":
            continue
        if (staged_root / relpath).is_file():
            return True
    staged_text = (staged_root / "hooks" / "hooks.json").read_text(encoding="utf-8")
    for handler in user.handlers:
        command = handler.command.strip()
        if not command or "rule_loader.py" in command:
            continue
        if command in staged_text:
            return True
    return False


def _agent_artifact_present(
    capability: CapabilityRef,
    target: Target,
    staged_root: Path,
    plugin_dir: Path | None,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> bool:
    roots = _kind_component_roots(
        component_source_roots if component_source_roots is not None else {},
        CapabilityKind.AGENT.value,
    )
    for posix in (
        _component_output_relative(
            capability.source_path, CapabilityKind.AGENT.value, roots
        ),
        "/".join(_normalized_parts(PurePath(capability.source_path))),
    ):
        parts = tuple(part for part in posix.split("/") if part)
        if not parts or parts[0] != "agents":
            continue
        if staged_root.joinpath(*parts).is_file():
            return True
    return False


def _workflow_artifact_present(
    capability: CapabilityRef,
    target: Target,
    staged_root: Path,
    plugin_dir: Path | None,
) -> bool:
    document = _load_typed_for_check(
        load_workflow_document, capability, staged_root, plugin_dir
    )
    if document is None:
        return False
    return (staged_root / "skills" / document.name / "SKILL.md").is_file()


def _extra_artifact_present(
    capability: CapabilityRef,
    target: Target,
    staged_root: Path,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> bool:
    if target not in native_targets(capability.kind):
        return False
    parts = _normalized_parts(PurePath(capability.source_path))
    if not parts:
        return False
    posix = "/".join(parts)
    if capability.kind is CapabilityKind.CONNECTOR:
        if parts != (".app.json",):
            return False
    elif capability.kind is CapabilityKind.LSP:
        return (staged_root / ".lsp.json").is_file()
    elif capability.kind is CapabilityKind.OUTPUT_STYLE:
        roots = _kind_component_roots(
            component_source_roots if component_source_roots is not None else {},
            CapabilityKind.OUTPUT_STYLE.value,
        )
        posix = _component_output_relative(
            posix, CapabilityKind.OUTPUT_STYLE.value, roots
        )
        parts = tuple(part for part in posix.split("/") if part)
        if not parts or parts[0] != "output-styles":
            return False
        return staged_root.joinpath(*parts).is_file()
    elif capability.kind is CapabilityKind.THEME:
        if parts[0] != "themes":
            return False
    elif capability.kind is CapabilityKind.MONITOR:
        if parts[0] != "monitors":
            return False
    return staged_root.joinpath(*parts).is_file()


def _rule_loader_check_issues(
    target: Target, staged_root: Path
) -> tuple[CompilerIssue, ...]:
    if not (staged_root / "compiled-rules" / "index.json").is_file():
        return ()
    path = staged_root / "hooks" / "hooks.json"
    if not path.is_file():
        return rule_loader_missing_issues({}, target, compiled_rules_present=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return rule_loader_missing_issues(
        payload, target, compiled_rules_present=True
    )


def _presence_issue(
    code: str, capability: CapabilityRef, target: Target, missing: bool
) -> CompilerIssue:
    if missing:
        message = "{0} target is missing artifact for {1}".format(
            target.value, capability.capability_id
        )
    else:
        message = "{0} target has unexpected {1} artifact for {2}".format(
            target.value, capability.kind.value, capability.capability_id
        )
    return CompilerIssue(
        severity="block",
        code=code,
        path=capability.source_path,
        message=message,
    )


_EXPLAINED_MISSING_CODES = frozenset(
    {
        "undeclared-target-unsupported",
        "hook-script-escape",
        "illegal-codex-skill-name",
        "generated-skill-conflict",
        "duplicate-output-path",
        "mcp-secret-in-source",
    }
)


def _missing_already_explained(
    capability: CapabilityRef,
    target: Target,
    explained_issues: tuple[CompilerIssue, ...] | list[CompilerIssue] | None,
) -> bool:
    del target  # collected_issues are already scoped to one stage_target call
    if not explained_issues:
        return False
    source = capability.source_path.replace("\\", "/")
    for issue in explained_issues:
        if issue.code not in _EXPLAINED_MISSING_CODES:
            continue
        if issue.path.replace("\\", "/") == source:
            return True
    return False


def _kind_applies_to_target(
    capabilities: tuple[CapabilityRef, ...],
    kind: CapabilityKind,
    target: Target,
    config: OverrideConfig,
) -> bool:
    return any(
        item.kind is kind and target in resolved_targets(config, item)
        for item in capabilities
    )


def _shared_sidecar_unexpected_issues(
    capabilities: tuple[CapabilityRef, ...],
    target: Target,
    staged_root: Path,
    config: OverrideConfig,
) -> tuple[CompilerIssue, ...]:
    """Flag a shared sidecar only when no remaining document owns it."""
    issues: list[CompilerIssue] = []
    mcp_relative = mcp_output_relative(target)
    if (staged_root / mcp_relative).is_file() and not _kind_applies_to_target(
        capabilities, CapabilityKind.MCP, target, config
    ):
        issues.append(
            _presence_issue(
                "unexpected-mcp-artifact",
                _capability_ref(
                    CapabilityKind.MCP,
                    "mcp:{0}".format(mcp_relative),
                    mcp_relative,
                ),
                target,
                missing=False,
            )
        )
    lsp_relative = extra_output_relative(CapabilityKind.LSP, ".lsp.json")
    if (staged_root / lsp_relative).is_file() and not _kind_applies_to_target(
        capabilities, CapabilityKind.LSP, target, config
    ):
        issues.append(
            _presence_issue(
                "unexpected-lsp-artifact",
                _capability_ref(
                    CapabilityKind.LSP,
                    "lsp:{0}".format(lsp_relative),
                    lsp_relative,
                ),
                target,
                missing=False,
            )
        )
    return tuple(issues)


def check_target_artifacts(
    capabilities: tuple[CapabilityRef, ...],
    target: Target,
    staged_root: Path,
    config: OverrideConfig | None = None,
    plugin_dir: Path | None = None,
    explained_issues: tuple[CompilerIssue, ...] | list[CompilerIssue] | None = None,
    component_source_roots: dict[str, frozenset[str]] | None = None,
) -> tuple[CompilerIssue, ...]:
    """Require every discovered source capability in this Phase 1 target."""
    target = _require_target(target)
    if config is None:
        config = load_override_config(plugin_dir if plugin_dir is not None else staged_root)
    issues = []
    for capability in capabilities:
        if (
            capability.kind.value in _COMPONENT_DEFAULT_DIRS
            and not _source_owned_by_kind(
                capability.source_path,
                capability.kind.value,
                component_source_roots,
            )
        ):
            continue
        if capability.kind is CapabilityKind.SKILL:
            artifact = _artifact_path(capability, target)
            exists = artifact is not None and (staged_root / artifact).is_file()
            unexpected_exists = exists
            unexpected_code = "unexpected-skill-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.RULE:
            exists = _rule_artifact_present(
                capability, target, staged_root, component_source_roots
            )
            unexpected_exists = exists
            unexpected_code = "unexpected-rule-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.COMMAND:
            exists = _command_artifact_present(
                capability,
                target,
                staged_root,
                plugin_dir,
                capabilities,
                config,
                component_source_roots,
            )
            unexpected_exists = _command_unexpected_present(
                capability,
                target,
                staged_root,
                plugin_dir,
                capabilities,
                config,
                component_source_roots,
            )
            unexpected_code = "unexpected-command-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.PLUGIN_METADATA:
            artifact = _artifact_path(capability, target)
            if artifact is not None and (staged_root / artifact).is_file():
                continue
            if not _missing_already_explained(capability, target, explained_issues):
                issues.append(
                    _presence_issue(
                        "missing-target-artifact", capability, target, missing=True
                    )
                )
            continue
        elif capability.kind is CapabilityKind.HOOK:
            exists = _hook_artifact_present(capability, staged_root)
            unexpected_exists = _hook_unexpected_present(
                capability, staged_root, plugin_dir
            )
            unexpected_code = "unexpected-hook-artifact"
            presence_targets = _hook_presence_targets(config, capability)
        elif capability.kind is CapabilityKind.AGENT:
            exists = _agent_artifact_present(
                capability,
                target,
                staged_root,
                plugin_dir,
                component_source_roots,
            )
            unexpected_exists = exists
            unexpected_code = "unexpected-agent-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.WORKFLOW:
            exists = _workflow_artifact_present(
                capability, target, staged_root, plugin_dir
            )
            unexpected_exists = exists
            unexpected_code = "unexpected-workflow-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind is CapabilityKind.MCP:
            exists = (staged_root / mcp_output_relative(target)).is_file()
            unexpected_exists = False
            unexpected_code = "unexpected-mcp-artifact"
            presence_targets = resolved_targets(config, capability)
        elif capability.kind in _EXTRAS_KINDS:
            exists = _extra_artifact_present(
                capability, target, staged_root, component_source_roots
            )
            unexpected_exists = (
                exists and capability.kind is not CapabilityKind.LSP
            )
            unexpected_code = "unexpected-{0}-artifact".format(capability.kind.value)
            presence_targets = resolved_targets(config, capability)
        else:
            artifact = _artifact_path(capability, target)
            if artifact is not None and (staged_root / artifact).is_file():
                continue
            if not _missing_already_explained(capability, target, explained_issues):
                issues.append(
                    _presence_issue(
                        "missing-target-artifact", capability, target, missing=True
                    )
                )
            continue
        if target not in presence_targets:
            if unexpected_exists:
                issues.append(
                    _presence_issue(
                        unexpected_code, capability, target, missing=False
                    )
                )
            continue
        if exists:
            continue
        if _missing_already_explained(capability, target, explained_issues):
            continue
        issues.append(
            _presence_issue(
                "missing-target-artifact", capability, target, missing=True
            )
        )
    issues.extend(
        _shared_sidecar_unexpected_issues(
            capabilities, target, staged_root, config
        )
    )
    issues.extend(_rule_loader_check_issues(target, staged_root))
    return tuple(issues)
