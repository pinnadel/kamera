"""
Phase 2 Face Analyzer
Extracts face quality signals using MediaPipe FaceLandmarker (Tasks API, 478 landmarks).

Signals returned per photo:
  - face_count / face_detected
  - face_sharpness_score  — Laplacian variance on face crop only
  - eyes_open             — True/False blink detection via blendshape coefficients
  - eye_openness_ratio    — 0.0 (closed) → 1.0 (fully open), mean of both eyes
  - face_size_ratio       — face bounding box area as fraction of total image area
  - face_center_offset_x/y — how far the primary face is from image center
                             (0,0 = dead center; ±1.0 = at image edge)

When no face is detected every signal value is None. The quality scorer must
skip all face signals rather than penalising non-portrait shots.

Model: MediaPipe face_landmarker.task (downloaded on first use to data/models/).
"""

import os
import sys
import urllib.request
import cv2
import numpy as np
import rawpy
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from pathlib import Path
from typing import Optional
from contextlib import contextmanager


@contextmanager
def _suppress_stderr():
    """Suppress C-level stderr during MediaPipe FaceLandmarker initialisation.

    MediaPipe 0.10+ writes W0000/I0000 messages directly to file descriptor 2
    from the TFLite C++ runtime. Redirecting Python's sys.stderr has no effect.
    We duplicate the real fd, point fd 2 at /dev/null for the block, then
    restore. Actual errors still propagate normally as Python exceptions.
    """
    saved_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)
    os.close(devnull_fd)
    try:
        yield
    finally:
        os.dup2(saved_fd, 2)
        os.close(saved_fd)

# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
_LANDMARKER_PATH = Path(__file__).parent.parent / "data" / "models" / "face_landmarker.task"

_DETECTOR_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
_DETECTOR_PATH = Path(__file__).parent.parent / "data" / "models" / "blaze_face_short_range.tflite"


def _ensure_model() -> Path:
    """Download the FaceLandmarker model on first use. Cached after that."""
    if not _LANDMARKER_PATH.exists():
        _LANDMARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading MediaPipe FaceLandmarker model to {_LANDMARKER_PATH} …")
        urllib.request.urlretrieve(_LANDMARKER_URL, _LANDMARKER_PATH)
        print("Download complete.")
    return _LANDMARKER_PATH


def _ensure_detector_model() -> Path:
    """Download the BlazeFace detector model on first use. Cached after that."""
    if not _DETECTOR_PATH.exists():
        _DETECTOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading MediaPipe BlazeFace detector model to {_DETECTOR_PATH} …")
        urllib.request.urlretrieve(_DETECTOR_URL, _DETECTOR_PATH)
        print("Download complete.")
    return _DETECTOR_PATH


# ---------------------------------------------------------------------------
# Blendshape indices for eye blink
# Eye blink blendshapes are 0.0 = open, 1.0 = fully closed.
# We invert to get openness (1.0 = open, 0.0 = closed).
# ---------------------------------------------------------------------------

_BLINK_LEFT  = "eyeBlinkLeft"
_BLINK_RIGHT = "eyeBlinkRight"
_EYE_CLOSED_THRESHOLD = 0.4  # blink score above this = eye closed

# Expression blendshapes — added 2026-05-05 for Session 3a.
_SMILE_LEFT    = "mouthSmileLeft"
_SMILE_RIGHT   = "mouthSmileRight"
_JAW_OPEN      = "jawOpen"

# Detection passes: (confidence, use_clahe)
# Pass 1 — standard confidence, raw image.
# Pass 2 — lower confidence + CLAHE contrast boost for backlit/low-contrast faces.
_DETECTION_PASSES = [
    (0.5, False),
    (0.3, True),
]


_FACE_MAX_SIDE = 2000  # MediaPipe works well up to this; beyond adds latency with no accuracy gain


def analyze_faces_array(rgb: np.ndarray) -> dict:
    """
    Run face analysis on a pre-decoded RGB numpy array (H×W×3, uint8).

    Called by quality_analyzer when the RAW has already been decoded once and
    shared across all scorers — avoids a redundant rawpy.postprocess() call.
    """
    h_orig, w_orig = rgb.shape[:2]

    # Downscale if the image is larger than MediaPipe needs — keeps inference fast
    # regardless of whether the caller passed half-size or full-res data.
    if max(h_orig, w_orig) > _FACE_MAX_SIDE:
        scale = _FACE_MAX_SIDE / max(h_orig, w_orig)
        rgb = cv2.resize(rgb, (int(w_orig * scale), int(h_orig * scale)), interpolation=cv2.INTER_AREA)

    height, width = rgb.shape[:2]
    model_path = _ensure_model()

    mp_result = None
    for confidence, use_clahe in _DETECTION_PASSES:
        detect_rgb = _apply_clahe(rgb) if use_clahe else rgb
        mp_result = _run_detection(detect_rgb, model_path, confidence)
        if mp_result.face_landmarks:
            break

    if mp_result and mp_result.face_landmarks:
        face_count = len(mp_result.face_landmarks)
        primary_idx = _pick_primary_face_index(mp_result.face_landmarks, width, height)
        landmarks = mp_result.face_landmarks[primary_idx]
        bbox = _landmarks_to_bbox(landmarks, width, height, padding=0.10)

        face_size_ratio = round((bbox[2] * bbox[3]) / (width * height), 4)
        face_cx = bbox[0] + bbox[2] / 2
        face_cy = bbox[1] + bbox[3] / 2
        offset_x = round((face_cx - width / 2) / (width / 2), 3)
        offset_y = round((face_cy - height / 2) / (height / 2), 3)

        eyes_open, eye_openness, smile_score, mouth_open_score = _analyze_face_blendshapes(
            mp_result.face_blendshapes[primary_idx] if mp_result.face_blendshapes else None
        )
        face_sharpness = _face_region_sharpness(rgb, bbox)

        # Per-face eye state for ALL detected faces. Used by the
        # `reject_closed_eyes_all_faces` setting so a group photo isn't
        # killed when one person blinks. None when blendshapes unavailable.
        if mp_result.face_blendshapes:
            faces_eyes_open = [
                _analyze_face_blendshapes(bs)[0]
                for bs in mp_result.face_blendshapes
            ]
        else:
            faces_eyes_open = None

        return {
            'face_detected': True,
            'face_count': face_count,
            'face_sharpness_score': face_sharpness,
            'eyes_open': eyes_open,
            'eye_openness_ratio': round(eye_openness, 3),
            'face_size_ratio': face_size_ratio,
            'face_center_offset_x': offset_x,
            'face_center_offset_y': offset_y,
            'face_bbox': bbox,
            'smile_score': round(smile_score, 3),
            'mouth_open_score': round(mouth_open_score, 3),
            'faces_eyes_open': faces_eyes_open,
        }

    blaze = _blaze_face_detect(rgb, width, height)
    if blaze:
        x, y, w_b, h_b, face_count = blaze
        bbox = (x, y, w_b, h_b)
        face_size_ratio = round((w_b * h_b) / (width * height), 4)
        offset_x = round(((x + w_b / 2) - width / 2) / (width / 2), 3)
        offset_y = round(((y + h_b / 2) - height / 2) / (height / 2), 3)
        face_sharpness = _face_region_sharpness(rgb, bbox)
        return {
            'face_detected': True,
            'face_count': face_count,
            'face_sharpness_score': face_sharpness,
            'eyes_open': None,
            'eye_openness_ratio': None,
            'face_size_ratio': face_size_ratio,
            'face_center_offset_x': offset_x,
            'face_center_offset_y': offset_y,
            'face_bbox': bbox,
            'smile_score': None,      # blendshapes unavailable without landmarks
            'mouth_open_score': None,
            'faces_eyes_open': None,
        }

    return _no_face_result()


def analyze_faces(image_path: str) -> dict:
    """
    Run face analysis on a single photo.

    Tries detection twice: first at standard confidence on the raw image,
    then at reduced confidence on a CLAHE-enhanced copy. The CLAHE pass
    recovers backlit and low-contrast faces (e.g. window-light baby portraits)
    that fall just below the standard confidence threshold.

    Returns a dict with face quality signals. If no face is detected after
    both passes, all signal values are None and face_detected is False.
    """
    path = Path(image_path)

    try:
        rgb = _load_image(path)
    except Exception as e:
        return _no_face_result(error=str(e))

    height, width = rgb.shape[:2]
    model_path = _ensure_model()

    # Pass 1 & 2: FaceLandmarker (478 landmarks + blendshapes for eye detection)
    mp_result = None
    for confidence, use_clahe in _DETECTION_PASSES:
        detect_rgb = _apply_clahe(rgb) if use_clahe else rgb
        mp_result = _run_detection(detect_rgb, model_path, confidence)
        if mp_result.face_landmarks:
            break

    if mp_result and mp_result.face_landmarks:
        face_count = len(mp_result.face_landmarks)
        primary_idx = _pick_primary_face_index(mp_result.face_landmarks, width, height)
        landmarks = mp_result.face_landmarks[primary_idx]
        bbox = _landmarks_to_bbox(landmarks, width, height, padding=0.10)

        face_size_ratio = round((bbox[2] * bbox[3]) / (width * height), 4)
        face_cx = bbox[0] + bbox[2] / 2
        face_cy = bbox[1] + bbox[3] / 2
        offset_x = round((face_cx - width / 2) / (width / 2), 3)
        offset_y = round((face_cy - height / 2) / (height / 2), 3)

        eyes_open, eye_openness, smile_score, mouth_open_score = _analyze_face_blendshapes(
            mp_result.face_blendshapes[primary_idx] if mp_result.face_blendshapes else None
        )
        face_sharpness = _face_region_sharpness(rgb, bbox)

        # Per-face eye state for ALL detected faces. Used by the
        # `reject_closed_eyes_all_faces` setting so a group photo isn't
        # killed when one person blinks. None when blendshapes unavailable.
        if mp_result.face_blendshapes:
            faces_eyes_open = [
                _analyze_face_blendshapes(bs)[0]
                for bs in mp_result.face_blendshapes
            ]
        else:
            faces_eyes_open = None

        return {
            'face_detected': True,
            'face_count': face_count,
            'face_sharpness_score': face_sharpness,
            'eyes_open': eyes_open,
            'eye_openness_ratio': round(eye_openness, 3),
            'face_size_ratio': face_size_ratio,
            'face_center_offset_x': offset_x,
            'face_center_offset_y': offset_y,
            'face_bbox': bbox,
            'smile_score': round(smile_score, 3),
            'mouth_open_score': round(mouth_open_score, 3),
            'faces_eyes_open': faces_eyes_open,
        }

    # Pass 3: BlazeFace fallback — fires on challenging cases (infant faces, heavy backlighting)
    # where FaceLandmarker's 478-landmark model can't get enough facial structure.
    # Returns face position + sharpness but no eye data (blendshapes require landmarks).
    # bbox + count come from a single inference (was: two passes — bbox first, count second).
    blaze = _blaze_face_detect(rgb, width, height)
    if blaze:
        x, y, w_b, h_b, face_count = blaze
        bbox = (x, y, w_b, h_b)
        face_size_ratio = round((w_b * h_b) / (width * height), 4)
        offset_x = round(((x + w_b / 2) - width / 2) / (width / 2), 3)
        offset_y = round(((y + h_b / 2) - height / 2) / (height / 2), 3)
        face_sharpness = _face_region_sharpness(rgb, bbox)
        return {
            'face_detected': True,
            'face_count': face_count,
            'face_sharpness_score': face_sharpness,
            'eyes_open': None,
            'eye_openness_ratio': None,
            'face_size_ratio': face_size_ratio,
            'face_center_offset_x': offset_x,
            'face_center_offset_y': offset_y,
            'face_bbox': bbox,
            'smile_score': None,
            'mouth_open_score': None,
            'faces_eyes_open': None,
        }

    return _no_face_result()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_detection(rgb: np.ndarray, model_path: Path, confidence: float):
    """Run FaceLandmarker on an RGB array at the given confidence threshold."""
    options = mp_vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.IMAGE,
        num_faces=10,
        min_face_detection_confidence=confidence,
        min_face_presence_confidence=confidence,
        output_face_blendshapes=True,
    )
    with _suppress_stderr():
        landmarker = mp_vision.FaceLandmarker.create_from_options(options)
    with landmarker:
        return landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))


def _blaze_face_detect(rgb: np.ndarray, width: int, height: int) -> Optional[tuple]:
    """
    Run BlazeFace short-range detector and return `(bbox, face_count)` for the
    primary face, or `None` if no face found.

    BlazeFace is a lighter model (224KB) that catches faces FaceLandmarker's
    478-landmark model misses — particularly infant faces and heavy backlighting
    where there isn't enough facial gradient structure for the landmarker.

    Returns a tuple (x, y, w, h, count) — the bbox plus the total number of
    faces found in the same inference. Callers that need only the bbox can
    slice. The count comes from the same forward pass, eliminating the
    redundant second BlazeFace call that the previous _blaze_all_detections
    helper required (live-batch evidence 2026-05-05: doubled BlazeFace cost
    on photos that fell into the fallback path).
    """
    detector_path = _ensure_detector_model()
    for confidence, use_clahe in _DETECTION_PASSES:
        detect_rgb = _apply_clahe(rgb) if use_clahe else rgb
        opts = mp_vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(detector_path)),
            min_detection_confidence=confidence,
        )
        with _suppress_stderr():
            det = mp_vision.FaceDetector.create_from_options(opts)
        with det:
            result = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=detect_rgb))
        if result.detections:
            # Return the largest detection by area
            best = max(result.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height)
            bb = best.bounding_box
            pad = int(max(bb.width, bb.height) * 0.1)
            x0 = max(0, bb.origin_x - pad)
            y0 = max(0, bb.origin_y - pad)
            x1 = min(width,  bb.origin_x + bb.width  + pad)
            y1 = min(height, bb.origin_y + bb.height + pad)
            return (x0, y0, x1 - x0, y1 - y0, len(result.detections))
    return None


def _apply_clahe(rgb: np.ndarray) -> np.ndarray:
    """
    Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) to the
    luminance channel of an RGB image and return the result as RGB.

    CLAHE boosts local contrast in dark regions without overexposing bright
    ones — the right tool for backlit faces where the face sits in shadow
    while the background is blown out. Operating on the L channel of LAB
    keeps hue and saturation unchanged so the pixel values remain valid
    for MediaPipe's RGB input.
    """
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_chan = clahe.apply(l_chan)
    enhanced_lab = cv2.merge([l_chan, a_chan, b_chan])
    return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2RGB)


def _load_image(path: Path) -> np.ndarray:
    """Load JPEG or RAW as an RGB numpy array, with EXIF rotation applied. Raises on failure."""
    if path.suffix.upper() in ('.RAF', '.NEF'):
        try:
            with rawpy.imread(str(path)) as raw:
                return raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
        except Exception:
            with rawpy.imread(str(path)) as raw:
                thumb = raw.extract_thumb()
                buf = np.frombuffer(thumb.data, dtype=np.uint8)
                bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    else:
        # Use Pillow so EXIF orientation is applied before handing pixels to MediaPipe.
        # cv2.imread ignores the orientation tag — a portrait JPEG stored as landscape
        # pixels gets passed to the detector sideways, causing missed detections.
        from PIL import Image as PilImage, ImageOps
        pil = PilImage.open(str(path)).convert('RGB')
        pil = ImageOps.exif_transpose(pil)
        return np.array(pil)


def _landmarks_to_bbox(landmarks, width: int, height: int, padding: float = 0.0) -> tuple:
    """Convert normalised landmark list to a padded pixel bounding box (x, y, w, h)."""
    xs = [lm.x * width  for lm in landmarks]
    ys = [lm.y * height for lm in landmarks]
    x0, x1 = int(min(xs)), int(max(xs))
    y0, y1 = int(min(ys)), int(max(ys))
    pad_x = int((x1 - x0) * padding)
    pad_y = int((y1 - y0) * padding)
    x0 = max(0, x0 - pad_x)
    y0 = max(0, y0 - pad_y)
    x1 = min(width - 1,  x1 + pad_x)
    y1 = min(height - 1, y1 + pad_y)
    return (x0, y0, x1 - x0, y1 - y0)


def _pick_primary_face_index(face_landmarks_list, width: int, height: int) -> int:
    """Return the index of the largest face by bounding-box area."""
    best_idx, best_area = 0, -1
    for i, landmarks in enumerate(face_landmarks_list):
        bbox = _landmarks_to_bbox(landmarks, width, height)
        area = bbox[2] * bbox[3]
        if area > best_area:
            best_area = area
            best_idx = i
    return best_idx


def _analyze_face_blendshapes(blendshapes) -> tuple[bool, float, float, float]:
    """
    Extract eye openness, smile, and jaw-open scores from MediaPipe blendshapes.

    Blendshape scores are 0.0 (shape absent) → 1.0 (shape fully present).

    Returns:
        (eyes_open, eye_openness_ratio, smile_score, mouth_open_score)
        Falls back to (True, 1.0, 0.0, 0.0) when blendshapes are unavailable.
    """
    if not blendshapes:
        return True, 1.0, 0.0, 0.0

    blink_left = blink_right = 0.0
    smile_left = smile_right = 0.0
    jaw_open = 0.0

    for bs in blendshapes:
        name = bs.category_name
        if name == _BLINK_LEFT:
            blink_left = bs.score
        elif name == _BLINK_RIGHT:
            blink_right = bs.score
        elif name == _SMILE_LEFT:
            smile_left = bs.score
        elif name == _SMILE_RIGHT:
            smile_right = bs.score
        elif name == _JAW_OPEN:
            jaw_open = bs.score

    mean_blink = (blink_left + blink_right) / 2
    eye_openness = 1.0 - mean_blink
    eyes_open = mean_blink < _EYE_CLOSED_THRESHOLD
    smile_score = (smile_left + smile_right) / 2
    mouth_open_score = jaw_open

    return eyes_open, eye_openness, smile_score, mouth_open_score


def _face_region_sharpness(rgb: np.ndarray, bbox: tuple) -> float:
    """
    Laplacian variance on the face crop only.

    Catches the case where global sharpness is high (sharp background)
    but the face is soft — common when AF locks on the wrong plane.
    """
    x, y, w, h = bbox
    crop = rgb[y:y + h, x:x + w]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(gray.astype(np.float64), cv2.CV_64F)
    return round(float(lap.var()), 1)


def _no_face_result(error: Optional[str] = None) -> dict:
    """Consistent no-face response so callers never need to handle None."""
    result: dict = {
        'face_detected': False,
        'face_count': 0,
        'face_sharpness_score': None,
        'eyes_open': None,
        'eye_openness_ratio': None,
        'face_size_ratio': None,
        'face_center_offset_x': None,
        'face_center_offset_y': None,
        'face_bbox': None,
        'smile_score': None,
        'mouth_open_score': None,
        'faces_eyes_open': None,
    }
    if error:
        result['error'] = error
    return result
