"""
PhotosSink — a decision is a keyword on an Apple Photos asset.

## Why keywords and not albums

The obvious design is one album per decision. It is wrong, and measurably so:
AppleScript cannot remove a photo from an album at all, so photoscript emulates
removal by deleting the album and rebuilding it without the removed photos.
Measured on a 6-photo album: adding 6 took 0.23s, removing ONE took 1.27s —
roughly 0.21s per photo already in the album. A single undo on a 500-photo
Keeps album would take a minute and a half.

Keywords are O(1) in both directions (set 0.03s, clear 0.19s, independent of
how many photos carry them) and Photos builds Smart Albums from them, so the
album view the user wants still exists — Photos maintains it instead of KaMeRa.

## Why AppleScript and not PhotoKit

PhotoKit would be the right API and is unavailable to us:
PHPhotoLibrary.requestAuthorizationForAccessLevel(readWrite) returns `denied`
instantly, with no user prompt, because a bare Python binary carries no
NSPhotoLibraryUsageDescription. Getting it needs a signed .app bundle. Do not
re-attempt PHAssetChangeRequest without that.

## Reject cannot delete

Neither AppleScript nor (from this process) PhotoKit can delete an asset, so R
marks and nothing more. Emptying is the user's own step in Photos, via a Smart
Album on KaMeRa:Reject. supports_bulk_trash is False accordingly.

## The wrong-library problem

Asset UUIDs are per-library and AppleScript writes always land in whatever
library Photos.app currently has open. preflight() compares the tab's library
to the open one, but that check can go stale between the check and the write.
The real guarantee is structural: looking the asset up by UUID in the wrong
library returns nothing, so a mis-targeted write cannot happen — it becomes a
409. The write is then read back and verified before the caller is allowed to
record the decision.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from backend import photos_library
from backend.database import get_db

logger = logging.getLogger(__name__)

KEYWORD_PREFIX = "KaMeRa:"
DECISION_KEYWORDS: dict[str, str] = {
    "keep":   f"{KEYWORD_PREFIX}Keep",
    "maybe":  f"{KEYWORD_PREFIX}Maybe",
    "reject": f"{KEYWORD_PREFIX}Reject",
}
ALL_DECISION_KEYWORDS = frozenset(DECISION_KEYWORDS.values())


class PhotosSink:
    source_type = "photos"

    supports_bulk_trash = False        # Photos exposes no delete to us at all
    supports_mark_reviewed = False     # no _Keeps folder to promote out of
    supports_auto_cull = False         # the model is trained on camera photos
    supports_companion_mirror = False  # pairing is a folder concept
    supports_watch = False             # the watcher watches directories
    supports_folder_overrides = False  # nothing is ever moved
    supports_path_reaper = False       # file existence is the wrong predicate

    # ------------------------------------------------------------------
    def preflight(self) -> None:
        """Fail early and legibly when Photos has a different library open.

        Advisory only — the binding check is the UUID lookup in _photo_for().
        This exists so the user gets "open library X" instead of "not found".
        """
        if not photos_library.is_available():
            raise HTTPException(
                status_code=501,
                detail="Apple Photos support is unavailable on this system",
            )

    def _expected_library(self, image_row: dict) -> str | None:
        source_folder = image_row.get("source_folder")
        if not source_folder:
            return None
        with get_db() as conn:
            row = conn.execute(
                "SELECT library_path FROM photos_albums WHERE source_folder = ?",
                (source_folder,),
            ).fetchone()
        return row["library_path"] if row else None

    def _check_library(self, image_row: dict) -> None:
        expected = self._expected_library(image_row)
        current = photos_library.current_library_path()
        if expected and current and expected != current:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Photos has a different library open "
                    f"({_name_of(current)}). Open {_name_of(expected)} to make "
                    f"decisions on this tab."
                ),
            )

    # ------------------------------------------------------------------
    def _photo_for(self, image_row: dict):
        """Resolve the asset in the CURRENTLY OPEN library, or 409.

        UUIDs are per-library, so an empty result means the open library is not
        the one this photo came from. That is the structural guarantee against
        writing a keyword onto the wrong person's photo.
        """
        photos_uuid = image_row.get("photos_uuid")
        if not photos_uuid:
            raise HTTPException(
                status_code=500,
                detail=f"Image {image_row.get('id')} has no photos_uuid",
            )
        self._check_library(image_row)

        import photoscript

        matches = photoscript.PhotosLibrary().photos(uuid=[photos_uuid])
        photo = next(iter(matches), None) if matches else None
        if photo is None:
            raise HTTPException(
                status_code=409,
                detail="Photo not found in the open Photos library — it may have "
                       "been deleted, or Photos has a different library open",
            )
        return photo

    @staticmethod
    def _set_marker(photo, marker: str | None) -> None:
        """Replace KaMeRa's keyword with `marker` (or remove it entirely).

        The user's own keywords are read, preserved and written back. Assigning
        a bare list here would silently destroy them, which is why this is the
        one place that touches `keywords` and why the result is verified.
        """
        existing = list(photo.keywords or [])
        kept = [k for k in existing if k not in ALL_DECISION_KEYWORDS]
        photo.keywords = kept + [marker] if marker else kept

        after = set(photo.keywords or [])
        if marker and marker not in after:
            raise HTTPException(
                status_code=500,
                detail=f"Photos did not accept the {marker!r} keyword — decision not recorded",
            )
        if not marker and after & ALL_DECISION_KEYWORDS:
            raise HTTPException(
                status_code=500,
                detail="Photos did not clear the KaMeRa keyword — undo not recorded",
            )
        lost = set(kept) - after
        if lost:
            logger.error("Photos dropped user keywords %s while writing %s", lost, marker)

    # ------------------------------------------------------------------
    def apply(self, image_row: dict, decision: str, *, is_auto: bool) -> str:
        marker = DECISION_KEYWORDS.get(decision)
        if marker is None:
            raise HTTPException(status_code=422, detail=f"Unknown decision {decision!r}")
        self._set_marker(self._photo_for(image_row), marker)
        # The asset never moves — the caller writes this straight back, so the
        # DB and the library stay in agreement.
        return image_row["file_path"]

    def undo(self, image_row: dict, previous_path: str | None) -> str:
        self._set_marker(self._photo_for(image_row), None)
        return image_row["file_path"]

    def mark_reviewed(self, image_row: dict) -> str:  # pragma: no cover - gated upstream
        raise HTTPException(
            status_code=400,
            detail="Mark reviewed is not available for Photos albums",
        )

    def bulk_trash(self, image_row: dict) -> None:  # pragma: no cover - gated upstream
        raise HTTPException(
            status_code=400,
            detail="Photos cannot delete assets from KaMeRa — use a Smart Album "
                   "on KaMeRa:Reject and delete them in Photos",
        )


def _name_of(library_path: str) -> str:
    from pathlib import Path

    return Path(library_path).stem
