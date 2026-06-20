"""
Folder watcher for KaMeRa.

Uses watchdog to monitor a directory for new photo files.
When a file appears (or is moved in from another folder), it queues
the file for analysis and stores the result in SQLite.

Usage (called from main.py at startup):
    watcher = FolderWatcher(db_path)
    watcher.start("~/Pictures/RAW temporary Folder")
    # ... later ...
    watcher.stop()
"""

import threading
import time
import logging
import uuid as _uuid
from pathlib import Path
from typing import Callable, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.database import get_db, write_shooting_log
from phase1_technical.quality_analyzer import analyze_photo_quality
from phase1_technical.exif_parser import extract_exif

log = logging.getLogger(__name__)

# Called by the watcher when a file fails to analyze. Receives a dict shaped
# like {"file", "error", "step", "ts"} so callers can route it into shared
# error logs (e.g. /debug/last-errors).
ErrorCallback = Callable[[dict], None]

from backend.constants import SUPPORTED_EXTENSIONS, DECISION_SUBFOLDERS  # noqa: E402 — re-export for callers that imported from here historically
# .hif is intentionally excluded — Fuji writes it next to every .RAF in RAW+HIF
# mode. We treat HIF as a sidecar of the RAF (move-only, never analyzed).

# Delay before analyzing a newly detected file.
# Cameras and card readers write files in chunks — analyzing mid-write gives corrupt results.
# 2 seconds is enough for most JPEG transfers; RAW files may need more on slow readers.
SETTLE_SECONDS = 2.0


def _analyze_and_store(file_path: Path, db_path: Path,
                       on_error: Optional[ErrorCallback] = None) -> None:
    """Analyze one file and upsert it into the images table."""
    from datetime import datetime
    path_str = str(file_path)
    try:
        result    = analyze_photo_quality(path_str)
        exif      = extract_exif(path_str)
        sharpness = result["sharpness"]
        exposure  = result["exposure"]

        with get_db(db_path) as conn:
            inserted = conn.execute(
                """
                INSERT INTO images (
                    file_path, filename, format, source_folder, uuid,
                    camera, shot_at, focal_length_mm, aperture, shutter_speed, iso,
                    sharpness_score, exposure_score, overall_score,
                    analysis_status, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', DATETIME('now'))
                ON CONFLICT(file_path) DO UPDATE SET
                    uuid            = COALESCE(images.uuid, excluded.uuid),
                    camera          = excluded.camera,
                    shot_at         = excluded.shot_at,
                    focal_length_mm = excluded.focal_length_mm,
                    aperture        = excluded.aperture,
                    shutter_speed   = excluded.shutter_speed,
                    iso             = excluded.iso,
                    sharpness_score = excluded.sharpness_score,
                    exposure_score  = excluded.exposure_score,
                    overall_score   = excluded.overall_score,
                    analysis_status = 'done'
                RETURNING uuid
                """,
                (
                    path_str, file_path.name,
                    file_path.suffix.lstrip(".").upper(),
                    str(file_path.parent),
                    str(_uuid.uuid4()),
                    exif["camera"], exif["shot_at"],
                    exif["focal_length_mm"], exif["aperture"],
                    exif["shutter_speed"], exif["iso"],
                    sharpness["sharpness_score"],
                    exposure["exposure_score"],
                    result["overall_quality_score"],
                ),
            ).fetchone()
            sample_uuid = inserted["uuid"] if inserted is not None else None
            write_shooting_log(
                conn,
                sample_uuid=sample_uuid,
                shot_at=exif["shot_at"],
                camera=exif["camera"],
                lens_model=exif.get("lens_model"),
                film_simulation=exif.get("film_simulation"),
                format=file_path.suffix.lstrip(".").upper(),
                focal_length_mm=exif["focal_length_mm"],
                aperture=exif["aperture"],
                shutter_speed=exif["shutter_speed"],
                iso=exif["iso"],
                overall_score=result["overall_quality_score"],
            )
        log.info("Watcher analyzed: %s (score %.1f)", file_path.name, result["overall_quality_score"])

    except Exception as e:
        log.exception("Watcher failed to analyze: %s", file_path.name)
        # Mark as error so the UI doesn't show it as pending forever
        try:
            with get_db(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO images (file_path, filename, format, analysis_status, imported_at)
                    VALUES (?, ?, ?, 'error', DATETIME('now'))
                    ON CONFLICT(file_path) DO UPDATE SET analysis_status = 'error'
                    """,
                    (path_str, file_path.name, file_path.suffix.lstrip(".").upper()),
                )
        except Exception:
            pass
        # Surface the failure via the optional callback so the UI can toast it.
        if on_error is not None:
            try:
                on_error({"file": file_path.name, "error": str(e),
                          "step": "watcher", "ts": datetime.now().isoformat()})
            except Exception:
                log.exception("Watcher error callback raised")


class _PhotoEventHandler(FileSystemEventHandler):
    """Called by watchdog on file system events."""

    def __init__(self, db_path: Path, on_error: Optional[ErrorCallback] = None,
                 owner: Optional["FolderWatcher"] = None):
        super().__init__()
        self._db_path = db_path
        self._on_error = on_error
        # Back-reference so analyze threads can report in-flight / analyzed
        # counts to the owning FolderWatcher (drives GET /watch-progress).
        self._owner = owner

    def _handle(self, path_str: str) -> None:
        path = Path(path_str)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        # Skip files that already live in a decision subfolder (_Keeps/_Maybes/
        # _Trash). A keep/maybe/reject MOVE into _Keeps fires on_moved with that
        # destination; re-analyzing it is both pointless (already decided) and
        # racy — an external archiver (Provenance) may move the keeper out of
        # _Keeps before our 2s settle elapses, making cv2.imread fail and toast
        # a spurious "Could not load image". Dropping it here, before any thread
        # spawns, fixes that at the source and keeps the counters honest.
        if any(part in DECISION_SUBFOLDERS for part in path.parts):
            return
        # Run analysis in a daemon thread so we don't block the watchdog event loop
        threading.Thread(
            target=self._settle_then_analyze,
            args=(path,),
            daemon=True,
        ).start()

    def _settle_then_analyze(self, path: Path) -> None:
        """Wait for the file to finish writing before analyzing.

        In-flight is incremented at the very START (before the settle sleep) so a
        burst of on_created events (e.g. a Fuji frame's fast JPG followed by its
        slow RAF) keeps in_flight > 0 across the gap — the live "analyzing…"
        indicator must not clear between them. The finally always decrements, so
        a failing file can't strand the counter.
        """
        if self._owner is not None:
            self._owner._enter_inflight()
        analyzed = False
        try:
            time.sleep(SETTLE_SECONDS)
            if path.exists():
                _analyze_and_store(path, self._db_path, on_error=self._on_error)
                analyzed = True
        finally:
            if self._owner is not None:
                self._owner._exit_inflight(analyzed)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        # Fired when a file is moved into the watched folder from another location
        if not event.is_directory:
            self._handle(event.dest_path)

    def on_deleted(self, event):
        # Fired when a file is deleted from the watched folder. Drop its row so
        # the grid stays in sync. Non-recursive observer → only top-level
        # deletes fire, which is what we want (K/X/M moves into _Keeps/_Maybes/
        # _Trash subfolders are tracked separately as decisions, not deletes).
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        path_str = str(path)
        try:
            with get_db(self._db_path) as conn:
                conn.execute("DELETE FROM images WHERE file_path = ?", (path_str,))
            log.info("Watcher removed row for deleted file: %s", path.name)
        except Exception:
            log.exception("Watcher failed to remove row for: %s", path.name)


class FolderWatcher:
    """
    Manages a watchdog Observer that monitors one folder at a time.

    Thread-safe: start/stop can be called from the FastAPI main thread.
    """

    def __init__(self, db_path: Path, on_error: Optional[ErrorCallback] = None):
        self._db_path   = db_path
        self._on_error  = on_error
        self._observer  = None
        self._watch_path: str | None = None
        # Live-analysis counters for GET /watch-progress. _in_flight = analyze
        # threads currently settling/running; _analyzed = files this session has
        # finished. Guarded by _lock (touched from many daemon analyze threads).
        self._in_flight = 0
        self._analyzed  = 0
        self._lock = threading.Lock()

    @property
    def watching(self) -> str | None:
        """Currently watched folder path, or None."""
        return self._watch_path

    @property
    def in_flight(self) -> int:
        """Number of files currently being settled/analyzed by the watcher."""
        with self._lock:
            return self._in_flight

    @property
    def analyzed(self) -> int:
        """Files analyzed since the current watch session started."""
        with self._lock:
            return self._analyzed

    def _enter_inflight(self) -> None:
        with self._lock:
            self._in_flight += 1

    def _exit_inflight(self, analyzed: bool) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            if analyzed:
                self._analyzed += 1

    def start(self, folder_path: str) -> None:
        """Start watching a folder. Stops any existing watch first."""
        self.stop()
        path = Path(folder_path)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {folder_path}")

        # Fresh session → reset counters so /watch-progress reflects only this
        # watch's activity.
        with self._lock:
            self._in_flight = 0
            self._analyzed = 0

        handler = _PhotoEventHandler(self._db_path, on_error=self._on_error,
                                     owner=self)
        self._observer = Observer()
        self._observer.schedule(handler, str(path), recursive=False)
        self._observer.start()
        self._watch_path = str(path)
        log.info("Watcher started: %s", folder_path)

    def stop(self) -> None:
        """Stop watching. Safe to call even if not currently watching."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        self._watch_path = None
