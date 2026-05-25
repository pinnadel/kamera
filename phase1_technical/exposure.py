"""
Exposure Analysis Module
Detects under/over exposure and clipping in images.

This module calculates:
- Mean brightness (average gray level)
- Histogram distribution
- Clipped highlight percentage
- Crushed shadow percentage
- Overall exposure score
- Exposure warning (flag, not judgment)
- Intentionality heuristic (artistic vs accidental)
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Dict

from .utils import load_as_gray


def _analyze_gray_array(img: np.ndarray) -> Dict[str, float]:
    """Compute exposure metrics from a uint8 grayscale numpy array.

    Shared between `analyze_exposure` (file path entry point) and
    `analyze_exposure_array` (already-decoded RGB entry point) so the math
    only lives in one place.
    """
    mean_brightness = float(np.mean(img))
    std_brightness  = float(np.std(img))
    total_pixels    = img.size

    highlight_clip_pct = float(np.sum(img == 255)) / total_pixels * 100
    shadow_clip_pct    = float(np.sum(img == 0))   / total_pixels * 100

    score = _calculate_exposure_score(
        mean_brightness, highlight_clip_pct, shadow_clip_pct
    )

    # Intentionality heuristic:
    # Dark image with HIGH contrast = probably artistic (moody low-key)
    # Dark image with LOW contrast  = probably a mistake (just underexposed)
    is_likely_intentional = (
        mean_brightness < 80 and std_brightness > 40
    ) or (
        mean_brightness > 200 and std_brightness > 35
    )

    if highlight_clip_pct > 2:
        warning = "clipped_highlights"
    elif shadow_clip_pct > 5:
        warning = "clipped_shadows"
    elif mean_brightness < 50:
        warning = "dark"
    elif mean_brightness > 200:
        warning = "bright"
    else:
        warning = "ok"

    return {
        'mean_brightness':       round(mean_brightness, 2),
        'std_brightness':        round(std_brightness, 2),
        'highlight_clip_pct':    round(highlight_clip_pct, 2),
        'shadow_clip_pct':       round(shadow_clip_pct, 4),
        'exposure_score':        score,
        'exposure_warning':      warning,
        'is_likely_intentional': is_likely_intentional,
    }


def analyze_exposure(image_path: str) -> Dict[str, float]:
    """
    Analyze the exposure quality of an image (file-path entry point).

    Loads the file via `load_as_gray` (which extracts the embedded JPEG
    thumbnail for RAW formats) and delegates to `_analyze_gray_array`.

    For RAW photos already decoded elsewhere in the pipeline, prefer
    `analyze_exposure_array(rgb_full)` — it avoids re-opening the file
    and re-decoding the embedded JPEG thumbnail (~1–3s per RAF/NEF).
    """
    return _analyze_gray_array(load_as_gray(image_path))


def analyze_exposure_array(rgb: np.ndarray) -> Dict[str, float]:
    """
    Analyze exposure from an already-decoded RGB uint8 array (HxWx3).

    Used by the batch analysis pipeline so the RAW file is opened exactly
    once: `rawpy.imread().postprocess()` produces `rgb_full`, which then
    feeds sharpness, face, IQA, aesthetic, embedding, AND exposure — no
    second `rawpy` call, no second JPEG decode.

    The grayscale conversion uses ITU-R 601 luma weights (0.299/0.587/0.114)
    in float to match `cv2.cvtColor(BGR2GRAY)` exactly. Result is cast back
    to uint8 so clipping checks (`== 255`, `== 0`) match the file-path path.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"analyze_exposure_array expects HxWx3 RGB, got shape {rgb.shape}")
    # ITU-R 601 luma — same coefficients OpenCV uses for BGR2GRAY.
    # Compute in float32 to dodge uint8 overflow during the multiply-add,
    # then round and cast back so == 255 / == 0 checks behave identically.
    gray = (
        0.114 * rgb[..., 2].astype(np.float32) +   # B (rgb[2] in RGB layout)
        0.587 * rgb[..., 1].astype(np.float32) +   # G
        0.299 * rgb[..., 0].astype(np.float32)     # R
    )
    gray = np.clip(np.round(gray), 0, 255).astype(np.uint8)
    return _analyze_gray_array(gray)


def _piecewise_linear(x: float, points: list) -> float:
    """
    Linearly interpolate between control points. Clamps to endpoints outside range.
    points: list of (x_val, y_val) sorted ascending by x_val.
    """
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return points[-1][1]


def _brightness_penalty(deviation: float) -> float:
    """
    Continuous piecewise-linear penalty for mean brightness deviation from 128.
    deviation = abs(mean - 128), range 0–128.

    Control points (deviation → penalty):
      0  → 0
      30 → 5
      50 → 18
      80 → 35
      128 → 50
    """
    points = [(0, 0), (30, 5), (50, 18), (80, 35), (128, 50)]
    return _piecewise_linear(deviation, points)


def _clipping_penalty(clip_pct: float, max_penalty: float) -> float:
    """
    Continuous piecewise-linear penalty for clipping percentage.

    Control points (clip_pct → fraction of max_penalty):
      0   → 0
      1   → 0.05  (tiny clip, barely noticeable)
      2   → 0.15
      5   → 0.45
      10  → 0.75
      20+ → 1.0   (fully saturated)

    Scale by max_penalty to get actual deduction.
    Highlights max_penalty=35, shadows max_penalty=28
    (highlights more damaging because detail is unrecoverable).
    """
    fractions = [(0, 0.0), (1, 0.05), (2, 0.15), (5, 0.45), (10, 0.75), (20, 1.0)]
    fraction = _piecewise_linear(clip_pct, fractions)
    return fraction * max_penalty


def _calculate_exposure_score(
    mean: float,
    highlight_clip: float,
    shadow_clip: float
) -> int:
    """
    Calculate an overall exposure quality score (0-100).

    Uses smooth piecewise-linear penalty functions instead of cliff-edge
    brackets, so a tiny change in pixel statistics never causes a large
    score jump.

    Scoring logic:
    - Perfect exposure: mean near 128, minimal clipping → 100
    - Good exposure: mean 90-170, <5% clipping → ~80-97
    - Acceptable: mean 60-200, <10% clipping → ~60-80
    - Poor: extreme mean or heavy clipping → <60

    Note: This score reflects technical correctness only.
    Intentional low-key or high-key shots may score low here
    but will be re-evaluated by the ML model in Phase 3.

    Args:
        mean: Mean brightness value (0-255)
        highlight_clip: Percentage of clipped highlights
        shadow_clip: Percentage of crushed shadows

    Returns:
        Exposure score (0-100)
    """
    deviation = abs(mean - 128)
    penalty = (
        _brightness_penalty(deviation)
        + _clipping_penalty(highlight_clip, max_penalty=35.0)
        + _clipping_penalty(shadow_clip, max_penalty=28.0)
    )
    return max(0, min(100, round(100 - penalty)))


def analyze_histogram(image_path: str) -> Dict:
    """
    Analyze histogram distribution to understand exposure character.

    Args:
        image_path: Path to the image file

    Returns:
        Dictionary with histogram analysis:
        - shadow_pct: Percentage of dark tones
        - midtone_pct: Percentage of middle tones
        - highlight_pct: Percentage of bright tones
        - distribution_type: Classification of exposure style
    """

    img = load_as_gray(image_path)

    # Calculate histogram (256 bins for 0-255)
    histogram = cv2.calcHist([img], [0], None, [256], [0, 256])
    histogram = histogram.flatten()  # Make it a 1D array

    # Split into three tonal regions
    shadows = np.sum(histogram[0:85])       # Dark tones (0-84)
    midtones = np.sum(histogram[85:170])    # Middle grays (85-169)
    highlights = np.sum(histogram[170:256]) # Bright tones (170-255)

    total = shadows + midtones + highlights

    shadow_pct = (shadows / total) * 100
    midtone_pct = (midtones / total) * 100
    highlight_pct = (highlights / total) * 100

    return {
        'shadow_pct': round(shadow_pct, 1),
        'midtone_pct': round(midtone_pct, 1),
        'highlight_pct': round(highlight_pct, 1),
        'distribution_type': _classify_distribution(
            shadow_pct, midtone_pct, highlight_pct
        )
    }


def compute_histogram(rgb: np.ndarray) -> Dict:
    """
    Build a per-channel + luminance histogram from an RGB array.

    Accepts an HxWx3 uint8 numpy array (the decoded image used elsewhere in the
    pipeline). Returns 256-bin counts for R, G, B, and Rec.709 luminance plus
    aggregate clipping percentages so the frontend can render a Lightroom-style
    histogram with on-demand shadow/highlight clipping callouts.

    Two definitions of "clipped" are reported:
      • `visible` — ALL channels at the extreme (with a 2-step JPEG tolerance):
        rgb >= 253 for highlights, rgb <= 2 for shadows. This matches what
        a photographer perceives as "blown" or "crushed" — a pixel of (255,
        255, 250) is light cream, not white. The visual clipping overlay
        (/clipping-mask) uses this exact definition, so the percent shown
        in the panel agrees with the tinted area in the preview.
      • `r/g/b`  — strict per-channel (== 255 or == 0). Useful for diagnosing
        single-channel saturation (e.g. the red channel cooking on a sunset
        when green/blue still hold detail). Shown in the panel's per-channel
        breakdown when a clipping toggle is active.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"compute_histogram expects HxWx3 RGB, got shape {rgb.shape}")

    # Pull channels as 1-D views — bincount is the fastest path to 256 bins
    # for uint8 data and avoids OpenCV's per-call overhead.
    r = rgb[..., 0].ravel()
    g = rgb[..., 1].ravel()
    b = rgb[..., 2].ravel()
    total = r.size

    # Rec.709 luminance: matches what most cameras and editors call "luma".
    # Must round before casting — truncation piles all near-white pixels into
    # bin 254 (0.2126+0.7152+0.0722)*255 = 254.97 → floor → 254, making
    # that single bin dominate maxVal and squashing everything else to flat.
    lum = np.clip(
        np.round(0.2126 * rgb[..., 0].astype(np.float32)
                 + 0.7152 * rgb[..., 1].astype(np.float32)
                 + 0.0722 * rgb[..., 2].astype(np.float32)),
        0, 255,
    ).astype(np.uint8).ravel()

    def _bins(channel):
        # bincount with minlength=256 guarantees a fixed-size 256-bin output
        # even when some bins are empty (e.g. pure-white shot has no bin 0).
        return np.bincount(channel, minlength=256).tolist()

    # Visible (all-channel) — what the overlay tints. Tolerance accommodates
    # JPEG quantization rounding so a "white" pixel saved as (255,255,253)
    # still counts as blown.
    visible_hi = int(np.sum((rgb >= 253).all(axis=2)))
    visible_lo = int(np.sum((rgb <=   2).all(axis=2)))

    return {
        "r":   _bins(r),
        "g":   _bins(g),
        "b":   _bins(b),
        "lum": _bins(lum),
        "total_pixels": int(total),
        "clip_hi": {
            "r":       round(float(np.sum(r == 255)) / total * 100, 3),
            "g":       round(float(np.sum(g == 255)) / total * 100, 3),
            "b":       round(float(np.sum(b == 255)) / total * 100, 3),
            "visible": round(visible_hi / total * 100, 3),
        },
        "clip_lo": {
            "r":       round(float(np.sum(r == 0)) / total * 100, 3),
            "g":       round(float(np.sum(g == 0)) / total * 100, 3),
            "b":       round(float(np.sum(b == 0)) / total * 100, 3),
            "visible": round(visible_lo / total * 100, 3),
        },
    }


def _classify_distribution(shadows: float, mids: float, highs: float) -> str:
    """
    Classify the type of exposure based on tonal distribution.

    Args:
        shadows: Percentage of shadow tones
        mids: Percentage of midtones
        highs: Percentage of highlight tones

    Returns:
        Description of tonal distribution style
    """

    if mids > 50:
        return "Balanced - Good midtone distribution"
    elif shadows > 50:
        return "Low-key - Predominantly dark tones"
    elif highs > 50:
        return "High-key - Predominantly bright tones"
    else:
        return "Mixed - Varied tonal distribution"


# Usage: python exposure.py <image_path_or_folder>
if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("Usage: python exposure.py <image_path_or_folder>")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_dir():
        extensions = {'.jpg', '.jpeg', '.png', '.raf', '.nef'}
        images = [f for f in target.iterdir() if f.suffix.lower() in extensions]
        images.sort()
    else:
        images = [target]

    for path in images:
        result = analyze_exposure(str(path))
        hist = analyze_histogram(str(path))

        print(f"\n📊 {path.name}")
        print(f"   Brightness (mean):      {result['mean_brightness']} / 255")
        print(f"   Contrast (std):         {result['std_brightness']}")
        print(f"   Highlight Clipping:     {result['highlight_clip_pct']:.1f}%")
        print(f"   Shadow Clipping:        {result['shadow_clip_pct']:.1f}%")
        print(f"   Score:                  {result['exposure_score']} / 100")
        print(f"   ⚠️  Warning:             {result['exposure_warning']}")
        print(f"   🎨 Intentional?         {'Possibly yes' if result['is_likely_intentional'] else 'Probably not'}")
        print(f"   Tonal Distribution:     {hist['distribution_type']}")
        print(f"   Shadows / Mids / Highs: {hist['shadow_pct']}% / {hist['midtone_pct']}% / {hist['highlight_pct']}%")
        print(f"   {'─'*50}")