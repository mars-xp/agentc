import os
import sys
from pathlib import Path

root = os.environ.get("PLUGIN_ROOT") or os.environ.get("CURSOR_PLUGIN_ROOT")
if not root:
    sys.exit(1)
path = Path(root) / "scripts" / "server.py"
if not path.is_file():
    sys.exit(2)
sys.exit(0)
