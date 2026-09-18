"""
Tests for the Phase-1 sharpness scoring guards added 2026-07-26:

  * Option C — the micro-texture false-sharp cap in
    batch_sharpness_analyzer._score_from_gray. A defocused frame full of fine,
    dense micro-texture (fabric weave, sensor noise) lights up the per-pixel
    Laplacian/Tenengrad measures everywhere, so the p90 tile aggregation reads
    it as uniformly sharp and saturates the score. The cap fires when the
    fine/coarse Tenengrad ratio is extreme.

  * Option D — the soft-saturation curve in
    quality_analyzer.sharpness_score_from_ratio, which replaces the old hard
    `min(100, ratio * 50)` clip that pinned ~half the library to exactly 100.

Both use synthetic numpy images so nothing on disk is required.
"""
import numpy as np

from phase1_technical.batch_sharpness_analyzer import (
    _score_from_gray,
    _microtexture_ratio,
    _MICROTEXTURE_RATIO,
    _MICROTEXTURE_CAP,
)
from phase1_technical.quality_analyzer import sharpness_score_from_ratio


# ---------------------------------------------------------------------------
# Option D — soft-saturation curve
# ---------------------------------------------------------------------------


def test_curve_preserves_threshold_anchor():
    # ratio 1.0 = at threshold = score 50, unchanged from the legacy formula.
    assert sharpness_score_from_ratio(1.0) == 50


def test_curve_preserves_label_cuts():
    # Sharp>=70 lived at ratio 1.4, Borderline>=40 at ratio 0.8 under the old
    # `ratio * 50`. The knee sits above both, so neither moves.
    assert sharpness_score_from_ratio(1.4) == 70
    assert sharpness_score_from_ratio(0.8) == 40


def test_curve_no_longer_clips_hard_at_knee():
    # The old formula hit 100 at ratio 2.0; the new curve spreads the tail so a
    # merely-sharp photo lands below 100 and only the extreme tail rounds up.
    assert sharpness_score_from_ratio(2.0) < 100
    assert sharpness_score_from_ratio(2.5) < 100


def test_curve_is_monotonic_and_bounded():
    prev = -1
    for r in [x / 10 for x in range(0, 200)]:  # ratio 0.0 → 20.0
        s = sharpness_score_from_ratio(r)
        assert 0 <= s <= 100
        assert s >= prev  # non-decreasing
        prev = s


def test_curve_continuous_at_knee():
    # No jump discontinuity where the linear and log pieces meet (ratio 1.8).
    below = sharpness_score_from_ratio(1.79)
    at = sharpness_score_from_ratio(1.8)
    above = sharpness_score_from_ratio(1.81)
    assert below <= at <= above
    assert at - below <= 2 and above - at <= 2


# ---------------------------------------------------------------------------
# Option C — micro-texture cap
# ---------------------------------------------------------------------------


def _fine_texture(h=512, w=512):
    """Fine random noise — high-frequency energy everywhere that collapses on a
    4× downsample. This is the micro-texture false-sharp fingerprint (fabric
    weave / sensor noise on a defocused frame), and mirrors the real DSCF0976
    ratio (~16). A seeded RNG keeps it deterministic. (A perfect 1-pixel
    checkerboard would NOT work: the 3×3 Sobel kernel cancels to exactly 0 on a
    perfectly alternating grid — real texture is irregular, hence noise here.)"""
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, size=(h, w), dtype=np.uint8)


def _coarse_structure(h=512, w=512):
    """A low-frequency sinusoidal grating (~8 cycles across the frame) — real
    macro-structure whose gradient energy survives a 4× downsample, so the
    fine/coarse ratio stays low and the cap never fires. This models a genuinely
    in-focus scene (multi-pixel edges), the opposite of fine micro-texture."""
    xx = np.linspace(0, 8 * 2 * np.pi, w)
    row = (127.5 * (1 + np.sin(xx))).astype(np.uint8)
    return np.tile(row, (h, 1))


def test_microtexture_ratio_high_for_fine_pattern():
    assert _microtexture_ratio(_fine_texture()) >= _MICROTEXTURE_RATIO


def test_microtexture_ratio_low_for_coarse_structure():
    assert _microtexture_ratio(_coarse_structure()) < _MICROTEXTURE_RATIO


def test_cap_fires_on_fine_texture():
    result = _score_from_gray(_fine_texture())
    assert result["microtexture_capped"] is True
    assert result["normalized_score"] == _MICROTEXTURE_CAP


def test_cap_does_not_fire_on_coarse_structure():
    result = _score_from_gray(_coarse_structure())
    assert result["microtexture_capped"] is False
    # Score is whatever the fusion produced — untouched by the guard.
    assert result["normalized_score"] != _MICROTEXTURE_CAP or result["normalized_score"] <= _MICROTEXTURE_CAP


def test_cap_only_lowers_never_raises():
    # A flat gray image has near-zero energy → tiny normalized_score, well below
    # the cap. The guard must not touch a score already under the cap.
    flat = np.full((512, 512), 128, dtype=np.uint8)
    result = _score_from_gray(flat)
    assert result["normalized_score"] <= _MICROTEXTURE_CAP
    assert result["microtexture_capped"] is False


def test_microtexture_ratio_fails_open_on_bad_input():
    # A degenerate 1x1 array must not raise — the guard returns 0.0 so the
    # caller keeps the uncapped score (fail-open).
    assert _microtexture_ratio(np.zeros((1, 1), dtype=np.uint8)) == 0.0
