from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from plugin_compiler.model import Target

CONTRACTS_DIR = Path(__file__).resolve().parent

REQUIRED_JSON = (
    "manifest.json",
    "hook_events.json",
    "default_components.json",
    "plugin_root.json",
    "identity.json",
    "hook_handler_types.json",
    "path_fields.json",
    "skill_frontmatter.json",
    "marketplace.json",
    "known_gaps.json",
)

KNOWN_GAP_IDS = frozenset()

_CACHE: dict[str, Any] | None = None
_CACHE_DIR: Path | None = None


def _contracts_path(name: str) -> Path:
    return CONTRACTS_DIR / name


def _read_contract_json(name: str) -> Any:
    path = _contracts_path(name)
    if not path.is_file():
        raise FileNotFoundError(
            "missing contract file in contracts/: {0}".format(path.as_posix())
        )
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "invalid JSON in contracts/{0}: {1}".format(name, exc.msg)
        ) from exc


def load_contracts(*, force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_DIR
    if _CACHE is not None and not force and _CACHE_DIR == CONTRACTS_DIR:
        return _CACHE

    loaded: dict[str, Any] = {}
    for name in REQUIRED_JSON:
        loaded[name.removesuffix(".json")] = _read_contract_json(name)
    _CACHE = loaded
    _CACHE_DIR = CONTRACTS_DIR
    return loaded


def known_gap_ids() -> frozenset[str]:
    data = load_contracts()
    return frozenset(data["known_gaps"]["ids"])


def _hook_handler_matrix() -> dict[str, Any]:
    data = load_contracts().get("hook_handler_types")
    if not isinstance(data, dict):
        raise ValueError(
            "invalid hook_handler_types.json: expected object at root"
        )
    return data


def hook_group_fields(host: str) -> tuple[str, ...]:
    hosts = _hook_handler_matrix().get("hosts")
    if not isinstance(hosts, dict):
        return ()
    host_data = hosts.get(host)
    if not isinstance(host_data, dict):
        return ()
    fields = host_data.get("group_fields")
    if not isinstance(fields, list):
        return ()
    return tuple(str(item) for item in fields if isinstance(item, str) and item)


def hook_handler_types(host: str) -> tuple[str, ...]:
    hosts = _hook_handler_matrix().get("hosts")
    if not isinstance(hosts, dict):
        return ()
    host_data = hosts.get(host)
    if not isinstance(host_data, dict):
        return ()
    types = host_data.get("handler_types")
    if not isinstance(types, list):
        return ()
    return tuple(str(item) for item in types if isinstance(item, str) and item)


def hook_host_shape(host: str) -> str:
    hosts = _hook_handler_matrix().get("hosts")
    if not isinstance(hosts, dict):
        raise ValueError(
            "invalid hook_handler_types.json: missing hosts object"
        )
    host_data = hosts.get(host)
    if not isinstance(host_data, dict):
        raise ValueError(
            "invalid hook_handler_types.json: unknown host {0}".format(host)
        )
    shape = host_data.get("shape")
    if shape in ("flat", "nested"):
        return shape
    raise ValueError(
        "invalid hook_handler_types.json: bad shape for host {0}".format(host)
    )


def hook_cross_host_action(
    handler_type: str,
    *,
    from_host: str,
    to_host: str,
) -> str | None:
    if from_host == to_host:
        return "passthrough-fields"

    rows = _hook_handler_matrix().get("cross_host")
    if not isinstance(rows, list):
        return None

    best_specificity = -1
    best_action: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("handler_type") != handler_type:
            continue
        row_from = row.get("from_host")
        row_to = row.get("to_host")
        if row_from is not None and row_from != from_host:
            continue
        if row_to is not None and row_to != to_host:
            continue
        specificity = (1 if row_from is not None else 0) + (
            1 if row_to is not None else 0
        )
        if specificity < best_specificity:
            continue
        if specificity > best_specificity:
            best_specificity = specificity
            best_action = row.get("action")
            continue
        if row.get("action") != best_action:
            raise ValueError(
                "ambiguous cross_host rows for handler_type={0} "
                "from_host={1} to_host={2}".format(
                    handler_type, from_host, to_host
                )
            )

    if best_action == "block" or best_action is None:
        return None
    return str(best_action)


def mapped_hook_events(
    event_name: str, *, source_host: str | None, target: Target
) -> tuple[str, ...] | None:
    if source_host == target.value:
        return (event_name,)
    to_host = target.value
    rows = load_contracts()["hook_events"]["rows"]
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            row.get("from_host") != source_host
            or row.get("from_event") != event_name
            or row.get("to_host") != to_host
        ):
            continue
        if row.get("no-equivalent"):
            return None
        events = row.get("to_events")
        if isinstance(events, list) and events:
            return tuple(str(item) for item in events)
        return None
    return None


def hook_event_decision_stdio(
    event_name: str, *, source_host: str | None, target: Target
) -> bool:
    if source_host is None or source_host == target.value:
        return False
    rows = load_contracts()["hook_events"]["rows"]
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            row.get("from_host") != source_host
            or row.get("from_event") != event_name
            or row.get("to_host") != target.value
        ):
            continue
        if row.get("no-equivalent"):
            return False
        return bool(row.get("decision_stdio"))
    return False


def hook_event_not_equivalent(
    event_name: str, *, source_host: str | None, target: Target
) -> bool:
    if source_host is None or source_host == target.value:
        return False
    rows = load_contracts()["hook_events"]["rows"]
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            row.get("from_host") != source_host
            or row.get("from_event") != event_name
            or row.get("to_host") != target.value
        ):
            continue
        if row.get("no-equivalent"):
            return False
        return bool(row.get("event-not-equivalent"))
    return False


def compiled_rule_hook_events(target: Target) -> tuple[str, ...]:
    data = load_contracts().get("hook_events")
    if not isinstance(data, dict):
        return ()
    names = data.get("compiled_rule_events")
    if not isinstance(names, list):
        return ()
    events: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name:
            continue
        mapped = mapped_hook_events(name, source_host=target.value, target=target)
        if not mapped:
            continue
        for item in mapped:
            if item not in events:
                events.append(item)
    return tuple(events)


def default_component_entries(dialect: str) -> tuple[tuple[str, str], ...]:
    data = load_contracts()["default_components"]
    if not isinstance(data, dict):
        return ()
    entries = data.get(dialect)
    if not isinstance(entries, list):
        return ()
    result: list[tuple[str, str]] = []
    for item in entries:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        relative = str(item[0]).replace("\\", "/")
        kind = str(item[1])
        if not relative or not kind:
            continue
        result.append((relative, kind))
    return tuple(result)


_PLACEHOLDER_TOKEN = re.compile(r"\$\{[^}]+\}")
_UNBRACED_SHELL_VAR = re.compile(r"(?<![A-Za-z0-9_])\$([A-Za-z_][A-Za-z0-9_]*)")
_EMIT_PLUGIN_ROOT_TOKEN = {
    Target.CODEX: "${PLUGIN_ROOT}",
    Target.CURSOR: "${CURSOR_PLUGIN_ROOT}",
    Target.CLAUDE_CODE: "${CLAUDE_PLUGIN_ROOT}",
}


def _plugin_root_placeholders() -> list[dict[str, Any]]:
    data = load_contracts().get("plugin_root")
    if not isinstance(data, dict):
        return []
    placeholders = data.get("placeholders")
    if not isinstance(placeholders, list):
        return []
    return [item for item in placeholders if isinstance(item, dict)]


def _placeholder_shell_var(item: dict[str, Any]) -> str | None:
    shell = item.get("shell_var")
    if isinstance(shell, str) and shell.startswith("$") and not shell.startswith("${"):
        return shell
    token = item.get("token")
    if isinstance(token, str) and token.startswith("${") and token.endswith("}"):
        inner = token[2:-1]
        if inner:
            return "${0}".format(inner)
    return None


def _placeholder_hosts(item: dict[str, Any]) -> list[str]:
    hosts = item.get("hosts")
    if not isinstance(hosts, list):
        return []
    return [str(host) for host in hosts if isinstance(host, str) and host]


def plugin_root_token(target: Target) -> str | None:
    return _EMIT_PLUGIN_ROOT_TOKEN.get(target)


def _plugin_root_path_prefixes() -> tuple[str, ...]:
    prefixes: list[str] = []
    for item in _plugin_root_placeholders():
        token = item.get("token")
        if isinstance(token, str) and token.startswith("${") and token.endswith("}"):
            prefix = token if token.endswith("/") else token + "/"
            if prefix not in prefixes:
                prefixes.append(prefix)
    return tuple(prefixes)


def _starts_with_plugin_root(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in _plugin_root_path_prefixes())


def is_plugin_relative_token(token: str) -> bool:
    text = token.strip()
    if not text or text.startswith("-"):
        return False
    return (
        _starts_with_plugin_root(text)
        or text.startswith("./")
        or text.startswith("hooks/")
        or text.startswith("../")
    )


def needs_plugin_root_prefix(token: str) -> bool:
    text = token.strip()
    if not is_plugin_relative_token(text):
        return False
    return not _starts_with_plugin_root(text)


def has_unsupported_shell_syntax(command: str) -> bool:
    if "`" in command or "$(" in command:
        return True
    if "&&" in command or "||" in command:
        return True
    if "|" in command or ">" in command or "<" in command:
        return True
    return False


def _strip_leading_dot_slash(path: str) -> str:
    while path.startswith("./"):
        path = path[2:]
    return path


def contains_unprefixed_relative(text: str) -> bool:
    try:
        tokens = shlex.split(text, posix=True)
        return any(needs_plugin_root_prefix(token) for token in tokens)
    except ValueError:
        return "./" in text or "hooks/" in text or "../" in text


def _prefix_relative_token(token: str, target: Target) -> str | None:
    root = plugin_root_token(target)
    if root is None:
        return None
    return root + "/" + _strip_leading_dot_slash(token)


def plugin_root_shell_vars(target: Target) -> tuple[str, ...]:
    found: list[str] = []
    for item in _plugin_root_placeholders():
        if target.value not in _placeholder_hosts(item):
            continue
        shell = _placeholder_shell_var(item)
        if shell and shell not in found:
            found.append(shell)
    return tuple(found)


def plugin_root_shell_var(target: Target) -> str:
    """Unbraced `$VAR` for the target's official emit root."""
    token = plugin_root_token(target)
    if (
        not isinstance(token, str)
        or not token.startswith("${")
        or not token.endswith("}")
    ):
        raise ValueError(
            "rule hooks are not generated for target {0}".format(target.value)
        )
    inner = token[2:-1]
    if not inner:
        raise ValueError(
            "rule hooks are not generated for target {0}".format(target.value)
        )
    return "${0}".format(inner)


def rewrite_hook_plugin_root(command: str, target: Target) -> str | None:
    data = load_contracts()["plugin_root"]
    placeholders = data.get("placeholders")
    if not isinstance(placeholders, list):
        placeholders = []
    listed_tokens: list[tuple[str, object]] = []
    listed_shells: list[tuple[str, object]] = []
    for item in placeholders:
        if not isinstance(item, dict):
            continue
        token = item.get("token")
        hosts = item.get("hosts")
        if isinstance(token, str) and token:
            listed_tokens.append((token, hosts))
        shell = _placeholder_shell_var(item)
        if shell:
            listed_shells.append((shell, hosts))
    listed_token_set = {token for token, _hosts in listed_tokens}
    listed_shell_set = {shell for shell, _hosts in listed_shells}
    target_token = plugin_root_token(target)
    try:
        target_shell = plugin_root_shell_var(target)
    except ValueError:
        target_shell = None
    result = command
    for token in _PLACEHOLDER_TOKEN.findall(result):
        if token in listed_token_set:
            if target_token is None:
                return None
            result = result.replace(token, target_token)
        elif "PLUGIN_ROOT" in token:
            return None
    names = sorted(set(_UNBRACED_SHELL_VAR.findall(result)), key=len, reverse=True)
    for name in names:
        shell = "${0}".format(name)
        if shell in listed_shell_set:
            if target_shell is None:
                return None
            pattern = (
                r"(?<![A-Za-z0-9_])"
                + re.escape(shell)
                + r"(?![A-Za-z0-9_])"
            )
            result = re.sub(pattern, lambda _match: target_shell, result)
        elif "PLUGIN_ROOT" in name:
            return None

    if has_unsupported_shell_syntax(result) and contains_unprefixed_relative(result):
        return None
    if has_unsupported_shell_syntax(result) and not contains_unprefixed_relative(result):
        return result

    try:
        tokens = shlex.split(result, posix=True)
    except ValueError:
        if contains_unprefixed_relative(result):
            return None
        return result

    if needs_plugin_root_prefix(result.strip()):
        return _prefix_relative_token(result.strip(), target)

    if not any(needs_plugin_root_prefix(token) for token in tokens):
        return result

    rewritten: list[str] = []
    for token in tokens:
        if needs_plugin_root_prefix(token):
            prefixed = _prefix_relative_token(token, target)
            if prefixed is None:
                return None
            if any(ch.isspace() for ch in token):
                rewritten.append(shlex.quote(prefixed))
            else:
                rewritten.append(prefixed)
        else:
            rewritten.append(token)
    return " ".join(rewritten)


def _identity_flag_map(key: str) -> dict[str, bool]:
    data = load_contracts().get("identity")
    if not isinstance(data, dict):
        return {}
    mapping = data.get(key)
    if not isinstance(mapping, dict):
        return {}
    return {str(name): bool(value) for name, value in mapping.items()}


def source_requires_semver(dialect: str) -> bool:
    return _identity_flag_map("source_requires_semver").get(dialect, False)


def target_requires_semver(target: Target) -> bool:
    return _identity_flag_map("target_requires_semver").get(target.value, False)


def path_field_entries(host: str) -> tuple[tuple[str, str], ...]:
    data = load_contracts().get("path_fields")
    if not isinstance(data, dict):
        return ()
    fields = data.get("fields")
    if not isinstance(fields, list):
        return ()
    found: list[tuple[str, str]] = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        if item.get("host") != host:
            continue
        dotted = item.get("path")
        kind = item.get("value_kind")
        if isinstance(dotted, str) and dotted and isinstance(kind, str) and kind:
            found.append((dotted, kind))
    return tuple(found)


def path_array_dotted_paths() -> tuple[str, ...]:
    data = load_contracts().get("path_fields")
    if not isinstance(data, dict):
        return ()
    fields = data.get("fields")
    if not isinstance(fields, list):
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for item in fields:
        if not isinstance(item, dict):
            continue
        if item.get("value_kind") != "path-array":
            continue
        dotted = item.get("path")
        if not isinstance(dotted, str) or not dotted or dotted in seen:
            continue
        seen.add(dotted)
        found.append(dotted)
    return tuple(found)


def marketplace_contract() -> dict[str, Any]:
    data = load_contracts().get("marketplace")
    if not isinstance(data, dict):
        raise ValueError("invalid marketplace.json: expected object at root")
    return data


def marketplace_name_pattern() -> re.Pattern[str]:
    data = marketplace_contract()
    raw = data.get("name_pattern")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("invalid marketplace.json: missing name_pattern")
    return re.compile(raw)


def marketplace_interface_specs() -> dict[str, str]:
    data = marketplace_contract().get("interface")
    if not isinstance(data, dict):
        raise ValueError("invalid marketplace.json: interface must be an object")
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and key and value
    }


def marketplace_policy_specs() -> dict[str, str]:
    data = marketplace_contract().get("policy")
    if not isinstance(data, dict):
        raise ValueError("invalid marketplace.json: policy must be an object")
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and key and value
    }


def skill_frontmatter_fields(host: str) -> frozenset[str]:
    data = load_contracts().get("skill_frontmatter")
    if not isinstance(data, dict):
        return frozenset()
    hosts = data.get("hosts")
    if not isinstance(hosts, dict):
        return frozenset()
    host_data = hosts.get(host)
    if not isinstance(host_data, dict):
        return frozenset()
    fields = host_data.get("fields")
    if not isinstance(fields, list):
        return frozenset()
    return frozenset(
        item for item in fields if isinstance(item, str) and item
    )
