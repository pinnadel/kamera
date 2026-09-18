"""
Source-backend registry. Dispatch is by `images.source_type`, never by path.

See backend/sources/base.py for what a sink owns and why the seam exists.
"""

from __future__ import annotations

from fastapi import HTTPException

from backend.sources.base import SourceSink
from backend.sources.folder import FolderSink

# Sinks are stateless, so one shared instance each is fine. PhotosSink is
# registered lazily inside _sinks() because importing it pulls osxphotos /
# photoscript, which only exist on macOS — a Linux or Windows install must
# still be able to import this module and use folder tabs.
_FOLDER_SINK = FolderSink()
_SINKS: dict[str, SourceSink] = {"folder": _FOLDER_SINK}


def _sinks() -> dict[str, SourceSink]:
    if "photos" not in _SINKS:
        try:
            from backend.sources.photos import PhotosSink
        except Exception:  # pragma: no cover - platform/dependency dependent
            return _SINKS
        _SINKS["photos"] = PhotosSink()
    return _SINKS


def sink_for(image_row: dict) -> SourceSink:
    """Return the sink that owns this photo's decisions.

    A row written before migration v52 has no source_type; it is a folder
    photo by definition, which the column's DEFAULT already encodes — the
    `or "folder"` covers rows built in memory by tests and callers.
    """
    source_type = image_row.get("source_type") or "folder"
    sink = _sinks().get(source_type)
    if sink is None:
        # Never guess. Falling back to FolderSink for an unknown source would
        # mean trying to move a file that must not be moved.
        raise HTTPException(
            status_code=500,
            detail=f"No write-back backend for source_type {source_type!r} "
                   f"(image {image_row.get('id')})",
        )
    return sink


__all__ = ["SourceSink", "FolderSink", "sink_for"]
