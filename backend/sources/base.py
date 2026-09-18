"""
The SourceSink seam: where a decision goes when it leaves the database.

KaMeRa was built on one assumption — a decision IS a file move. K/M/R relocate
the photo into _Keeps / _Maybes / _Rejects and `images.file_path` follows. That
assumption holds for a folder on disk and is false for every other source.

An Apple Photos library is the case that broke it. Its `originals/` files are
real files with real paths, so KaMeRa can read and score them in place — but
moving one corrupts the Photos catalogue irrecoverably. The decision has to be
expressed *without* touching the filesystem, as a keyword on the asset.

So the sink owns exactly one thing: **committing the decision to whatever
backing store the photo came from**. Everything else in `_apply_decision` — the
`_decision_move_lock`, the `images.file_path` update, the `decisions` /
`auto_decision_log` / `training_samples` writes, the companion mirror, the
retrain trigger — is source-agnostic and stays in `decisions.py`.

Dispatch is by `images.source_type`, a real column, never by parsing a path.

## The capability flags

Not every source can do everything KaMeRa's folder workflow does. Photos cannot
delete a photo at all (neither AppleScript nor — from this process — PhotoKit),
has no notion of a `_Keeps_reviewed` subfolder, and must not be swept by
sync-folder's `Path.exists()` reaper. Rather than scatter `if source_type ==`
through the routers, each sink declares what it supports and the routers ask.

A flag is a capability statement, not a safety mechanism. The safety mechanism
is `file_mover._assert_outside_photos_library`, which refuses at the one place
in the process that can actually move or trash a file.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SourceSink(Protocol):
    """Commits decisions for one kind of photo source."""

    #: Matches `images.source_type`.
    source_type: str

    # --- capabilities -----------------------------------------------------
    #: `POST /trash-rejects` may send this source's files to the system Trash.
    supports_bulk_trash: bool
    #: The `_Keeps` → `_Keeps_reviewed` promotion applies.
    supports_mark_reviewed: bool
    #: Auto-cull may decide photos from this source.
    supports_auto_cull: bool
    #: A RAW/processed pair here is two files that must move together.
    supports_companion_mirror: bool
    #: The folder watcher can watch this source.
    supports_watch: bool
    #: Per-tab destination folders are meaningful.
    supports_folder_overrides: bool
    #: `POST /sync-folder` may drop rows whose file_path no longer exists.
    supports_path_reaper: bool

    def preflight(self) -> None:
        """Raise HTTPException(409) if the store is not writable right now.

        Called before any decision is committed. A folder is always writable;
        a Photos library is only writable while Photos.app has *that* library
        open.
        """

    def apply(self, image_row: dict, decision: str, *, is_auto: bool) -> str:
        """Commit `decision` for this photo. Returns its new `file_path`.

        Sinks that do not move files return the path unchanged — the caller
        writes it back either way, so the DB and disk always agree.
        """

    def undo(self, image_row: dict, previous_path: str | None) -> str:
        """Reverse the committed decision. Returns the new `file_path`."""

    def mark_reviewed(self, image_row: dict) -> str:
        """Promote an auto-cull keep to human-reviewed. Returns new `file_path`."""

    def bulk_trash(self, image_row: dict) -> None:
        """Send this photo to the system Trash (bulk reject cleanup)."""
