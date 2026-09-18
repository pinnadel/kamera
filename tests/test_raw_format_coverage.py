"""
Guards against RAW formats being half-wired.

The failure this prevents, twice observed: a format gets added to
`RAW_FORMATS` (so `is_raw_format()` says yes, previews demosaic it, the burst
ranker's RAW fallback accepts it) while every *other* list that has to know
about it is left alone. `.3fr` shipped that way until 2026-06-30; `.dng`,
`.arw`, `.cr2` and `.cr3` until 2026-08-28, when a DNG was invisible to every
folder walk because `SUPPORTED_EXTENSIONS` had never been updated — and would
have crashed in the scorers' `cv2.imread` branch had it been discovered.

The fix was to derive the lowercase suffix list and the RAW half of
`SUPPORTED_EXTENSIONS` from `RAW_FORMATS`. These tests pin that derivation, and
pin the scorer decode branches to the shared constant so nobody reintroduces a
hand-typed `('.RAF', '.NEF', '.3FR')` tuple.
"""
import inspect
from pathlib import Path

import pytest

from backend.constants import (
    RAW_FORMATS,
    RAW_DECODE_SUFFIXES,
    SUPPORTED_EXTENSIONS,
    _HIF_PAIRED_RAW_EXTS,
    _PROCESSED_SIBLING_EXTS,
    has_processed_sibling,
    is_raw_format,
    is_standalone_hif,
)


def test_raw_decode_suffixes_mirrors_raw_formats():
    """The lowercase suffix list is derived, not hand-maintained."""
    assert set(RAW_DECODE_SUFFIXES) == {"." + fmt.lower() for fmt in RAW_FORMATS}


def test_every_raw_format_is_discoverable():
    """
    The actual .dng bug: a format rawpy can decode but no folder walk can see.
    `SUPPORTED_EXTENSIONS` gates the analyzer, the file watcher, and GET /images'
    folder scan — a RAW format missing here is simply never found on disk.
    """
    missing = {fmt for fmt in RAW_FORMATS if "." + fmt.lower() not in SUPPORTED_EXTENSIONS}
    assert not missing, f"RAW formats not discoverable by any folder walk: {sorted(missing)}"


@pytest.mark.parametrize("fmt", sorted(RAW_FORMATS))
def test_raw_format_round_trips_through_is_raw_format(fmt):
    """`images.format` is written as `path.suffix.lstrip('.').upper()`."""
    assert is_raw_format(fmt)
    assert is_raw_format(fmt.lower())


def test_supported_extensions_split_is_exhaustive():
    """Every supported extension is either RAW or processed — never neither, never both."""
    assert set(RAW_DECODE_SUFFIXES) | _PROCESSED_SIBLING_EXTS == SUPPORTED_EXTENSIONS
    assert not set(RAW_DECODE_SUFFIXES) & _PROCESSED_SIBLING_EXTS


def test_hif_pairing_covers_every_raw_format():
    """
    Only Fuji ships RAW+HIF today, but the sidecar gate must not be the one
    list that still names three formats by hand.
    """
    assert _HIF_PAIRED_RAW_EXTS == frozenset(RAW_DECODE_SUFFIXES)


@pytest.mark.parametrize("raw_ext", sorted(RAW_DECODE_SUFFIXES))
def test_hif_next_to_any_raw_is_a_sidecar(tmp_path: Path, raw_ext):
    hif = tmp_path / "IMG_0001.HIF"
    hif.write_bytes(b"fake hif")
    (tmp_path / f"IMG_0001{raw_ext}").write_bytes(b"fake raw")
    assert is_standalone_hif(hif) is False


@pytest.mark.parametrize("raw_ext", sorted(RAW_DECODE_SUFFIXES))
def test_jpeg_priority_skip_sees_every_raw_format(tmp_path: Path, raw_ext):
    """
    A DJI DNG+JPG pair must skip the DNG under analysis_mode=jpeg_priority
    exactly like a Fuji RAF+JPG pair does.
    """
    raw = tmp_path / f"IMG_0002{raw_ext}"
    raw.write_bytes(b"fake raw")
    assert has_processed_sibling(raw) is False, "no sibling yet"

    (tmp_path / "IMG_0002.JPG").write_bytes(b"fake jpeg")
    assert has_processed_sibling(raw) is True


# The scorer decode branches. Each of these used to carry its own literal
# tuple; three of them drifted. Assert they read the shared constant instead.
def test_scorers_use_the_shared_decode_suffix_constant():
    from phase1_technical import utils as p1_utils
    from phase1_technical import batch_sharpness_analyzer as p1_sharp
    from phase1_technical import quality_analyzer as p1_quality
    from phase2_quality import face_analyzer as p2_face

    for module in (p1_utils, p1_sharp, p1_quality, p2_face):
        assert getattr(module, "RAW_DECODE_SUFFIXES", None) is RAW_DECODE_SUFFIXES, (
            f"{module.__name__} does not import the shared RAW_DECODE_SUFFIXES"
        )


@pytest.mark.parametrize(
    "module_name, func_name",
    [
        ("phase1_technical.utils", "load_as_gray"),
        ("phase1_technical.batch_sharpness_analyzer", "detect_sharpness"),
        ("phase2_quality.face_analyzer", "_load_image"),
    ],
)
def test_no_hardcoded_raw_suffix_tuples_in_decode_branches(module_name, func_name):
    """
    Source-level guard. A regex over the whole repo would be brittle; pinning
    the three functions that actually branch on suffix is enough, and names the
    offender directly when someone retypes the tuple.
    """
    import importlib

    module = importlib.import_module(module_name)
    source = inspect.getsource(getattr(module, func_name))
    for literal in (".RAF", ".NEF", ".3FR", ".raf", ".nef", ".3fr"):
        assert literal not in source, (
            f"{module_name}.{func_name} hardcodes {literal!r} — "
            "use RAW_DECODE_SUFFIXES so new formats are picked up automatically"
        )
