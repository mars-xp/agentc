#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path


def load_index(compiled_root: Path) -> dict:
    path = compiled_root / "index.json"
    if not path.is_file():
        raise RuntimeError("compiled-rules index missing: {0}".format(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "compiled-rules index corrupt: {0}: {1}".format(path, exc)
        ) from exc
    if not isinstance(data, dict) or "rules" not in data:
        raise RuntimeError("compiled-rules index corrupt: {0}".format(path))
    return data


def path_matches_glob(file_path: str, pattern: str) -> bool:
    posix = file_path.replace("\\", "/")
    if posix.startswith("/") or (len(posix) >= 2 and posix[1] == ":"):
        return False
    return _match_glob_parts(
        posix.split("/"),
        pattern.replace("\\", "/").split("/"),
    )


def _match_glob_parts(path_parts: list[str], pattern_parts: list[str]) -> bool:
    if not pattern_parts:
        return not path_parts
    head, *rest = pattern_parts
    if head == "**":
        while rest and rest[0] == "**":
            rest = rest[1:]
        if not rest:
            return True
        for index in range(len(path_parts) + 1):
            if _match_glob_parts(path_parts[index:], rest):
                return True
        return False
    if not path_parts:
        return False
    if fnmatch.fnmatch(path_parts[0], head):
        return _match_glob_parts(path_parts[1:], rest)
    return False


_APPLY_PATCH_FILE = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$",
    re.MULTILINE,
)
_QUOTED_CHUNK = re.compile(r"`([^`\n]+)`|'([^'\n]+)'|\"([^\"\n]+)\"")
_UNQUOTED_PATH = re.compile(
    r"(?:(?<=^)|(?<=[\s(@]))"
    r"((?:[A-Za-z]:)?(?:\./)?(?:[A-Za-z0-9_.-]+/)+\.?[A-Za-z0-9_.-]+)"
)
_BASH_SCRIPT_FLAGS = frozenset({"-c", "-e", "--command"})
_TOOL_PATH_KEYS = ("file_path", "notebook_path", "path")


def _is_absolute_posix(posix: str) -> bool:
    return posix.startswith("/") or (len(posix) >= 2 and posix[1] == ":")


def _normalize_relative(posix: str) -> str | None:
    parts: list[str] = []
    for part in posix.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None
    return "/".join(parts)


def _relativize_to_cwd(path: str, cwd: str) -> str | None:
    posix = path.strip().replace("\\", "/")
    if not posix:
        return None
    if _is_absolute_posix(posix):
        try:
            relative = Path(path).resolve().relative_to(Path(cwd).resolve())
        except (ValueError, OSError):
            base = cwd.replace("\\", "/").rstrip("/")
            prefix = base.lower() + "/"
            if posix.lower().startswith(prefix):
                return _normalize_relative(posix[len(base) + 1 :])
            return None
        as_posix = relative.as_posix()
        if as_posix == "." or as_posix.startswith("../"):
            return None
        return as_posix
    return _normalize_relative(posix)


_KNOWN_ROOT_FILES = frozenset(
    {
        "Makefile",
        "Dockerfile",
        "LICENSE",
        "CHANGELOG",
        "Gemfile",
        "Rakefile",
        "Procfile",
    }
)


def _is_syntax_path(text: str) -> bool:
    posix = text.strip().lstrip("@").replace("\\", "/")
    if not posix or "://" in posix:
        return False
    if "/" in posix:
        return True
    last = posix.rsplit("/", 1)[-1]
    if "." in last:
        return True
    return last in _KNOWN_ROOT_FILES


def _paths_from_prompt(prompt: str) -> tuple[str, ...]:
    found: list[str] = []
    masked = list(prompt)
    for match in _QUOTED_CHUNK.finditer(prompt):
        chunk = next(group for group in match.groups() if group is not None)
        if _is_syntax_path(chunk):
            found.append(chunk.strip().lstrip("@"))
        start, end = match.span()
        for index in range(start, end):
            masked[index] = " "
    for match in _UNQUOTED_PATH.finditer("".join(masked)):
        found.append(match.group(1).rstrip(".,;:!?").lstrip("@"))
    return tuple(found)


def _paths_from_apply_patch(command: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in _APPLY_PATCH_FILE.finditer(command))


def _bash_file_arg(token: str) -> bool:
    posix = token.replace("\\", "/")
    if not posix or posix.startswith("-") or "://" in posix:
        return False
    last = posix.rsplit("/", 1)[-1]
    if "." in last:
        return True
    return last == "Makefile"


def _paths_from_bash(command: str) -> tuple[str, ...]:
    if not command.strip():
        return ()
    if "`" in command or "$(" in command:
        return ()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ()
    skip_next = False
    found: list[str] = []
    for index, token in enumerate(tokens):
        if index == 0:
            continue
        if skip_next:
            skip_next = False
            continue
        if token in _BASH_SCRIPT_FLAGS:
            skip_next = True
            continue
        if token.startswith("-"):
            if "=" in token:
                value = token.split("=", 1)[1]
                if _bash_file_arg(value):
                    found.append(value)
            continue
        if _bash_file_arg(token):
            found.append(token)
    return tuple(found)


def _paths_from_tool_params(tool_input: dict) -> tuple[str, ...]:
    found: list[str] = []
    for key in _TOOL_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value)
    return tuple(found)


def _paths_from_pre_tool_use(event: dict) -> tuple[str, ...]:
    tool_name = str(event.get("tool_name") or "").lower()
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return ()
    if tool_name == "apply_patch":
        command = tool_input.get("command")
        if isinstance(command, str):
            return _paths_from_apply_patch(command)
        return ()
    if tool_name in ("bash", "shell"):
        command = tool_input.get("command")
        if isinstance(command, str):
            return _paths_from_bash(command)
        return ()
    return _paths_from_tool_params(tool_input)


def extract_event_paths(event: dict, cwd: str | None) -> tuple[str, ...]:
    event_name = event.get("hook_event_name") or ""
    if event_name == "SessionStart":
        return ()
    base = cwd if cwd else os.getcwd()
    if event_name == "UserPromptSubmit":
        prompt = event.get("prompt")
        raw = _paths_from_prompt(prompt) if isinstance(prompt, str) else ()
    elif event_name == "PreToolUse":
        raw = _paths_from_pre_tool_use(event)
    else:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        relative = _relativize_to_cwd(item, base)
        if relative and relative not in seen:
            seen.add(relative)
            out.append(relative)
    return tuple(out)


def match_rules(index: dict, event: dict) -> tuple[str, ...]:
    event_name = event.get("hook_event_name") or ""
    event_cwd = event.get("cwd")
    cwd = event_cwd if isinstance(event_cwd, str) and event_cwd else os.getcwd()
    paths = extract_event_paths(event, cwd)
    matched = []
    for rule in index.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("id")
        if not rule_id:
            continue
        when = rule.get("when")
        if event_name == "SessionStart" and when == "session_start":
            matched.append(rule_id)
            continue
        if event_name in ("UserPromptSubmit", "PreToolUse") and when == "path_scope":
            globs = rule.get("globs") or []
            if paths and any(
                path_matches_glob(file_path, item)
                for file_path in paths
                for item in globs
            ):
                matched.append(rule_id)
    return tuple(matched)


def _require_inside(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    resolved_candidate.relative_to(resolved_root)
    return resolved_candidate


def _read_compiled_body(compiled_root: Path, body_rel: object) -> str:
    if not isinstance(body_rel, str) or not body_rel.strip():
        raise RuntimeError("compiled rule body must be a non-empty relative path")
    posix = body_rel.replace("\\", "/")
    if _is_absolute_posix(posix):
        raise RuntimeError(
            "absolute compiled rule body path is not allowed: {0}".format(body_rel)
        )
    parts: list[str] = []
    for part in posix.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise RuntimeError(
                "parent path segments are not allowed in compiled rule body: {0}".format(
                    body_rel
                )
            )
        parts.append(part)
    if not parts:
        raise RuntimeError(
            "compiled rule body must name a file inside compiled-rules/: {0}".format(
                body_rel
            )
        )
    try:
        body_path = _require_inside(compiled_root, compiled_root.joinpath(*parts))
    except ValueError as exc:
        raise RuntimeError(
            "compiled rule body resolves outside compiled-rules/: {0}".format(body_rel)
        ) from exc
    if not body_path.is_file() or body_path.is_dir():
        raise RuntimeError(
            "compiled rule body is not a regular file inside compiled-rules/: {0}".format(
                body_rel
            )
        )
    try:
        return body_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(
            "compiled rule body is not readable UTF-8: {0}: {1}".format(body_rel, exc)
        ) from exc


def _session_file_name(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return "{0}.json".format(digest)


def _state_path(state_dir: Path, session_id: str) -> Path:
    candidate = state_dir / _session_file_name(session_id)
    return _require_inside(state_dir, candidate)


def _plugin_identity() -> str:
    root = os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        root = str(Path(__file__).resolve().parent.parent)
    return hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()


def _read_injected(state_dir: Path, session_id: str) -> list:
    path = _state_path(state_dir, session_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    injected = data.get("injected") if isinstance(data, dict) else None
    if not isinstance(injected, list):
        return []
    return [str(item) for item in injected]


def _write_injected(state_dir: Path, session_id: str, injected: list) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"injected": injected}, ensure_ascii=False) + "\n"
    path = _state_path(state_dir, session_id)
    handle, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=str(state_dir))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def handle_event(event: dict, compiled_root: Path, state_dir: Path) -> dict:
    event_name = event.get("hook_event_name") or ""
    session_id = str(event.get("session_id") or "default")
    index = load_index(compiled_root)
    already = _read_injected(state_dir, session_id)
    already_set = set(already)
    id_to_rule = {}
    for rule in index.get("rules") or []:
        if isinstance(rule, dict) and rule.get("id"):
            id_to_rule[rule["id"]] = rule
    bodies = []
    newly = []
    for rule_id in match_rules(index, event):
        if rule_id in already_set:
            continue
        rule = id_to_rule.get(rule_id)
        if not rule:
            continue
        bodies.append(_read_compiled_body(compiled_root, rule.get("body")))
        newly.append(rule_id)
    if newly:
        _write_injected(state_dir, session_id, already + newly)
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": "\n\n".join(bodies),
        }
    }


def _default_state_dir() -> Path:
    identity = _plugin_identity()
    env = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if env:
        root = Path(env)
        return _require_inside(root, root / identity)
    return Path(tempfile.gettempdir()) / "plugin-compiler-rule-loader" / identity


def main() -> int:
    compiled_root = Path(__file__).resolve().parent.parent / "compiled-rules"
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write("invalid hook event JSON: {0}\n".format(exc))
        return 1
    if not isinstance(event, dict):
        sys.stderr.write("invalid hook event JSON: expected object\n")
        return 1
    try:
        output = handle_event(event, compiled_root, _default_state_dir())
    except (OSError, RuntimeError) as exc:
        sys.stderr.write("{0}\n".format(exc))
        return 1
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
