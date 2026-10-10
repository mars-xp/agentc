#!/usr/bin/env python3
import json
import sys

json.load(sys.stdin)
json.dump(
    {"permission": "deny", "agent_message": "why"},
    sys.stdout,
)
