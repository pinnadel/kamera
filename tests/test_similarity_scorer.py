"""Smoke tests for the SigLIP-2 similarity scorer.

These pin a non-obvious quirk: `google/siglip2-base-patch16-224` ships with
SigLIP-2 weights but reports `model_type="siglip"` — the checkpoint uses the
SigLIP-1 architecture with new weights. AutoModel resolves it to
`SiglipModel` (both encoders); we read `.vision_model` and `.text_model`.

If a future "modernization" swaps AutoModel for `Siglip2Model` (NaFlex
variant), analysis breaks with a conv-stem shape mismatch on every photo.
This test asserts the loaded class is SiglipModel so the regression is
caught in CI.
"""
import pytest

from phase2_quality import similarity_scorer


def test_siglip2_loads_with_siglip1_class():
    """The 2-prefix is in the model ID but NOT in the architecture class."""
    try:
        model, _processor, _tokenizer = similarity_scorer._get_siglip()
    except Exception as exc:
        pytest.skip(f"Model load failed (likely offline / no HF cache): {exc}")
    assert type(model).__name__ == "SiglipModel", (
        f"Expected SiglipModel (SigLIP-1 architecture, SigLIP-2 weights); "
        f"got {type(model).__name__}. See similarity_scorer.py docstring for why."
    )
    assert type(model.vision_model).__name__ == "SiglipVisionTransformer"
    assert type(model.text_model).__name__ == "SiglipTextTransformer"


# ---------------------------------------------------------------------------
# Layered union gate tests
#
# Background: when SigLIP cosine is the only signal, photos of "same subject
# category, different scene" (e.g. baby-in-pastels indoor vs outdoor)
# transitively chain-merge into one giant cluster. The layered gate adds
# face-identity / histogram / scene-tag checks on top of the cosine
# threshold to prevent that.
# ---------------------------------------------------------------------------

import os
import tempfile

import numpy as np

from phase2_quality.similarity_scorer import (
    _cosine,
    _scenes_blocked,
    _pairwise_can_union,
    group_by_similarity,
)


def _l2(v):
    a = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return (a / n).tolist() if n > 0 else a.tolist()


def test_scenes_blocked_symmetric():
    assert _scenes_blocked("indoor", "landscape") is True
    assert _scenes_blocked("landscape", "indoor") is True
    assert _scenes_blocked("portrait", "macro") is True
    assert _scenes_blocked("indoor", "portrait") is False  # not in blocker set
    assert _scenes_blocked("indoor", "indoor") is False    # same tag never blocks
    assert _scenes_blocked(None, "indoor") is False        # missing tag never blocks


def test_face_identity_gate_allows_same_person():
    """When both photos have face_embedding and they're similar, allow union."""
    same_face = _l2([1.0, 0.0, 0.0, 0.0])
    extras = {
        1: {"face_embedding": same_face, "scene": None, "file_path": None},
        2: {"face_embedding": same_face, "scene": None, "file_path": None},
    }
    assert _pairwise_can_union(1, 2, extras, {}) is True


def test_face_identity_gate_blocks_different_person():
    """When both have face_embedding and they're dissimilar, deny union."""
    extras = {
        1: {"face_embedding": _l2([1.0, 0.0, 0.0, 0.0]), "scene": None, "file_path": None},
        2: {"face_embedding": _l2([0.0, 1.0, 0.0, 0.0]), "scene": None, "file_path": None},
    }
    assert _pairwise_can_union(1, 2, extras, {}) is False


def test_scene_blocker_denies_face_less_opposites():
    """Face-less photos with incompatible scene tags must not union."""
    extras = {
        1: {"face_embedding": None, "scene": "indoor",    "file_path": None},
        2: {"face_embedding": None, "scene": "landscape", "file_path": None},
    }
    # Histogram is skipped because the scene-blocker fires first.
    assert _pairwise_can_union(1, 2, extras, {}) is False


def test_mixed_face_no_face_falls_through_to_histogram():
    """When exactly one has a face, the face-identity gate doesn't apply
    and the call falls through to scene + histogram."""
    extras = {
        1: {"face_embedding": _l2([1.0, 0.0]), "scene": "indoor",  "file_path": None},
        2: {"face_embedding": None,            "scene": "indoor",  "file_path": None},
    }
    # No file_path → histogram fail-opens. Should allow union.
    assert _pairwise_can_union(1, 2, extras, {}) is True


def _write_solid_jpeg(path: str, rgb: tuple[int, int, int], size: int = 64) -> None:
    from PIL import Image as _Image
    img = _Image.new("RGB", (size, size), color=rgb)
    img.save(path, format="JPEG", quality=90)


def test_histogram_gate_denies_color_opposites():
    """Two solid-color JPEGs of opposing colors must not union once their
    histograms are computed."""
    with tempfile.TemporaryDirectory() as tmp:
        red  = os.path.join(tmp, "red.jpg")
        blue = os.path.join(tmp, "blue.jpg")
        _write_solid_jpeg(red,  (255,   0,   0))
        _write_solid_jpeg(blue, (  0,   0, 255))
        extras = {
            1: {"face_embedding": None, "scene": None, "file_path": red},
            2: {"face_embedding": None, "scene": None, "file_path": blue},
        }
        assert _pairwise_can_union(1, 2, extras, {}) is False


def test_histogram_gate_allows_color_match():
    """Two solid-red JPEGs (near-identical histograms) must union."""
    with tempfile.TemporaryDirectory() as tmp:
        red1 = os.path.join(tmp, "red1.jpg")
        red2 = os.path.join(tmp, "red2.jpg")
        _write_solid_jpeg(red1, (255, 0, 0))
        _write_solid_jpeg(red2, (255, 0, 0))
        extras = {
            1: {"face_embedding": None, "scene": None, "file_path": red1},
            2: {"face_embedding": None, "scene": None, "file_path": red2},
        }
        assert _pairwise_can_union(1, 2, extras, {}) is True


def test_no_extras_means_legacy_behaviour():
    """When extras is None (legacy callers), the gate fails-open and
    pure-cosine union-find applies."""
    # Two near-identical embeddings: should union under legacy path.
    v1 = _l2([1.0, 0.0, 0.0, 0.0])
    v2 = _l2([0.99, 0.01, 0.0, 0.0])
    items = [(1, v1, 1000.0), (2, v2, 1001.0)]
    groups = group_by_similarity(items, threshold=0.90, time_gap_seconds=60.0)
    assert groups == [[1, 2]]


def test_layered_gate_splits_cross_scene_cluster():
    """End-to-end: two face-less photos with high SigLIP cosine but opposing
    scene tags must end up in separate groups (or no group at all)."""
    # Build two embeddings with cosine ~ 0.95 — well above 0.90 threshold.
    v1 = _l2([1.0,  0.0, 0.0, 0.0])
    v2 = _l2([0.95, 0.05, 0.0, 0.0])
    items = [(1, v1, 1000.0), (2, v2, 1001.0)]
    extras = {
        1: {"face_embedding": None, "scene": "indoor",    "file_path": None},
        2: {"face_embedding": None, "scene": "landscape", "file_path": None},
    }
    groups = group_by_similarity(
        items, threshold=0.90, time_gap_seconds=60.0, extras=extras,
    )
    # Should produce zero groups of size ≥ 2 because the only candidate
    # edge gets gated out.
    assert groups == []


def test_time_gap_default_is_60_seconds():
    """Pin the default: signature default must be 60.0 after the
    2026-05-14 scene-fusion fix."""
    import inspect
    sig = inspect.signature(group_by_similarity)
    assert sig.parameters["time_gap_seconds"].default == 60.0
