#!/usr/bin/env python3
import json
import sys

payload = json.load(sys.stdin)
command = payload.get("command")
if not isinstance(command, str):
    command = ""
if command == "rm -rf /":
    json.dump({"permission": "deny"}, sys.stdout)
elif command == "ls":
    json.dump(
        {"permission": "allow", "updated_input": {"command": "ls -la"}},
        sys.stdout,
    )
else:
    json.dump({"permission": "allow"}, sys.stdout)
