"""
Thread-safe model loading status registry.

Each scorer calls begin() before loading its model and end() when done.
The FastAPI /model-status endpoint calls snapshot() to read current state.

Why a shared module rather than per-scorer state:
  Multiple scorers run in a background thread (analyze-folder), so we need a
  single location the endpoint can always read without importing each scorer.
  A threading.Lock guarantees consistent reads even when a scorer thread and
  the polling request thread access the dict at the same time.
"""

import threading
import time

_lock = threading.Lock()
_active: dict[str, dict] = {}  # model_id → status entry


def begin(model_id: str, name: str, size_mb: int, downloading: bool) -> None:
    """
    Register a model as currently loading.

    downloading=True  → weights not yet on disk, network transfer in progress
    downloading=False → weights cached locally, loading into RAM
    """
    with _lock:
        _active[model_id] = {
            "name": name,
            "state": "downloading" if downloading else "loading",
            "size_mb": size_mb,
            "started_at": time.time(),
        }


def update_progress(model_id: str, current_mb: float, total_mb: float | None = None) -> None:
    """Patch progress fields on an already-`begin()`ed entry. Safe to call
    many times per second from a download loop — no-op if the model isn't
    in the active set (e.g. update arrives after end() in a race).

    `current_mb` is bytes-downloaded-so-far in megabytes; `total_mb` is the
    full payload size if the source has told us. Both fed straight to the
    frontend's progress display.
    """
    with _lock:
        entry = _active.get(model_id)
        if entry is None:
            return
        entry["current_mb"] = current_mb
        if total_mb is not None:
            entry["total_mb"] = total_mb


def end(model_id: str) -> None:
    """Mark a model as fully loaded and remove it from the active set."""
    with _lock:
        _active.pop(model_id, None)


def snapshot() -> dict:
    """Return a JSON-serialisable snapshot of current loading state."""
    with _lock:
        models = list(_active.values())
    return {
        "loading": len(models) > 0,
        "models": models,
    }
