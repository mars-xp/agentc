from __future__ import annotations

from pathlib import Path

from plugin_compiler.model import Target
from plugin_compiler.paths import BuildLayout
from plugin_compiler.zip_tree import zip_plugin_tree
from plugin_targets.spi import ParsedPlugin


def codex_zip_path(name: str, version: str, out_dir: Path) -> Path:
    return Path(out_dir) / "{0}-{1}-codex.zip".format(name, version)


def pack_codex_zip(parsed: ParsedPlugin, staged: Path, layout: BuildLayout) -> Path:
    """Archive a published Codex tree into build/outputs/codex/ (helper zip)."""
    name = parsed.identity.name
    version = parsed.identity.version or "0.0.0"
    out_dir = layout.output(Target.CODEX.value, name).parent
    return zip_plugin_tree(staged, name, codex_zip_path(name, version, out_dir))
