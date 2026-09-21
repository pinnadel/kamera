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

# ---------------------------------------------------------------------------
# Auto-cull AI-ranking progress — written by run_auto_cull's ranking phase,
# read by /auto-cull/progress.
#
# Separate from _progress above on purpose: an auto-cull can be started while a
# batch is analysing, and the two would clobber each other's counters. The
# ranking phase is the only part of auto-cull that takes real time (~60 s per
# group that isn't in the burst_rankings cache), so this reports groups, not
# photos. Stopping aborts BEFORE any decision is applied — nothing has moved on
# disk at that point, so a stopped auto-cull is a no-op, not a half-run.
# ---------------------------------------------------------------------------
_autocull_progress: dict = {
    "running": False,
    "phase": None,          # "ranking" | "applying" | None
    "total": 0,             # groups to rank
    "done": 0,
    "cached": 0,            # of those done, how many were free (cache hits)
    "stop_requested": False,
    "source_folder": None,
    "started_at": None,
}

_autocull_stop_event: threading.Event = threading.Event()

# Shared cancellation primitive: the analyzer pool watches this Event so a
# Stop press can interrupt mid-photo (between scorer boundaries), not just
# between files. The flag in _progress stays for the loop check; this Event
# is for the scorers running on the worker pool.
_stop_event: threading.Event = threading.Event()

# Guards the window between "file physically moved on disk" and "images.file_path
# updated to match" in _apply_decision. POST /sync-folder drops any row whose
# file_path doesn't exist on disk — if it reads mid-move, a photo that was just
# safely relocated to _Keeps/ looks "missing" at its old path and its row (plus
# decisions/burst_members/bursts) gets permanently deleted, even though the file
# is fine. Both sides take this lock around their move+update / read+delete
# sections so sync-folder never observes torn state. See 2026-07-23 incident:
# 109 rows deleted this way while a scripted batch of decisions raced a
# sync-folder call triggered by tab focus.
_decision_move_lock: threading.Lock = threading.Lock()

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

# One model per training corpus. Corpus names are the same strings as
# images.source_type, so a row picks its model straight from its source type.
# `_personal_model` stays bound to the folder model — it is the name every
# existing call site imports, and the folder corpus is what they all meant.
_personal_models: dict[str, PersonalModel] = {
    "folder": PersonalModel("folder"),
    "photos": PersonalModel("photos"),
}
_personal_model: PersonalModel = _personal_models["folder"]


def model_for(corpus: str) -> PersonalModel:
    """Model for a corpus / source_type. Unknown names fall back to folder."""
    return _personal_models.get(corpus, _personal_model)


def invalidate_all(image_ids: list[int]) -> None:
    """Drop cached scores for these images in every model.

    Callers know an image changed but not which corpus owns it, and a stale
    score in the model that happens not to own the row is just as wrong.
    """
    for model in _personal_models.values():
        model.invalidate_many(image_ids)


def clear_all_caches() -> None:
    for model in _personal_models.values():
        model.clear_cache()
