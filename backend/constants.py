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
#
# .hif is included as a directly-analyzable format (Fuji X100VI/X Half can
# shoot HIF-only, no RAW at all — those files were previously invisible to
# KaMeRa). But .hif is ALSO written as a sidecar next to a .RAF in RAW+HIF
# mode, where it must stay move-only (see file_mover.py) and never get its
# own DB row / independent analysis — that would double-count the shot and
# double-move on decision. is_standalone_hif() below is the gate: callers
# that walk a folder for analysis candidates must skip a .hif file that has
# a same-stem RAW sibling.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".raf", ".nef", ".3fr", ".png", ".tiff", ".tif", ".hif",
})

# Uppercase canonical format names used in `images.format` column for
# RAW files. Kept aligned with rawpy's supported set; do not add formats
# rawpy can't decode (the analyzer would crash). Used to gate RAW-only
# code paths (preview demosaic, sidecar-pref invalidation, the burst
# ranker's on-demand preview fallback).
RAW_FORMATS: frozenset[str] = frozenset({
    "RAF", "NEF", "3FR", "ARW", "CR2", "CR3", "DNG",
})

# Extensions that, when found as a same-stem sibling of a .hif file, mean
# the .hif is a camera-baked sidecar preview (not a standalone photo).
_HIF_PAIRED_RAW_EXTS: frozenset[str] = frozenset({".raf", ".nef", ".3fr"})


def is_standalone_hif(path) -> bool:
    """
    True if `path` is a .hif/.HIF file with no same-stem RAW sibling in its
    folder — i.e. a real photo (X100VI/X Half HIF-only shooting mode), not a
    RAW+HIF sidecar preview. Non-.hif paths always return False.

    Folder is scanned once per call; callers filtering many candidates
    should cache siblings per-directory rather than calling this in a
    tight loop over a large folder.
    """
    from pathlib import Path as _Path
    p = _Path(path)
    if p.suffix.lower() != ".hif":
        return False
    stem_lower = p.stem.lower()
    try:
        siblings = list(p.parent.iterdir())
    except OSError:
        return True
    for sibling in siblings:
        if sibling.suffix.lower() in _HIF_PAIRED_RAW_EXTS and sibling.stem.lower() == stem_lower:
            return False
    return True

def is_raw_format(fmt: str | None) -> bool:
    """True if `fmt` (an images.format value, e.g. 'RAF', 'JPG') is RAW."""
    return bool(fmt) and fmt.upper() in RAW_FORMATS


# Extensions that count as a "processed" sibling for the jpeg_priority
# analysis-mode skip (backend/routers/settings.py::analysis_mode). Deliberately
# the non-RAW half of SUPPORTED_EXTENSIONS minus .hif-as-sidecar nuance —
# .hif itself counts, since an X100VI/X Half RAW+HIF pair should skip the RAW
# the same way a RAW+JPEG pair does.
_PROCESSED_SIBLING_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".hif", ".png", ".tiff", ".tif",
})


def has_processed_sibling(path) -> bool:
    """
    True if `path` (a RAW file) has a same-stem JPEG/HIF/etc. sibling in its
    folder — i.e. it's safe to skip scoring it under analysis_mode=jpeg_priority
    because a processed rendition exists to cull from instead. Non-RAW paths
    and RAW files with no such sibling return False (the latter must still be
    analyzed in full — nothing else to judge the shot by).

    Folder is scanned once per call; callers filtering many candidates should
    cache siblings per-directory rather than calling this in a tight loop.
    """
    from pathlib import Path as _Path
    p = _Path(path)
    if p.suffix.upper().lstrip(".") not in RAW_FORMATS:
        return False
    stem_lower = p.stem.lower()
    try:
        siblings = list(p.parent.iterdir())
    except OSError:
        return False
    for sibling in siblings:
        if (sibling.suffix.lower() in _PROCESSED_SIBLING_EXTS
                and sibling.stem.lower() == stem_lower
                and sibling != p):
            return True
    return False


def companion_stem_matches(filename_a: str, filename_b: str) -> bool:
    """
    True if two filenames are the same shot in different formats — same stem,
    case-insensitive (APFS default), different file. Used to pair a RAW+JPEG
    (or RAW+standalone-HIF) capture across two independent `images` rows.

    Does NOT check source_folder — callers must already have scoped the
    comparison to the same folder (two files with the same stem in different
    folders are unrelated shots, not a pair).
    """
    from pathlib import Path as _Path
    if filename_a == filename_b:
        return False
    return _Path(filename_a).stem.lower() == _Path(filename_b).stem.lower()


def shot_at_to_unix(value) -> float | None:
    """
    Normalise a SQLite shot_at column to Unix seconds for burst clustering.

    Accepts either an integer/float epoch or an ISO-8601 string. Handles the
    two string shapes SQLite actually stores — 'YYYY-MM-DDTHH:MM:SS' and
    'YYYY-MM-DD HH:MM:SS' (space separator) — and a trailing 'Z' UTC marker.
    Returns None if the value is missing or unparseable.

    Canonical home for what used to be two subtly-divergent copies: one in
    routers/model.py (handled int epochs + 'Z' but not the space separator)
    and a "tiny copy" in routers/decisions.py (handled the space separator but
    silently returned None for 'Z'-suffixed and epoch values). Consolidated
    here — stdlib-only, importable from either router without a circular
    import — so the two clustering call sites can never drift apart again.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime
    s = str(value).strip().replace("Z", "+00:00")
    # SQLite may store a space between date and time; fromisoformat wants 'T'
    # on older Pythons. Normalise only the first separator.
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


# Subfolder names a photo is moved INTO once a keep/maybe/reject decision is
# made (see file_mover.resolve_dest_folder). Files here are post-decision and
# must never be (re)analyzed: the watcher skips them, so an external archiver
# (Provenance) moving a keeper out of _Keeps can't race the watcher into a
# "Could not load image" error. Canonical home — previously duplicated as
# inline literals in routers/images.py, routers/analysis.py, file_mover.py.
DECISION_SUBFOLDERS: frozenset[str] = frozenset({"_Keeps", "_Maybes", "_Rejects"})
