"""
File move operations for post-decision culling.

All public functions are pure filesystem operations:
  - Never touch DB — callers handle that.
  - Never modify RAW file contents — only shutil.move().
  - Camera-baked HIF previews (Fuji X-series) follow the primary file
    automatically.
"""

import logging
import os
import shutil
from pathlib import Path

from send2trash import send2trash

logger = logging.getLogger(__name__)

_DECISION_FOLDERS: dict[str, str] = {
    "keep":   "_Keeps",
    "maybe":  "_Maybes",
    "reject": "_Trash",
}

_DECISION_SETTING_KEY: dict[str, str] = {
    "keep":   "keeps_folder",
    "maybe":  "maybes_folder",
    "reject": "trash_folder",
}


def resolve_dest_folder(
    decision: str,
    source_folder: str | Path,
    overrides: dict[str, str] | None = None,
) -> Path:
    """
    Return the absolute destination folder for a decision.

    overrides is a dict with optional keys: keeps_folder, maybes_folder, trash_folder.
    If the key for this decision is present and non-empty, use it as an absolute path.
    Otherwise create the default subfolder inside source_folder.
    """
    key = _DECISION_SETTING_KEY[decision]
    if overrides and overrides.get(key):
        return Path(overrides[key])
    return Path(source_folder) / _DECISION_FOLDERS[decision]


_SIDECAR_EXTS: tuple[str, ...] = (".hif", ".HIF")


def move_photo(current_path: str | Path, dest_folder: str | Path) -> str:
    """
    Move a RAW file and any sidecar siblings to dest_folder.

    Sidecars moved alongside the primary:
      - .hif / .HIF — Fuji camera-baked HEIF preview written next to the RAF

    Returns the new absolute path of the primary file.
    No-op (returns current_path unchanged) if the file is already in dest_folder.
    Raises FileNotFoundError if the source file does not exist.
    Raises OSError for any filesystem error (permissions, disk full, etc.).

    Refuses to move symlinks. A symlinked photo in a watched folder could
    silently mutate / trash the real RAW at the symlink target — for an app
    whose only data-loss invariant is "never modify RAW files" this is not
    a risk worth running.
    """
    src = Path(current_path)
    dst_dir = Path(dest_folder)

    if src.is_symlink():
        raise OSError(f"Refusing to move symlink: {src}")
    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")

    dst_dir.mkdir(parents=True, exist_ok=True)

    if src.parent.resolve() == dst_dir.resolve():
        logger.debug("move_photo: already at destination, skipping: %s", src)
        return str(src)

    dst_path = _claim_path(dst_dir, src.name)
    shutil.move(str(src), str(dst_path))
    logger.info("Moved %s → %s", src.name, dst_path)

    # Move HIF companion preview if present. Case-insensitive sibling match
    # because APFS is case-insensitive by default; we preserve the *real*
    # on-disk casing in the destination.
    src_stem_lower = src.stem.lower()
    for sibling in src.parent.iterdir():
        if not sibling.is_file() or sibling.is_symlink() or sibling == src:
            continue
        if sibling.stem.lower() != src_stem_lower:
            continue
        if sibling.suffix.lower() != ".hif":
            continue
        sidecar_dst = _claim_path(dst_dir, dst_path.stem + sibling.suffix)
        shutil.move(str(sibling), str(sidecar_dst))
        logger.info("Moved sidecar %s → %s", sibling.name, sidecar_dst)

    return str(dst_path)


def trash_photo(current_path: str | Path) -> str:
    """
    Send a RAW file and any sidecar siblings to the system Trash.

    Behaves like move_photo() but the destination is the OS trash bin —
    files can still be restored from Trash but disappear from the source
    folder immediately. HIF sidecars follow the primary, matching the same
    case-insensitive sibling logic move_photo() uses.

    Returns the original path (so callers can still log "rejected" with the
    original location). The DB row's `file_path` should be cleared by the
    caller since the file is no longer addressable in the working tree.
    """
    src = Path(current_path)
    if src.is_symlink():
        raise OSError(f"Refusing to trash symlink: {src}")
    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")

    sidecars: list[Path] = []
    src_stem_lower = src.stem.lower()
    for sibling in src.parent.iterdir():
        if not sibling.is_file() or sibling.is_symlink() or sibling == src:
            continue
        if sibling.stem.lower() != src_stem_lower:
            continue
        if sibling.suffix.lower() == ".hif":
            sidecars.append(sibling)

    # Trash the primary first; if it succeeds, follow with sidecars. send2trash
    # is per-file (no atomic group), so a sidecar failure leaves an orphan but
    # never the other way around (which would lose the photo).
    send2trash(str(src))
    logger.info("Trashed %s (system trash)", src)
    for sidecar in sidecars:
        try:
            send2trash(str(sidecar))
            logger.info("Trashed sidecar %s", sidecar.name)
        except OSError as e:
            logger.warning("Failed to trash sidecar %s: %s", sidecar.name, e)

    return str(src)


def _claim_path(directory: Path, filename: str) -> Path:
    """
    Atomically claim a non-existing path in `directory` for `filename`.

    Uses O_CREAT | O_EXCL to create an empty placeholder file the moment we
    pick a name, so a parallel decision worker (or the watchdog) can't slip
    in between `exists()` and `shutil.move()` and cause silent overwrite of
    an irreplaceable RAW. The placeholder is closed immediately; shutil.move
    will overwrite it with the real file.

    Appends _1, _2, … before the extension on collision.
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix

    def _try(name: str) -> Path | None:
        candidate = directory / name
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return None
        os.close(fd)
        return candidate

    won = _try(filename)
    if won is not None:
        return won
    i = 1
    while True:
        won = _try(f"{stem}_{i}{suffix}")
        if won is not None:
            return won
        i += 1
