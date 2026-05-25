"""
Phase 2 Aesthetic Scorer — TOPIQ-IAA (AVA-trained Image Aesthetic Assessment).

What this is, and why it's different from TOPIQ-NR (iqa_scorer.py):

  TOPIQ-NR asks: "Does this photo look technically correct?"
                 (blur, noise, compression artifacts, tonal balance)

  TOPIQ-IAA asks: "Would a human find this beautiful?"
                  (composition, mood, colour harmony, subject interest) —
                  trained on AVA's 250k human aesthetic ratings.

TOPIQ-IAA shares the no-reference TOPIQ backbone but its head is fine-tuned
on the AVA aesthetic-assessment task. Replaces the previous CLIP ViT-L/14 +
LAION linearMSE head (sac+logos+ava1) — drops the 890 MB CLIP backbone and
uses the same pyiqa interface that's already proven in iqa_scorer.

Score is 0–100 (higher = more aesthetically appealing) to match every other
scorer in the pipeline. pyiqa wraps the AVA scale internally; we clamp to
[0, 1] and rescale to [0, 100].

Model weights (~100 MB shared with TOPIQ-NR cache) auto-download to
~/.cache/pyiqa/ on first use. CPU inference: ~1–2s per image at 1024px
preview scale.
"""

import logging
from pathlib import Path

from PIL import Image

from phase2_quality.model_status import begin as _begin, end as _end

logger = logging.getLogger(__name__)


# Pinned to CPU for the same reasons documented in iqa_scorer.py:
#   TOPIQ's CLIP semantic backbone weights don't move with .to(device),
#   and MPS doesn't implement F.adaptive_avg_pool2d with non-divisor sizes.
import torch as _torch
_DEVICE = _torch.device("cpu")


def _get_device():
    """Backward-compatible accessor for the (cached) CPU device."""
    return _DEVICE


_MODEL_ID = "topiq-iaa"
_MODEL_NAME = "TOPIQ-IAA Aesthetic"
_MODEL_SIZE_MB = 100  # shares the TOPIQ-NR cache; first-time download only if iqa hasn't loaded yet


def _is_topiq_cached() -> bool:
    """Return True if pyiqa already has TOPIQ weights on disk.

    Shared with iqa_scorer; once either metric has loaded the cache exists.
    pyiqa caches under torch.hub's directory (~/.cache/torch/hub/pyiqa).
    """
    cache = Path.home() / ".cache" / "torch" / "hub" / "pyiqa"
    return cache.exists() and any(cache.iterdir())


# Max pixel size on the longest side before inference. Same downscale as
# iqa_scorer — aesthetic judgment doesn't benefit from full resolution and
# saves significant CPU.
_MAX_SIDE = 1024

# Module-level cache — metric loaded once per process.
_metric = None
_load_failed = False  # Set after fatal load error so we don't retry every photo.

# Flag set the first time we observe an unexpected raw score range. Helps
# diagnose pyiqa wrapper changes without spamming logs.
_range_warned = False


def _get_metric():
    """Load TOPIQ-IAA metric, or return the already-loaded instance.

    On fatal load failure we set _load_failed and raise once. Subsequent calls
    short-circuit immediately rather than re-attempting load every photo.

    The /reload-models endpoint can flip _load_failed back to False via
    reset_load_failed() after the user fixes the underlying problem.
    """
    global _metric, _load_failed
    if _load_failed:
        raise RuntimeError("TOPIQ-IAA model failed to load earlier in this session.")
    if _metric is None:
        import pyiqa
        downloading = not _is_topiq_cached()
        _begin(_MODEL_ID, _MODEL_NAME, _MODEL_SIZE_MB, downloading)
        try:
            logger.info("Loading TOPIQ-IAA model (first use — may download ~100 MB)…")
            _metric = pyiqa.create_metric("topiq_iaa", device=_get_device())
            logger.info("TOPIQ-IAA model ready.")
        except Exception:
            _load_failed = True
            logger.exception("TOPIQ-IAA model load failed — disabling for this session.")
            raise
        finally:
            _end(_MODEL_ID)
    return _metric


def reset_load_failed() -> None:
    """Clear the failure flag so the next call retries loading. Used by
    /reload-models when the user has updated their environment."""
    global _load_failed, _metric
    _load_failed = False
    _metric = None


def _downscale(img):
    """Downscale a PIL RGB image to _MAX_SIDE on the longest axis."""
    w, h = img.size
    if max(w, h) > _MAX_SIDE:
        scale = _MAX_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _load_and_downscale(image_path: str):
    """
    Return a downscaled PIL RGB image for IAA inference.

    Handles JPEG/PNG natively and RAF/NEF via rawpy.
    """
    path = Path(image_path)
    suffix = path.suffix.lower()

    if suffix in (".raf", ".nef", ".cr2", ".arw", ".dng"):
        import rawpy
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
        img = Image.fromarray(rgb)
    else:
        img = Image.open(path).convert("RGB")

    return _downscale(img)


def _pil_to_tensor(img):
    """Convert a PIL RGB image to a [1, C, H, W] float32 tensor in [0, 1]."""
    import numpy as np
    import torch

    arr = np.array(img, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
    return tensor


def _normalize_raw_to_100(raw_score: float) -> float:
    """Map pyiqa's TOPIQ-IAA raw output to the 0–100 column scale.

    pyiqa's wrapper normalizes most metrics to [0, 1]; defensively handle the
    case where IAA outputs the underlying AVA scale (~1–10) instead — log
    once if observed so we can detect a pyiqa upstream change without
    silently producing clipped scores.
    """
    global _range_warned
    if raw_score > 1.5:
        if not _range_warned:
            logger.debug(
                "TOPIQ-IAA raw=%.3f on AVA scale (expected — pyiqa wrapper "
                "doesn't normalize IAA to [0,1]). Applying AVA-scale mapping.",
                raw_score,
            )
            _range_warned = True
        clamped = max(1.0, min(10.0, raw_score))
        return round((clamped - 1.0) / 9.0 * 100.0, 1)
    clamped = max(0.0, min(1.0, raw_score))
    return round(clamped * 100.0, 1)


def _score_tensor(tensor) -> dict:
    """Shared scoring core — both score_image and score_image_pil delegate here."""
    import torch
    metric = _get_metric()
    with torch.no_grad():
        raw_score = metric(tensor).item()

    aesthetic_score = _normalize_raw_to_100(raw_score)

    # Band cutoffs recalibrated for TOPIQ-IAA's narrower distribution on AVA.
    # Survey of 40 cached preview JPGs (2026-05-12): min=31.6, max=49.5,
    # mean=41.8, p10/25/50/75/90 = 35.7/39.2/42.9/45.8/47.8. The cutoffs below
    # roughly partition this into top ~15% / top ~50% / top ~85% / bottom ~15%.
    # If a fresh shoot's distribution shifts these percentiles, update here
    # and the mirrored cutoffs in frontend/src/ui/format.js (aestheticLabel)
    # and frontend/src/views/DetailView.jsx (aestheticTint, tooltip copy).
    if aesthetic_score >= 46:
        label = "Excellent"
    elif aesthetic_score >= 42:
        label = "Good"
    elif aesthetic_score >= 36:
        label = "Fair"
    else:
        label = "Poor"

    return {"aesthetic_score": aesthetic_score, "aesthetic_label": label}


def score_image_pil(img) -> dict:
    """
    Run TOPIQ-IAA on a pre-decoded PIL RGB image.

    Called by quality_analyzer when the RAW has already been decoded once and
    shared across all scorers — avoids a redundant rawpy.postprocess() call.
    """
    try:
        img = _downscale(img)
        tensor = _pil_to_tensor(img)
        return _score_tensor(tensor)
    except Exception as e:
        logger.exception("Aesthetic scoring failed on pre-decoded image: %s", e)
        return {"aesthetic_score": None, "aesthetic_label": None}


def score_image(image_path: str) -> dict:
    """
    Run TOPIQ-IAA on a single photo.

    Returns:
        {
            'aesthetic_score': float | None,   # 0–100, higher = more aesthetically appealing
            'aesthetic_label': str  | None,    # 'Excellent' | 'Good' | 'Fair' | 'Poor'
        }

    On any failure (model load error, corrupt file, unsupported format) the
    function returns None values so the rest of the analysis pipeline
    continues — same graceful-degradation pattern as iqa_scorer and
    face_analyzer.
    """
    try:
        img = _load_and_downscale(image_path)
        tensor = _pil_to_tensor(img)
        return _score_tensor(tensor)
    except Exception as e:
        logger.exception("Aesthetic scoring failed for %s: %s", image_path, e)
        return {"aesthetic_score": None, "aesthetic_label": None}
