# edge_sharpness.py
# Noise-robust, contrast-independent sharpness for the Photos corpus.
#
# Why this exists (2026-09-19): batch_sharpness_analyzer measures gradient
# *energy* (Laplacian variance / Tenengrad). Energy cannot tell an edge from
# sensor grain, so a blurred iPhone frame at ISO 1000 (noise σ≈6.6) scored 100:
# its face crop read 877 Laplacian variance against a pure-noise floor of ~867.
# A synthetic blur ladder reproduced it — σ=2 px of blur scored 5 clean, 97 with
# σ=6 noise, 100 with σ=10.
#
# This measures edge *width* instead. A step edge blurred by σ_b has peak
# gradient C / (√(2π)·σ_b), so width = contrast / (2.507 · peak gradient)
# recovers σ_b whatever the contrast or noise level. Measured on the same
# ladder: width stays flat under noise and rises monotonically with blur
# (1.25 → 2.82 px for σ_b 0 → 3 px at 1600 px long side).
#
# Deliberately Photos-only: the folder corpus's thresholds are user-tuned on the
# old scale and its labelled images are no longer on disk to re-validate against.

from typing import Optional

import cv2
import numpy as np

# All widths are quoted at this long side, so a 12 MP and a 48 MP HEIC are
# comparable. Area-averaging on the way down also suppresses grain.
_WORK_LONG_SIDE = 1600

_SMOOTH_SIGMA = 1.0          # pre-blur before the gradient; kills pixel-level grain
_PROFILE_REACH = 5           # px either side of the edge searched for its contrast
# An edge pixel must clear the frame's own noise gradient. The median gradient
# magnitude is dominated by flat noise, so it is a robust noise-floor probe.
_NOISE_FLOOR_K = 6.6
_MIN_CONTRAST = 18.0         # gray levels; below this an "edge" is texture, not structure

_TILE_GRID = (16, 12)        # rows × cols, for a portrait frame (transposed for landscape)
_MIN_EDGES_PER_TILE = 30
_MIN_TILES = 4
_MIN_EDGES_GLOBAL = 300
# Sharpest-tiles aggregate — same idea as the legacy p90-of-tiles: shallow depth
# of field leaves most of the frame intentionally soft, the subject decides.
_TILE_PERCENTILE = 20.0
_MIN_FACE_EDGES = 40

# Skin is intrinsically softer than the frame's sharpest tiles: median face
# width minus median global width across 962 Photos faces was 0.138 px. Removing
# it lets min(global, face) mean "the face is softer than it should be" rather
# than penalising every portrait.
_FACE_WIDTH_OFFSET = 0.14

# width (px @1600) → score. Anchored on the blur ladder — a clean in-camera
# sharp frame sits at 1.15–1.3 px, 1 px of added blur (w≈1.5) is visible at 100 %
# but fine at fit-to-screen, 1.5 px (w≈1.85) is soft at fit-to-screen. Keeps the
# legacy label bands: ≥70 Sharp, 40–70 Borderline, <40 Blurry.
_WIDTH_ANCHORS = (1.10, 1.20, 1.37, 1.75, 2.20, 2.80)
_SCORE_ANCHORS = (100.0, 90.0, 70.0, 40.0, 15.0, 0.0)


def width_to_score(width_px: float) -> int:
    return int(round(float(np.interp(width_px, _WIDTH_ANCHORS, _SCORE_ANCHORS))))


def _edge_widths(gray: np.ndarray):
    """(ys, xs, widths) of strong non-maximum-suppressed edge pixels."""
    g = cv2.GaussianBlur(gray, (0, 0), _SMOOTH_SIGMA)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    mag = np.hypot(gx, gy)
    floor = float(np.median(mag)) * _NOISE_FLOOR_K + 1e-6
    ux, uy = gx / (mag + 1e-6), gy / (mag + 1e-6)

    h, w = g.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    def along(img, offset):
        return cv2.remap(img, xx + ux * offset, yy + uy * offset,
                         cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # Non-maximum suppression along the gradient direction: one pixel per edge.
    keep = (mag >= along(mag, 1.0)) & (mag >= along(mag, -1.0)) & (mag > floor)
    ys, xs = np.nonzero(keep)
    if ys.size == 0:
        return ys, xs, np.empty(0, np.float32)

    hi = np.max([along(g, o)[ys, xs] for o in range(1, _PROFILE_REACH + 1)], axis=0)
    lo = np.min([along(g, -o)[ys, xs] for o in range(1, _PROFILE_REACH + 1)], axis=0)
    contrast = np.abs(hi - lo)
    widths = contrast / (2.507 * mag[ys, xs] + 1e-6)
    ok = contrast > _MIN_CONTRAST
    return ys[ok], xs[ok], widths[ok]


def _global_width(ys, xs, widths, shape) -> Optional[float]:
    """Sharpest-tiles aggregate; falls back to the plain median on sparse frames."""
    h, w = shape
    rows, cols = _TILE_GRID if h >= w else _TILE_GRID[::-1]
    ty = np.minimum(ys * rows // h, rows - 1)
    tx = np.minimum(xs * cols // w, cols - 1)
    tile_id = ty * cols + tx
    medians = []
    for t in np.unique(tile_id):
        sel = tile_id == t
        if sel.sum() >= _MIN_EDGES_PER_TILE:
            medians.append(np.median(widths[sel]))
    if len(medians) >= _MIN_TILES:
        return float(np.percentile(medians, _TILE_PERCENTILE))
    if widths.size >= _MIN_EDGES_GLOBAL:
        return float(np.median(widths))
    return None


def analyze_edge_sharpness(rgb: np.ndarray, face_bbox: Optional[tuple] = None) -> Optional[dict]:
    """
    Sharpness of an RGB frame from edge width. `face_bbox` is (x, y, w, h) in the
    coordinates of `rgb`; when the face carries enough edges, the softer of
    (frame, face) decides so a sharp background cannot rescue a soft face.

    Returns None when the frame has too few edges to judge (flat, dark, or
    featureless) — the caller must fall back rather than invent a score.
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h0, w0 = gray.shape
    scale = _WORK_LONG_SIDE / max(h0, w0)
    if scale < 1.0:
        gray = cv2.resize(gray, (round(w0 * scale), round(h0 * scale)), interpolation=cv2.INTER_AREA)
    else:
        scale = 1.0
    gray = gray.astype(np.float32)

    ys, xs, widths = _edge_widths(gray)
    frame_width = _global_width(ys, xs, widths, gray.shape)
    if frame_width is None:
        return None

    face_width = None
    if face_bbox:
        fx, fy, fw, fh = (v * scale for v in face_bbox)
        inside = (xs >= fx) & (xs < fx + fw) & (ys >= fy) & (ys < fy + fh)
        if inside.sum() >= _MIN_FACE_EDGES:
            face_width = float(np.median(widths[inside])) - _FACE_WIDTH_OFFSET

    effective = frame_width if face_width is None else max(frame_width, face_width)
    score = width_to_score(effective)
    return {
        'sharpness_score': score,
        # Calibrated on the same scale as the frame score (the skin offset is
        # already applied), so "face 30 / frame 80" compares like with like and
        # the soft-face threshold reads in the same units as every other one.
        'face_sharpness_score': None if face_width is None else width_to_score(face_width),
        'frame_sharpness_score': width_to_score(frame_width),
        'frame_width_px': round(frame_width, 3),
        'face_width_px': None if face_width is None else round(face_width + _FACE_WIDTH_OFFSET, 3),
        'n_edges': int(widths.size),
        'sharpness_label': 'Sharp' if score >= 70 else 'Borderline' if score >= 40 else 'Blurry',
    }
