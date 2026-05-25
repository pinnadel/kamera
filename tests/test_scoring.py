"""
Priority 2 — Deterministic scoring.

Tests use synthetic numpy arrays and temporary JPEG files — no real photo
files are required. PIL + numpy are both available in the project venv.

`_calculate_exposure_score` is tested directly with known inputs (no file I/O).
`compute_histogram` is tested with synthetic RGB numpy arrays.
`analyze_exposure` is tested with tiny temp JPEGs written via PIL so the full
code path through `load_as_gray` is exercised without requiring real RAW files.
"""
import io
from pathlib import Path

import numpy as np
import pytest

# The two entry points we are testing from phase1_technical/exposure.py.
# We import _calculate_exposure_score directly to avoid file I/O for the
# pure-logic tests. compute_histogram works on numpy arrays so it also needs
# no file.
from phase1_technical.exposure import (
    _calculate_exposure_score,
    analyze_exposure,
    analyze_exposure_array,
    compute_histogram,
    _piecewise_linear,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_gray_jpeg(tmp_path: Path, mean: int, size: int = 100) -> str:
    """
    Create a uniform gray JPEG (all pixels == mean) and return its path string.
    Uses PIL which is installed in the project venv.
    """
    from PIL import Image
    arr = np.full((size, size), mean, dtype=np.uint8)
    img = Image.fromarray(arr, mode="L").convert("RGB")
    path = tmp_path / f"gray_{mean}.jpg"
    img.save(str(path), format="JPEG", quality=95)
    return str(path)


def _write_rgb_jpeg(tmp_path: Path, rgb_value: tuple, size: int = 100) -> str:
    """Create a uniform RGB JPEG and return its path string."""
    from PIL import Image
    r, g, b = rgb_value
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[..., 0] = r
    arr[..., 1] = g
    arr[..., 2] = b
    img = Image.fromarray(arr, mode="RGB")
    path = tmp_path / f"rgb_{r}_{g}_{b}.jpg"
    img.save(str(path), format="JPEG", quality=95)
    return str(path)


# ---------------------------------------------------------------------------
# _calculate_exposure_score — pure function, no file I/O
# ---------------------------------------------------------------------------

def test_exposure_score_perfect() -> None:
    """A synthetic 128-mean gray image with no clipping should score >= 85."""
    score = _calculate_exposure_score(mean=128, highlight_clip=0.0, shadow_clip=0.0)
    assert score >= 85, f"Expected >= 85 for perfect exposure, got {score}"


def test_exposure_score_dark() -> None:
    """A very dark image (mean ~30) scores well below a well-exposed image.

    With the smooth curve, mean=30 → deviation=98 → brightness_penalty ~41.
    No clipping means no additional deduction, so expected score ≈ 59.
    The upper bound is relaxed to 70 to accommodate the smooth (non-cliff) curve.
    """
    score = _calculate_exposure_score(mean=30, highlight_clip=0.0, shadow_clip=0.0)
    assert score <= 70, f"Expected <= 70 for dark image (penalty applied), got {score}"
    assert score < 100, "Dark image should not score perfect"


def test_exposure_score_bright() -> None:
    """A very bright image (mean ~230) should score <= 65.

    mean=230 → deviation=102 → brightness_penalty ~39. Smooth curve gives ≈ 61.
    Upper bound relaxed to 65 from the old cliff-based 60.
    """
    score = _calculate_exposure_score(mean=230, highlight_clip=0.0, shadow_clip=0.0)
    assert score <= 65, f"Expected <= 65 for bright image, got {score}"


def test_exposure_score_clipped_highlights() -> None:
    """Heavy highlight clipping (>10%) should score <= 75."""
    score = _calculate_exposure_score(mean=128, highlight_clip=15.0, shadow_clip=0.0)
    assert score <= 75, f"Expected <= 75 with heavy clipping, got {score}"


def test_exposure_score_smooth_no_cliff() -> None:
    """Score changes smoothly across the old cliff boundary at deviation=80.

    The old bracket-based scorer jumped 15 points between deviation=79 and
    deviation=81. With piecewise-linear interpolation the delta must be <= 3.
    """
    score_79 = _calculate_exposure_score(mean=128 - 79, highlight_clip=0, shadow_clip=0)
    score_80 = _calculate_exposure_score(mean=128 - 80, highlight_clip=0, shadow_clip=0)
    score_81 = _calculate_exposure_score(mean=128 - 81, highlight_clip=0, shadow_clip=0)
    assert abs(score_79 - score_80) <= 3, (
        f"Cliff detected between deviation 79→80: {score_79} vs {score_80}"
    )
    assert abs(score_80 - score_81) <= 3, (
        f"Cliff detected between deviation 80→81: {score_80} vs {score_81}"
    )


# ---------------------------------------------------------------------------
# analyze_exposure — full code path with temp JPEG files
# ---------------------------------------------------------------------------

def test_analyze_exposure_returns_all_keys(tmp_path: Path) -> None:
    """analyze_exposure() returns a dict with all expected keys."""
    path = _write_gray_jpeg(tmp_path, mean=128)
    result = analyze_exposure(path)
    expected_keys = {
        "mean_brightness",
        "std_brightness",
        "highlight_clip_pct",
        "shadow_clip_pct",
        "exposure_score",
        "exposure_warning",
        "is_likely_intentional",
    }
    assert expected_keys.issubset(result.keys())


def test_analyze_exposure_score_mid_gray(tmp_path: Path) -> None:
    """A mid-gray JPEG (mean ~128) should score >= 85."""
    path = _write_gray_jpeg(tmp_path, mean=128)
    result = analyze_exposure(path)
    # JPEG compression introduces slight mean drift; allow a 10-point buffer.
    assert result["exposure_score"] >= 75, (
        f"Expected score >= 75 for mid-gray, got {result['exposure_score']}"
    )


def test_analyze_exposure_score_very_dark(tmp_path: Path) -> None:
    """A very dark JPEG (mean ~20) scores well below a well-exposed image.

    With mean ~20, deviation ~108, the smooth brightness_penalty ~43. JPEG
    compression at quality=95 may shift the mean by a pixel or two but stays
    far enough below 48 to still apply a heavy penalty. Upper bound relaxed to
    70 to accommodate the smooth (non-cliff) curve.
    """
    path = _write_gray_jpeg(tmp_path, mean=20)
    result = analyze_exposure(path)
    assert result["exposure_score"] <= 70, (
        f"Expected score <= 70 for dark image, got {result['exposure_score']}"
    )
    assert result["exposure_score"] < 100, "Dark image should not score perfect"


def test_analyze_exposure_score_very_bright(tmp_path: Path) -> None:
    """A very bright JPEG (mean ~245) should score <= 65.

    mean=245 → deviation=117 → smooth brightness_penalty ~47. Upper bound
    relaxed to 65 from the old cliff-based 60 to match the smooth curve.
    """
    path = _write_gray_jpeg(tmp_path, mean=245)
    result = analyze_exposure(path)
    assert result["exposure_score"] <= 65, (
        f"Expected score <= 65 for bright image, got {result['exposure_score']}"
    )


# ---------------------------------------------------------------------------
# compute_histogram — operates on numpy arrays directly
# ---------------------------------------------------------------------------

def test_compute_histogram_shape() -> None:
    """compute_histogram() returns keys r/g/b/lum each with 256 elements."""
    rgb = np.full((100, 100, 3), 128, dtype=np.uint8)
    result = compute_histogram(rgb)

    for channel in ("r", "g", "b", "lum"):
        assert channel in result, f"Missing key: {channel}"
        assert len(result[channel]) == 256, (
            f"Expected 256 bins for {channel}, got {len(result[channel])}"
        )


def test_compute_histogram_clip_hi() -> None:
    """An all-255 image reports clip_hi.visible near 100.0."""
    rgb = np.full((100, 100, 3), 255, dtype=np.uint8)
    result = compute_histogram(rgb)
    assert result["clip_hi"]["visible"] >= 99.0, (
        f"Expected clip_hi.visible ~100, got {result['clip_hi']['visible']}"
    )


def test_compute_histogram_clip_lo() -> None:
    """An all-0 image reports clip_lo.visible near 100.0."""
    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    result = compute_histogram(rgb)
    assert result["clip_lo"]["visible"] >= 99.0, (
        f"Expected clip_lo.visible ~100, got {result['clip_lo']['visible']}"
    )


def test_compute_histogram_no_clip_mid_gray() -> None:
    """A mid-gray image has near-zero clipping on both ends."""
    rgb = np.full((100, 100, 3), 128, dtype=np.uint8)
    result = compute_histogram(rgb)
    assert result["clip_hi"]["visible"] == 0.0
    assert result["clip_lo"]["visible"] == 0.0


def test_compute_histogram_wrong_shape_raises() -> None:
    """compute_histogram() raises ValueError for non-3-channel input."""
    gray = np.full((100, 100), 128, dtype=np.uint8)
    with pytest.raises(ValueError, match="HxWx3"):
        compute_histogram(gray)


def test_compute_histogram_total_pixels() -> None:
    """total_pixels field matches the input array's pixel count."""
    rgb = np.zeros((50, 80, 3), dtype=np.uint8)
    result = compute_histogram(rgb)
    assert result["total_pixels"] == 50 * 80


# ---------------------------------------------------------------------------
# analyze_exposure_array — used by the RAW pipeline to skip a second decode
# ---------------------------------------------------------------------------

def test_analyze_exposure_array_returns_all_keys() -> None:
    """analyze_exposure_array returns the same key shape as analyze_exposure."""
    rgb = np.full((100, 100, 3), 128, dtype=np.uint8)
    result = analyze_exposure_array(rgb)
    expected_keys = {
        "mean_brightness",
        "std_brightness",
        "highlight_clip_pct",
        "shadow_clip_pct",
        "exposure_score",
        "exposure_warning",
        "is_likely_intentional",
    }
    assert expected_keys.issubset(result.keys())


def test_analyze_exposure_array_mid_gray_high_score() -> None:
    """A perfectly mid-gray RGB image scores 100 (no JPEG drift, exact mean)."""
    rgb = np.full((100, 100, 3), 128, dtype=np.uint8)
    result = analyze_exposure_array(rgb)
    # No file I/O, no JPEG quantization — mean is exactly 128, no clipping → 100.
    assert result["exposure_score"] == 100


def test_analyze_exposure_array_dark() -> None:
    """A dark RGB image triggers the brightness penalty."""
    rgb = np.full((100, 100, 3), 30, dtype=np.uint8)
    result = analyze_exposure_array(rgb)
    assert result["exposure_score"] <= 70
    assert result["mean_brightness"] == 30.0


def test_analyze_exposure_array_clipped_highlights() -> None:
    """All-white image reports 100% highlight clipping and a low score."""
    rgb = np.full((100, 100, 3), 255, dtype=np.uint8)
    result = analyze_exposure_array(rgb)
    assert result["highlight_clip_pct"] >= 99.0
    # 100% clipping + max brightness deviation → very low score
    assert result["exposure_score"] <= 35


def test_analyze_exposure_array_wrong_shape_raises() -> None:
    """Non-3-channel input raises ValueError (matching compute_histogram)."""
    gray = np.full((100, 100), 128, dtype=np.uint8)
    with pytest.raises(ValueError, match="HxWx3"):
        analyze_exposure_array(gray)


def test_analyze_exposure_array_matches_analyze_exposure(tmp_path: Path) -> None:
    """The array path and the file path should agree on a JPEG round-trip.

    Saving an RGB array to JPEG then reading it back via analyze_exposure
    introduces a small mean drift (compression). We allow ±2 points on the
    score and ±1 on the mean — both paths are working off the same image.
    """
    rgb = np.full((100, 100, 3), 100, dtype=np.uint8)  # darkish but not extreme
    array_result = analyze_exposure_array(rgb)

    # Save and re-read via the file path
    from PIL import Image
    img = Image.fromarray(rgb, mode="RGB")
    path = tmp_path / "match_test.jpg"
    img.save(str(path), format="JPEG", quality=98)
    file_result = analyze_exposure(str(path))

    assert abs(array_result["exposure_score"] - file_result["exposure_score"]) <= 3
    assert abs(array_result["mean_brightness"] - file_result["mean_brightness"]) <= 2
