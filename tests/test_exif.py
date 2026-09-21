"""
Tests for phase1_technical/exif_parser.py.

Two paths to verify:
1. exifread-only path (JPEG): a fresh JPEG with EXIF tags returns populated fields
2. exiftool-fallback path: when exifread returns an empty dict, the fallback
   is invoked. We test the fallback wiring with a mock so the test doesn't
   require the exiftool binary or a real RAF file.

We do NOT test against a real RAF file in tests/ because RAFs are large
binary blobs that don't belong in the repo. The exiftool subprocess is
exercised live by every batch run; this test just verifies the routing
logic and that the fallback dict shape is correct.
"""
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from phase1_technical.exif_parser import (
    extract_exif,
    _extract_via_exifread,
    _extract_via_exiftool,
)


def _write_jpeg_with_exif(tmp_path: Path, name: str = "with_exif.jpg") -> str:
    """
    Create a minimal JPEG with synthetic EXIF tags (camera/aperture/iso).

    PIL writes EXIF blocks via Image.save(exif=...). The bytes need to be a
    valid TIFF/EXIF buffer; building one by hand is tedious, so we rely on
    piexif to construct it. piexif is a transitive dep via pyiqa; if absent
    the test falls back to a no-EXIF JPEG (and exifread returns empty).
    """
    from PIL import Image
    arr = np.full((100, 100, 3), 128, dtype=np.uint8)
    img = Image.fromarray(arr, mode="RGB")
    path = tmp_path / name
    # No EXIF — this JPEG will produce all-None via exifread, which is
    # exactly the trigger condition for the exiftool fallback path. Useful
    # for testing the routing.
    img.save(str(path), format="JPEG", quality=95)
    return str(path)


def test_extract_exif_returns_dict_with_expected_keys(tmp_path):
    """The result always has the eight expected keys, even for a bare JPEG."""
    path = _write_jpeg_with_exif(tmp_path)
    result = extract_exif(path)
    expected = {
        "camera", "shot_at", "focal_length_mm", "aperture", "shutter_speed",
        "iso", "lens_model", "film_simulation",
    }
    assert set(result.keys()) == expected


def test_extract_exif_no_tags_returns_all_none_or_falls_through(tmp_path):
    """A JPEG with no EXIF either returns all None (no exiftool installed)
    or invokes the exiftool fallback (which itself returns all None for
    a synthetic image with no real metadata).

    The function must not crash and must return the expected key set.
    """
    path = _write_jpeg_with_exif(tmp_path)
    result = extract_exif(path)
    for key in (
        "camera", "shot_at", "focal_length_mm", "aperture", "shutter_speed",
        "iso", "lens_model", "film_simulation",
    ):
        assert key in result


def test_exiftool_fallback_invoked_when_exifread_empty(tmp_path):
    """When exifread returns all-None, the exiftool fallback is called."""
    path = _write_jpeg_with_exif(tmp_path)

    fake_exiftool_result = {
        "camera": "FakeCam X1",
        "shot_at": "2026-05-05 10:30:00",
        "focal_length_mm": 35.0,
        "aperture": 2.8,
        "shutter_speed": 0.004,
        "iso": 400,
        "lens_model": "Fakelens 35mm f/1.4",
        "film_simulation": "Velvia",
    }

    with patch(
        "phase1_technical.exif_parser._extract_via_exiftool",
        return_value=fake_exiftool_result,
    ) as mock_fallback:
        result = extract_exif(path)
        # Fallback was called because exifread returned all-None for this bare JPEG
        mock_fallback.assert_called_once()
        # Result should be the fallback dict
        assert result == fake_exiftool_result


def test_exiftool_fallback_skipped_when_exifread_succeeds():
    """When exifread returns at least one populated tag (camera or shot_at
    or iso), the exiftool fallback is NOT called — saves the ~150ms cost.
    """
    fake_exifread_result = {
        "camera": "Real EXIF Camera",
        "shot_at": "2026-05-05 09:00:00",
        "focal_length_mm": 50.0,
        "aperture": 1.8,
        "shutter_speed": 0.002,
        "iso": 200,
        "lens_model": None,
        "film_simulation": None,
    }

    with patch(
        "phase1_technical.exif_parser._extract_via_exifread",
        return_value=fake_exifread_result,
    ), patch(
        "phase1_technical.exif_parser._extract_via_exiftool",
    ) as mock_fallback:
        result = extract_exif("dummy_path.jpg")
        mock_fallback.assert_not_called()
        assert result == fake_exifread_result


def test_exiftool_fallback_returns_none_on_failure(tmp_path):
    """When the exiftool fallback returns None (e.g. exiftool not installed),
    extract_exif gracefully returns the original (mostly empty) exifread result
    rather than raising.
    """
    path = _write_jpeg_with_exif(tmp_path)
    with patch(
        "phase1_technical.exif_parser._extract_via_exiftool",
        return_value=None,
    ):
        result = extract_exif(path)
        # Should not crash, just return whatever exifread produced
        assert isinstance(result, dict)
        assert "camera" in result


# ---------------------------------------------------------------------------
# exiftool print conversion (2026-09-20)
#
# pyexiftool defaults to common_args ["-G", "-n"]. The "-n" disables
# exiftool's print conversion: good for FNumber/ISO, destructive for every
# enum-backed tag. FilmMode arrived as the raw Fuji opcode 2816 instead of
# "Reala ACE", and the dashboard drew a histogram of opcodes.
#
# The parser now reads with print conversion ON, which means the numeric tags
# arrive formatted ("23.0 mm", "1/140") — a parsing job we control, versus an
# enum table we would have to chase every time Fujifilm ships a simulation.
# ---------------------------------------------------------------------------


def _run_exiftool_branch(metadata: dict) -> dict:
    """Drive _extract_via_exiftool with a canned exiftool payload."""
    from unittest.mock import MagicMock

    helper = MagicMock()
    helper.__enter__.return_value.get_metadata.return_value = [metadata]
    fake_module = MagicMock()
    fake_module.ExifToolHelper.return_value = helper

    with patch.dict("sys.modules", {"exiftool": fake_module}):
        from phase1_technical.exif_parser import _extract_via_exiftool
        return _extract_via_exiftool("/nonexistent.RAF")


def test_exiftool_branch_parses_printed_numeric_forms():
    """With print conversion on, exiftool formats numbers for humans."""
    out = _run_exiftool_branch({
        "EXIF:Model": "X100VI",
        "EXIF:Make": "FUJIFILM",
        "EXIF:FocalLength": "23.0 mm",     # unit suffix
        "EXIF:FNumber": "f/2.0",           # aperture notation
        "EXIF:ExposureTime": "1/140",      # fraction
        "EXIF:ISO": 500,
        "MakerNotes:FilmMode": "Reala ACE",
    })

    assert out["focal_length_mm"] == 23.0
    assert out["aperture"] == 2.0
    assert out["shutter_speed"] == pytest.approx(1 / 140)
    assert out["iso"] == 500
    assert out["film_simulation"] == "Reala ACE"


def test_exiftool_branch_still_accepts_bare_numbers():
    """Not every tag gets decorated — the plain path must keep working."""
    out = _run_exiftool_branch({
        "EXIF:Model": "Z6III",
        "EXIF:FocalLength": 135.0,
        "EXIF:FNumber": 1.8,
        "EXIF:ExposureTime": 0.004,
        "EXIF:ISO": "800",
    })

    assert out["focal_length_mm"] == 135.0
    assert out["aperture"] == 1.8
    assert out["shutter_speed"] == 0.004
    assert out["iso"] == 800


def test_unresolved_numeric_film_mode_is_dropped():
    """A bare integer FilmMode means print conversion regressed. A blank is
    honest; a histogram bar labelled "2816" is not."""
    out = _run_exiftool_branch({
        "EXIF:Model": "X100VI",
        "MakerNotes:FilmMode": 2816,
    })
    assert out["film_simulation"] is None


def test_exiftool_is_asked_to_keep_print_conversion():
    """Guard the actual fix: common_args must not carry "-n"."""
    from unittest.mock import MagicMock

    helper = MagicMock()
    helper.__enter__.return_value.get_metadata.return_value = [{"EXIF:Model": "X"}]
    fake_module = MagicMock()
    fake_module.ExifToolHelper.return_value = helper

    with patch.dict("sys.modules", {"exiftool": fake_module}):
        from phase1_technical.exif_parser import _extract_via_exiftool
        _extract_via_exiftool("/nonexistent.RAF")

    kwargs = fake_module.ExifToolHelper.call_args.kwargs
    assert "-n" not in kwargs.get("common_args", []), \
        "-n disables print conversion and turns FilmMode back into an opcode"


def test_both_extraction_paths_agree_on_camera_name():
    """One body, one name — regardless of which parser read the file.

    The exiftool branch used to build "Make Model" while exifread normalised
    to the short name, so RAF/HIF logged "FUJIFILM X100VI" and JPEG logged
    "X100VI". The dashboard showed the same camera as two rows.
    """
    from phase1_technical.camera_shake import _get_camera_name

    exifread_name = _get_camera_name({"Image Model": "X100VI"})
    exiftool_out = _run_exiftool_branch({
        "EXIF:Make": "FUJIFILM",
        "EXIF:Model": "X100VI",
    })

    assert exifread_name == "X100VI"
    assert exiftool_out["camera"] == exifread_name


def test_exiftool_branch_does_not_prepend_make():
    """Make is never prepended — an unknown body keeps its plain model name."""
    out = _run_exiftool_branch({
        "EXIF:Make": "Hasselblad",
        "EXIF:Model": "X2D II 100C",
    })
    assert out["camera"] == "X2D II 100C"


# ---------------------------------------------------------------------------
# exiftool discovery (2026-09-20)
#
# A LaunchAgent inherits launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin), not
# the login shell's, and `/bin/bash start.sh` reads no profile. Homebrew's
# exiftool was therefore invisible to the backend, and every RAF/HIF was
# analysed with an all-None EXIF dict: the photo still scored, so the row was
# written with camera, shot_at, lens, focal length, aperture, shutter and ISO
# all NULL. 1 530 rows — 23% of shooting_log — with no error anywhere.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _reset_exiftool_cache():
    """_find_exiftool memoises; tests must not inherit each other's answer."""
    import phase1_technical.exif_parser as ep
    saved = (ep._exiftool_path, ep._exiftool_warned, ep._EXIFTOOL_FALLBACKS)
    ep._exiftool_path, ep._exiftool_warned = None, False
    yield ep
    ep._exiftool_path, ep._exiftool_warned, ep._EXIFTOOL_FALLBACKS = saved


def test_exiftool_found_by_absolute_path_when_missing_from_path(_reset_exiftool_cache, tmp_path):
    """PATH is not the only way to find a binary that is plainly installed."""
    ep = _reset_exiftool_cache
    fake = tmp_path / "exiftool"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    ep._EXIFTOOL_FALLBACKS = (str(fake),)

    with patch("shutil.which", return_value=None):
        assert ep._find_exiftool() == str(fake)


def test_missing_exiftool_warns_loudly_exactly_once(_reset_exiftool_cache, caplog):
    """The old code logged this at debug level to avoid flooding a batch.

    Right instinct for a per-photo error, wrong one for a missing dependency:
    the condition holds for the entire run, so one warning says everything —
    and silence is how this cost 1 530 rows of metadata.
    """
    import logging
    ep = _reset_exiftool_cache
    ep._EXIFTOOL_FALLBACKS = ()

    with patch("shutil.which", return_value=None), caplog.at_level(logging.WARNING):
        assert ep._extract_via_exiftool("/whatever.RAF") is None
        assert ep._extract_via_exiftool("/whatever2.RAF") is None

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "one warning per process, not per photo"
    assert "exiftool not found" in warnings[0].getMessage()


def test_exiftool_helper_is_given_an_explicit_executable(_reset_exiftool_cache, tmp_path):
    """Relying on ExifToolHelper's own PATH lookup is what broke."""
    from unittest.mock import MagicMock

    ep = _reset_exiftool_cache
    fake_bin = tmp_path / "exiftool"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    ep._EXIFTOOL_FALLBACKS = (str(fake_bin),)

    helper = MagicMock()
    helper.__enter__.return_value.get_metadata.return_value = [{"EXIF:Model": "X100VI"}]
    fake_module = MagicMock()
    fake_module.ExifToolHelper.return_value = helper

    with patch("shutil.which", return_value=None), \
         patch.dict("sys.modules", {"exiftool": fake_module}):
        out = ep._extract_via_exiftool("/whatever.RAF")

    assert out["camera"] == "X100VI"
    assert fake_module.ExifToolHelper.call_args.kwargs["executable"] == str(fake_bin)
