#!/usr/bin/env python3
import json
import sys
import time

json.load(sys.stdin)
time.sleep(30)
json.dump({"permission": "allow"}, sys.stdout)
