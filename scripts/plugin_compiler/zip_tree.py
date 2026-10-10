from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PureWindowsPath

from .paths import require_inside


def zip_plugin_tree(tree: Path, archive_root_name: str, dest_zip: Path) -> Path:
    """Zip a plugin tree so the archive root directory is *archive_root_name*."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(tree.rglob("*")):
            if not path.is_file():
                continue
            arc = Path(archive_root_name) / path.relative_to(tree)
            info = zipfile.ZipInfo(arc.as_posix())
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    return dest_zip


def zip_member_destination(staging: Path, member_name: str) -> Path:
    """Resolve a ZIP member to a path under *staging*, or raise ValueError."""
    posix_name = member_name.replace("\\", "/")
    if posix_name.startswith("/") or posix_name.startswith("\\"):
        raise ValueError("ZIP member must not be absolute: {0!r}".format(member_name))
    windows = PureWindowsPath(member_name.replace("/", "\\"))
    if windows.is_absolute() or windows.drive:
        raise ValueError(
            "ZIP member must not be drive-qualified: {0!r}".format(member_name)
        )
    parts = tuple(part for part in posix_name.split("/") if part and part != ".")
    if not parts:
        raise ValueError("ZIP member name is empty: {0!r}".format(member_name))
    if any(part == ".." for part in parts):
        raise ValueError("ZIP member must not contain '..': {0!r}".format(member_name))
    dest = staging.joinpath(*parts)
    require_inside(staging.resolve(), dest.resolve())
    return dest


def extract_zip_filtered(zf: zipfile.ZipFile, staging: Path) -> None:
    """Extract ZIP members into *staging* after rejecting path-escape names."""
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    resolved_staging = staging.resolve()
    for info in zf.infolist():
        dest = zip_member_destination(staging, info.filename)
        require_inside(resolved_staging, dest.resolve())
        posix_name = info.filename.replace("\\", "/")
        if info.is_dir() or posix_name.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
