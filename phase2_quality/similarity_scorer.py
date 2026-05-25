"""
Phase 2 Similarity Scorer — SigLIP semantic embeddings for burst grouping.

What SigLIP does (the concept):
  Every photo gets a 768-number "fingerprint" that encodes visual semantics —
  subject, scene, mood, composition. Two fingerprints pointing in similar
  directions in this 768-dimensional space belong to the same burst.

  We measure direction similarity with cosine similarity:
    1.0 = identical content
    0.9+ = same burst / near-duplicate
    < 0.7 = visually different subjects

Why this beats timestamp clustering:
  Timestamps tell you WHEN photos were taken. SigLIP tells you WHAT they look
  like. Two frames 1s apart of different subjects should not group. Two frames
  30s apart of the same person should. Only content knows the difference.

Model: google/siglip2-base-patch16-224 (~300 MB, SigLIP-2 base variant)
  - 86M parameters (vision encoder only — no text encoder needed)
  - 768-dim embedding output (matches SigLIP-1 base — no schema change)
  - Downloaded to ~/.cache/huggingface/ on first use

  SigLIP-2 (arXiv:2502.14786) is a drop-in upgrade over SigLIP-1: same scale,
  same embedding dim, +~2.4 pts zero-shot accuracy. The fixed-resolution
  variant (`siglip2-base-patch16-224`) keeps the SigLIP-1 architecture
  (`model_type="siglip"`, `SiglipVisionModel`) — only the weights are new.
  The NaFlex variants (`siglip2-*-naflex`) use a different architecture
  (`Siglip2VisionModel`) and aren't a drop-in here.

  Alternative: google/siglip2-so400m-patch14-384 (~1.7 GB, 1152-dim output).
  Schema change required (embedding column shape) — explicitly not chosen.

CPU inference: ~0.3–1.0s per image at 224px resolution.
"""

import json
import logging
from pathlib import Path

from phase2_quality.model_status import begin as _begin, end as _end
from phase2_quality.device import get_device as _get_device

logger = logging.getLogger(__name__)

_MODEL_ID = "google/siglip2-base-patch16-224"
_MODEL_NAME = "SigLIP-2 Vision Encoder"
_MODEL_SIZE_MB = 300

# Output dimension for siglip2-base (matches siglip-1 base). If you switch to
# the so400m variant, change to 1152 and add a schema migration for the
# `embedding` column shape.
_EMBEDDING_DIM = 768

# Downscale to this before the processor handles its own 224px resize.
# Avoids decoding a 24 MP RAW into RAM for a model that wants 224×224.
_MAX_SIDE = 384

# Module-level cache — loaded once per process. The full SiglipModel bundles
# both encoders (vision + text) plus the processor + tokenizer, so one
# from_pretrained() warms everything needed by analyze, semantic search, and
# scene/concept tagging. ~50 MB more RAM than vision-only; the text encoder
# was always going to be loaded later anyway.
_siglip = None  # tuple(model, processor, tokenizer) once ready

# Permanent-failure flag — see _load_failed handling in _get_siglip().
_load_failed = False


def _is_siglip_cached() -> bool:
    """Return True if HuggingFace hub already has this model on disk."""
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        return any(repo.repo_id == _MODEL_ID for repo in info.repos)
    except Exception:
        return False


def _get_siglip():
    """Load SigLIP-2 model + processor + tokenizer; cache on first call.

    Returns (model, processor, tokenizer). `model` is a `SiglipModel` whose
    `.vision_model` and `.text_model` attributes are the encoders used by
    embed_image / embed_text.

    Why AutoModel (not SiglipVisionModel directly):
      transformers ≥4.5x changed SiglipConfig so `hidden_size` lives on
      `vision_config.hidden_size`, not at the top level. `SiglipVisionModel`
      still reads `config.hidden_size`, which raises AttributeError on every
      load. `AutoModel.from_pretrained()` returns the full `SiglipModel`,
      which initialises its sub-modules with the correct sub-configs.

    Permanent-failure caching:
      If load throws, set `_load_failed = True` so subsequent calls
      short-circuit to RuntimeError instead of re-running from_pretrained()
      per photo. Per lazy-load-failure-caching incident: a broken loader
      otherwise produces N stack traces for an N-photo batch.
    """
    global _siglip, _load_failed
    if _load_failed:
        raise RuntimeError(
            "SigLIP model failed to load earlier in this process; "
            "not retrying. Check the first SigLIP error in the log."
        )
    if _siglip is None:
        from transformers import AutoModel, AutoProcessor, AutoTokenizer

        downloading = not _is_siglip_cached()
        _begin(_MODEL_ID, _MODEL_NAME, _MODEL_SIZE_MB, downloading)
        try:
            logger.info("Loading SigLIP-2 model (first use — may download ~300 MB)…")
            model = AutoModel.from_pretrained(_MODEL_ID)
            model.eval()
            model = model.to(_get_device())
            processor = AutoProcessor.from_pretrained(_MODEL_ID)
            tokenizer = AutoTokenizer.from_pretrained(_MODEL_ID)
            if processor is None or tokenizer is None:
                raise RuntimeError(
                    f"SigLIP processor/tokenizer load returned None for {_MODEL_ID!r}"
                )
            _siglip = (model, processor, tokenizer)
            logger.info("SigLIP-2 model ready on %s.", _get_device())
        except Exception:
            _load_failed = True
            raise
        finally:
            _end(_MODEL_ID)

    return _siglip


def embed_text(query: str) -> list[float] | None:
    """Generate a SigLIP text embedding for a free-text search query.

    The embedding lives in the same 768-dim L2-normalised space as image
    embeddings from embed_image(), so cosine similarity (= dot product) gives
    semantic relevance directly.

    Returns:
        list of 768 floats, or None on failure (logged).
    """
    try:
        import torch
        import numpy as np

        model, _processor, tokenizer = _get_siglip()

        inputs = tokenizer(
            [query],
            return_tensors="pt",
            padding="max_length",
            max_length=64,
            truncation=True,
        )
        inputs = {k: v.to(_get_device()) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.text_model(**inputs)

        pooled = outputs.pooler_output  # shape: [1, 768]
        vec = pooled[0].cpu().numpy().astype(np.float32)

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return vec.tolist()

    except Exception as exc:
        logger.exception("SigLIP text embedding failed for query %r: %s", query, exc)
        return None


def _downscale(img):
    """Downscale a PIL RGB image to _MAX_SIDE on the longest axis."""
    from PIL import Image
    w, h = img.size
    if max(w, h) > _MAX_SIDE:
        scale = _MAX_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _open_image(image_path: str):
    """
    Open a photo as PIL RGB, downscaled to _MAX_SIDE on the longest axis.

    Handles JPEG/PNG natively and RAF/NEF via rawpy — same pattern as iqa_scorer
    and aesthetic_scorer.
    """
    from PIL import Image

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


def embed_image_pil(img) -> dict:
    """
    Generate a SigLIP embedding from a pre-decoded PIL RGB image.

    Called by quality_analyzer when the RAW has already been decoded once and
    shared across all scorers. Downscales to _MAX_SIDE before inference.
    """
    try:
        import torch
        import numpy as np

        model, processor, _tokenizer = _get_siglip()
        img = _downscale(img)

        inputs = processor(images=img, return_tensors="pt")
        inputs = {k: v.to(_get_device()) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.vision_model(**inputs)

        pooled = outputs.pooler_output
        vec = pooled[0].cpu().numpy().astype(np.float32)

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return {"embedding": vec.tolist()}

    except Exception as e:
        logger.exception("SigLIP embedding failed on pre-decoded image: %s", e)
        return {"embedding": None}


def embed_image(image_path: str) -> dict:
    """
    Generate a SigLIP semantic embedding for a single photo.

    Returns:
        {
            'embedding': list[float] | None   # 768-dim L2-normalised vector
        }

    The embedding is L2-normalised so cosine similarity = dot product between
    any two embeddings. Stored as JSON text in SQLite. None on any failure.
    """
    try:
        import torch
        import numpy as np

        model, processor, _tokenizer = _get_siglip()
        img = _open_image(image_path)

        inputs = processor(images=img, return_tensors="pt")
        inputs = {k: v.to(_get_device()) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model.vision_model(**inputs)

        # pooler_output: [1, hidden_dim] — the CLS-like summary embedding.
        # last_hidden_state[:, 0, :] is equivalent for SigLIP.
        pooled = outputs.pooler_output  # shape: [1, 768]
        vec = pooled[0].cpu().numpy().astype(np.float32)

        # L2-normalise so cosine similarity = simple dot product later.
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return {"embedding": vec.tolist()}

    except Exception as e:
        logger.exception("SigLIP embedding failed for %s: %s", image_path, e)
        return {"embedding": None}


# ---------------------------------------------------------------------------
# Burst grouping utility — runs server-side on stored embeddings.
# ---------------------------------------------------------------------------

# Layered gate constants. See group_by_similarity() docstring for the full
# rationale; below are just the numeric thresholds and the blocker set.

# Face-identity cosine threshold. Matches the /face-groups default (0.50)
# so a "same person" call here is consistent with what People mode shows.
_FACE_IDENTITY_COS = 0.50

# Histogram cosine threshold for face-less union. Strict — two photos must
# have very similar color distributions to qualify as the same scene.
_HIST_COS = 0.90

# Coarse RGB histogram resolution per channel. 4 × 4 × 4 = 64 bins.
_HIST_BINS = 4

# Long edge for the down-sampled JPEG we run the histogram on. The image
# is only used to count colors, so 128px is plenty and reads-from-disk
# stay sub-30ms even on RAW previews.
_HIST_LONG_EDGE = 128

# Scene-tag pairs that are "definitely different scenes" and must never
# union, regardless of SigLIP cosine or histogram match. Symmetric: each
# unordered pair stored in alphabetically-sorted form; the predicate
# normalises lookups the same way.
# Conservative list — only obvious opposites. Adjacent tags ("street" vs
# "indoor", "portrait" vs "indoor") deliberately omitted because they
# legitimately overlap in real shoots.
_SCENE_BLOCKERS: frozenset[tuple[str, str]] = frozenset({
    tuple(sorted(pair)) for pair in [
        ("indoor",    "landscape"),
        ("indoor",    "night"),
        ("indoor",    "water"),
        ("indoor",    "action"),
        ("landscape", "portrait"),
        ("landscape", "macro"),
        ("portrait",  "macro"),
        ("portrait",  "night"),
        ("portrait",  "water"),
        ("macro",     "action"),
        ("macro",     "night"),
        ("macro",     "water"),
    ]
})


def _scenes_blocked(a: str | None, b: str | None) -> bool:
    """Return True iff (a, b) is an explicit incompatible scene pair."""
    if not a or not b or a == b:
        return False
    pair = (a, b) if a < b else (b, a)
    return pair in _SCENE_BLOCKERS


def _compute_histogram(file_path: str) -> "list[float] | None":
    """Read an image, downscale, return a normalised 64-bin RGB histogram.

    Used by the face-less union gate to detect "different scene but
    visually similar enough that SigLIP still merged them." Cheap: ~10ms
    after the file is in the OS cache.

    Returns None on read failure so the caller can skip the gate (fail-open).
    """
    try:
        import numpy as np
        from PIL import Image as _Image
        with _Image.open(file_path) as img:
            img = img.convert("RGB")
            img.thumbnail((_HIST_LONG_EDGE, _HIST_LONG_EDGE), _Image.LANCZOS)
            arr = np.asarray(img, dtype=np.uint8)
        # Quantise each channel to _HIST_BINS bins, then flatten to 64-bin.
        bins_per_channel = _HIST_BINS
        step = 256 // bins_per_channel
        q = (arr // step).clip(0, bins_per_channel - 1)
        flat_index = q[..., 0] * bins_per_channel * bins_per_channel \
                   + q[..., 1] * bins_per_channel \
                   + q[..., 2]
        hist = np.bincount(flat_index.flatten(),
                           minlength=bins_per_channel ** 3).astype(np.float32)
        norm = np.linalg.norm(hist)
        if norm == 0:
            return None
        return (hist / norm).tolist()
    except Exception:
        return None


def _cosine(a, b) -> float:
    """Cosine similarity for two equal-length float lists. Assumes inputs
    may not be L2-normalised; the histogram path normalises at compute
    time, the face-embedding path does not, so we normalise here defensively.
    """
    import numpy as np
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _has_face_embedding(extra: dict | None) -> bool:
    if not extra:
        return False
    fe = extra.get("face_embedding")
    return isinstance(fe, list) and len(fe) > 0


def _pairwise_can_union(
    a_id: int,
    b_id: int,
    extras: dict[int, dict] | None,
    hist_cache: dict[int, list[float] | None],
) -> bool:
    """Layered gate: should we allow image a_id and b_id to merge into the
    same burst, given they already passed the SigLIP cosine threshold?

    Decision tree (only the FIRST matching rule fires):

    1. extras missing or both images have no metadata → allow (fail-open;
       preserves legacy behaviour when caller didn't supply extras).
    2. BOTH have face_embedding → require FaceNet cosine ≥ 0.50.
       Different identity → deny.
    3. At most one has a face → fall through to histogram + scene-tag:
       a. Scene-tag blocker: if both tags present and the pair is in
          _SCENE_BLOCKERS → deny.
       b. Histogram: compute on demand for both, require cosine ≥ 0.90.
          If either image's histogram can't be read → fail-open (allow).
    """
    if extras is None:
        return True

    a = extras.get(a_id) or {}
    b = extras.get(b_id) or {}

    # Rule 2: both have faces → face-identity gate is authoritative.
    if _has_face_embedding(a) and _has_face_embedding(b):
        cos = _cosine(a["face_embedding"], b["face_embedding"])
        return cos >= _FACE_IDENTITY_COS

    # Rule 3a: scene-tag blocker fires before the (more expensive) histogram.
    if _scenes_blocked(a.get("scene"), b.get("scene")):
        return False

    # Rule 3b: histogram check.
    def _hist(image_id: int, meta: dict) -> "list[float] | None":
        if image_id in hist_cache:
            return hist_cache[image_id]
        fp = meta.get("file_path")
        h = _compute_histogram(fp) if fp else None
        hist_cache[image_id] = h
        return h

    ha = _hist(a_id, a)
    hb = _hist(b_id, b)
    if ha is None or hb is None:
        return True  # fail-open if we can't read one of the previews
    return _cosine(ha, hb) >= _HIST_COS


def _cluster_segment(
    ids: list[int],
    vecs,  # np.ndarray, N × 768
    threshold: float,
    extras: dict[int, dict] | None = None,
    hist_cache: dict[int, list[float] | None] | None = None,
) -> list[list[int]]:
    """Run cosine-similarity union-find on one pre-segmented batch.

    When `extras` is provided, every candidate edge that passes the SigLIP
    threshold is additionally checked against the layered gate
    (`_pairwise_can_union`). When `extras` is None, behaviour is the
    legacy pure-cosine union-find.
    """
    import numpy as np

    if len(ids) < 2:
        return []

    sim_matrix = vecs @ vecs.T  # N × N — embeddings are L2-normalised → dot = cos
    parent = list(range(len(ids)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    cache = hist_cache if hist_cache is not None else {}

    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= threshold:
                if extras is not None and not _pairwise_can_union(
                    ids[i], ids[j], extras, cache,
                ):
                    continue
                union(i, j)

    groups: dict[int, list[int]] = {}
    for idx, image_id in enumerate(ids):
        root = find(idx)
        groups.setdefault(root, []).append(image_id)

    return [g for g in groups.values() if len(g) >= 2]


def group_by_similarity(
    items: list[tuple],
    threshold: float = 0.90,
    time_gap_seconds: float | None = 60.0,
    extras: dict[int, dict] | None = None,
) -> list[list[int]]:
    """
    Cluster image IDs by cosine similarity, with an optional time-gap
    primary split and an optional layered gate for face-less photos.

    The industry pattern (Aftershoot, Narrative Select, Lightroom Auto-Stack,
    Adobe mobile burst detection): photographers stop shooting between
    distinct moments, so the inter-frame gap is the strongest segmentation
    signal. Pure visual similarity at 0.90 mistakes "same family at same
    location across an hour" for one giant burst — a 120-photo super-cluster
    of clearly distinct moments. Splitting on time first, then visual
    similarity within each segment, recovers the actual moments.

    Algorithm:
      0. (Optional) If items include a `manual_group_id` slot (4-tuples),
         items with a non-NULL anchor are partitioned by anchor key into
         their own groups, bypassing time-segment + cosine entirely. These
         groups are merged into the final output with size ≥ 2 filtering.
      1. If time_gap_seconds is set and timestamps are available:
         sort the remaining (non-anchored) items by timestamp; cut into
         segments wherever consecutive gap > time_gap_seconds. Items with
         NULL shot_at form one extra "untimed" segment.
      2. Run cosine-similarity union-find within each segment independently
         and concatenate the group lists.

    Args:
        items: list of one of these tuple shapes (all items in a single
               call must share a shape):
                 (image_id, embedding)
                 (image_id, embedding, shot_at)
                 (image_id, embedding, shot_at, manual_group_id)
               shot_at is Unix seconds float, or None if unknown.
               manual_group_id is a string anchor key, or None if the photo
               participates in auto-clustering.
        threshold: cosine similarity cutoff for "same burst" within a segment.
        time_gap_seconds: maximum allowed gap (seconds) between consecutive
                          frames in the same segment. None disables the
                          time-gap split (legacy behaviour). Default 60 s.

    Returns:
        List of groups, each group being a list of image_ids.
        Only groups with ≥ 2 images are returned.
    """
    if len(items) < 2:
        return []

    import numpy as np

    tuple_len = len(items[0])
    has_timestamps = tuple_len >= 3
    has_manual_anchor = tuple_len >= 4

    # Process-call-local histogram cache. Survives across segments so two
    # face-less photos in different time segments don't recompute (they
    # never compare anyway, but defensive against future refactors).
    hist_cache: dict[int, list[float] | None] = {}

    # Phase 0 — split off manually-anchored photos. They form their own
    # groups regardless of cosine or time gap. NULL anchor = passes through
    # to phases 1+2 as today.
    anchored_groups: list[list[int]] = []
    free_items: list[tuple] = items
    if has_manual_anchor:
        buckets: dict[str, list[int]] = {}
        free_items = []
        for it in items:
            anchor = it[3]
            if anchor:
                buckets.setdefault(anchor, []).append(it[0])
            else:
                # Drop the 4th slot so downstream code can keep its current
                # 2- or 3-tuple shape — it never reads the anchor.
                free_items.append(it[:3])
        # Buckets of size ≥ 2 become groups; singleton buckets vanish
        # (consistent with the existing ≥2 rule for the rest of the algorithm).
        for ids in buckets.values():
            if len(ids) >= 2:
                anchored_groups.append(ids)

    # If everything was anchored (or only one free item remains), short-circuit.
    if len(free_items) < 2:
        return anchored_groups

    # Legacy path: no time data, or time-gap split disabled.
    if not has_timestamps or time_gap_seconds is None:
        ids = [item[0] for item in free_items]
        vecs = np.array([item[1] for item in free_items], dtype=np.float32)
        return anchored_groups + _cluster_segment(
            ids, vecs, threshold, extras, hist_cache,
        )

    # Split into a timed track (segmented by gap) plus an untimed bucket.
    timed   = [(i, v, t) for (i, v, t) in free_items if t is not None]
    untimed = [(i, v)    for (i, v, t) in free_items if t is None]

    timed.sort(key=lambda x: x[2])

    segments: list[list[tuple[int, list[float]]]] = []
    current: list[tuple[int, list[float]]] = []
    prev_t: float | None = None
    for image_id, vec, t in timed:
        if prev_t is not None and (t - prev_t) > time_gap_seconds:
            if current:
                segments.append(current)
            current = []
        current.append((image_id, vec))
        prev_t = t
    if current:
        segments.append(current)

    if untimed:
        segments.append(untimed)

    all_groups: list[list[int]] = list(anchored_groups)
    for seg in segments:
        if len(seg) < 2:
            continue
        seg_ids  = [s[0] for s in seg]
        seg_vecs = np.array([s[1] for s in seg], dtype=np.float32)
        all_groups.extend(_cluster_segment(
            seg_ids, seg_vecs, threshold, extras, hist_cache,
        ))

    return all_groups


# Scene labels for zero-shot classification. Phrased as captions to match
# how CLIP-like models were trained ("a photo of …" phrasing improves accuracy).
# Order determines the label returned; confidence is the winning cosine score.
SCENE_LABELS: dict[str, str] = {
    "portrait":  "a portrait photo of a person or face",
    "landscape": "a landscape photo of nature, mountains, fields, or sky",
    "street":    "a street photo of an urban scene, city, or architecture",
    "night":     "a night photo with stars, city lights, or dark sky",
    "macro":     "a close-up macro photo of a small object, flower, or insect",
    "indoor":    "an indoor photo inside a room, building, or interior",
    "action":    "an action sports or wildlife photo with motion or animals",
    "water":     "a photo of water — ocean, river, lake, or waterfall",
}

# Cache the text embeddings for scene labels — computed once on first call
# to tag_scene() so we don't re-embed the same strings on every photo.
_scene_text_embeddings: dict[str, list[float]] | None = None


def _get_scene_text_embeddings() -> dict[str, list[float]]:
    """Compute (or return cached) text embeddings for all SCENE_LABELS."""
    global _scene_text_embeddings
    if _scene_text_embeddings is None:
        logger.info("Computing scene-label text embeddings (one-time, ~1s)…")
        _scene_text_embeddings = {}
        for label, prompt in SCENE_LABELS.items():
            vec = embed_text(prompt)
            if vec is not None:
                _scene_text_embeddings[label] = vec
        logger.info("Scene embeddings ready for %d labels.", len(_scene_text_embeddings))
    return _scene_text_embeddings


def tag_scene(image_embedding: list[float]) -> tuple[str | None, float]:
    """
    Classify an image embedding into the nearest SCENE_LABELS category.

    Uses the cached SigLIP text embeddings for the scene labels, so the
    text model must be loaded at least once before calling this. Call
    _get_scene_text_embeddings() to trigger load if needed.

    Args:
        image_embedding: L2-normalised 768-dim vector from embed_image().

    Returns:
        (scene_name, confidence) where confidence is the cosine similarity
        to the best-matching scene label (0.0–1.0). Returns (None, 0.0) on
        failure (missing embeddings, zero vector, etc.).
    """
    import numpy as np

    scene_embeddings = _get_scene_text_embeddings()
    if not scene_embeddings or not image_embedding:
        return None, 0.0

    img_arr = np.array(image_embedding, dtype=np.float32)
    best_label: str | None = None
    best_score: float = -1.0

    for label, text_vec in scene_embeddings.items():
        score = float(np.dot(img_arr, np.array(text_vec, dtype=np.float32)))
        if score > best_score:
            best_score = score
            best_label = label

    return best_label, max(0.0, round(best_score, 4))


# Per-photo content-aware axes scored via SigLIP zero-shot. Each axis is a
# (positive prompt, negative prompt) pair — the final 0–1 score comes from
# sigmoid((sim_pos - sim_neg) / TEMP). Cosine deltas between paired prompts
# usually land in [-0.15, +0.15], and TEMP=0.05 spreads that into [~0.05, ~0.95]
# so the score actually moves across photos instead of clumping near 0.5.
#
# background_distraction is inverted by design: positive prompt = HIGH distraction.
# A high score means the background competes with the subject (i.e. bad), so
# auto-cull rejects on a HIGH value, not a low one.
CONCEPT_PROMPTS: dict[str, tuple[str, str]] = {
    "subject_prominence": (
        "a photograph where the main subject is clearly the focal point and instantly draws the eye",
        "a photograph where the main subject is small, hidden, or lost in the scene",
    ),
    "background_distraction": (
        "a photograph with a cluttered, busy background that competes with the subject",
        "a photograph with a clean, simple background that supports the subject",
    ),
    "eye_contact": (
        "a portrait where the subject is looking directly at the camera",
        "a portrait where the subject is looking away from the camera",
    ),
    "decisive_moment": (
        "a photograph capturing a fleeting moment of genuine gesture, action, or expression",
        "a photograph of a static, posed, or unremarkable moment",
    ),
}

# Temperature for the sigmoid that maps cosine-delta → 0–1.
# Empirically the pos-vs-neg cosine spread for SigLIP base sits around ±0.10;
# 0.05 spreads that into a usable 0–1 range without saturating at the tails.
_CONCEPT_SIGMOID_TEMPERATURE: float = 0.05

# Cache the pos/neg text embeddings for each concept — computed once on first
# call so we don't re-embed the same 8 strings on every photo.
_concept_text_embeddings: dict[str, tuple[list[float], list[float]]] | None = None


def _get_concept_text_embeddings() -> dict[str, tuple[list[float], list[float]]]:
    """Compute (or return cached) pos/neg text embeddings for CONCEPT_PROMPTS.

    Triggers a text-encoder load on first call (~1s warm-up after the first
    embed_text). Subsequent calls are dict lookups.
    """
    global _concept_text_embeddings
    if _concept_text_embeddings is None:
        logger.info("Computing concept text embeddings (one-time, ~1s)…")
        cache: dict[str, tuple[list[float], list[float]]] = {}
        for axis, (pos_prompt, neg_prompt) in CONCEPT_PROMPTS.items():
            pos_vec = embed_text(pos_prompt)
            neg_vec = embed_text(neg_prompt)
            if pos_vec is None or neg_vec is None:
                logger.warning("Could not embed concept prompts for %s — skipping.", axis)
                continue
            cache[axis] = (pos_vec, neg_vec)
        _concept_text_embeddings = cache
        logger.info("Concept embeddings ready for %d axes.", len(cache))
    return _concept_text_embeddings


def score_concepts(
    image_embedding: list[float] | None,
    face_detected: bool,
) -> dict[str, float | None]:
    """Score one image embedding against the CONCEPT_PROMPTS axes.

    Args:
        image_embedding: L2-normalised 768-dim vector from embed_image().
                         None or empty → all axes None.
        face_detected:   When False, eye_contact returns None (no portrait
                         → no meaningful eye-contact reading). The personal
                         model's imputer fills NaN at train time so the column
                         is still safe to include in the feature vector.

    Returns:
        {axis_name: 0.0–1.0 score or None}. Always returns the same key set
        as CONCEPT_PROMPTS so callers don't need defensive lookups.
    """
    import math
    import numpy as np

    keys = list(CONCEPT_PROMPTS.keys())
    if not image_embedding:
        return {k: None for k in keys}

    concept_embeddings = _get_concept_text_embeddings()
    if not concept_embeddings:
        return {k: None for k in keys}

    img_arr = np.array(image_embedding, dtype=np.float32)
    scores: dict[str, float | None] = {}
    for axis in keys:
        # eye_contact only meaningful for face photos — gate to None otherwise
        # so the personal-model imputer can fill the NaN at train time.
        if axis == "eye_contact" and not face_detected:
            scores[axis] = None
            continue

        pair = concept_embeddings.get(axis)
        if pair is None:
            scores[axis] = None
            continue
        pos_vec, neg_vec = pair
        sim_pos = float(np.dot(img_arr, np.array(pos_vec, dtype=np.float32)))
        sim_neg = float(np.dot(img_arr, np.array(neg_vec, dtype=np.float32)))
        # sigmoid((sim_pos - sim_neg) / T) → spreads typical ±0.10 cosine delta
        # into the 0–1 range without saturating.
        z = (sim_pos - sim_neg) / _CONCEPT_SIGMOID_TEMPERATURE
        scores[axis] = round(1.0 / (1.0 + math.exp(-z)), 4)
    return scores


def embedding_to_json(embedding: list[float] | None) -> str | None:
    """Serialize a float list to a compact JSON string for SQLite storage."""
    if embedding is None:
        return None
    return json.dumps(embedding, separators=(",", ":"))


def json_to_embedding(json_str: str | None) -> list[float] | None:
    """Deserialize a JSON string from SQLite back to a float list."""
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None
