"""`analyze_faces_array` must report its bbox in the CALLER's pixels.

It downscales to `_FACE_MAX_SIDE` before running MediaPipe, and for months it
returned the bbox in that downscaled frame while every caller cropped the
full-resolution array it had passed in (`_embed_face`, and later the edge-width
face region). On a 4032 px RAW the bbox came back at 0.496× its true position,
so the face identity embedding cropped whatever happened to sit in the
top-left quadrant.

No face model runs here: detection is stubbed, so the test is about the
coordinate contract and nothing else.
"""
import numpy as np
import pytest

from phase2_quality import face_analyzer


class _FakeLandmark:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _FakeResult:
    """A single face occupying the middle-right of the frame."""
    def __init__(self):
        self.face_landmarks = [[
            _FakeLandmark(0.60, 0.40), _FakeLandmark(0.80, 0.40),
            _FakeLandmark(0.60, 0.60), _FakeLandmark(0.80, 0.60),
        ]]
        self.face_blendshapes = []


@pytest.fixture
def stub_detector(monkeypatch):
    monkeypatch.setattr(face_analyzer, "_ensure_model", lambda: "unused.task")
    monkeypatch.setattr(face_analyzer, "_run_detection",
                        lambda rgb, model, conf: _FakeResult())


def _frame(long_side: int) -> np.ndarray:
    h = long_side
    w = int(long_side * 0.75)
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def test_bbox_is_returned_in_caller_pixels_not_detector_pixels(stub_detector):
    rgb = _frame(4032)                     # > _FACE_MAX_SIDE, so it gets downscaled
    result = face_analyzer.analyze_faces_array(rgb)
    x, y, w, h = result["face_bbox"]
    height, width = rgb.shape[:2]

    # The stub puts the face at 60–80 % across and 40–60 % down, plus 10 % padding.
    assert 0.55 * width < x < 0.62 * width, (x, width)
    assert 0.35 * height < y < 0.42 * height, (y, height)
    assert x + w <= width and y + h <= height


def test_bbox_matches_the_untouched_path_for_a_small_frame(stub_detector):
    """A frame under the detector limit is not resized, so both agree."""
    small = _frame(1200)
    box = face_analyzer.analyze_faces_array(small)["face_bbox"]
    height, width = small.shape[:2]
    assert 0.55 * width < box[0] < 0.62 * width
    assert 0.35 * height < box[1] < 0.42 * height


def test_size_and_offset_ratios_are_scale_free(stub_detector):
    """Ratios were always right; the fix must not move them."""
    big = face_analyzer.analyze_faces_array(_frame(4032))
    small = face_analyzer.analyze_faces_array(_frame(1200))
    assert big["face_size_ratio"] == pytest.approx(small["face_size_ratio"], abs=0.002)
    assert big["face_center_offset_x"] == pytest.approx(small["face_center_offset_x"], abs=0.01)
    assert big["face_center_offset_y"] == pytest.approx(small["face_center_offset_y"], abs=0.01)


def test_blazeface_fallback_bbox_is_also_rescaled(monkeypatch):
    monkeypatch.setattr(face_analyzer, "_ensure_model", lambda: "unused.task")
    monkeypatch.setattr(face_analyzer, "_run_detection",
                        lambda rgb, model, conf: type("R", (), {"face_landmarks": []})())
    # BlazeFace reports in the frame it was handed — the downscaled one.
    monkeypatch.setattr(face_analyzer, "_blaze_face_detect",
                        lambda rgb, w, h: (int(w * 0.6), int(h * 0.4), int(w * 0.2), int(h * 0.2), 1))
    rgb = _frame(4032)
    x, y, w, h = face_analyzer.analyze_faces_array(rgb)["face_bbox"]
    height, width = rgb.shape[:2]
    assert 0.58 * width < x < 0.62 * width, (x, width)
    assert 0.38 * height < y < 0.42 * height, (y, height)
