"""Edge-width sharpness (Photos corpus).

The regression that motivated it: the legacy gradient-energy metric read sensor
grain as sharpness, so a clearly blurred ISO-1000 frame scored 100. Every test
here is built from a synthetic frame so the blur and the noise are known
exactly, rather than from a photo whose "true" sharpness is a judgement call.
"""
import ast
from pathlib import Path

import cv2
import numpy as np
import pytest

from phase1_technical import edge_sharpness as es
from phase1_technical.edge_sharpness import analyze_edge_sharpness, width_to_score

ROOT = Path(__file__).resolve().parent.parent


def _scene(seed: int = 0, h: int = 1200, w: int = 1600) -> np.ndarray:
    """Random overlapping rectangles: plenty of strong, known-step edges."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 128, np.float32)
    for _ in range(260):
        x0, y0 = rng.integers(0, w - 40), rng.integers(0, h - 40)
        x1, y1 = x0 + rng.integers(30, 300), y0 + rng.integers(30, 300)
        img[y0:y1, x0:x1] = rng.integers(20, 235)
    return img


def _rgb(gray: np.ndarray) -> np.ndarray:
    g = np.clip(gray, 0, 255).astype(np.uint8)
    return np.stack([g, g, g], axis=-1)


def _blurred(sigma: float, seed: int = 0) -> np.ndarray:
    g = _scene(seed)
    return cv2.GaussianBlur(g, (0, 0), sigma) if sigma else g


def test_score_falls_monotonically_with_blur():
    scores = [analyze_edge_sharpness(_rgb(_blurred(s)))['sharpness_score'] for s in (0, 1.0, 2.0, 3.0)]
    assert scores == sorted(scores, reverse=True), scores
    assert scores[0] - scores[-1] >= 40, scores


def test_sensor_noise_does_not_rescue_a_blurred_frame():
    """The IMG_2332 failure: blur σ=2 + grain σ=8 read as sharp under the old metric."""
    rng = np.random.default_rng(1)
    clean = _blurred(2.0)
    noisy = clean + rng.normal(0, 8.0, clean.shape)
    s_clean = analyze_edge_sharpness(_rgb(clean))['sharpness_score']
    s_noisy = analyze_edge_sharpness(_rgb(noisy))['sharpness_score']
    assert s_noisy < 50, s_noisy
    assert abs(s_noisy - s_clean) <= 8, (s_clean, s_noisy)


def test_noise_does_not_penalise_a_sharp_frame_either():
    rng = np.random.default_rng(2)
    sharp = _scene()
    s = analyze_edge_sharpness(_rgb(sharp + rng.normal(0, 8.0, sharp.shape)))['sharpness_score']
    assert s >= 70, s


def test_result_is_independent_of_source_resolution():
    """Widths are quoted at a fixed long side, so a 2× frame of the same scene agrees."""
    small = _blurred(1.0)
    big = cv2.resize(small, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    a = analyze_edge_sharpness(_rgb(small))['frame_width_px']
    b = analyze_edge_sharpness(_rgb(big))['frame_width_px']
    assert abs(a - b) < 0.2, (a, b)


def test_soft_face_is_not_rescued_by_a_sharp_background():
    g = _scene()
    box = (600, 400, 400, 400)  # x, y, w, h
    x, y, w, h = box
    soft = g.copy()
    soft[y:y + h, x:x + w] = cv2.GaussianBlur(g, (0, 0), 3.0)[y:y + h, x:x + w]
    without = analyze_edge_sharpness(_rgb(soft))
    with_face = analyze_edge_sharpness(_rgb(soft), face_bbox=box)
    assert with_face['face_width_px'] is not None
    assert with_face['sharpness_score'] < without['sharpness_score'] - 15, (without, with_face)


def test_sharp_face_leaves_the_score_alone():
    g = _scene()
    box = (600, 400, 400, 400)
    a = analyze_edge_sharpness(_rgb(g))['sharpness_score']
    b = analyze_edge_sharpness(_rgb(g), face_bbox=box)['sharpness_score']
    assert abs(a - b) <= 5, (a, b)


def test_face_bbox_is_rescaled_with_the_frame():
    """The bbox arrives in source pixels; the frame is downscaled to 1600 px first."""
    g = _scene(h=2400, w=3200)  # 2× the working size
    box = (1200, 800, 800, 800)
    soft = g.copy()
    x, y, w, h = box
    soft[y:y + h, x:x + w] = cv2.GaussianBlur(g, (0, 0), 6.0)[y:y + h, x:x + w]
    r = analyze_edge_sharpness(_rgb(soft), face_bbox=box)
    assert r['face_width_px'] is not None and r['face_width_px'] > 1.8, r


def test_featureless_frame_returns_none_not_a_score():
    flat = np.full((1200, 1600), 90, np.float32)
    grain = flat + np.random.default_rng(3).normal(0, 6.0, flat.shape)
    assert analyze_edge_sharpness(_rgb(flat)) is None
    assert analyze_edge_sharpness(_rgb(grain)) is None


def test_width_to_score_is_monotone_and_holds_the_label_bands():
    widths = np.linspace(0.9, 3.2, 60)
    scores = [width_to_score(w) for w in widths]
    assert scores == sorted(scores, reverse=True)
    assert width_to_score(0.9) == 100 and width_to_score(3.2) == 0
    assert width_to_score(1.37) == 70   # Sharp / Borderline edge
    assert width_to_score(1.75) == 40   # Borderline / Blurry edge


def test_anchor_tables_line_up():
    assert len(es._WIDTH_ANCHORS) == len(es._SCORE_ANCHORS)
    assert list(es._WIDTH_ANCHORS) == sorted(es._WIDTH_ANCHORS)


def test_photos_falls_back_to_legacy_on_a_featureless_frame():
    """No edges to judge → keep the legacy number rather than invent one."""
    from phase1_technical.quality_analyzer import _apply_edge_sharpness
    legacy = {'sharpness_score': 42, 'sharpness_label': 'Borderline'}
    out = _apply_edge_sharpness(None, "photos", {}, legacy, "unused.heic", (1200, 1600))
    assert out is legacy


def test_photos_scores_by_edge_width_when_there_are_edges():
    from phase1_technical.quality_analyzer import _apply_edge_sharpness, measure_edge_sharpness
    rgb = _rgb(_blurred(3.0))
    edge = measure_edge_sharpness(rgb, {})
    legacy = {'sharpness_score': 100, 'sharpness_label': 'Sharp'}
    out = _apply_edge_sharpness(edge, "photos", {}, legacy, "unused.heic", rgb.shape[:2])
    assert out['sharpness_score'] < 40
    assert out['sharpness_label'] == 'Blurry'
    assert out['metric'] == 'edge_width'


def test_folder_keeps_the_legacy_score_untouched():
    """The whole point of the corpus split: folder's tuned scale must not move."""
    from phase1_technical.quality_analyzer import _apply_edge_sharpness, measure_edge_sharpness
    rgb = _rgb(_blurred(3.0))
    edge = measure_edge_sharpness(rgb, {})
    legacy = {'sharpness_score': 100, 'sharpness_label': 'Sharp'}
    face = {'face_sharpness_score': 877.5}
    out = _apply_edge_sharpness(edge, "folder", face, legacy, "unused.raf", rgb.shape[:2])
    assert out is legacy
    assert face['face_sharpness_score'] == 877.5


def test_photos_face_sharpness_is_replaced_on_the_frame_scale():
    """face_sharpness_score must stop being Laplacian variance for Photos —
    it feeds the soft-face reject and the GBR, and variance reads grain."""
    from phase1_technical.quality_analyzer import _apply_edge_sharpness, measure_edge_sharpness
    g = _scene()
    box = (600, 400, 400, 400)
    x, y, w, h = box
    soft = g.copy()
    soft[y:y + h, x:x + w] = cv2.GaussianBlur(g, (0, 0), 3.0)[y:y + h, x:x + w]
    rgb = _rgb(soft)
    face = {'face_detected': True, 'face_bbox': box, 'face_sharpness_score': 877.5}
    edge = measure_edge_sharpness(rgb, face)
    _apply_edge_sharpness(edge, "photos", face, None, "unused.heic", rgb.shape[:2])
    assert 0 <= face['face_sharpness_score'] <= 100
    assert face['face_sharpness_score'] < 40  # the crop really is soft


def test_face_score_survives_a_frame_with_no_measurable_face():
    from phase1_technical.quality_analyzer import _apply_edge_sharpness, measure_edge_sharpness
    rgb = _rgb(_scene())
    face = {'face_detected': False, 'face_bbox': None, 'face_sharpness_score': None}
    edge = measure_edge_sharpness(rgb, face)
    assert edge['face_sharpness_score'] is None
    _apply_edge_sharpness(edge, "photos", face, None, "unused.heic", rgb.shape[:2])
    assert face['face_sharpness_score'] is None


def test_batch_passes_the_corpus_to_the_analyzer():
    """Call-site guard: without this the new metric is unit-tested but never runs."""
    tree = ast.parse((ROOT / "backend/routers/analysis.py").read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "analyze_photo_quality"
             and any(k.arg == "sharpness_weight" for k in n.keywords)]  # the batch call
    assert calls, "batch call to analyze_photo_quality not found"
    for call in calls:
        assert "corpus" in {k.arg for k in call.keywords}
