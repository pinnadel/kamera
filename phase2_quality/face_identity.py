"""
Phase 2 Face Identity — FaceNet (InceptionResnetV1, VGGFace2-pretrained)
embeddings for "same person" clustering.

Why this exists separately from similarity_scorer.py:
  similarity_scorer's SigLIP embeddings describe the WHOLE PHOTO — subject,
  scene, mood, composition. That's right for burst detection ("two photos of
  the same moment look almost identical"), but wrong for People mode: when a
  photographer asks "show me every photo of Aunt Jane", the background,
  lighting, clothing, and pose all change. Scene embeddings collapse on those
  signals; identity embeddings stay stable.

  FaceNet was trained specifically to map face crops to a 512-dim space where
  same-person pairs cluster together regardless of lighting/pose/expression.
  Cosine similarity ~0.50 is the standard "same person" decision boundary
  for InceptionResnetV1 (vs SigLIP's 0.78–0.90 range for visual similarity).

Pipeline:
  rgb_array (full photo, decoded once by quality_analyzer) + bbox from
  face_analyzer  →  crop with 15% padding  →  resize to 160×160  →
  forward pass  →  L2-normalised 512-dim embedding (model auto-normalises).

Model: InceptionResnetV1, vggface2 pretrained (~107 MB)
  - 27.9M parameters
  - 512-dim output
  - Cached at ~/.cache/torch/checkpoints/ on first download
  - Pure PyTorch — runs on MPS (Apple Silicon), CUDA, or CPU via get_device()

Cost: ~30–80 ms per face on MPS, including the crop+resize. Cheap relative
to the rest of the per-photo pipeline (RAW decode, IQA, aesthetic).

Multi-face note: this module returns ONE embedding per photo — for the face
whose bbox we're given, which is the largest detected face. Group photos
where multiple known people appear are clustered by whichever face is
biggest. That's a deliberate Phase-1 simplification; per-face embeddings
(one row per detected face, many-to-one with images) is a follow-up.
"""

import json
import logging

from phase2_quality.model_status import begin as _begin, end as _end
from phase2_quality.device import get_device as _get_device

logger = logging.getLogger(__name__)

_MODEL_ID = "facenet-vggface2"
_MODEL_NAME = "FaceNet (InceptionResnetV1)"
_MODEL_SIZE_MB = 107

_EMBEDDING_DIM = 512
_INPUT_SIZE = 160  # Standard FaceNet input size

# Module-level cache.
_model = None
# Sticky after a load failure so we don't retry on every photo in a batch.
_load_failed = False


def _is_facenet_cached() -> bool:
    """True if the InceptionResnetV1 weights are already on disk."""
    try:
        import os
        from pathlib import Path
        cache = Path(os.environ.get("TORCH_HOME") or Path.home() / ".cache" / "torch")
        # facenet-pytorch stores weights as 20180402-114759-vggface2.pt
        return any(cache.rglob("*vggface2*.pt"))
    except Exception:
        return False


def _get_model():
    """Load InceptionResnetV1 on first call; cache for the rest of the process."""
    global _model, _load_failed
    if _load_failed:
        return None
    if _model is None:
        try:
            from facenet_pytorch import InceptionResnetV1

            downloading = not _is_facenet_cached()
            _begin(_MODEL_ID, _MODEL_NAME, _MODEL_SIZE_MB, downloading)
            try:
                logger.info("Loading FaceNet (first use — may download ~107 MB)…")
                model = InceptionResnetV1(pretrained="vggface2").eval()
                model = model.to(_get_device())
                _model = model
                logger.info("FaceNet ready on %s.", _get_device())
            finally:
                _end(_MODEL_ID)
        except Exception:
            logger.exception("FaceNet model load failed — disabling for this session")
            _load_failed = True
            return None

    return _model


def _crop_face(rgb, bbox, padding: float = 0.15):
    """
    Crop a square-ish face region from an RGB array with `padding` extra
    margin around the bbox.

    The model wants the whole face (forehead to chin, ear to ear), but
    face_analyzer's bbox tracks landmarks tightly. 15% padding gives the
    network the head context it was trained on. Clamps to image bounds so
    bboxes near edges still produce a valid crop.
    """
    import numpy as np
    h, w = rgb.shape[:2]
    x, y, bw, bh = bbox
    pad_x = int(bw * padding)
    pad_y = int(bh * padding)
    x0 = max(0, int(x) - pad_x)
    y0 = max(0, int(y) - pad_y)
    x1 = min(w, int(x) + int(bw) + pad_x)
    y1 = min(h, int(y) + int(bh) + pad_y)
    if x1 <= x0 or y1 <= y0:
        return None
    return rgb[y0:y1, x0:x1]


def embed_face(rgb_array, bbox, padding: float = 0.15) -> list[float] | None:
    """
    Compute a 512-dim FaceNet identity embedding for the face at `bbox`
    inside `rgb_array`.

    Args:
        rgb_array: full photo as an HxWx3 uint8 numpy array (RGB order).
        bbox:      (x, y, w, h) in pixel coordinates as returned by
                   face_analyzer. Either tuple or list works.
        padding:   fraction of bbox size to pad on each side (default 0.15).

    Returns:
        list of 512 floats (L2-normalised), or None if the model isn't
        available, the crop is degenerate, or inference fails.

    Failure modes are silent-by-design — face identity is additive
    metadata. A NULL embedding just means the photo won't appear in
    People-mode groups; the rest of the analysis pipeline keeps working.
    """
    if rgb_array is None or bbox is None:
        return None

    model = _get_model()
    if model is None:
        return None

    try:
        import numpy as np
        import torch
        from PIL import Image

        crop = _crop_face(rgb_array, bbox, padding=padding)
        if crop is None or crop.size == 0:
            return None

        # PIL → 160×160 → tensor in [-1, 1] (FaceNet's expected normalisation).
        img = Image.fromarray(crop).resize((_INPUT_SIZE, _INPUT_SIZE), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32)  # H, W, 3 in [0, 255]
        arr = (arr - 127.5) / 128.0              # → [-1, 1]
        tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)  # 1, 3, 160, 160
        tensor = tensor.to(_get_device())

        with torch.no_grad():
            emb = model(tensor)  # InceptionResnetV1 returns L2-normalised by default

        vec = emb[0].cpu().numpy().astype(np.float32)
        # Defensive re-norm — should already be L2=1 but cheap insurance.
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    except Exception:
        logger.exception("FaceNet embedding failed (bbox=%s)", bbox)
        return None


def face_embedding_to_json(embedding: list[float] | None) -> str | None:
    """Serialise to a compact JSON string for SQLite storage."""
    if embedding is None:
        return None
    return json.dumps(embedding, separators=(",", ":"))


def json_to_face_embedding(json_str: str | None) -> list[float] | None:
    """Deserialise from SQLite text back to a float list."""
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None
