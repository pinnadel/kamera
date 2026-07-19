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

import queue
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
        # If the file vanished while we were analyzing it, that's an expected
        # race — an external mover (Provenance's import staging / archive, or a
        # K/M/R decision) moved or renamed it out from under us between the
        # settle check and now. Don't mark it 'error' or toast a "[Errno 2] No
        # such file or directory" — just drop it silently. (FileNotFoundError
        # covers the direct case; the broad not-exists check also catches a
        # FileNotFoundError surfaced as a generic Exception by a scorer.)
        if isinstance(e, FileNotFoundError) or not file_path.exists():
            log.info("Watcher skipped vanished file (moved/renamed): %s",
                     file_path.name)
            return
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

    def __init__(self, db_path: Path, owner: "FolderWatcher"):
        super().__init__()
        self._db_path = db_path
        # Owning FolderWatcher — detected files go onto its analysis queue,
        # which one worker thread drains sequentially (drives /watch-progress).
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
        # a spurious "Could not load image". Dropping it here, before anything
        # is queued, fixes that at the source and keeps the counters honest.
        if any(part in DECISION_SUBFOLDERS for part in path.parts):
            return
        self._owner._enqueue(path)

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

    Detected files are queued and analyzed by ONE worker thread, sequentially.
    The original thread-per-file design melted down on bulk drops: copying a
    full card into the watched folder spawned one rawpy decode thread per file
    (2026-07-19 incident: 263 files → 334 threads, 13 GB RSS, API wedged).
    Sequential matches the batch loop — parallelism lives inside a photo's
    scorer fan, never across photos.

    Thread-safe: start/stop can be called from the FastAPI main thread.
    """

    def __init__(self, db_path: Path, on_error: Optional[ErrorCallback] = None):
        self._db_path   = db_path
        self._on_error  = on_error
        self._observer  = None
        self._watch_path: str | None = None
        # Analysis pipeline: watchdog event threads enqueue, one worker drains.
        # _pending dedupes paths already queued (one copy can fire several
        # events for the same file). start() creates a fresh queue + worker per
        # session; stop() abandons the backlog via _worker_stop + sentinel.
        self._queue: queue.Queue = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._worker_stop = threading.Event()
        self._pending: set[str] = set()
        # Live-analysis counters for GET /watch-progress. _in_flight = files
        # queued or currently analyzing; _analyzed = files this session has
        # finished. Guarded by _lock (touched from watchdog + worker threads).
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

    def _enqueue(self, path: Path) -> None:
        """Queue a detected file for the worker. Dedupes repeat events.

        In-flight is bumped at enqueue time so a burst of on_created events
        (e.g. a Fuji frame's fast JPG followed by its slow RAF) keeps
        in_flight > 0 across the gap — the live "analyzing…" indicator must
        not clear between them.
        """
        key = str(path)
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
            self._in_flight += 1
        self._queue.put((path, time.monotonic()))

    def _finish(self, path: Path, analyzed: bool) -> None:
        with self._lock:
            self._pending.discard(str(path))
            self._in_flight = max(0, self._in_flight - 1)
            if analyzed:
                self._analyzed += 1

    def _worker_loop(self, q: queue.Queue, stop_evt: threading.Event) -> None:
        """Drain the queue one file at a time until stopped.

        SETTLE_SECONDS is measured from the file's event, not from dequeue —
        under a backlog the settle window has long elapsed by the time a file
        reaches the front, so the worker proceeds without sleeping.
        """
        while True:
            item = q.get()
            if item is None or stop_evt.is_set():
                return
            path, seen_at = item
            analyzed = False
            try:
                remaining = SETTLE_SECONDS - (time.monotonic() - seen_at)
                if remaining > 0:
                    time.sleep(remaining)
                if stop_evt.is_set():
                    return
                if path.exists():
                    _analyze_and_store(path, self._db_path, on_error=self._on_error)
                    analyzed = True
            except Exception:
                # _analyze_and_store handles its own errors; this guards the
                # loop itself so one bad file can't kill the whole session.
                log.exception("Watcher worker error on: %s", path.name)
            finally:
                self._finish(path, analyzed)

    def start(self, folder_path: str) -> None:
        """Start watching a folder. Stops any existing watch first."""
        self.stop()
        path = Path(folder_path)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {folder_path}")

        # Fresh session → zeroed counters for /watch-progress, and a fresh
        # queue + worker (the old queue may still hold the stop sentinel).
        with self._lock:
            self._in_flight = 0
            self._analyzed = 0
            self._pending.clear()
        self._queue = queue.Queue()
        self._worker_stop = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop,
            args=(self._queue, self._worker_stop),
            daemon=True,
            name="watcher-analyze",
        )
        self._worker.start()

        handler = _PhotoEventHandler(self._db_path, owner=self)
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
        if self._worker is not None:
            # Signal first, then unblock the blocking get(). The worker drops
            # any backlog and exits after at most the file it is currently
            # analyzing (a RAW decode is uncancellable, same as the batch).
            self._worker_stop.set()
            self._queue.put(None)
            self._worker = None
        self._watch_path = None
