"""
Camera Shake Detection Module
Combines two complementary approaches:

1. EXIF-based risk assessment (fast, preventive)
   - Applies the 1/focal_length rule
   - Accounts for IBIS per camera body
   - Runs before pixel analysis as a quick filter

2. Pixel-based blur measurement (slower, definitive)
   - Gradient direction consistency (directional blur detection)
   - FFT power spectrum elongation (frequency domain analysis)
   - Panning shot detection (intentional horizontal motion)

Combined output gives both a risk prediction AND a measured result.
In Phase 3, the ML model can learn when EXIF and pixels disagree.
"""

import cv2
import numpy as np
import exifread
from pathlib import Path
from typing import Dict, Optional, Tuple

from .utils import load_as_gray


# Per-camera IBIS configuration (stops of stabilization)
CAMERA_IBIS = {
    "X100VI": 4,   # Fujifilm X100VI — IBIS
    "X Half":  0,  # Fujifilm X Half — no IBIS
    "Z6III":   5,  # Nikon Z6III — IBIS
}


# ─────────────────────────────────────────────
# EXIF helpers
# ─────────────────────────────────────────────

def _parse_fraction(tag_value) -> Optional[float]:
    """Convert EXIF fraction string like '1/250' to float 0.004."""
    value = str(tag_value).strip()
    if "/" in value:
        parts = value.split("/")
        try:
            num = float(parts[0])
            den = float(parts[1])
            return num / den if den != 0 else None
        except (ValueError, IndexError):
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _get_camera_name(tags: dict) -> Optional[str]:
    """Match EXIF model tag against known cameras."""
    model_tag = tags.get("Image Model")
    if not model_tag:
        return None
    model = str(model_tag).strip()
    if "X100VI" in model:
        return "X100VI"
    if "X-H" in model or "X Half" in model:
        return "X Half"
    if "Z 6" in model or "Z6" in model:
        return "Z6III"
    return model


def _analyze_exif(image_path: str) -> dict:
    """
    Extract shake-relevant EXIF data and apply the 1/focal_length rule.

    Returns a dict with:
    - shutter_speed, focal_length, iso, camera, ibis_stops
    - safe_threshold: slowest safe shutter speed (IBIS-adjusted)
    - exif_risk: "low", "moderate", "high", or "unknown"
    """
    with open(image_path, "rb") as f:
        tags = exifread.process_file(f, stop_tag="EXIF ISOSpeedRatings")

    shutter_tag = tags.get("EXIF ExposureTime")
    shutter_speed = _parse_fraction(shutter_tag) if shutter_tag else None
    shutter_speed_str = str(shutter_tag) + "s" if shutter_tag else "unknown"

    focal_tag = tags.get("EXIF FocalLength")
    focal_length = _parse_fraction(focal_tag) if focal_tag else None

    iso_tag = tags.get("EXIF ISOSpeedRatings")
    try:
        iso = int(str(iso_tag)) if iso_tag else None
    except ValueError:
        iso = None

    camera = _get_camera_name(tags)
    ibis_stops = CAMERA_IBIS.get(camera, 0)

    # IBIS-adjusted safe threshold
    # Each stop doubles the safe exposure time: 4 stops = 16x longer
    if focal_length and focal_length > 0:
        base_threshold = 1.0 / focal_length
        safe_threshold = base_threshold * (2 ** ibis_stops)
    else:
        safe_threshold = None

    # Determine EXIF risk
    if shutter_speed is None:
        exif_risk = "unknown"
    elif safe_threshold is None:
        exif_risk = "moderate" if shutter_speed < 1 / 60 else "low"
    else:
        # Direct comparison — no ratio confusion
        # shutter_speed >= safe_threshold means fast enough → low risk
        # shutter_speed < safe_threshold means too slow → risky
        # Example: 1/500s (0.002) vs safe 1/4329s (0.00023) → 0.002 > 0.00023 → LOW
        # Example: 1/10s  (0.1)   vs safe 1/50s  (0.02)     → 0.1   > 0.02    → LOW (wait, also wrong)
        # Simpler mental model: a LARGER float = SLOWER shutter
        # safe_threshold is the SLOWEST acceptable speed (largest acceptable float)
        # if shutter_speed (float) <= safe_threshold → fast enough → low risk
        if shutter_speed <= safe_threshold:
            exif_risk = "low"
        elif shutter_speed <= safe_threshold * 2:
            exif_risk = "moderate"
        else:
            exif_risk = "high"

    # High ISO upgrades risk (difficult light = more likely to shake)
    if iso and iso > 6400 and exif_risk == "low":
        exif_risk = "moderate"

    return {
        "shutter_speed": round(shutter_speed, 6) if shutter_speed else None,
        "shutter_speed_str": shutter_speed_str,
        "focal_length": focal_length,
        "iso": iso,
        "camera": camera,
        "ibis_stops": ibis_stops,
        "safe_threshold": round(safe_threshold, 6) if safe_threshold else None,
        "exif_risk": exif_risk,
    }


# ─────────────────────────────────────────────
# Pixel analysis (from Claude Code)
# ─────────────────────────────────────────────

def _analyze_gradient_directions(gray: np.ndarray) -> Tuple[float, float]:
    """
    Measure how consistently image gradients point in one direction.
    Camera shake causes edges to cluster in the blur direction.

    Returns:
        (direction_consistency, motion_angle_degrees)
        consistency: 0=random directions, 1=perfectly uniform (= severe shake)
        angle: 0-180 degrees, estimated camera motion direction
    """
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.sqrt(gx ** 2 + gy ** 2)

    # Only strong edges (top 20%) to avoid noise
    threshold = np.percentile(magnitude, 80)
    strong_mask = magnitude > threshold

    if strong_mask.sum() < 100:
        return 0.0, 0.0

    angles = np.arctan2(np.abs(gy[strong_mask]), gx[strong_mask]) * 180 / np.pi

    # Circular stats in double-angle space (handles 0/180 wrap-around)
    angles_rad = np.deg2rad(angles * 2)
    mean_cos = np.mean(np.cos(angles_rad))
    mean_sin = np.mean(np.sin(angles_rad))

    consistency = float(np.sqrt(mean_cos ** 2 + mean_sin ** 2))
    dominant_angle = float(np.rad2deg(np.arctan2(mean_sin, mean_cos)) / 2)
    if dominant_angle < 0:
        dominant_angle += 180

    motion_angle = (dominant_angle + 90) % 180
    return consistency, motion_angle


def _analyze_fft_elongation(gray: np.ndarray) -> float:
    """
    Measure elongation of the FFT power spectrum.
    Motion blur creates a band-like pattern in the frequency domain.

    Returns:
        Elongation ratio >= 1.0 (1.0 = no directional blur)
    """
    h, w = gray.shape
    crop_h, crop_w = min(h, 512), min(w, 512)
    y0 = (h - crop_h) // 2
    x0 = (w - crop_w) // 2
    crop = gray[y0:y0 + crop_h, x0:x0 + crop_w].astype(np.float32)

    window = np.outer(np.hanning(crop_h), np.hanning(crop_w))
    fft_shift = np.fft.fftshift(np.fft.fft2(crop * window))
    power = np.log1p(np.abs(fft_shift) ** 2)

    # Zero out DC component
    cy, cx = power.shape[0] // 2, power.shape[1] // 2
    power[cy - 5:cy + 5, cx - 5:cx + 5] = 0

    y_coords = (np.mgrid[0:power.shape[0], 0:power.shape[1]][0] - cy).astype(np.float64)
    x_coords = (np.mgrid[0:power.shape[0], 0:power.shape[1]][1] - cx).astype(np.float64)

    total = power.sum()
    if total == 0:
        return 1.0

    mxx = float((power * x_coords ** 2).sum() / total)
    myy = float((power * y_coords ** 2).sum() / total)
    mxy = float((power * x_coords * y_coords).sum() / total)

    trace = mxx + myy
    det = mxx * myy - mxy ** 2
    discriminant = max(0.0, (trace / 2) ** 2 - det)
    lambda1 = trace / 2 + np.sqrt(discriminant)
    lambda2 = trace / 2 - np.sqrt(discriminant)

    return float(lambda1 / lambda2) if lambda2 > 0 else 1.0


def _calculate_shake_score(consistency: float, fft_elongation: float) -> int:
    """Pixel-based shake quality score (0-100, 100 = no shake)."""
    score = 100
    if consistency > 0.7:
        score -= 50
    elif consistency > 0.5:
        score -= 35
    elif consistency > 0.3:
        score -= 15

    if fft_elongation > 3.0:
        score -= 30
    elif fft_elongation > 2.0:
        score -= 20
    elif fft_elongation > 1.5:
        score -= 10

    return max(0, min(100, score))


# ─────────────────────────────────────────────
# Combined analysis — main entry point
# ─────────────────────────────────────────────

def analyze_camera_shake(image_path: str) -> Dict:
    """
    Full camera shake analysis: EXIF prediction + pixel measurement.

    Args:
        image_path: Path to image file (JPEG, RAF, NEF)

    Returns:
        Combined dictionary with all shake metrics.
        Key fields:
        - exif_risk:     "low/moderate/high" — predicted from metadata
        - shake_score:   0-100 — measured from pixels
        - shake_warning: "ok/slight_shake/shake/severe_shake"
        - is_likely_intentional: True if horizontal panning shot detected
        - conflict: True if EXIF and pixels disagree (useful for Phase 3 ML)
    """
    # 1. EXIF analysis (fast)
    exif = _analyze_exif(image_path)

    # 2. Pixel analysis (slower but definitive)
    img = load_as_gray(image_path)
    consistency, motion_angle = _analyze_gradient_directions(img)
    fft_elongation = _analyze_fft_elongation(img)
    shake_score = _calculate_shake_score(consistency, fft_elongation)

    # 3. Pixel-based warning
    if consistency > 0.7 or fft_elongation > 3.0:
        shake_warning = "severe_shake"
    elif consistency > 0.5 or fft_elongation > 2.0:
        shake_warning = "shake"
    elif consistency > 0.3 or fft_elongation > 1.5:
        shake_warning = "slight_shake"
    else:
        shake_warning = "ok"

    # 4. Intentionality: horizontal blur at moderate level = likely panning shot
    horizontal_blur = motion_angle < 20 or motion_angle > 160
    moderate_shake = 0.3 < consistency < 0.65
    is_likely_intentional = horizontal_blur and moderate_shake

    # 5. Conflict detection — valuable signal for Phase 3 ML
    # EXIF says "high" but pixels show no blur → IBIS worked perfectly
    # EXIF says "low" but pixels show blur → something else caused it
    pixel_risk = (
        "high" if shake_warning in ("shake", "severe_shake")
        else "moderate" if shake_warning == "slight_shake"
        else "low"
    )
    conflict = exif["exif_risk"] != pixel_risk and exif["exif_risk"] != "unknown"

    return {
        # EXIF side
        "shutter_speed": exif["shutter_speed"],
        "shutter_speed_str": exif["shutter_speed_str"],
        "focal_length": exif["focal_length"],
        "iso": exif["iso"],
        "camera": exif["camera"],
        "ibis_stops": exif["ibis_stops"],
        "safe_threshold": exif["safe_threshold"],
        "exif_risk": exif["exif_risk"],

        # Pixel side
        "direction_consistency": round(consistency, 3),
        "fft_elongation": round(fft_elongation, 2),
        "motion_angle_deg": round(motion_angle, 1),
        "shake_score": shake_score,
        "shake_warning": shake_warning,
        "is_likely_intentional": is_likely_intentional,

        # Combined signal
        "conflict": conflict,
    }


# ─────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python camera_shake.py <image_path_or_folder>")
        sys.exit(1)

    target = Path(sys.argv[1])
    images = (
        sorted(f for f in target.iterdir() if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".raf", ".nef"})
        if target.is_dir() else [target]
    )

    for path in images:
        r = analyze_camera_shake(str(path))

        risk_icon  = {"low": "✅", "moderate": "⚠️", "high": "🔴"}.get(r["exif_risk"], "❓")
        pixel_icon = {"ok": "✅", "slight_shake": "⚠️", "shake": "🔴", "severe_shake": "🔴"}.get(r["shake_warning"], "❓")

        print(f"\n📷 {path.name}")
        print(f"   Camera:            {r['camera'] or 'unknown'}  ({r['ibis_stops']} stops IBIS)")
        print(f"   Shutter speed:     {r['shutter_speed_str']}  |  Focal length: {r['focal_length']}mm  |  ISO: {r['iso']}")
        if r["safe_threshold"]:
            print(f"   Safe threshold:    1/{round(1/r['safe_threshold'])}s")
        print(f"   EXIF risk:         {risk_icon}  {r['exif_risk'].upper()}")
        print(f"   Pixel measurement: {pixel_icon}  {r['shake_warning']}  (Score: {r['shake_score']}/100)")
        print(f"   Motion angle:      {r['motion_angle_deg']}°")
        print(f"   🎨 Intentional?    {'Possibly yes (panning?)' if r['is_likely_intentional'] else 'Probably not'}")
        if r["conflict"]:
            print(f"   ⚡ CONFLICT:       EXIF and pixels disagree → interesting for Phase 3")
        print(f"   {'─' * 55}")
