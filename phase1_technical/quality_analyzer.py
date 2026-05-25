"""
Phase 1 Technical Analyzer
Combines sharpness and exposure into a single quality score.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class StopRequested(Exception):
    """Raised mid-analysis when the caller's stop_event has been set."""
    pass

from phase1_technical.batch_sharpness_analyzer import (
    detect_sharpness as _detect_sharpness,
    detect_sharpness_array as _detect_sharpness_array,
    get_threshold,
)
from phase1_technical.exposure import analyze_exposure, analyze_exposure_array, compute_histogram
from phase2_quality.face_analyzer import analyze_faces, analyze_faces_array
from phase2_quality.face_identity import embed_face as _embed_face
from phase2_quality.iqa_scorer import score_image as _score_iqa, score_image_pil as _score_iqa_pil
from phase2_quality.aesthetic_scorer import score_image as _score_aesthetic, score_image_pil as _score_aesthetic_pil
from phase2_quality.similarity_scorer import embed_image as _embed_image, embed_image_pil as _embed_image_pil, tag_scene as _tag_scene, score_concepts as _score_concepts, json_to_embedding as _json_to_embedding


DEFAULT_SHARPNESS_WEIGHT       = 0.65
DEFAULT_KEEP_THRESHOLD         = 70.0  # personal model: score ≥ keep → keep
DEFAULT_MAYBE_THRESHOLD        = 45.0  # personal model: score ≥ maybe → maybe (else reject)
DEFAULT_FALLBACK_KEEP          = 60.0  # threshold mode (no model): overall ≥ keep → keep
DEFAULT_FALLBACK_MAYBE         = 40.0  # threshold mode (no model): overall ≥ maybe + sharpness OK → maybe
DEFAULT_FALLBACK_SHARPNESS_FLOOR = 40.0  # threshold mode: sharpness_score below this is auto-rejected
DEFAULT_FACE_SHARP_FLOOR       = 20.0  # face-soft instant reject when frame is sharp
DEFAULT_REJECT_SOFT_FACE       = True
DEFAULT_REJECT_BLURRY_FRAME    = True
DEFAULT_REJECT_CLOSED_EYES     = True
# When True, the closed-eyes reject only fires when EVERY detected face has
# closed eyes. The legacy `eyes_open` column stores only the primary face's
# state, so we conservatively scope this to single-face shots — group photos
# never auto-reject for blinks.
DEFAULT_REJECT_CLOSED_EYES_ALL_FACES = False
# EXIF-based instant rejects — off by default (require user opt-in).
DEFAULT_REJECT_RECIPROCAL_RULE  = False  # shutter too slow for focal length (handheld only)
DEFAULT_REJECT_ABOVE_ISO_CEILING = False
DEFAULT_ISO_CEILING             = 0.0    # 0 = disabled; users set their own noise threshold
# SigLIP-derived content axis (v0.13). Gated on face_detected so we don't reject
# clean-background landscapes that happen to score 0.9 on the "busy" prompt
# (the signal is most reliable for subject-vs-background portraits).
DEFAULT_REJECT_HIGH_BACKGROUND_DISTRACTION = False
# Calibrated for SigLIP-2's narrower sigmoid distribution (PR2, 2026-05-12).
# Survey of 60 cached preview JPGs: bg-distraction min=0.45, p50=0.55, p90=0.59,
# max=0.61. The previous default 0.85 was calibrated for SigLIP-1's wider
# spread; with SigLIP-2 it never fires. 0.65 sits just above the observed p99
# so it catches the busiest-backdrop tail of a representative shoot while
# staying inert on the clean-background majority. Re-tune per shoot via
# Settings → Model after you've seen the rule fire (or not) on real photos.
DEFAULT_BACKGROUND_DISTRACTION_CEILING     = 0.65  # 0.0-1.0; only fires above this

# PR3 (2026-05-12): when the personal model is ready, photos whose ensemble
# prediction has a high std_dev AND whose personal_score lands within ±std_dev
# of a decision boundary are routed to "maybe" instead of being committed to
# a hard keep/reject. Reduces flip-flopping near the cutoffs.
DEFAULT_AUTO_CULL_UNCERTAIN_TO_MAYBE = True
# Threshold in 0-100 personal_score units. std_dev below this is treated as
# confident; at or above this AND near a boundary triggers the routing.
# Default 8.0 = roughly one "tier" of disagreement among ensemble members
# on the personal_score scale.
DEFAULT_UNCERTAINTY_THRESHOLD: float = 8.0

# How often analyze_photo_quality should call torch.mps.empty_cache().
# Every photo costs ~5-20ms and 741 calls = 3.7-14.8s of pure cache flush
# overhead. The `del rgb_full, pil_full` lines on the same path do the
# real GC work (the numpy buffers were the multi-MB allocations); the
# empty_cache() flush only catches MPS staging-memory drift, which doesn't
# need to fire every photo. Every 10 photos still bounds RSS and saves
# ~3-13s on a typical batch. The counter is module-level so a single batch
# thread sees consistent behaviour.
_MPS_FLUSH_EVERY = 10
_mps_call_counter = 0


def _read_float(key: str, default: float, lo: float, hi: float) -> float:
    try:
        from backend.database import get_setting
        raw = get_setting(key)
        if raw is None:
            return default
        v = float(raw)
        if lo <= v <= hi:
            return v
    except Exception:
        pass
    return default


def _read_bool(key: str, default: bool) -> bool:
    try:
        from backend.database import get_setting
        raw = get_setting(key)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return default


def get_sharpness_weight() -> float:
    return _read_float("sharpness_weight", DEFAULT_SHARPNESS_WEIGHT, 0.0, 1.0)


def compute_overall_score(
    sharpness_score: float,
    exposure_score: float,
    sharpness_weight: float | None = None,
) -> float:
    """Weighted blend using the user's configured sharpness weight.

    Pass `sharpness_weight` explicitly when calling from a hot loop to skip
    the per-call DB read. The batch loop in analysis.py reads the weight
    ONCE before iterating photos and threads it through here — that saves
    one sqlite3.connect open/commit/close per photo (≈10ms × 741 = ~7s
    on a typical batch). Settings can't change mid-batch anyway.
    """
    w = sharpness_weight if sharpness_weight is not None else get_sharpness_weight()
    return sharpness_score * w + exposure_score * (1.0 - w)


def get_decision_thresholds() -> dict:
    """Read all auto-decision thresholds + instant-reject toggles in one go."""
    return {
        "keep_threshold":            _read_float("keep_threshold",            DEFAULT_KEEP_THRESHOLD,         0.0, 100.0),
        "maybe_threshold":           _read_float("maybe_threshold",           DEFAULT_MAYBE_THRESHOLD,        0.0, 100.0),
        "fallback_keep":             _read_float("fallback_keep",             DEFAULT_FALLBACK_KEEP,          0.0, 100.0),
        "fallback_maybe":            _read_float("fallback_maybe",            DEFAULT_FALLBACK_MAYBE,         0.0, 100.0),
        "fallback_sharpness_floor":  _read_float("fallback_sharpness_floor",  DEFAULT_FALLBACK_SHARPNESS_FLOOR, 0.0, 100.0),
        "face_sharpness_floor":      _read_float("face_sharpness_floor",      DEFAULT_FACE_SHARP_FLOOR,       0.0, 100.0),
        "reject_soft_face":           _read_bool ("reject_soft_face",           DEFAULT_REJECT_SOFT_FACE),
        "reject_blurry_frame":        _read_bool ("reject_blurry_frame",        DEFAULT_REJECT_BLURRY_FRAME),
        "reject_closed_eyes":         _read_bool ("reject_closed_eyes",         DEFAULT_REJECT_CLOSED_EYES),
        "reject_closed_eyes_all_faces": _read_bool ("reject_closed_eyes_all_faces", DEFAULT_REJECT_CLOSED_EYES_ALL_FACES),
        "reject_reciprocal_rule":     _read_bool ("reject_reciprocal_rule",     DEFAULT_REJECT_RECIPROCAL_RULE),
        "reject_above_iso_ceiling":   _read_bool ("reject_above_iso_ceiling",   DEFAULT_REJECT_ABOVE_ISO_CEILING),
        "iso_ceiling":                _read_float("iso_ceiling",                DEFAULT_ISO_CEILING, 0.0, 204800.0),
        "reject_high_background_distraction": _read_bool(
            "reject_high_background_distraction", DEFAULT_REJECT_HIGH_BACKGROUND_DISTRACTION),
        "background_distraction_ceiling":     _read_float(
            "background_distraction_ceiling", DEFAULT_BACKGROUND_DISTRACTION_CEILING, 0.5, 0.99),
        # PR3 boundary routing.
        "auto_cull_uncertain_to_maybe": _read_bool(
            "auto_cull_uncertain_to_maybe", DEFAULT_AUTO_CULL_UNCERTAIN_TO_MAYBE),
        "uncertainty_threshold":        _read_float(
            "uncertainty_threshold", DEFAULT_UNCERTAINTY_THRESHOLD, 0.0, 50.0),
    }


def calculate_sharpness(image_path: str) -> dict:
    """
    Run sharpness detection and return a normalized 0-100 score.

    Conversion: score = 50 at the per-format threshold, 100 at 2× threshold.
    Below threshold = blurry territory (<50), above = sharp (50-100).
    """
    path = Path(image_path)
    result = _detect_sharpness(path)

    if result is None:
        raise ValueError(f"Could not analyze sharpness: {image_path}")

    threshold = get_threshold(path)
    ratio = result['normalized_score'] / threshold
    sharpness_score = min(100, round(ratio * 50))

    if sharpness_score >= 70:
        sharpness_label = "Sharp"
    elif sharpness_score >= 40:
        sharpness_label = "Borderline"
    else:
        sharpness_label = "Blurry"

    return {
        'sharpness_score': sharpness_score,
        'laplacian_variance': round(result['raw_score'], 1),
        'normalized_score': round(result['normalized_score'], 1),
        'sharpness_label': sharpness_label,
        'width': result['width'],
        'height': result['height'],
    }


def analyze_photo_quality(
    image_path: str,
    stop_event: threading.Event | None = None,
    sharpness_weight: float | None = None,
) -> dict:
    """
    Run full technical analysis on a photo (Phase 1 + Phase 2 face signals).

    Weighting rationale:
    - Default 65% sharpness / 35% exposure — sharpness is non-recoverable,
      exposure is recoverable in RAW. User can adjust this split via
      Settings → Model → Scoring weights (compute_overall_score reads it live).
    Overall score uses Phase 1 signals only; face signals are additive data
    for future Phase 3 ML training — not folded into the numeric score
    so existing calibration is unchanged.

    stop_event: optional threading.Event the batch loop can set to abort mid-photo.
      Checked at every scorer boundary; if set, raises StopRequested and tears
      down the worker pool with cancel_futures=True so queued work dies fast.
      Already-running futures still finish (Python can't preempt threads). The
      RAW decode itself is uncancellable — stop is honored after the decode.
    """
    def _check_stop():
        if stop_event is not None and stop_event.is_set():
            raise StopRequested()

    path = Path(image_path)
    if path.suffix.lower() in (".raf", ".nef", ".cr2", ".arw", ".dng"):
        import rawpy
        import numpy as _np
        from PIL import Image as _PILImage
        import io as _io

        # One decode for everything. Previously the pipeline paid postprocess()
        # twice (once in calculate_sharpness via batch_sharpness_analyzer, once
        # here for the ML scorers). Each call on a 24 MP RAF costs 2–5 s.
        # Now: decode once → share the numpy array with sharpness + face,
        # and a PIL view with iqa + aesthetic + embedding. Exposure still uses
        # the fast embedded JPEG thumbnail via load_as_gray(), so it stays on
        # its own path and doesn't need the decoded array.
        try:
            with rawpy.imread(str(path)) as raw:
                rgb_full = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
            pil_full = _PILImage.fromarray(rgb_full)
        except rawpy.LibRawFileUnsupportedError:
            # Nikon Z6 III / Z8 / Z9 write NEFs with "High Efficiency*" compression
            # that current LibRaw can't demosaic. The full-resolution embedded
            # JPEG (extracted via extract_thumb) is fine for culling — same
            # 6048×4032 dimensions, just baked colors. Sharpness/exposure/face/
            # IQA/aesthetic/embedding all work on JPEG input.
            with rawpy.imread(str(path)) as raw:
                thumb = raw.extract_thumb()
            if thumb.format != rawpy.ThumbFormat.JPEG:
                raise
            pil_full = _PILImage.open(_io.BytesIO(thumb.data)).convert("RGB")
            rgb_full = _np.asarray(pil_full)

        # Stop check: RAW decode just finished (uncancellable). If the user
        # pressed Stop while we were demosaicing, bail before launching scorers.
        _check_stop()

        # All scorers share the already-decoded rgb_full / pil_full. Previously
        # exposure re-opened the RAW file and decoded the embedded JPEG thumbnail
        # a second time (1–3s wasted per photo on RAF/NEF). analyze_exposure_array
        # converts the RGB array to grayscale in numpy (~50ms) and runs the same
        # math as the file-path version.
        pool = ThreadPoolExecutor(max_workers=6)
        try:
            fut_sharp  = pool.submit(_detect_sharpness_array, rgb_full)
            fut_exp    = pool.submit(analyze_exposure_array,  rgb_full)
            fut_face   = pool.submit(analyze_faces_array,     rgb_full)
            fut_iqa    = pool.submit(_score_iqa_pil,          pil_full)
            fut_aes    = pool.submit(_score_aesthetic_pil,    pil_full)
            fut_embed  = pool.submit(_embed_image_pil,        pil_full)
            fut_hist   = pool.submit(compute_histogram,       rgb_full)

            def _get(fut, step_name):
                _check_stop()
                try:
                    return fut.result()
                except StopRequested:
                    raise
                except Exception as exc:
                    raise RuntimeError(f"[{step_name}] {exc}") from exc

            raw_sharpness     = _get(fut_sharp, "sharpness")
            exposure_result   = _get(fut_exp,   "exposure")
            face_result       = _get(fut_face,  "face-detection")
            iqa_result        = _get(fut_iqa,   "iqa-scoring")
            aesthetic_result  = _get(fut_aes,   "aesthetic-scoring")
            similarity_result = _get(fut_embed, "embedding")
            histogram_result  = _get(fut_hist,  "histogram")
        except StopRequested:
            # Kill queued futures immediately. Running ones can't be preempted
            # but at least the unstarted ones don't block shutdown.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        if raw_sharpness is None:
            raise ValueError(f"Could not analyze sharpness: {image_path}")

        # Face identity embedding — sequential after face detection completes
        # because it needs the bbox. ~30-80 ms on MPS, fine after a 15 s RAW
        # decode. Stored on the face_result dict so the writer can serialise
        # it with the rest of the face data.
        #
        # _check_stop bookends: pre- so we don't pay the embed cost when the
        # user has already pressed Stop; post- so the next photo's RAW decode
        # doesn't start when cold-loading the FaceNet weights stalled stop
        # acknowledgment by 1-3 s on first photo.
        if face_result.get('face_detected') and face_result.get('face_bbox'):
            _check_stop()
            face_result['face_embedding'] = _embed_face(rgb_full, face_result['face_bbox'])
        _check_stop()

        threshold = get_threshold(path)
        ratio = raw_sharpness['normalized_score'] / threshold
        sharpness_score = min(100, round(ratio * 50))
        sharpness_label = "Sharp" if sharpness_score >= 70 else "Borderline" if sharpness_score >= 40 else "Blurry"
        sharpness_result = {
            'sharpness_score': sharpness_score,
            'laplacian_variance': round(raw_sharpness['raw_score'], 1),
            'normalized_score': round(raw_sharpness['normalized_score'], 1),
            'sharpness_label': sharpness_label,
            'width': raw_sharpness['width'],
            'height': raw_sharpness['height'],
        }

        # Drop heavy references immediately — the multi-MB numpy buffers in
        # rgb_full / pil_full are the real memory pressure, and `del` makes
        # them eligible for GC right now. Flush MPS staging memory only every
        # _MPS_FLUSH_EVERY photos: empty_cache() costs ~5-20ms per call and
        # the `del` already handles the per-photo GC, so a less aggressive
        # cadence still bounds RSS without paying the flush cost 741 times.
        del rgb_full, pil_full
        global _mps_call_counter
        _mps_call_counter += 1
        if _mps_call_counter % _MPS_FLUSH_EVERY == 0:
            try:
                import torch
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
            except Exception:
                # Never let cleanup break a successful analysis.
                pass
    else:
        # JPEG/PNG — cheap to decode, use path-based scorers + sequential Phase 1.
        sharpness_result = calculate_sharpness(image_path)
        _check_stop()
        exposure_result = analyze_exposure(image_path)
        _check_stop()
        # Decode once for the histogram AND face-identity embedding.
        # compute_histogram needs an RGB array (not a path); the face embed
        # later in this branch also needs the same array. On HIF/HEIC files
        # this decode is 1-3 s — doing it twice (as the prior code did)
        # silently doubled the Stop-acknowledge latency.
        rgb_jpeg = None
        try:
            from PIL import Image as _PILImage
            import numpy as _np
            with _PILImage.open(image_path) as _pil:
                rgb_jpeg = _np.asarray(_pil.convert("RGB"))
            histogram_result = compute_histogram(rgb_jpeg)
        except Exception:
            histogram_result = None
        _check_stop()
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            fut_face  = pool.submit(analyze_faces,    image_path)
            fut_iqa   = pool.submit(_score_iqa,       image_path)
            fut_aes   = pool.submit(_score_aesthetic, image_path)
            fut_embed = pool.submit(_embed_image,     image_path)

            def _get(fut, step_name):
                _check_stop()
                try:
                    return fut.result()
                except StopRequested:
                    raise
                except Exception as exc:
                    raise RuntimeError(f"[{step_name}] {exc}") from exc

            face_result       = _get(fut_face,  "face-detection")
            iqa_result        = _get(fut_iqa,   "iqa-scoring")
            aesthetic_result  = _get(fut_aes,   "aesthetic-scoring")
            similarity_result = _get(fut_embed, "embedding")
        except StopRequested:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        # Face identity embedding for JPEG/PNG path. Reuses the array already
        # decoded above so a HIF/HEIC file isn't decoded twice. Bookended with
        # _check_stop() like the RAW path so Stop is honoured before the next
        # photo's RAW decode starts (FaceNet cold-load is 1-3 s on first call).
        if face_result.get('face_detected') and face_result.get('face_bbox') and rgb_jpeg is not None:
            _check_stop()
            try:
                face_result['face_embedding'] = _embed_face(
                    rgb_jpeg, face_result['face_bbox'],
                )
            except Exception:
                logger.exception("face_embedding (jpeg path) failed for %s", image_path)
            _check_stop()
        del rgb_jpeg

    overall_score = compute_overall_score(
        sharpness_result['sharpness_score'],
        exposure_result['exposure_score'],
        sharpness_weight=sharpness_weight,
    )

    # Zero-shot scene tagging — free cosine product on the already-computed
    # image embedding against pre-cached scene-label text embeddings.
    # The scene label text embeddings are computed once (triggering the text
    # model load) and then cached for all subsequent photos. ~0 ms per photo
    # after the first batch call.
    scene_label: str | None = None
    scene_confidence: float = 0.0
    img_emb = similarity_result.get("embedding") if similarity_result else None
    if img_emb is not None:
        try:
            scene_label, scene_confidence = _tag_scene(img_emb)
        except Exception:
            pass  # scene tag is non-critical; log is swallowed to avoid spamming

    # Same trick for SigLIP zero-shot content axes (subject prominence,
    # background distraction, eye contact, decisive moment). Reuses the cached
    # image embedding — no extra decode/encode pass. eye_contact is None for
    # non-portrait shots; the personal-model imputer fills the NaN at train time.
    concepts: dict[str, float | None] = {
        "subject_prominence": None,
        "background_distraction": None,
        "eye_contact": None,
        "decisive_moment": None,
    }
    if img_emb is not None:
        try:
            concepts = _score_concepts(
                img_emb,
                face_detected=bool(face_result.get('face_detected')),
            )
        except Exception:
            logger.exception("score_concepts failed (non-critical)")

    return {
        'sharpness': sharpness_result,
        'exposure': exposure_result,
        'face': face_result,
        'iqa': iqa_result,
        'aesthetic': aesthetic_result,
        'similarity': similarity_result,
        'scene': {'scene': scene_label, 'scene_confidence': scene_confidence},
        'concepts': concepts,
        'histogram': histogram_result,
        'overall_quality_score': round(overall_score, 1),
    }


def get_quality_breakdown(image_path: str) -> str:
    """Human-readable explanation of quality assessment."""
    result = analyze_photo_quality(image_path)
    sharpness = result['sharpness']
    exposure = result['exposure']
    face = result['face']

    face_lines = ""
    if face.get('face_detected'):
        eyes = "Open" if face['eyes_open'] else "CLOSED"
        face_lines = (
            f"\n👤 FACE: detected ({face['face_count']} face(s))\n"
            f"   Face Sharpness: {face['face_sharpness_score']}\n"
            f"   Eyes: {eyes} (openness {face['eye_openness_ratio']})\n"
            f"   Face Size: {face['face_size_ratio']:.1%} of frame\n"
        )
    else:
        face_lines = "\n👤 FACE: none detected\n"

    return f"""
📊 QUALITY BREAKDOWN
{'='*50}

🔍 SHARPNESS: {sharpness['sharpness_score']}/100 (Weight: {int(round(get_sharpness_weight() * 100))}%)
   Laplacian Variance: {sharpness['laplacian_variance']:.1f}
   Status: {sharpness['sharpness_label']}

💡 EXPOSURE: {exposure['exposure_score']}/100 (Weight: {100 - int(round(get_sharpness_weight() * 100))}%)
   Mean Brightness: {exposure['mean_brightness']:.1f}/255
   Highlight Clipping: {exposure['highlight_clip_pct']:.2f}%
   Shadow Clipping: {exposure['shadow_clip_pct']:.2f}%
{face_lines}
{'='*50}
OVERALL SCORE: {result['overall_quality_score']}/100
"""
