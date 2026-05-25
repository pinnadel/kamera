"""
Burst ranking via a local vision LLM (Ollama).

Sends N preview JPEGs of a burst to a vision-capable Ollama model in a single
chat call. Asks the model to rank them best-to-worst with a one-line reason
per photo. Result is cached in the `burst_rankings` table keyed by the sha1
of the sorted member-ids tuple, so re-opening the same group is instant.

Why a new use of the LLM (and not parsing /generate-explanation):
  The per-photo explanation is absolute paraphrase of numeric scores. Within
  a burst those numbers barely move, so the prose ends up near-identical
  across frames — useless for tiebreaking. A burst rank needs RELATIVE
  judgment: peak smile vs 80 ms before, eyes fully open vs mid-blink, gesture
  apex vs between. That requires the model to see all N frames in one
  context and reason comparatively. Lazy: only fires when the user opens
  GroupLoupe; cached until burst membership changes.

Degrades gracefully: no vision model installed → status="no_vision_model",
caller falls back to score-based AI-pick. Parse failures don't crash.
"""

import base64
import hashlib
import json
import logging
import re
from pathlib import Path

from phase2_quality.llm_explainer import (
    _OLLAMA_BASE,
    _OLLAMA_KEY,
    _OLLAMA_NATIVE,
    _TIMEOUT,
    _VISION_PREFIXES,
    list_models,
)

logger = logging.getLogger(__name__)

# Token budget. Each JSON ranking line is ~30 tokens (index, rank, reason).
# 400 gives headroom for up to a dozen lines plus surrounding brackets/whitespace.
_MAX_TOKENS = 400


# Inflight rank registry. Coalesces concurrent /rank-burst calls for the
# same membership hash so we never fire two qwen requests in parallel for
# the same group — that was a real bug: the prerank worker and the user's
# loupe-open could each launch a call when the user clicked a tile mid-
# prerank. Two concurrent qwen runs on a 6.5 GB-VRAM Mac → OOM / timeout
# (the slower call returns "error"), and the loupe wedged on the failure
# state even though the worker eventually wrote the cache.
#
# Contract: the first caller for a given hash takes the slot (event + None
# result); concurrent callers wait on the event then read the result. The
# slot is cleared once the rank finishes, so the next user reach goes
# through the cache like normal.
import threading as _threading

_inflight_lock = _threading.Lock()
_inflight: dict[str, _threading.Event] = {}
_inflight_results: dict[str, dict] = {}
# Max time a waiter will block on an inflight call before giving up. The
# LLM call itself is bounded by _TIMEOUT in llm_explainer; this is just a
# safety against an absurd lock leak.
_INFLIGHT_WAIT_TIMEOUT = 300.0  # seconds

# Practical caps on burst size.
# - 8 attached images is the sweet spot for "best of burst" decisions on a
#   local 7-8B vision model. Above this the model's comparative reasoning
#   degrades (it starts losing track of which image is which) AND each
#   extra image is another ~400 vision tokens of encoder work — the
#   dominant share of rank-call latency. Bursts larger than 8 are pre-
#   filtered server-side to the top-8 candidates by
#   `backend.group_scoring.top_n_candidates` before the LLM is called.
#   The unranked tail stays in the group at its score-based standing.
# - Reduced from 12 → 8 on 2026-05-17 alongside a resolution cut
#   (640→512px) and intra-burst near-duplicate dedup, after baselining
#   the 12/640 configuration at ~110s/group. See docs/CHANGELOG.md.
# - Below 2 members there's nothing to compare.
_MAX_MEMBERS = 8
_MIN_MEMBERS = 2


def _members_hash(image_ids: list[int]) -> str:
    """Order-independent content-stable hash of a burst's membership.

    Sorting first means [3,1,2] and [1,2,3] share a cache entry — the same
    set of photos should never re-rank just because the caller passed them
    in a different order.
    """
    canonical = ",".join(str(i) for i in sorted(image_ids))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _pick_vision_model() -> str | None:
    """Return the first vision-capable Ollama model installed locally, or None.

    Stricter than llm_explainer's general picker: text-only models can't
    rank images by sight, so we'd rather return None and let the UI nudge
    the user to `ollama pull qwen2.5vl:7b` than burn a call that can't see.
    """
    for m in list_models():
        name = m.lower()
        if any(name.startswith(p) for p in _VISION_PREFIXES):
            return m
    return None


_PROMPT_TEMPLATE = (
    "You are reviewing a burst of {n} nearly-identical photos taken seconds apart. "
    "They show the same subject, scene, and framing — but differ in micro-moments: "
    "peak expression, eyes fully open vs blinking or mid-blink, face sharpness, "
    "decisive moment vs static.\n\n"
    "Photos are attached in order. Image 1 is the first attached, image {n} is the last.\n\n"
    "Rank ALL {n} from best to worst. Return ONLY a JSON array, no prose, no markdown fences:\n"
    "[{{\"image_index\": <1..{n}>, \"rank\": <1..{n}>, \"reason\": \"<≤10 words>\"}}, ...]\n\n"
    "Constraints:\n"
    "- Use every image_index 1..{n} exactly once.\n"
    "- rank=1 is best.\n"
    "- Reasons must be ≤10 words and reference a visible micro-difference.\n\n"
    "Example output (for a 3-photo burst):\n"
    "[{{\"image_index\": 2, \"rank\": 1, \"reason\": \"peak smile, both eyes open\"}}, "
    "{{\"image_index\": 1, \"rank\": 2, \"reason\": \"smile starting, eyes open\"}}, "
    "{{\"image_index\": 3, \"rank\": 3, \"reason\": \"blinking, mid-laugh awkward\"}}]"
)


def _build_prompt(n: int) -> str:
    return _PROMPT_TEMPLATE.format(n=n)


# Long-edge target for previews sent to the vision model. Above this the
# vision encoder's token cost explodes (a 6240×4160 JPEG costs ~33K vision
# tokens per image; even an 8K context can only hold one of those, never
# mind comparing four). Each step down roughly halves vision-encoder cost,
# which dominates total rank-call latency:
#   - 768 → ~1200 image tokens each
#   - 640 → ~600 image tokens each
#   - 512 → ~400 image tokens each
# 512 is the current sweet spot for KaMeRa's portrait-distance bursts —
# face crops are still ~120px wide (enough for "peak smile vs blink"
# decisions), and the smaller payload shaves ~25% off /api/chat wall time
# at n=8 vs. the previous 640px / n=12 configuration. Tested 2026-05-17.
#
# If the resolution changes, invalidate the burst_rankings cache so users
# don't see ranks produced at a different fidelity (the rows themselves
# remain valid JSON, just inconsistent with current behaviour).
_RANK_PREVIEW_LONG_EDGE = 512


def _read_preview_b64(path: str) -> str | None:
    """Read an image from disk, downscale to a sane resolution for the
    vision model, re-encode as JPEG, and return base64. None on read failure.

    Downscaling is critical: a full-res X100VI JPEG (~8 MB, 6240×4160)
    expands to ~33K vision tokens after the encoder — four of those drown
    any local model's context window and stall inference for minutes. At
    768px on the long edge we get crisp-enough images for comparative
    judgment at ~150–600 tokens each, and an 8K context comfortably holds
    a 12-image burst with headroom for prompt + output."""
    try:
        from PIL import Image as _Image
        import io as _io
        with _Image.open(path) as img:
            img = img.convert("RGB")
            # thumbnail preserves aspect ratio and is a no-op when the image
            # is already smaller than the bound.
            img.thumbnail(
                (_RANK_PREVIEW_LONG_EDGE, _RANK_PREVIEW_LONG_EDGE),
                _Image.LANCZOS,
            )
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


# Formats whose source file is already a viewable image — safe to feed
# directly to the vision model when the on-disk preview cache is empty.
_DIRECT_PASS_FORMATS = frozenset({"JPEG", "JPG", "PNG", "HIF", "HEIC", "HEIF", "WEBP"})

# RAW formats handled by the on-demand-generate fallback below. Imported
# from backend.constants so this set stays aligned with the analyzer's
# canonical RAW_FORMATS — only formats the analyzer actually decodes
# belong here, otherwise the fallback would invoke a generator that
# can't produce a preview for the format.
from backend.constants import RAW_FORMATS as _RAW_FALLBACK_FORMATS

# Cache of image_ids whose RAW preview generation has already failed in
# this process, so one bad file doesn't make every member of a burst pay
# the demosaic-then-fail cost. Reset across process restarts.
_raw_preview_failed: set[int] = set()


def _resolve_preview_bytes(row, conn) -> str | None:
    """Get base64-encoded preview bytes for one image row.

    Resolution order:
      1. Cached preview at row['preview_path'] (set for RAFs via /previews/<id>
         or by this fallback's own write-back).
      2. Source file itself for directly-viewable formats (JPEG/PNG/HIF/...).
      3. On-demand generate-and-cache for RAW formats whose preview wasn't
         pre-rendered (Z6III HE* NEFs and any RAF the user never opened).
         Uses the same generator + cache path as /previews/<id>, so the
         next call (and the next viewer load) hits the cache.

    Returns None when none of the above work; caller skips the photo.

    Why path #3 exists: a batch can finish with hundreds of RAW rows whose
    preview_path is still NULL — NEFs that took the extract_thumb fast path
    in the analyzer never write a cached preview, and RAFs only generate
    one when /previews/<id> is hit. Without this fallback every burst the
    prerank worker touches comes back with 0/N readable previews.
    """
    cached = row["preview_path"] if "preview_path" in row.keys() else None
    if cached:
        b64 = _read_preview_b64(cached)
        if b64 is not None:
            return b64

    fmt_raw = (row["format"] if "format" in row.keys() else "") or ""
    fmt = fmt_raw.upper()

    # 2. Source file itself for already-viewable formats.
    if fmt in _DIRECT_PASS_FORMATS:
        src = row["file_path"] if "file_path" in row.keys() else None
        if src:
            return _read_preview_b64(src)

    # 3. RAW without cached preview → generate, cache, write-back, read.
    if fmt in _RAW_FALLBACK_FORMATS:
        img_id = row["id"] if "id" in row.keys() else None
        src = row["file_path"] if "file_path" in row.keys() else None
        if img_id is None or not src:
            return None
        if img_id in _raw_preview_failed:
            return None
        try:
            from pathlib import Path as _Path
            from backend.routers.analysis import (
                _generate_preview as _gen,
                PREVIEW_CACHE_DIR as _CACHE_DIR,
            )
            jpeg_bytes = _gen(_Path(src), fmt)
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file = _CACHE_DIR / f"{img_id}.jpg"
            cache_file.write_bytes(jpeg_bytes)
            try:
                conn.execute(
                    "UPDATE images SET preview_path = ? WHERE id = ?",
                    (str(cache_file), img_id),
                )
                conn.commit()
            except Exception:
                logger.exception(
                    "burst_ranker: cached RAW preview for id=%s but UPDATE failed",
                    img_id,
                )
            return _read_preview_b64(str(cache_file))
        except Exception:
            logger.exception(
                "burst_ranker: RAW preview fallback failed for id=%s (%s) — skipping",
                img_id, src,
            )
            _raw_preview_failed.add(img_id)
            return None

    return None


def _parse_rankings(raw: str, expected_n: int) -> list[dict] | None:
    """Defensive JSON parse — tolerates code fences and leading prose.

    Validation:
      - shape is a JSON array of length expected_n
      - every image_index in 1..expected_n appears exactly once
      - every rank in 1..expected_n appears exactly once
      - reason is a string (any length; we don't enforce ≤10 words at parse time)

    Returns None on any failure; caller treats that as status="error" and
    the frontend falls back to score-based AI-pick.
    """
    # Strip markdown code fences if the model added them despite instructions.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    # Some models add leading prose. Grab the first JSON array we find.
    m = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or len(data) != expected_n:
        return None

    seen_indices: set[int] = set()
    seen_ranks: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            return None
        idx = item.get("image_index")
        rank = item.get("rank")
        reason = item.get("reason", "")
        if not isinstance(idx, int) or not isinstance(rank, int):
            return None
        if not (1 <= idx <= expected_n) or not (1 <= rank <= expected_n):
            return None
        if idx in seen_indices or rank in seen_ranks:
            return None
        if not isinstance(reason, str):
            return None
        seen_indices.add(idx)
        seen_ranks.add(rank)
    return data


def _result(
    status: str,
    *,
    model: str | None,
    members_hash: str | None,
    cached: bool = False,
    rankings: list | None = None,
    evaluated_ids: list[int] | None = None,
    filtered_from: int = 0,
) -> dict:
    """Build a uniform response dict so every return path includes the new
    `evaluated_ids` and `filtered_from` fields. Keeps the call sites below
    short and prevents a missed field on any branch."""
    return {
        "status": status,
        "model": model,
        "members_hash": members_hash,
        "cached": cached,
        "rankings": rankings or [],
        "evaluated_ids": evaluated_ids or [],
        "filtered_from": filtered_from,
    }


def _pre_filter_top_n(image_ids: list[int], conn, n: int) -> list[int]:
    """Trim a burst down to its top-n candidates using the shared scoring
    helper. Pulls the relevant score columns in one SQL round-trip so the
    function is O(1) DB calls regardless of input size."""
    from backend.group_scoring import top_n_candidates

    placeholders = ",".join("?" * len(image_ids))
    rows = conn.execute(
        f"""
        SELECT id, face_detected, face_sharpness_score, eyes_open,
               sharpness_score, iqa_score, aesthetic_score, overall_score
        FROM images WHERE id IN ({placeholders})
        """,
        image_ids,
    ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    # Preserve input order for stable tie-breaking, then drop ids we didn't
    # find rows for (rare — would mean a request referenced a deleted image).
    ordered = [by_id[i] for i in image_ids if i in by_id]
    return [r["id"] for r in top_n_candidates(ordered, n)]


# Cosine threshold above which two photos in the same already-clustered
# burst are treated as near-duplicates and collapsed to one representative
# before the LLM rank call. 0.97 is intentionally tight — the burst is
# already past the 0.90 union-gate, so anything ≥ 0.97 is "two frames
# fired ~50 ms apart with no perceivable change" rather than "same scene,
# different moment." Lower numbers (e.g. 0.95) start collapsing genuine
# micro-moment differences that ARE what the LLM is good at picking out.
# Tunable; if you raise it, the LLM input shrinks and ranks get faster.
_NEAR_DUP_COSINE = 0.97


def _collapse_near_duplicates(
    image_ids: list[int],
    conn,
) -> tuple[list[int], dict[int, list[int]]]:
    """Collapse cosine-near-duplicate frames within a burst to one rep each.

    Greedy union-find: walks pairs in priority order (top_n_candidates'
    scoring), keeps the higher-priority frame as the representative when
    cosine ≥ _NEAR_DUP_COSINE. Returns:
      - reps: representative image_ids in priority order (subset of input)
      - merged: rep_id → list of original ids it absorbed (rep included,
                always at index 0). Lets the caller surface the rank for
                all merged ids together when displaying results.

    Why dedup BEFORE the LLM: vision-encoder cost scales linearly with
    image count, and the model gets distracted by near-identical frames
    (it sometimes picks one of two indistinguishable shots as rank 1 just
    because it happened to be first in the attached order). Sending only
    visibly-different frames is faster AND gives the model a cleaner
    comparative signal.

    Why dedup BEFORE _members_hash: the cache key reflects what was
    actually sent to the LLM. If the same burst comes in again with the
    same dedup result, we still hit the cache.

    Falls back to a no-op (returns image_ids as-is, each mapped to itself)
    if embeddings are missing or any computation throws — better to send
    a duplicate-rich burst than to skip the rank.
    """
    if len(image_ids) < 2:
        return list(image_ids), {i: [i] for i in image_ids}
    try:
        import numpy as _np
        from phase2_quality.similarity_scorer import json_to_embedding
        from backend.group_scoring import top_n_candidates

        placeholders = ",".join("?" * len(image_ids))
        rows = conn.execute(
            f"""
            SELECT id, embedding, face_detected, face_sharpness_score,
                   eyes_open, sharpness_score, iqa_score, aesthetic_score,
                   overall_score
            FROM images WHERE id IN ({placeholders})
            """,
            image_ids,
        ).fetchall()
        by_id = {r["id"]: dict(r) for r in rows}
        # Drop rows we can't dedup (missing embedding) — keep them as-is
        # in the output but they can't merge anyone.
        emb_by_id: dict[int, "_np.ndarray"] = {}
        for img_id in image_ids:
            row = by_id.get(img_id)
            if row is None:
                continue
            vec = json_to_embedding(row["embedding"])
            if vec is None:
                continue
            emb_by_id[img_id] = _np.asarray(vec, dtype=_np.float32)

        if len(emb_by_id) < 2:
            return list(image_ids), {i: [i] for i in image_ids}

        # Walk in priority order so the "kept" rep is the better photo.
        ordered_ids = [
            r["id"]
            for r in top_n_candidates(
                [by_id[i] for i in image_ids if i in by_id],
                len(image_ids),
            )
        ]

        merged_into: dict[int, int] = {}  # absorbed_id → rep_id
        reps: list[int] = []
        for img_id in ordered_ids:
            if img_id in merged_into:
                continue
            reps.append(img_id)
            vec_a = emb_by_id.get(img_id)
            if vec_a is None:
                continue
            for other_id in ordered_ids:
                if other_id == img_id or other_id in merged_into:
                    continue
                # Don't absorb something that's already a rep — only consider
                # ids that haven't been claimed yet AND that we haven't
                # already promoted.
                if other_id in reps:
                    continue
                vec_b = emb_by_id.get(other_id)
                if vec_b is None:
                    continue
                cos = float(_np.dot(vec_a, vec_b))
                if cos >= _NEAR_DUP_COSINE:
                    merged_into[other_id] = img_id

        merged: dict[int, list[int]] = {r: [r] for r in reps}
        for absorbed, rep in merged_into.items():
            merged.setdefault(rep, [rep]).append(absorbed)

        if len(reps) < len(image_ids):
            logger.info(
                "burst_ranker: dedup collapsed %d→%d frames (threshold=%.2f)",
                len(image_ids), len(reps), _NEAR_DUP_COSINE,
            )
        return reps, merged
    except Exception:
        logger.exception("burst_ranker: dedup failed, sending full burst")
        return list(image_ids), {i: [i] for i in image_ids}


def rank_burst(image_ids: list[int], conn) -> dict:
    """Rank a burst of photos with a vision LLM. Lazy + cached.

    Bursts >_MAX_MEMBERS are pre-filtered server-side to the top-N candidates
    by `backend.group_scoring.top_n_candidates` (face_sharpness → eyes_open
    → sharpness → IQA → aesthetic → overall_score) before the LLM is called.
    The non-evaluated photos remain in the group with their score-based
    standing intact — they just don't get an LLM-authored reason.

    Args:
        image_ids: ≥2 image IDs. Order is not significant — the canonical
                   members_hash sorts the (post-filter) list first.
        conn:      sqlite3.Connection from get_db(). We read preview_path
                   for each id and commit a new burst_rankings row on success.

    Returns:
        {
          "status":        "ranked" | "no_vision_model" | "too_few" | "near_duplicates" | "error",
          "model":         str | None,         # the Ollama model name we used (or would have)
          "members_hash":  str | None,         # hash of the EVALUATED (post-filter) set
          "cached":        bool,                # True if served from burst_rankings
          "rankings":      [{"image_id": int, "rank": int, "reason": str}, ...],
          "evaluated_ids": list[int],           # ≤_MAX_MEMBERS sorted ids actually ranked
          "filtered_from": int,                 # original input count (≥ len(evaluated_ids))
        }
    """
    filtered_from = len(image_ids)
    if filtered_from < _MIN_MEMBERS:
        return _result(
            "too_few", model=None, members_hash=None,
            evaluated_ids=[], filtered_from=filtered_from,
        )

    # Pre-filter large bursts before any cache lookup. The cache key is
    # the hash of the trimmed set — two 50-photo groups whose top-N
    # happens to be byte-identical share a cache row, which is fine since
    # the LLM input would also be byte-identical.
    if filtered_from > _MAX_MEMBERS:
        image_ids = _pre_filter_top_n(image_ids, conn, _MAX_MEMBERS)
        if len(image_ids) < _MIN_MEMBERS:
            return _result(
                "error", model=None, members_hash=None,
                evaluated_ids=[], filtered_from=filtered_from,
            )

    # Hash of the post-pre-filter set. Used for cache lookups of BOTH the
    # 'ranked' outcome AND the 'near_duplicates' outcome (the latter is
    # persisted under this hash so the annotator and re-queries can find
    # it without re-running dedup — see v47 migration).
    pre_dedup_hash = _members_hash(image_ids)

    # Cache hit on the pre-dedup hash? Two paths:
    #   - 'near_duplicates' outcome: dedup previously collapsed this burst
    #     below _MIN_MEMBERS. Surface as status='near_duplicates' so the UI
    #     can render the dedicated chip.
    #   - 'ranked' outcome: would only land here if the burst was 'ranked'
    #     without any dedup collapse AND happened to share its post-dedup
    #     hash with its pre-dedup hash (when no near-dups exist). Rare but
    #     valid: serve it.
    pre_cached_row = conn.execute(
        "SELECT rankings_json, model, outcome FROM burst_rankings WHERE members_hash = ?",
        (pre_dedup_hash,),
    ).fetchone()
    if pre_cached_row is not None and pre_cached_row["outcome"] == "near_duplicates":
        return _result(
            "near_duplicates", model=pre_cached_row["model"],
            members_hash=pre_dedup_hash, cached=True,
            evaluated_ids=sorted(image_ids), filtered_from=filtered_from,
        )

    # Collapse near-duplicates so the LLM sees only visibly-different frames.
    # `merged_map` lets us expand the LLM's per-rep rankings back to every
    # absorbed id so the caller knows all original photos' standings.
    reps, merged_map = _collapse_near_duplicates(image_ids, conn)
    if len(reps) < _MIN_MEMBERS:
        # Whole burst collapsed to one frame (or fewer). Nothing to compare.
        # Persist the outcome so future calls short-circuit and the UI can
        # show the 'Near-duplicate frames' chip without re-running dedup.
        try:
            conn.execute(
                """
                INSERT INTO burst_rankings (members_hash, member_ids, rankings_json, model, outcome)
                VALUES (?, ?, '[]', NULL, 'near_duplicates')
                ON CONFLICT(members_hash) DO UPDATE SET
                    member_ids    = excluded.member_ids,
                    rankings_json = '[]',
                    outcome       = 'near_duplicates',
                    created_at    = DATETIME('now')
                """,
                (pre_dedup_hash, json.dumps(sorted(image_ids))),
            )
            conn.commit()
        except Exception:
            logger.exception("burst_ranker: failed to persist near_duplicates outcome")
        return _result(
            "near_duplicates", model=None, members_hash=pre_dedup_hash,
            evaluated_ids=image_ids, filtered_from=filtered_from,
        )
    image_ids = reps

    h = _members_hash(image_ids)
    evaluated_ids = sorted(image_ids)

    # Cache hit on the post-dedup hash? Don't gate on model availability —
    # a cached ranking is still valid even if the user later uninstalled
    # the model that produced it.
    cached_row = conn.execute(
        "SELECT rankings_json, model FROM burst_rankings WHERE members_hash = ? AND outcome = 'ranked'",
        (h,),
    ).fetchone()
    if cached_row is not None:
        try:
            rankings = json.loads(cached_row["rankings_json"])
            return _result(
                "ranked", model=cached_row["model"], members_hash=h,
                cached=True, rankings=rankings,
                evaluated_ids=evaluated_ids, filtered_from=filtered_from,
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            logger.warning("Stale burst_rankings row for %s — re-ranking", h)

    # ── Inflight coalescing ──────────────────────────────────────────────
    # If another caller is already ranking this exact membership, wait for
    # its result instead of starting a parallel qwen call. Two parallel
    # calls on the same Mac would compete for the qwen runner's VRAM and
    # likely OOM or timeout one of them — visible to the user as the
    # familiar "AI rank unavailable" error after the loupe re-opened on
    # a group the prerank worker was still processing.
    with _inflight_lock:
        existing_event = _inflight.get(h)
        if existing_event is None:
            # We're the leader. Claim the slot.
            event = _threading.Event()
            _inflight[h] = event
            _inflight_results.pop(h, None)  # defensive — leftover from a prior leader
            is_leader = True
        else:
            event = existing_event
            is_leader = False

    if not is_leader:
        # Wait for the leader's call to finish, then read its result. We
        # don't re-run the cache lookup here because the leader's success
        # path writes both the cache row AND _inflight_results, so reading
        # _inflight_results is fastest and lets non-success statuses (e.g.
        # "error", "no_vision_model") propagate without a second roundtrip.
        logger.info("burst_ranker: coalescing onto inflight call for hash %s", h)
        if not event.wait(_INFLIGHT_WAIT_TIMEOUT):
            return _result(
                "error", model=None, members_hash=h,
                evaluated_ids=evaluated_ids, filtered_from=filtered_from,
            )
        with _inflight_lock:
            shared = _inflight_results.get(h)
        if shared is not None:
            # Return a copy so the caller's evaluated_ids / filtered_from
            # reflect THEIR input (a later caller may have passed a
            # different pre-filter input even if the post-filter hash
            # matches). Other fields are universally true for this hash.
            out = dict(shared)
            out["evaluated_ids"] = evaluated_ids
            out["filtered_from"] = filtered_from
            out["cached"] = True   # waited on a coalesced call → no new LLM cost
            return out
        # Shared result missing (leader crashed before publish) — fall
        # through and try to run our own call.

    # Leader path: actually compute the rank. Wrapped in a try/finally so
    # that no matter which return branch fires, we publish the result to
    # waiters and clear the inflight slot. If the inner call raises, we
    # publish an error result so waiters don't sit on the event forever.
    leader_result: dict | None = None
    try:
        leader_result = _leader_compute_rank(
            h, evaluated_ids, filtered_from, conn, merged_map,
        )
        return leader_result
    finally:
        with _inflight_lock:
            _inflight_results[h] = leader_result if leader_result is not None else _result(
                "error", model=None, members_hash=h,
                evaluated_ids=evaluated_ids, filtered_from=filtered_from,
            )
            ev = _inflight.pop(h, None)
        if ev is not None:
            ev.set()


def _leader_compute_rank(h, evaluated_ids, filtered_from, conn, merged_map=None):
    """Body of rank_burst once the caller is confirmed the leader for this
    hash. Extracted into its own function so the try/finally in the parent
    can publish + signal regardless of which early-return fires.

    `merged_map` carries the near-dup collapse from rank_burst — if any
    absorbed ids exist, the final rankings are expanded so each absorbed
    photo inherits its representative's rank.
    """
    if merged_map is None:
        merged_map = {i: [i] for i in evaluated_ids}
    model_id = _pick_vision_model()
    if model_id is None:
        return _result(
            "no_vision_model", model=None, members_hash=h,
            evaluated_ids=evaluated_ids, filtered_from=filtered_from,
        )

    # Pull preview JPEGs in stable hash-matching order. The helper tries the
    # cached preview first, then falls back to the source file for already-
    # viewable formats (JPEG/PNG/HIF/etc.), and finally generates+caches a
    # preview on-demand for RAW formats whose preview_path is still NULL
    # (Z6III HE* NEFs that took the extract_thumb fast path in the analyzer,
    # and any RAF the user hasn't opened yet). Side effect: the write-back
    # populates preview_path for /previews/<id> too.
    previews_b64: list[str] = []
    valid_ids: list[int] = []
    for img_id in evaluated_ids:
        row = conn.execute(
            "SELECT id, preview_path, file_path, format FROM images WHERE id = ?",
            (img_id,),
        ).fetchone()
        if row is None:
            continue
        b64 = _resolve_preview_bytes(row, conn)
        if b64 is None:
            continue
        previews_b64.append(b64)
        valid_ids.append(img_id)

    if len(valid_ids) < _MIN_MEMBERS:
        # Diagnose-able log line — silent "error" used to make this case
        # invisible (no Ollama call, no warning, just the chip in the UI).
        logger.warning(
            "burst_ranker: only %d/%d previews readable for hash %s — returning error. "
            "Most common cause: non-RAW analyses with no cached preview and no fallback path.",
            len(valid_ids), len(evaluated_ids), h,
        )
        return _result(
            "error", model=model_id, members_hash=h,
            evaluated_ids=evaluated_ids, filtered_from=filtered_from,
        )

    actual_n = len(valid_ids)

    # Call Ollama's native /api/chat directly instead of going through the
    # OpenAI-compatibility shim. Reason: Ollama's `options.num_ctx` field is
    # the only way to override the per-request context window, and the
    # OpenAI shim silently drops unknown fields (extra_body went into a
    # void). With the default 4K context, 4 attached images overflow the
    # token budget and prompt-processing thrashes for minutes before the
    # request times out.
    #
    # Native /api/chat schema:
    #   { model, messages: [{role, content, images?: [b64...]}], stream,
    #     options: { num_ctx, temperature, num_predict, ... } }
    # Images attach via the `images` field on the user message (already-
    # base64 strings, no data: prefix), NOT via OpenAI's content[] image_url
    # parts — those are an OpenAI invention the native API doesn't speak.
    import httpx as _httpx
    try:
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a photography curator who ranks burst photos by "
                        "comparing micro-moments. Return only valid JSON, no prose."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_prompt(actual_n),
                    "images": previews_b64,
                },
            ],
            "stream": False,
            "options": {
                # qwen2.5vl supports 125K tokens but a 32K KV cache on top
                # of the 5.5 GB Q4 weights overflowed Apple-silicon unified
                # memory on the test machine (Ollama returned HTTP 500
                # "model failed to load"). 8K is the sweet spot: at 512px
                # each preview costs ~400 vision tokens after the encoder,
                # so 8K fits the 8-image cap _MAX_MEMBERS imposes (≤3200
                # image tokens) plus the prompt (~200) and output (160)
                # with plenty of headroom, and the KV cache stays under
                # ~1 GB. (Pre-2026-05-17 this was 12 images at 640px =
                # ~7200 image tokens; current configuration is leaner.)
                "num_ctx": 8192,
                "temperature": 0.0,
                "num_predict": _MAX_TOKENS,
            },
        }
        logger.info(
            "burst_ranker: calling /api/chat model=%s n=%d num_ctx=8192",
            model_id, actual_n,
        )
        r = _httpx.post(
            f"{_OLLAMA_NATIVE}/api/chat",
            json=payload,
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            logger.error("Ollama /api/chat returned HTTP %d: %s", r.status_code, r.text[:200])
            return _result(
                "error", model=model_id, members_hash=h,
                evaluated_ids=evaluated_ids, filtered_from=filtered_from,
            )
        data = r.json()
        # Native /api/chat returns {message: {role, content}, ...}.
        raw = (data.get("message", {}).get("content") or "").strip()
    except Exception:
        logger.exception("Ollama burst-rank call failed for hash %s", h)
        return _result(
            "error", model=model_id, members_hash=h,
            evaluated_ids=evaluated_ids, filtered_from=filtered_from,
        )

    parsed = _parse_rankings(raw, actual_n)
    if parsed is None:
        logger.warning("Ollama burst-rank parse failed for %s; raw=%r", h, raw[:200])
        return _result(
            "error", model=model_id, members_hash=h,
            evaluated_ids=evaluated_ids, filtered_from=filtered_from,
        )

    # Re-key from 1-based attached index → image_id (using the sorted order
    # we attached previews in).
    rankings = [
        {
            "image_id": valid_ids[item["image_index"] - 1],
            "rank": item["rank"],
            "reason": item["reason"],
        }
        for item in parsed
    ]

    # Expand to absorbed near-duplicates: each rep's rank + reason copies
    # onto every id that was collapsed into it. The frontend uses
    # `rank` to surface the AI-pick ring; without this, absorbed photos
    # would silently fall out of the ranked set even though they're
    # visually identical to a top-ranked frame.
    if any(len(v) > 1 for v in merged_map.values()):
        rep_to_ranking = {r["image_id"]: r for r in rankings}
        expanded: list[dict] = list(rankings)
        for rep_id, absorbed_ids in merged_map.items():
            base = rep_to_ranking.get(rep_id)
            if base is None:
                continue
            for absorbed in absorbed_ids:
                if absorbed == rep_id:
                    continue
                expanded.append({
                    "image_id": absorbed,
                    "rank": base["rank"],
                    "reason": f"near-duplicate of #{rep_id}",
                })
        rankings = expanded

    # All-ids the rankings cover (reps + absorbed). Used in the result and
    # also stored as member_ids in the cache so re-loads carry the full set.
    all_ranked_ids = sorted({r["image_id"] for r in rankings})

    # Only cache when every evaluated rep was actually ranked. If some
    # previews were unreadable we'd be storing a partial result under the
    # full-set hash — the next call would return the partial set forever
    # even after the missing previews land. Better to re-run the vision
    # call than to poison the cache.
    if len(valid_ids) == len(evaluated_ids):
        conn.execute(
            """
            INSERT INTO burst_rankings (members_hash, member_ids, rankings_json, model, outcome)
            VALUES (?, ?, ?, ?, 'ranked')
            ON CONFLICT(members_hash) DO UPDATE SET
                member_ids    = excluded.member_ids,
                rankings_json = excluded.rankings_json,
                model         = excluded.model,
                outcome       = 'ranked',
                created_at    = DATETIME('now')
            """,
            (
                h,
                json.dumps(all_ranked_ids),
                json.dumps(rankings, separators=(",", ":")),
                model_id,
            ),
        )
        conn.commit()
    else:
        logger.warning(
            "burst_ranker: %d/%d previews unreadable for hash %s — skipping cache write",
            len(evaluated_ids) - len(valid_ids), len(evaluated_ids), h,
        )

    return _result(
        "ranked", model=model_id, members_hash=h,
        rankings=rankings, evaluated_ids=all_ranked_ids,
        filtered_from=filtered_from,
    )
