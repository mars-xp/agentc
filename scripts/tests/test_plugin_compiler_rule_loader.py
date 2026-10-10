#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin_compiler.compiled_rules import (
    compiled_rule_entry,
    copy_rule_loader,
    write_compiled_rules,
    write_rule_hooks,
)
from plugin_compiler.model import Target
from plugin_compiler.rule_loader import extract_event_paths, handle_event, path_matches_glob
from plugin_compiler.rules import parse_rule_markdown


ALWAYS_SOURCE = """---
description: Always on helper
alwaysApply: true
---
# Body

Keep this.
"""

SCOPED_SOURCE = """---
description: Python files only
alwaysApply: false
globs: "**/*.py"
---
# Scoped
"""

SRC_PY_SOURCE = """---
description: Src python only
alwaysApply: false
globs: src/*.py
---
# SrcPy
"""


def _always_document():
    return parse_rule_markdown(ALWAYS_SOURCE, "rules/always.mdc")


def _scoped_document():
    return parse_rule_markdown(SCOPED_SOURCE, "rules/py.mdc")


def _src_py_document():
    return parse_rule_markdown(SRC_PY_SOURCE, "rules/src-py.mdc")


def _official_pre_tool(tool_name: str, tool_input: dict, cwd: str = "/workspace") -> dict:
    return {
        "session_id": "s1",
        "transcript_path": "/workspace/.transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def _inject_src_rule(event: dict) -> str:
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_src_py_document(),), destination)
        result = handle_event(
            event,
            destination / "compiled-rules",
            destination / "state",
        )
        return result["hookSpecificOutput"]["additionalContext"]


def _first_handler(hooks: dict, event_name: str) -> dict:
    return hooks["hooks"][event_name][0]["hooks"][0]


def _run_copied_loader(
    plugin_root: Path, event: dict, env: dict | None = None
) -> subprocess.CompletedProcess:
    loader = plugin_root / "hooks" / "rule_loader.py"
    return subprocess.run(
        [sys.executable, str(loader)],
        input=json.dumps(event).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def test_glob_matrix():
    # Relative POSIX paths (caller already made them relative to cwd).
    assert path_matches_glob("src/a.py", "src/*.py")
    assert not path_matches_glob("src/pkg/a.py", "src/*.py")
    assert path_matches_glob("src/a.py", "src/**/*.py")
    assert path_matches_glob("src/pkg/a.py", "src/**/*.py")
    assert not path_matches_glob("test/a.py", "src/**/*.py")
    assert not path_matches_glob("src/a.md", "**/*.py")
    # Explicit absolute path: matcher must not treat workspace-outside paths as matches.
    assert not path_matches_glob("/tmp/a.py", "**/*.py")


def test_compiled_rule_entry_session_start_and_path_scope():
    always = compiled_rule_entry(_always_document())
    scoped = compiled_rule_entry(_scoped_document())
    assert always == {
        "id": "rule:rules/always.mdc",
        "when": "session_start",
        "globs": [],
        "body": "bodies/rules/always.mdc.txt",
    }
    assert scoped == {
        "id": "rule:rules/py.mdc",
        "when": "path_scope",
        "globs": ["**/*.py"],
        "body": "bodies/rules/py.mdc.txt",
    }


def test_write_compiled_rules_index_and_bodies():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        index_path = write_compiled_rules(
            (_always_document(), _scoped_document()), destination
        )
        assert index_path == destination / "compiled-rules" / "index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["rules"][0]["id"] == "rule:rules/always.mdc"
        assert data["rules"][0]["when"] == "session_start"
        assert data["rules"][0]["globs"] == []
        assert data["rules"][0]["body"] == "bodies/rules/always.mdc.txt"
        assert data["rules"][1]["id"] == "rule:rules/py.mdc"
        assert data["rules"][1]["body"] == "bodies/rules/py.mdc.txt"
        body_file = destination / "compiled-rules" / data["rules"][0]["body"]
        assert body_file.read_text(encoding="utf-8").startswith("# Body")
        scoped_body = destination / "compiled-rules" / data["rules"][1]["body"]
        assert scoped_body.read_text(encoding="utf-8").startswith("# Scoped")


def test_session_start_injects_always_apply_body():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(), _scoped_document()), destination)
        result = handle_event(
            {"hook_event_name": "SessionStart", "session_id": "s1"},
            destination / "compiled-rules",
            destination / "state",
        )
        output = result["hookSpecificOutput"]
        assert output["hookEventName"] == "SessionStart"
        assert "# Body" in output["additionalContext"]
        assert "# Scoped" not in output["additionalContext"]


def test_path_scope_matches_py_not_md():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_scoped_document(),), destination)
        compiled = destination / "compiled-rules"
        py_result = handle_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "s1",
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.py"},
            },
            compiled,
            destination / "state-py",
        )
        md_result = handle_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "s2",
                "tool_name": "Write",
                "tool_input": {"file_path": "foo.md"},
            },
            compiled,
            destination / "state-md",
        )
        assert "# Scoped" in py_result["hookSpecificOutput"]["additionalContext"]
        assert py_result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert md_result["hookSpecificOutput"]["additionalContext"] == ""
        assert md_result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_extract_session_start_empty():
    event = {
        "session_id": "s1",
        "transcript_path": "/workspace/.transcript.jsonl",
        "cwd": "/workspace",
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    assert extract_event_paths(event, "/workspace") == ()


def test_apply_patch_update_file_matches_src_glob():
    injected = _inject_src_rule(
        _official_pre_tool(
            "apply_patch",
            {
                "command": (
                    "*** Begin Patch\n"
                    "*** Update File: src/a.py\n"
                    "@@\n"
                    "+print(1)\n"
                    "*** End of File\n"
                )
            },
        )
    )
    assert "# SrcPy" in injected


def test_extract_apply_patch_add_update_delete_headers():
    event = _official_pre_tool(
        "apply_patch",
        {
            "command": (
                "*** Begin Patch\n"
                "*** Add File: src/new.py\n"
                "+print(1)\n"
                "*** End of File\n"
                "*** Update File: src/a.py\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End of File\n"
                "*** Delete File: src/gone.py\n"
                "*** End Patch\n"
            )
        },
    )
    assert extract_event_paths(event, "/workspace") == (
        "src/new.py",
        "src/a.py",
        "src/gone.py",
    )


def test_apply_patch_move_to_extracts_source_and_destination():
    event = _official_pre_tool(
        "apply_patch",
        {
            "command": (
                "*** Begin Patch\n"
                "*** Update File: old.py\n"
                "*** Move to: src/new.py\n"
                "*** End Patch\n"
            )
        },
    )
    assert extract_event_paths(event, "/workspace") == ("old.py", "src/new.py")


def test_apply_patch_move_to_triggers_destination_glob():
    injected = _inject_src_rule(
        _official_pre_tool(
            "apply_patch",
            {
                "command": (
                    "*** Update File: old.py\n"
                    "*** Move to: src/new.py\n"
                )
            },
        )
    )
    assert "# SrcPy" in injected


def test_user_prompt_syntax_clear_path_injects():
    injected = _inject_src_rule(
        {
            "session_id": "s1",
            "transcript_path": "/workspace/.transcript.jsonl",
            "cwd": "/workspace",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Please review src/a.py and also `src/b.py`.",
        }
    )
    assert "# SrcPy" in injected


def test_user_prompt_natural_language_does_not_guess():
    event = {
        "session_id": "s1",
        "transcript_path": "/workspace/.transcript.jsonl",
        "cwd": "/workspace",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Please fix the python files in the project.",
    }
    assert extract_event_paths(event, "/workspace") == ()
    assert _inject_src_rule(event) == ""


def test_pretooluse_write_absolute_path_relativized_to_cwd():
    injected = _inject_src_rule(
        _official_pre_tool(
            "Write",
            {
                "file_path": "/workspace/src/a.py",
                "content": "print(1)\n",
            },
        )
    )
    assert "# SrcPy" in injected


def test_pretooluse_edit_file_path_param():
    event = _official_pre_tool(
        "Edit",
        {
            "file_path": "/workspace/src/a.py",
            "old_string": "a",
            "new_string": "b",
        },
    )
    assert extract_event_paths(event, "/workspace") == ("src/a.py",)


def test_path_outside_cwd_does_not_inject():
    event = _official_pre_tool(
        "Write",
        {"file_path": "/tmp/a.py", "content": "print(1)\n"},
        cwd="/workspace",
    )
    assert extract_event_paths(event, "/workspace") == ()
    assert _inject_src_rule(event) == ""


def test_bash_identifiable_file_arg_injects():
    injected = _inject_src_rule(
        _official_pre_tool("Bash", {"command": "cat src/a.py", "description": "show file"})
    )
    assert "# SrcPy" in injected


def test_bash_unreliable_command_does_not_inject():
    piped = _official_pre_tool("Bash", {"command": "python -c 'print(1)'"})
    listed = _official_pre_tool("Bash", {"command": "ls"})
    subst = _official_pre_tool("Bash", {"command": "cat $(find . -name '*.py')"})
    assert extract_event_paths(piped, "/workspace") == ()
    assert extract_event_paths(listed, "/workspace") == ()
    assert extract_event_paths(subst, "/workspace") == ()
    assert _inject_src_rule(piped) == ""
    assert _inject_src_rule(listed) == ""


def test_quoted_identifier_is_not_extracted():
    for token in ("alwaysApply", "permission", "PreToolUse", "allow"):
        event = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "请使用 `{0}`".format(token),
            "cwd": "/workspace",
        }
        assert extract_event_paths(event, "/workspace") == (), token


def test_quoted_dockerfile_is_extracted():
    event = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "请修改 `Dockerfile`",
        "cwd": "/workspace",
    }
    assert extract_event_paths(event, "/workspace") == ("Dockerfile",)


def test_quoted_root_readme_is_extracted():
    event = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "请修改 `README.md`",
        "cwd": "/workspace",
    }
    assert extract_event_paths(event, "/workspace") == ("README.md",)


def test_unquoted_readme_sentence_is_not_extracted():
    event = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "请修改 README.md",
        "cwd": "/workspace",
    }
    assert extract_event_paths(event, "/workspace") == ()


def test_bash_root_file_args_extracted():
    cwd = "/workspace"
    assert extract_event_paths(
        _official_pre_tool("Bash", {"command": "cat README.md"}), cwd
    ) == ("README.md",)
    assert extract_event_paths(
        _official_pre_tool("Bash", {"command": "git diff package.json"}), cwd
    ) == ("package.json",)
    assert extract_event_paths(
        _official_pre_tool("Bash", {"command": "touch Makefile"}), cwd
    ) == ("Makefile",)


def test_bash_curl_url_is_not_extracted():
    event = _official_pre_tool("Bash", {"command": "curl https://example.com/a"})
    assert extract_event_paths(event, "/workspace") == ()


def test_quoted_nested_path_still_extracted():
    event = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "请修改 `src/foo.py`",
        "cwd": "/workspace",
    }
    assert extract_event_paths(event, "/workspace") == ("src/foo.py",)


def test_at_and_quoted_root_filenames_are_extracted():
    at_event = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "请修改 `@README.md`",
        "cwd": "/workspace",
    }
    double_quoted = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": '请修改 "Makefile"',
        "cwd": "/workspace",
    }
    assert extract_event_paths(at_event, "/workspace") == ("README.md",)
    assert extract_event_paths(double_quoted, "/workspace") == ("Makefile",)


def test_bash_extensionless_non_makefile_is_not_extracted():
    event = _official_pre_tool("Bash", {"command": "touch Dockerfile"})
    assert extract_event_paths(event, "/workspace") == ()


def test_same_session_does_not_reinject():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        compiled = destination / "compiled-rules"
        state = destination / "state"
        event = {"hook_event_name": "SessionStart", "session_id": "s1"}
        first = handle_event(event, compiled, state)
        second = handle_event(event, compiled, state)
        assert "# Body" in first["hookSpecificOutput"]["additionalContext"]
        assert second["hookSpecificOutput"]["additionalContext"] == ""
        other = handle_event(
            {"hook_event_name": "SessionStart", "session_id": "s2"},
            compiled,
            state,
        )
        assert "# Body" in other["hookSpecificOutput"]["additionalContext"]


def test_no_match_emits_empty_additional_context():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_scoped_document(),), destination)
        result = handle_event(
            {"hook_event_name": "SessionStart", "session_id": "s1"},
            destination / "compiled-rules",
            destination / "state",
        )
        assert result["hookSpecificOutput"]["additionalContext"] == ""


def test_long_rule_body_is_injected_in_full():
    body = "# Long\n" + ("X" * 8000)
    source = (
        "---\n"
        "description: Long always rule\n"
        "alwaysApply: true\n"
        "---\n"
        + body
    )
    document = parse_rule_markdown(source, "rules/long.mdc")
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((document,), destination)
        result = handle_event(
            {"hook_event_name": "SessionStart", "session_id": "s1"},
            destination / "compiled-rules",
            destination / "state",
        )
        injected = result["hookSpecificOutput"]["additionalContext"]
        assert body in injected
        assert "X" * 8000 in injected


def test_missing_index_exits_nonzero():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        copy_rule_loader(destination)
        proc = _run_copied_loader(
            destination,
            {"hook_event_name": "SessionStart", "session_id": "s1"},
        )
        assert proc.returncode != 0
        assert proc.stderr.decode("utf-8").strip() != ""


def test_corrupt_index_exits_nonzero():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        copy_rule_loader(destination)
        index = destination / "compiled-rules" / "index.json"
        index.parent.mkdir(parents=True)
        index.write_text("{not-json", encoding="utf-8")
        proc = _run_copied_loader(
            destination,
            {"hook_event_name": "SessionStart", "session_id": "s1"},
        )
        assert proc.returncode != 0
        assert proc.stderr.decode("utf-8").strip() != ""


def test_copied_loader_reads_compiled_rules_relative_to_script():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        copy_rule_loader(destination)
        env = os.environ.copy()
        env["PLUGIN_DATA"] = str(destination / "plugin-data")
        proc = _run_copied_loader(
            destination,
            {"hook_event_name": "SessionStart", "session_id": "s1"},
            env=env,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8")
        payload = json.loads(proc.stdout.decode("utf-8"))
        assert "# Body" in payload["hookSpecificOutput"]["additionalContext"]
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_codex_hooks_json_has_limit_zero_and_plugin_root():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        path = write_rule_hooks(destination, Target.CODEX)
        assert path == destination / "hooks" / "hooks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for event_name in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
            assert event_name in data["hooks"]
        session = _first_handler(data, "SessionStart")
        prompt = _first_handler(data, "UserPromptSubmit")
        assert session["type"] == "command"
        assert "python3" in session["command"]
        assert "$PLUGIN_ROOT/hooks/rule_loader.py" in session["command"]
        assert "CLAUDE_PLUGIN_ROOT" not in session["command"]
        assert session["additionalContextLimit"] == 0
        assert prompt["additionalContextLimit"] == 0


def test_claude_hooks_json_uses_claude_plugin_root():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        path = write_rule_hooks(destination, Target.CLAUDE_CODE)
        data = json.loads(path.read_text(encoding="utf-8"))
        for event_name in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
            assert event_name in data["hooks"]
        session = _first_handler(data, "SessionStart")
        assert "python3" in session["command"]
        assert "$CLAUDE_PLUGIN_ROOT/hooks/rule_loader.py" in session["command"]
        stripped = session["command"].replace("$CLAUDE_PLUGIN_ROOT", "")
        assert "$PLUGIN_ROOT" not in stripped
        assert "additionalContextLimit" not in session


def _assert_json_files_stay_inside(root: Path) -> list[Path]:
    resolved_root = root.resolve()
    files = [path for path in root.rglob("*.json") if path.is_file()]
    assert files
    for path in files:
        path.resolve().relative_to(resolved_root)
        json.loads(path.read_text(encoding="utf-8"))
    return files


def test_session_id_dotdot_does_not_create_files_outside_state_dir():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        plugin_data = destination / "plugin-data"
        plugin_data.mkdir()
        handle_event(
            {"hook_event_name": "SessionStart", "session_id": "../escape"},
            destination / "compiled-rules",
            plugin_data,
        )
        assert not (destination / "escape.json").exists()
        assert not (destination / "escape").exists()
        leaked = [
            path
            for path in destination.rglob("*")
            if path.is_file() and "escape" in path.name
        ]
        assert leaked == []
        _assert_json_files_stay_inside(plugin_data)


def test_session_id_absolute_does_not_create_files_outside_state_dir():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        plugin_data = destination / "plugin-data"
        plugin_data.mkdir()
        leaked = destination / "leaked.json"
        handle_event(
            {
                "hook_event_name": "SessionStart",
                "session_id": str(destination / "leaked"),
            },
            destination / "compiled-rules",
            plugin_data,
        )
        assert not leaked.exists()
        _assert_json_files_stay_inside(plugin_data)


def test_legal_session_id_writes_readable_json_without_raw_filename():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        state = destination / "state"
        handle_event(
            {"hook_event_name": "SessionStart", "session_id": "s1"},
            destination / "compiled-rules",
            state,
        )
        assert not (state / "s1.json").exists()
        files = _assert_json_files_stay_inside(state)
        assert all(path.parent == state for path in files)
        assert all("/" not in path.name and ".." not in path.name for path in files)
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload.get("injected")


def test_state_dir_includes_plugin_identity_so_plugins_do_not_share_session_files():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        copy_rule_loader(destination)
        plugin_data = destination / "plugin-data"
        event = {"hook_event_name": "SessionStart", "session_id": "s1"}
        env_a = os.environ.copy()
        env_a["PLUGIN_DATA"] = str(plugin_data)
        env_a["PLUGIN_ROOT"] = str(destination / "plugin-a")
        env_b = os.environ.copy()
        env_b["PLUGIN_DATA"] = str(plugin_data)
        env_b["PLUGIN_ROOT"] = str(destination / "plugin-b")
        first = _run_copied_loader(destination, event, env=env_a)
        second = _run_copied_loader(destination, event, env=env_b)
        assert first.returncode == 0, first.stderr.decode("utf-8")
        assert second.returncode == 0, second.stderr.decode("utf-8")
        assert "# Body" in json.loads(first.stdout.decode("utf-8"))[
            "hookSpecificOutput"
        ]["additionalContext"]
        assert "# Body" in json.loads(second.stdout.decode("utf-8"))[
            "hookSpecificOutput"
        ]["additionalContext"]
        assert not (plugin_data / "s1.json").exists()
        identity_dirs = [path for path in plugin_data.iterdir() if path.is_dir()]
        assert len(identity_dirs) == 2
        for identity_dir in identity_dirs:
            identity_dir.resolve().relative_to(plugin_data.resolve())
            _assert_json_files_stay_inside(identity_dir)
        again = _run_copied_loader(destination, event, env=env_a)
        assert again.returncode == 0, again.stderr.decode("utf-8")
        assert (
            json.loads(again.stdout.decode("utf-8"))["hookSpecificOutput"][
                "additionalContext"
            ]
            == ""
        )


def test_runtime_body_parent_path_exits_nonzero_without_injecting():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        copy_rule_loader(destination)
        write_rule_hooks(destination, Target.CODEX)
        index_path = destination / "compiled-rules" / "index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["rules"][0]["body"] = "../hooks/hooks.json"
        index_path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        proc = _run_copied_loader(
            destination,
            {"hook_event_name": "SessionStart", "session_id": "s1"},
        )
        assert proc.returncode != 0
        stdout = proc.stdout.decode("utf-8")
        assert "# Body" not in stdout
        assert "Keep this" not in stdout


def test_runtime_absolute_body_exits_nonzero():
    with TemporaryDirectory() as tmp:
        destination = Path(tmp)
        write_compiled_rules((_always_document(),), destination)
        copy_rule_loader(destination)
        index_path = destination / "compiled-rules" / "index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["rules"][0]["body"] = "/etc/passwd"
        index_path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        proc = _run_copied_loader(
            destination,
            {"hook_event_name": "SessionStart", "session_id": "s1"},
        )
        assert proc.returncode != 0
        stdout = proc.stdout.decode("utf-8")
        assert "# Body" not in stdout
        assert "Keep this" not in stdout


def main() -> int:
    failures = []
    for key, value in sorted(globals().items()):
        if key.startswith("test_") and callable(value):
            try:
                value()
                print("PASS", key)
            except Exception as exc:
                failures.append((key, exc))
                print("FAIL", key, type(exc).__name__, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
