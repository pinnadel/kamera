"""
Phase 2 IQA Scorer — Perceptual Image Quality Assessment via TOPIQ.

TOPIQ (Top-down Image Quality) is a no-reference IQA model trained on human
expert ratings. "No-reference" means it judges a photo without needing a
pristine original to compare against — the same way a senior art director
looks at a photo and immediately knows whether it looks right.

Score is 0–100 (higher = better perceptual quality). This is separate from
our sharpness/exposure math: a photo of grey wall can score 100 there but
only 40 here. That gap is exactly the Phase 3 delta learning signal.

Model weights (~100 MB) auto-download to ~/.cache/pyiqa/ on first use.
Subsequent calls load from disk cache in ~1–2 seconds on macOS CPU.
Inference runs at 1024px preview scale (~0.5–2s per image).
"""

import logging
from pathlib import Path

from PIL import Image

from phase2_quality.model_status import begin as _begin, end as _end


# TOPIQ is pinned to CPU. pyiqa's TOPIQ model wraps a CLIP semantic backbone
# whose weights aren't reached by `.to(device)` on the outer model — they
# stay on CPU even when the rest moves to MPS, producing dtype mismatch
# errors on the first conv. Even when that's worked around, TOPIQ's
# cross-attention path uses F.adaptive_avg_pool2d with non-divisor output
# sizes which MPS doesn't implement
# (https://github.com/pytorch/pytorch/issues/96056). The model is fast
# enough on CPU (~1–2s per image) that the speedup isn't worth the
# brittleness — pinning to CPU is the right call.
#
# Module-level constant (was a function called per photo).
import torch as _torch
_DEVICE = _torch.device("cpu")


def _get_device():
    """Backward-compatible accessor for the (cached) CPU device."""
    return _DEVICE

logger = logging.getLogger(__name__)

_MODEL_ID  = "topiq"
_MODEL_NAME = "TOPIQ IQA"
_MODEL_SIZE_MB = 100


def _is_topiq_cached() -> bool:
    """Return True if pyiqa has already downloaded TOPIQ weights to disk.

    pyiqa caches under torch.hub's directory (~/.cache/torch/hub/pyiqa), not
    a top-level ~/.cache/pyiqa as the name suggests. Checking the wrong path
    means the "downloading" toast fires on every cold start even when weights
    are present — purely cosmetic but misleading.
    """
    cache = Path.home() / ".cache" / "torch" / "hub" / "pyiqa"
    return cache.exists() and any(cache.iterdir())

# Max pixel size on the longest side before inference.
# Reduces memory and inference time with negligible quality impact for IQA.
_MAX_SIDE = 1024

# Module-level cache — metric is loaded once per process, same as face model.
_metric = None
_load_failed = False  # Set after a fatal load error so we don't retry every photo.


def _get_metric():
    """Load TOPIQ no-reference metric, or return the already-loaded instance.

    On fatal load failure we set _load_failed and raise once. Subsequent calls
    short-circuit immediately rather than re-attempting load every photo —
    a broken model load on a 700-photo batch was retrying 200+ times before
    this guard, wasting ~50ms each and flooding the log.

    The /reload-models endpoint can flip _load_failed back to False to retry
    after the user fixes the underlying problem (e.g. torch version).
    """
    global _metric, _load_failed
    if _load_failed:
        raise RuntimeError("TOPIQ model failed to load earlier in this session.")
    if _metric is None:
        import torch
        import pyiqa
        downloading = not _is_topiq_cached()
        _begin(_MODEL_ID, _MODEL_NAME, _MODEL_SIZE_MB, downloading)
        try:
            logger.info("Loading TOPIQ model (first use — may download ~100 MB)…")
            _metric = pyiqa.create_metric("topiq_nr", device=_get_device())
            logger.info("TOPIQ model ready.")
        except Exception:
            _load_failed = True
            logger.exception("TOPIQ model load failed — disabling for this session.")
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
    Return a downscaled PIL RGB image for IQA inference.

    Handles JPEG/PNG natively and RAF/NEF via rawpy. Downscaling to _MAX_SIDE
    on the longest axis preserves the perceptual content TOPIQ cares about
    while keeping inference fast on CPU.
    """
    from PIL import Image

    path = Path(image_path)
    suffix = path.suffix.lower()

    if suffix in (".raf", ".nef", ".3fr", ".cr2", ".arw", ".dng"):
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

    arr = np.array(img, dtype=np.float32) / 255.0   # H, W, C in [0, 1]
    tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)  # 1, C, H, W
    return tensor


def _score_tensor(tensor) -> dict:
    """Shared scoring core — both score_image and score_image_pil delegate here."""
    import torch
    metric = _get_metric()
    with torch.no_grad():
        raw_score = metric(tensor).item()

    # TOPIQ is calibrated to [0, 1] but can slightly exceed bounds on edge cases.
    raw_score = max(0.0, min(1.0, raw_score))
    iqa_score = round(raw_score * 100, 1)

    if iqa_score >= 75:
        label = "Excellent"
    elif iqa_score >= 55:
        label = "Good"
    elif iqa_score >= 35:
        label = "Fair"
    else:
        label = "Poor"

    return {"iqa_score": iqa_score, "iqa_label": label}


def score_image_pil(img) -> dict:
    """
    Run TOPIQ on a pre-decoded PIL RGB image.

    Called by quality_analyzer when the RAW has already been decoded once and
    shared across all scorers — avoids a redundant rawpy.postprocess() call.
    The caller is responsible for passing a reasonably-sized image; this
    function still downscales to _MAX_SIDE if needed.
    """
    try:
        img = _downscale(img)
        # Tensor is created from a numpy array (CPU memory) and TOPIQ runs
        # on CPU — no `.to(device)` needed. The previous explicit move was
        # a provable no-op (CPU→CPU) called per photo.
        tensor = _pil_to_tensor(img)
        return _score_tensor(tensor)
    except Exception as e:
        logger.exception("IQA scoring failed on pre-decoded image: %s", e)
        return {"iqa_score": None, "iqa_label": None}


def score_image(image_path: str) -> dict:
    """
    Run TOPIQ no-reference IQA on a single photo.

    Returns:
        {
            'iqa_score': float | None,  # 0–100 perceptual quality, None on failure
            'iqa_label': str  | None,   # 'Excellent' | 'Good' | 'Fair' | 'Poor'
        }

    On any failure (model load error, corrupt file, unsupported format) the
    function returns None values so the rest of the analysis pipeline continues.
    This matches the BlazeFace fallback pattern: degrade gracefully, never block.
    """
    try:
        img = _load_and_downscale(image_path)
        # No-op .to(cpu) removed — see score_image_pil.
        tensor = _pil_to_tensor(img)
        return _score_tensor(tensor)
    except Exception as e:
        logger.exception("IQA scoring failed for %s: %s", image_path, e)
        return {"iqa_score": None, "iqa_label": None}
