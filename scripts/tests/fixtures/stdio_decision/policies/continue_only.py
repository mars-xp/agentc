#!/usr/bin/env python3
import json
import sys

json.load(sys.stdin)
json.dump({"continue": True}, sys.stdout)
