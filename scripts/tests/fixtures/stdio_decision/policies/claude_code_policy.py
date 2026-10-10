#!/usr/bin/env python3
import json
import sys

payload = json.load(sys.stdin)
tool_input = payload.get("tool_input")
command = ""
if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
    command = tool_input["command"]
decision = "deny" if command == "rm -rf /" else "allow"
out = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
    }
}
if command == "ls":
    out["updatedInput"] = {"command": "ls -la"}
json.dump(out, sys.stdout)
