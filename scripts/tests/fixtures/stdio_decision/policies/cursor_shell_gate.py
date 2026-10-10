#!/usr/bin/env python3
import json
import sys

payload = json.load(sys.stdin)
if payload.get("tool_name") == "Shell":
    json.dump({"permission": "allow"}, sys.stdout)
else:
    json.dump(
        {
            "permission": "deny",
            "agent_message": "expected Shell, got {0!r}".format(
                payload.get("tool_name")
            ),
        },
        sys.stdout,
    )
