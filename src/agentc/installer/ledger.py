"""Install-state ledger outside the plugin destination tree."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

INSTALL_HOSTS = ("cursor", "codex", "claude-code")


def ledger_path(install_root: Path, host: str, name: str) -> Path:
    return install_root / ".agentc" / "installs" / host / f"{name}.json"


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_tree(tree: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        files[path.relative_to(tree).as_posix()] = hash_file(path)
    return files


def write_ledger(
    *,
    install_root: Path,
    host: str,
    name: str,
    dest: Path,
    files: dict[str, str],
) -> Path:
    from plugin_compiler.paths import require_inside

    path = ledger_path(install_root, host, name)
    require_inside(install_root.resolve(), path.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": host,
        "name": name,
        "dest": dest.as_posix(),
        "files": files,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def read_ledger(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def drifted_files(dest: Path, files: dict[str, str]) -> tuple[str, ...]:
    drifted: list[str] = []
    for rel, expected in files.items():
        current = dest / rel
        if not current.is_file() or hash_file(current) != expected:
            drifted.append(rel)
    return tuple(drifted)
