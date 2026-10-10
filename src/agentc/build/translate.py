"""Translate CanonicalProject IR into plugin_compiler request objects."""

from __future__ import annotations

from pathlib import Path

from agentc.model.ir import CanonicalProject
from agentc.overlay.ops import (
    OVERLAY_HOSTS,
    check_overlays,
    fields_for_host,
    parse_overlay_ops,
)
from plugin_compiler.agents import AgentDocument, load_agent_document
from plugin_compiler.commands import CommandDocument, load_command_document
from plugin_compiler.manifests import PluginIdentity
from plugin_compiler.model import CapabilityKind, CapabilityRef
from plugin_compiler.overrides import DEFAULT_TARGETS, OverrideConfig
from plugin_compiler.paths import BuildLayout
from plugin_compiler.rules import RuleDocument, load_rule_document
from plugin_compiler.skills import SkillDocument, load_skill_document
from plugin_targets.spi import CompileRequest, ParsedPlugin, SourceExtension, TargetOverlay

_SOURCE_DIALECT = "agent-project"


class TranslateError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def _skill_md_path(project_root: Path, skill_rel: str) -> Path:
    return project_root / skill_rel / "SKILL.md"


def _load_skill(
    project_root: Path, skill_rel: str
) -> SkillDocument:
    skill_md = _skill_md_path(project_root, skill_rel)
    if not skill_md.is_file():
        raise TranslateError(
            "missing skill document: {0}".format(skill_md.as_posix())
        )
    try:
        return load_skill_document(
            skill_md, project_root, source_dialect=_SOURCE_DIALECT
        )
    except RuntimeError as exc:
        raise TranslateError(str(exc)) from exc


def _load_skills(project_root: Path, ir: CanonicalProject) -> tuple[SkillDocument, ...]:
    return tuple(_load_skill(project_root, rel) for rel in ir.skill_paths)


def _load_rule(project_root: Path, rule_rel: str) -> RuleDocument:
    rule_path = project_root / rule_rel
    if not rule_path.is_file():
        raise TranslateError(
            "missing rule document: {0}".format(rule_path.as_posix())
        )
    try:
        return load_rule_document(rule_path, project_root)
    except RuntimeError as exc:
        raise TranslateError(str(exc)) from exc


def _load_rules(project_root: Path, ir: CanonicalProject) -> tuple[RuleDocument, ...]:
    return tuple(_load_rule(project_root, rel) for rel in ir.rule_paths)


def _load_command(project_root: Path, command_rel: str) -> CommandDocument:
    command_path = project_root / command_rel
    if not command_path.is_file():
        raise TranslateError(
            "missing command document: {0}".format(command_path.as_posix())
        )
    try:
        return load_command_document(command_path, project_root)
    except RuntimeError as exc:
        raise TranslateError(str(exc)) from exc


def _load_commands(
    project_root: Path, ir: CanonicalProject
) -> tuple[CommandDocument, ...]:
    return tuple(_load_command(project_root, rel) for rel in ir.command_paths)


def _load_agent(project_root: Path, agent_rel: str) -> AgentDocument:
    agent_path = project_root / agent_rel
    if not agent_path.is_file():
        raise TranslateError(
            "missing agent document: {0}".format(agent_path.as_posix())
        )
    try:
        return load_agent_document(
            agent_path, project_root, source_dialect=_SOURCE_DIALECT
        )
    except RuntimeError as exc:
        raise TranslateError(str(exc)) from exc


def _load_agents(project_root: Path, ir: CanonicalProject) -> tuple[AgentDocument, ...]:
    return tuple(_load_agent(project_root, rel) for rel in ir.agent_paths)


def _skill_dir_source_path(skill_file_path: str) -> str:
    suffix = "/SKILL.md"
    if skill_file_path.endswith(suffix):
        return skill_file_path[: -len(suffix)]
    return skill_file_path


def _capabilities_for_skills(
    skills: tuple[SkillDocument, ...],
) -> tuple[CapabilityRef, ...]:
    return tuple(
        CapabilityRef(
            capability_id="skill:{0}".format(_skill_dir_source_path(skill.source_path)),
            kind=CapabilityKind.SKILL,
            source_path=_skill_dir_source_path(skill.source_path),
            source_dialect=_SOURCE_DIALECT,
        )
        for skill in skills
    )


def _capabilities_for_rules(
    rules: tuple[RuleDocument, ...],
) -> tuple[CapabilityRef, ...]:
    return tuple(
        CapabilityRef(
            capability_id=rule.capability_id,
            kind=CapabilityKind.RULE,
            source_path=rule.source_path,
            source_dialect=_SOURCE_DIALECT,
        )
        for rule in rules
    )


def _capabilities_for_commands(
    commands: tuple[CommandDocument, ...],
) -> tuple[CapabilityRef, ...]:
    return tuple(
        CapabilityRef(
            capability_id=command.capability_id,
            kind=CapabilityKind.COMMAND,
            source_path=command.source_path,
            source_dialect=_SOURCE_DIALECT,
        )
        for command in commands
    )


def _capabilities_for_agents(
    agents: tuple[AgentDocument, ...],
) -> tuple[CapabilityRef, ...]:
    return tuple(
        CapabilityRef(
            capability_id=agent.capability_id,
            kind=CapabilityKind.AGENT,
            source_path=agent.source_path,
            source_dialect=_SOURCE_DIALECT,
        )
        for agent in agents
    )


def _load_target_overlays(project_root: Path) -> tuple[TargetOverlay, ...]:
    issues = check_overlays(project_root)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        first = errors[0]
        raise TranslateError("{0}: {1}".format(first.path, first.message))
    ops, _parsed_issues = parse_overlay_ops(project_root)
    overlays: list[TargetOverlay] = []
    for host in OVERLAY_HOSTS:
        fields, remove_keys = fields_for_host(ops, host)
        if not fields and not remove_keys:
            continue
        overlays.append(
            TargetOverlay(target_id=host, fields=fields, remove_keys=remove_keys)
        )
    return tuple(overlays)


def _split_identity_overlay_fields(
    identity: PluginIdentity,
    overlays: tuple[TargetOverlay, ...],
) -> tuple[PluginIdentity, tuple[TargetOverlay, ...]]:
    """Lift name/version/author off overlay JSON into identity.

    Codex/Claude adapters reject those keys in overlay merge. Host-skills
    still supplies them via overlays/<host>/*.json; translate moves them
    onto PluginIdentity so _put_identity can write them.
    """
    name = identity.name
    version = identity.version
    author = identity.author
    rewritten: list[TargetOverlay] = []
    changed = False
    for overlay in overlays:
        fields = dict(overlay.fields)
        overlay_name = fields.pop("name", None)
        if isinstance(overlay_name, str) and overlay_name:
            name = overlay_name
            changed = True
        overlay_version = fields.pop("version", None)
        if isinstance(overlay_version, str) and overlay_version:
            version = overlay_version
            changed = True
        if "author" in overlay.fields:
            author = fields.pop("author")
            changed = True
        rewritten.append(
            TargetOverlay(
                target_id=overlay.target_id,
                fields=fields,
                remove_keys=getattr(overlay, "remove_keys", ()),
            )
        )
    if not changed:
        return identity, overlays
    return (
        PluginIdentity(
            name=name,
            version=version,
            description=identity.description,
            author=author,
            homepage=identity.homepage,
            repository=identity.repository,
            license=identity.license,
            keywords=identity.keywords,
        ),
        tuple(rewritten),
    )


def translate_to_parsed(project_root: Path, ir: CanonicalProject) -> ParsedPlugin:
    resolved_root = project_root.resolve()
    skills = _load_skills(resolved_root, ir)
    rules = _load_rules(resolved_root, ir)
    commands = _load_commands(resolved_root, ir)
    agents = _load_agents(resolved_root, ir)
    capabilities = (
        _capabilities_for_skills(skills)
        + _capabilities_for_rules(rules)
        + _capabilities_for_commands(commands)
        + _capabilities_for_agents(agents)
    )
    overlays = _load_target_overlays(resolved_root)
    identity, overlays = _split_identity_overlay_fields(
        PluginIdentity(
            name=ir.name,
            version=ir.version,
            description=ir.name,
            author=None,
        ),
        overlays,
    )
    return ParsedPlugin(
        plugin_dir=resolved_root,
        plugin_name=ir.project_id,
        source_dialect=_SOURCE_DIALECT,
        identity=identity,
        source_extension=SourceExtension(dialect=_SOURCE_DIALECT, fields={}),
        skills=skills,
        capabilities=capabilities,
        commands=commands,
        rules=rules,
        hooks=(),
        agents=agents,
        workflows=(),
        mcp_documents=(),
        connectors=(),
        lsp_documents=(),
        output_styles=(),
        themes=(),
        monitors=(),
        override_config=OverrideConfig(
            schema_version=1,
            default_targets=DEFAULT_TARGETS,
            overrides={},
            command_bindings={},
        ),
        target_overlays=overlays,
        declared_resources=(),
        source_issues=(),
    )


def translate_to_request(project_root: Path, ir: CanonicalProject) -> CompileRequest:
    return CompileRequest(
        parsed=translate_to_parsed(project_root, ir),
        layout=BuildLayout.from_repo(project_root),
        runner=None,
        collected_issues=[],
    )
