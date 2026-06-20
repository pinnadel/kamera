"""
Stdlib-only constants shared across the KaMeRa backend.

Anything that lives here must:
  - depend only on the Python standard library
  - have no side effects on import
  - never need to be mutated at runtime

The rule is: if you can import this module from inside a hot path
(e.g. a single-photo scorer, the burst_ranker fallback) without
paying the cost of pulling rawpy/pyiqa/torch through `analysis.py`,
it belongs here. Otherwise leave it in its specialised module.

Why this exists: prior to 2026-05-17, SUPPORTED_EXTENSIONS lived in
backend.routers.analysis. Several routers + the file watcher needed
it but importing analysis.py is expensive (it loads the analysis
pipeline's heavy deps transitively). The file_watcher even duplicated
the constant with a "keep in sync" comment that drifted. Moving the
constants here is the cheapest fix.
"""

# Photo file extensions the analyzer + watcher recognise. Lowercase with
# the leading dot so callers can match against `path.suffix.lower()`
# directly. Update both this list AND
# phase2_quality/burst_ranker.py::_DIRECT_PASS_FORMATS /
# _RAW_FALLBACK_FORMATS when adding a new format.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".raf", ".nef", ".png", ".tiff", ".tif",
})

# Uppercase canonical format names used in `images.format` column for
# RAW files. Kept aligned with rawpy's supported set; do not add formats
# rawpy can't decode (the analyzer would crash). Used to gate RAW-only
# code paths (preview demosaic, sidecar-pref invalidation, the burst
# ranker's on-demand preview fallback).
RAW_FORMATS: frozenset[str] = frozenset({
    "RAF", "NEF", "ARW", "CR2", "CR3", "DNG",
})

# Subfolder names a photo is moved INTO once a keep/maybe/reject decision is
# made (see file_mover.resolve_dest_folder). Files here are post-decision and
# must never be (re)analyzed: the watcher skips them, so an external archiver
# (Provenance) moving a keeper out of _Keeps can't race the watcher into a
# "Could not load image" error. Canonical home — previously duplicated as
# inline literals in routers/images.py, routers/analysis.py, file_mover.py.
DECISION_SUBFOLDERS: frozenset[str] = frozenset({"_Keeps", "_Maybes", "_Trash"})
