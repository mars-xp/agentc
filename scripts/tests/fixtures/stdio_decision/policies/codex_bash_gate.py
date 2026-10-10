#!/usr/bin/env python3
import json
import sys

payload = json.load(sys.stdin)
if payload.get("tool_name") == "Bash":
    json.dump(
        {"hookSpecificOutput": {"permissionDecision": "allow"}},
        sys.stdout,
    )
else:
    json.dump(
        {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "expected Bash, got {0!r}".format(
                    payload.get("tool_name")
                ),
            }
        },
        sys.stdout,
    )
