from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

from .contracts import compiled_rule_hook_events, plugin_root_shell_var, plugin_root_shell_vars
from .model import CompilerIssue, Target
from .paths import require_inside
from .rules import RuleDocument
from .text import write_text_utf8_lf


_CODEX_LIMIT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})
_LEGAL_WHEN = frozenset({"session_start", "path_scope"})
_SCHEMA_VERSION = 1
_CODEX_MANIFEST = Path(".codex-plugin/plugin.json")
_CODEX_DEFAULT_HOOKS = "hooks/hooks.json"
_CODEX_FOREIGN_HOST_NEEDLES = (
    ".cursor/projects/",
    "canvases/<name>.canvas.tsx",
    "/canvases/",
    "~/.cursor/plugins",
)


class _RuleLoaderCommandMap:
    def get(self, host: object, default: str | None = None) -> str | None:
        if not isinstance(host, str):
            return default
        try:
            target = Target(host)
        except ValueError:
            return default
        if target is Target.CURSOR:
            return default
        vars_ = plugin_root_shell_vars(target)
        if not vars_:
            return default
        return "python3 {0}/hooks/rule_loader.py".format(vars_[0])


RULE_LOADER_COMMANDS = _RuleLoaderCommandMap()


def _compiled_body_relative(source_path: str) -> str:
    posix = source_path.replace("\\", "/").lstrip("/")
    return "bodies/{0}.txt".format(posix)


def _normalized_body_key(body: str) -> str:
    posix = body.replace("\\", "/")
    parts = tuple(
        part for part in PurePosixPath(posix).parts if part not in ("", ".")
    )
    return "/".join(parts)


def compiled_rule_entry(document: RuleDocument) -> dict:
    return {
        "id": document.capability_id,
        "when": "session_start" if document.always_apply else "path_scope",
        "globs": list(document.globs),
        "body": _compiled_body_relative(document.source_path),
    }


def write_compiled_rules(
    documents: tuple[RuleDocument, ...],
    destination: Path,
) -> Path:
    compiled_root = destination / "compiled-rules"
    entries = []
    for document in documents:
        entry = compiled_rule_entry(document)
        body_path = require_inside(compiled_root, compiled_root / entry["body"])
        write_text_utf8_lf(body_path, document.body)
        entries.append(entry)
    index_path = compiled_root / "index.json"
    write_text_utf8_lf(
        index_path,
        json.dumps(
            {"schema_version": 1, "rules": entries},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return index_path


def _hook_events(target: Target) -> tuple[str, ...]:
    events = compiled_rule_hook_events(target)
    if not events:
        raise ValueError(
            "rule hooks are not generated for target {0}".format(target.value)
        )
    return events


def _plugin_root_var(target: Target) -> str:
    if target is Target.CURSOR:
        raise ValueError(
            "rule hooks are not generated for target {0}".format(target.value)
        )
    return plugin_root_shell_var(target)


def rule_loader_command(target: Target) -> str:
    return "python3 {0}/hooks/rule_loader.py".format(_plugin_root_var(target))


def write_rule_hooks(destination: Path, target: Target) -> Path:
    command = rule_loader_command(target)
    apply_limit = target is Target.CODEX
    hooks: dict = {}
    for event_name in _hook_events(target):
        handler = {"type": "command", "command": command}
        if apply_limit and event_name in _CODEX_LIMIT_EVENTS:
            handler["additionalContextLimit"] = 0
        hooks[event_name] = [{"hooks": [handler]}]
    path = destination / "hooks" / "hooks.json"
    write_text_utf8_lf(
        path,
        json.dumps({"hooks": hooks}, ensure_ascii=False, indent=2) + "\n",
    )
    return path


def copy_rule_loader(destination: Path) -> Path:
    source = Path(__file__).resolve().parent / "rule_loader.py"
    output = destination / "hooks" / "rule_loader.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    return output


def _issue(code: str, path: str, message: str) -> CompilerIssue:
    return CompilerIssue(severity="block", code=code, path=path, message=message)


def _read_json_object(
    path: Path, *, code: str, label: str
) -> tuple[dict | None, CompilerIssue | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, _issue(code, path.as_posix(), "{0} is missing".format(label))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, _issue(
            code,
            path.as_posix(),
            "{0} is not readable JSON: {1}".format(label, exc),
        )
    if not isinstance(raw, dict):
        return None, _issue(
            code, path.as_posix(), "{0} root must be an object".format(label)
        )
    return raw, None


def _body_path_issues(compiled_root: Path, body: object) -> list[CompilerIssue]:
    if not isinstance(body, str) or not body.strip():
        return [
            _issue(
                "compiled-rule-body-escape",
                "compiled-rules/index.json",
                "compiled rule body must be a non-empty relative path",
            )
        ]
    normalized = body.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or PureWindowsPath(body).is_absolute():
        return [
            _issue(
                "compiled-rule-body-escape",
                body,
                "absolute compiled rule body paths are not allowed",
            )
        ]
    if ".." in candidate.parts:
        return [
            _issue(
                "compiled-rule-body-escape",
                body,
                "parent path segments are not allowed in compiled rule bodies",
            )
        ]
    parts = tuple(part for part in candidate.parts if part not in ("", "."))
    if not parts:
        return [
            _issue(
                "compiled-rule-body-escape",
                body,
                "compiled rule body must name a file inside compiled-rules/",
            )
        ]
    current = compiled_root
    for part in parts:
        current = current / part
        if not current.is_symlink():
            continue
        try:
            require_inside(compiled_root, current)
        except ValueError:
            return [
                _issue(
                    "compiled-rule-body-escape",
                    body,
                    "compiled rule body symlink escapes compiled-rules/",
                )
            ]
    try:
        resolved = require_inside(compiled_root, compiled_root.joinpath(*parts))
    except ValueError:
        return [
            _issue(
                "compiled-rule-body-escape",
                body,
                "compiled rule body resolves outside compiled-rules/",
            )
        ]
    if not resolved.is_file():
        return [
            _issue(
                "compiled-rule-body-escape",
                body,
                "compiled rule body is missing inside compiled-rules/",
            )
        ]
    return []


def _first_handler(hooks: object, event_name: str) -> dict | None:
    if not isinstance(hooks, dict):
        return None
    entries = hooks.get(event_name)
    if not isinstance(entries, list) or not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    inner = first.get("hooks")
    if not isinstance(inner, list) or not inner:
        return None
    handler = inner[0]
    return handler if isinstance(handler, dict) else None


def _hooks_command_issues(destination: Path, target: Target) -> list[CompilerIssue]:
    path = destination / "hooks" / "hooks.json"
    display = "hooks/hooks.json"
    payload, error = _read_json_object(
        path, code="invalid-rule-hooks", label="hooks/hooks.json"
    )
    if error is not None:
        if not path.is_file():
            return [
                _issue(
                    "invalid-rule-hooks",
                    display,
                    "compiled rules require hooks/hooks.json",
                )
            ]
        return [error]
    hooks = payload.get("hooks") if payload is not None else None
    expected = rule_loader_command(target)
    other_target = Target.CLAUDE_CODE if target is Target.CODEX else Target.CODEX
    other_var = plugin_root_shell_var(other_target)
    issues: list[CompilerIssue] = []
    for event_name in _hook_events(target):
        handler = _first_handler(hooks, event_name)
        if handler is None:
            issues.append(
                _issue(
                    "invalid-rule-hooks",
                    display,
                    "hooks event {0} must declare a command handler".format(event_name),
                )
            )
            continue
        command = handler.get("command")
        if handler.get("type") != "command" or not isinstance(command, str):
            issues.append(
                _issue(
                    "invalid-rule-hooks",
                    display,
                    "hooks event {0} command is missing".format(event_name),
                )
            )
            continue
        if expected not in command or other_var in command.replace(
            _plugin_root_var(target), ""
        ):
            issues.append(
                _issue(
                    "invalid-rule-hooks",
                    display,
                    "hooks event {0} must run {1!r}".format(event_name, expected),
                )
            )
        if (
            target is Target.CODEX
            and event_name in _CODEX_LIMIT_EVENTS
            and handler.get("additionalContextLimit") != 0
        ):
            issues.append(
                _issue(
                    "invalid-rule-hooks",
                    display,
                    "Codex event {0} must set additionalContextLimit to 0".format(
                        event_name
                    ),
                )
            )
    return issues


def _codex_default_hooks_undeclared_issues(
    destination: Path, target: Target
) -> list[CompilerIssue]:
    """Do not treat Codex default hooks/hooks.json as an undeclared component.

    Task 11 omits the Codex manifest ``hooks`` key on purpose so the official
    validator can discover ``hooks/hooks.json`` by convention. That omission is
    not an undeclared-component failure.
    """
    if target is not Target.CODEX:
        return []
    hooks_file = destination / "hooks" / "hooks.json"
    if not hooks_file.is_file():
        return []
    manifest_path = destination / _CODEX_MANIFEST
    payload, _error = _read_json_object(
        manifest_path, code="compiled-rule-schema", label="Codex plugin manifest"
    )
    if payload is None:
        return []
    if "hooks" in payload:
        return []
    relative = _CODEX_DEFAULT_HOOKS
    if hooks_file.resolve().name != "hooks.json":
        return []
    # Default discovery path only; any other hooks location would be undeclared.
    try:
        require_inside(destination, hooks_file)
    except ValueError:
        return [
            _issue(
                "undeclared-component",
                relative,
                "hooks file resolves outside the staged plugin",
            )
        ]
    return []


def _codex_foreign_host_semantics_issues(
    compiled_root: Path, rules: list
) -> list[CompilerIssue]:
    issues: list[CompilerIssue] = []
    for entry in rules:
        if not isinstance(entry, dict):
            continue
        body = entry.get("body")
        if not isinstance(body, str) or not body.strip():
            continue
        normalized = body.replace("\\", "/")
        parts = tuple(
            part for part in PurePosixPath(normalized).parts if part not in ("", ".")
        )
        if not parts:
            continue
        try:
            body_path = require_inside(compiled_root, compiled_root.joinpath(*parts))
        except ValueError:
            continue
        if not body_path.is_file():
            continue
        try:
            content = body_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for needle in _CODEX_FOREIGN_HOST_NEEDLES:
            if needle in content:
                issues.append(
                    _issue(
                        "compiled-rule-target-host-semantics",
                        body,
                        "compiled rule body contains Cursor-only host semantics: {0!r}".format(
                            needle
                        ),
                    )
                )
                break
    return issues


def validate_compiled_rule_bundle(
    destination: Path, target: Target
) -> tuple[CompilerIssue, ...]:
    if not isinstance(target, Target):
        raise ValueError("unsupported target: {0!r}".format(target))
    destination = Path(destination)
    index_path = destination / "compiled-rules" / "index.json"
    if not index_path.is_file():
        return ()
    issues: list[CompilerIssue] = []
    payload, error = _read_json_object(
        index_path, code="compiled-rule-schema", label="compiled-rules index"
    )
    if error is not None:
        return (error,)
    assert payload is not None
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != _SCHEMA_VERSION:
        issues.append(
            _issue(
                "compiled-rule-schema",
                "compiled-rules/index.json",
                "schema_version must be {0}".format(_SCHEMA_VERSION),
            )
        )
    rules = payload.get("rules")
    if not isinstance(rules, list):
        issues.append(
            _issue(
                "compiled-rule-list",
                "compiled-rules/index.json",
                "compiled-rules index rules must be a list",
            )
        )
        rules = []
    seen_ids: set[str] = set()
    seen_bodies: set[str] = set()
    compiled_root = destination / "compiled-rules"
    for entry in rules:
        if not isinstance(entry, dict):
            issues.append(
                _issue(
                    "compiled-rule-list",
                    "compiled-rules/index.json",
                    "each compiled rule must be an object",
                )
            )
            continue
        rule_id = entry.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            issues.append(
                _issue(
                    "compiled-rule-duplicate-id",
                    "compiled-rules/index.json",
                    "compiled rule id must be a non-empty string",
                )
            )
        elif rule_id in seen_ids:
            issues.append(
                _issue(
                    "compiled-rule-duplicate-id",
                    "compiled-rules/index.json",
                    "compiled rule id is not unique: {0}".format(rule_id),
                )
            )
        else:
            seen_ids.add(rule_id)
        when = entry.get("when")
        if when not in _LEGAL_WHEN:
            issues.append(
                _issue(
                    "compiled-rule-when",
                    "compiled-rules/index.json",
                    "compiled rule when must be session_start or path_scope",
                )
            )
        globs = entry.get("globs")
        if not isinstance(globs, list) or any(
            not isinstance(item, str) for item in globs
        ):
            issues.append(
                _issue(
                    "compiled-rule-globs",
                    "compiled-rules/index.json",
                    "compiled rule globs must be a list of strings",
                )
            )
        body = entry.get("body")
        issues.extend(_body_path_issues(compiled_root, body))
        if isinstance(body, str) and body.strip():
            key = _normalized_body_key(body)
            if key and key in seen_bodies:
                issues.append(
                    _issue(
                        "compiled-rule-body-collision",
                        "compiled-rules/index.json",
                        "compiled rule body path is not unique: {0}".format(key),
                    )
                )
            elif key:
                seen_bodies.add(key)
    if target is Target.CODEX:
        issues.extend(_codex_foreign_host_semantics_issues(compiled_root, rules))
    if target is Target.CURSOR:
        return tuple(issues)
    loader = destination / "hooks" / "rule_loader.py"
    if not loader.is_file():
        issues.append(
            _issue(
                "missing-rule-loader",
                "hooks/rule_loader.py",
                "compiled rules require hooks/rule_loader.py",
            )
        )
    issues.extend(_hooks_command_issues(destination, target))
    issues.extend(_codex_default_hooks_undeclared_issues(destination, target))
    return tuple(issues)
