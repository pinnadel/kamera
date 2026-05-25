"""
Shared mutable state for the KaMeRa backend.

All routers import from here so there is exactly one copy of every
piece of runtime state. No circular-import risk: this module imports
only from stdlib and from packages that have no dependency on any
backend router.

Import pattern:
    from backend.state import _progress, _stop_event, _personal_model, watcher

Mutate _progress in-place (`_progress["running"] = True`) — every
importer holds a reference to the same dict object, so mutations are
visible everywhere without re-importing.
"""

import threading
from pathlib import Path

from backend.database import DEFAULT_DB_PATH
from backend.file_watcher import FolderWatcher
from phase3_learning.personal_model import PersonalModel

# ---------------------------------------------------------------------------
# Preview cache directory — used by analysis and image-list routers.
# ---------------------------------------------------------------------------
PREVIEW_CACHE_DIR: Path = DEFAULT_DB_PATH.parent / "previews"

# ---------------------------------------------------------------------------
# Batch-analysis progress — written by analyze_folder, read by /analyze-progress.
# ---------------------------------------------------------------------------
_progress: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_file": None,
    "started_at": None,
    "stop_requested": False,
    "source_folder": None,
    # Written by _run_batch on completion; available on the final /analyze-progress poll.
    "analyzed_count": 0,
    "error_count": 0,
    # Wall-clock duration of the batch in seconds (float). Set when _run_batch
    # exits (success, stop, or error) and read by the frontend completion banner
    # so the user sees "Analyzed 741 photos in 53m 1s" instead of having to
    # check the console. None while running and on a fresh process.
    "elapsed_seconds": None,
}

# Shared cancellation primitive: the analyzer pool watches this Event so a
# Stop press can interrupt mid-photo (between scorer boundaries), not just
# between files. The flag in _progress stays for the loop check; this Event
# is for the scorers running on the worker pool.
_stop_event: threading.Event = threading.Event()

# ---------------------------------------------------------------------------
# Per-file error log — appended by batch loop and watcher, read by
# /debug/last-errors. Defined here so the watcher on_error callback can
# capture it at construction time (before any router is imported).
# ---------------------------------------------------------------------------
_last_errors: list[dict] = []


def _record_error(err: dict) -> None:
    """Append a watcher/batch error and trim to the last 50 entries."""
    _last_errors.append(err)
    if len(_last_errors) > 50:
        _last_errors[:] = _last_errors[-50:]


# ---------------------------------------------------------------------------
# Singletons initialised once at module load
# ---------------------------------------------------------------------------
watcher: FolderWatcher = FolderWatcher(DEFAULT_DB_PATH, on_error=_record_error)
_personal_model: PersonalModel = PersonalModel()
