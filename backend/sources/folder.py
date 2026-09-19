"""
FolderSink — a decision is a file move. KaMeRa's original and default source.

Every method here is lifted verbatim out of backend/routers/decisions.py rather
than rewritten, so behaviour is byte-identical to the pre-seam code. The
comments explaining *why* each rule exists moved with it; they are load-bearing
(the undo path reconstruction in particular encodes four distinct cases that
`source_folder + filename` would get wrong).

This module is the ONLY importer of backend/file_mover in the repo. That is
half of the never-corrupt-a-Photos-library guarantee: PhotosSink cannot reach a
filesystem mutation because it has no path to one. The other half is
file_mover._assert_outside_photos_library, which refuses regardless of caller.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import HTTPException

from backend.database import get_bool_setting, get_folder_overrides
from backend.file_mover import (
    _KEEPS_REVIEWED_SUBFOLDER,
    move_photo,
    resolve_dest_folder,
    trash_photo,
)

logger = logging.getLogger(__name__)


def _reject_to_system_trash_enabled() -> bool:
    """Opt-in: route R through the OS Trash instead of a _Rejects subfolder.

    Off by default — the folder workflow is non-destructive, and a trashed
    photo cannot be undone in-app (the file is no longer addressable). When on,
    a reject goes through send2trash instead, and can be recovered from the
    Trash / Recycle Bin.
    """
    # Reads through get_bool_setting: settings are written as "1"/"0", and the
    # literal `== "true"` test that used to live here never matched, so this
    # toggle did nothing from the day it shipped (see database.get_bool_setting).
    return get_bool_setting("reject_to_system_trash", False)


class FolderSink:
    source_type = "folder"

    supports_bulk_trash = True
    supports_mark_reviewed = True
    supports_auto_cull = True
    supports_companion_mirror = True
    supports_watch = True
    supports_folder_overrides = True
    supports_path_reaper = True

    def preflight(self) -> None:
        """A folder on disk is always writable; failures surface per-operation."""

    # ------------------------------------------------------------------
    def apply(self, image_row: dict, decision: str, *, is_auto: bool) -> str:
        current_path = image_row["file_path"]
        source_folder = image_row.get("source_folder")

        if decision == "reject" and _reject_to_system_trash_enabled():
            try:
                return trash_photo(current_path)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Trash failed: {exc}")

        # Default subfolder lives next to the photo at *ingest time* — mirrors
        # the subfolder shape from recursive analysis (a photo in /root/Italy/
        # goes to /root/Italy/_Keeps/ not /root/_Keeps/) while staying correct
        # on re-decisions. Using `Path(current_path).parent` here would nest
        # _Keeps/_Maybes/ when K→M because the photo has already been moved
        # into _Keeps. `images.source_folder` is the durable ingest parent,
        # captured at analysis time, so it always points to the original
        # location regardless of intermediate moves.
        photo_parent = source_folder or str(Path(current_path).parent)
        overrides = get_folder_overrides(photo_parent)
        # A manual keep (is_auto=False) already IS the human review
        # Provenance's gate exists to guarantee — route it straight to
        # _Keeps/_Keeps_reviewed so it skips the extra "mark reviewed"
        # step. Auto-cull keeps (is_auto=True) land in plain _Keeps and
        # wait for a human to confirm via mark_reviewed(). See
        # docs/PLAN-keeps-reviewed.md.
        dest_folder = resolve_dest_folder(
            decision, photo_parent, overrides,
            reviewed=(decision == "keep" and not is_auto),
        )

        try:
            return move_photo(current_path, dest_folder)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"File move failed: {exc}")

    # ------------------------------------------------------------------
    def undo(self, image_row: dict, previous_path: str | None) -> str:
        current_path = image_row["file_path"]

        # Per-photo undo path: when the caller didn't supply a previous_path
        # (i.e. the undo isn't replaying a recent action, it's "send this
        # photo home"), move the file UP out of the K/M/R subfolder (and, for a
        # reviewed keep, the extra _Keeps_reviewed level too). This handles four
        # cases correctly:
        #   - Default subfolders: .../source/_Keeps/photo.JPG → .../source/photo.JPG
        #   - Subfolder analysis: .../source/sub/_Keeps/photo.JPG → .../source/sub/photo.JPG
        #   - Custom override:    /any/where/MyKeeps/photo.JPG  → /any/where/photo.JPG
        #   - Reviewed keep:      .../source/_Keeps/_Keeps_reviewed/photo.JPG → .../source/photo.JPG
        # Using `images.source_folder + filename` would flatten subfolder structure
        # and break custom-override undo. Filename comes from the live file (not the
        # DB) so any de-dup suffix added by `_claim_path` during the original move
        # comes along for the round trip.
        if previous_path is None:
            filename = Path(current_path).name
            parent = Path(current_path).parent
            # A reviewed keep sits two levels deep (_Keeps/_Keeps_reviewed/photo)
            # instead of one (_Keeps/photo) — strip the extra level first so
            # "home" lands outside _Keeps either way.
            if parent.name == _KEEPS_REVIEWED_SUBFOLDER:
                parent = parent.parent
            home = parent.parent
            previous_path = str(home / filename)

        # Already at home (e.g. someone manually moved the file back, or path
        # normalization quirks): skip the move and just clear DB state.
        already_home = (
            os.path.normpath(current_path).lower()
            == os.path.normpath(previous_path).lower()
        )
        if already_home:
            return current_path

        if not Path(current_path).exists():
            raise HTTPException(
                status_code=409,
                detail="File missing — cannot undo (may be in system Trash)",
            )
        prev_parent = Path(previous_path).parent
        if not prev_parent.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Source folder no longer exists: {prev_parent}",
            )
        try:
            return move_photo(current_path, prev_parent)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"File move failed: {exc}")

    # ------------------------------------------------------------------
    def mark_reviewed(self, image_row: dict) -> str:
        current_path = Path(image_row["file_path"])

        parts = current_path.parts
        if "_Keeps" not in parts:
            raise HTTPException(status_code=400, detail="Photo is not currently in _Keeps")
        if _KEEPS_REVIEWED_SUBFOLDER in parts:
            raise HTTPException(status_code=400, detail="Photo is already in _Keeps_reviewed")

        keeps_idx = parts.index("_Keeps")
        keeps_folder = Path(*parts[: keeps_idx + 1])
        dest_folder = keeps_folder / _KEEPS_REVIEWED_SUBFOLDER

        try:
            return move_photo(current_path, dest_folder)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"File move failed: {exc}")

    # ------------------------------------------------------------------
    def bulk_trash(self, image_row: dict) -> None:
        trash_photo(image_row["file_path"])
