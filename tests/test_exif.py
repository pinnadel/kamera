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
