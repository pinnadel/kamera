"""
Personal model and similarity-group endpoints.
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from backend.constants import shot_at_to_unix as _shot_at_to_unix
from backend.database import get_db, set_setting
from backend.group_scoring import compute_best_reason
from backend.routers.settings import tunable_setting_keys
from backend.state import _personal_model, _personal_models, clear_all_caches, model_for
from phase2_quality.model_status import snapshot as _model_status_snapshot
from phase3_learning.auto_trainer import (
    force_train_sync,
    status_snapshot as _auto_train_snapshot,
)
from phase3_learning.feature_extractor import FEATURE_SCHEMA_VERSION
from phase3_learning.personal_model import MIN_DECISIONS
from phase2_quality.similarity_scorer import (
    embedding_to_json as _embedding_to_json,
    group_by_similarity as _group_by_similarity,
    json_to_embedding as _json_to_embedding,
)
from phase2_quality.face_identity import (
    json_to_face_embedding as _json_to_face_embedding,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Hero-selection scoring (compute_best_reason / score_candidate / top_n_candidates)
# lives in backend/group_scoring.py so the burst-rank pre-filter can reuse it.


# Below the threshold below, pre-ranking is intentionally skipped (LLM
# wouldn't change the score-based pick meaningfully on 2-photo groups, and
# still costs a full model call). Mirror the prerank module's _MIN_GROUP_SIZE
# so the grid's "not_applicable" badge matches what the worker actually skips.
_PRERANK_MIN_GROUP_SIZE = 3


def _annotate_prerank_state(groups: list[dict], conn) -> None:
    """In-place: add a `prerank_state` field to each group dict, and promote
    the LLM's rank-1 pick over the score-based `best_image_id`/`best_reason`
    wherever a ranking is actually available.

    States:
      "ready"            → a successfully-ranked burst_rankings row exists.
                           Opening the loupe will be instant (cache hit).
                           `best_image_id`/`best_reason` now reflect the
                           vision model's rank-1, and `best_is_ai_pick` is
                           True — this is the one case where the group's
                           "best" is an actual AI judgement, not a score
                           heuristic guessing at one.
      "near_duplicates"  → AI dedup determined the frames are visually
                           near-identical (cosine ≥ 0.97) and no per-photo
                           rank was meaningful. Loupe shows score-based pick;
                           grid chip explains why.
      "in_progress"      → the background prerank worker is mid-call on this
                           group right now. Exactly one group can be in this
                           state at a time (the worker is single-threaded).
      "pending"          → group meets _PRERANK_MIN_GROUP_SIZE and will be
                           ranked by the worker, but isn't yet.
      "not_applicable"   → group is too small to be pre-ranked; the loupe
                           shows the score-based pick directly.

    In every state except "ready", `best_is_ai_pick` stays False and
    `best_image_id`/`best_reason` keep whatever the caller already computed
    (the score-based pick) — callers must set that default plus a
    `_user_override` flag (popped here) on every group before calling this,
    so a manually-pinned hero (Settings → hero override) is never displaced
    by an AI pick the user didn't ask for.

    Implementation: one IN-clause query against burst_rankings to find every
    cached hash, outcome AND rankings payload, plus a single snapshot() call
    to read the worker's current job hash. O(1) DB roundtrip regardless of
    group count.
    """
    from phase2_quality.burst_ranker import _members_hash
    from phase2_quality.prerank import snapshot as _prerank_snapshot

    # Pre-compute each group's hash so we only iterate the group list twice
    # (once for hashes, once for the per-group assignment).
    hashes = [
        _members_hash([img["id"] for img in g["images"]])
        for g in groups
    ]
    eligible_hashes = [
        h for h, g in zip(hashes, groups)
        if len(g["images"]) >= _PRERANK_MIN_GROUP_SIZE
    ]
    row_by_hash: dict[str, dict] = {}
    if eligible_hashes:
        placeholders = ",".join("?" * len(eligible_hashes))
        rows = conn.execute(
            f"SELECT members_hash, outcome, rankings_json FROM burst_rankings WHERE members_hash IN ({placeholders})",
            eligible_hashes,
        ).fetchall()
        row_by_hash = {r["members_hash"]: r for r in rows}

    current_hash = _prerank_snapshot().get("current_job_hash")

    for h, g in zip(hashes, groups):
        manual_override = g.pop("_user_override", False)
        if len(g["images"]) < _PRERANK_MIN_GROUP_SIZE:
            g["prerank_state"] = "not_applicable"
        elif h in row_by_hash:
            row = row_by_hash[h]
            if row["outcome"] == "near_duplicates":
                g["prerank_state"] = "near_duplicates"
            else:
                g["prerank_state"] = "ready"
                if not manual_override:
                    _promote_ai_pick(g, row["rankings_json"])
        elif h == current_hash:
            g["prerank_state"] = "in_progress"
        else:
            g["prerank_state"] = "pending"


def _promote_ai_pick(group: dict, rankings_json: str | None) -> None:
    """In-place: overwrite `best_image_id`/`best_reason` with the vision
    model's rank-1 pick from a cached 'ranked' burst_rankings row, and set
    `best_is_ai_pick = True`. Leaves the score-based pick untouched (and
    `best_is_ai_pick` False) if the payload is unreadable or its rank-1 id
    isn't actually a member of this group — both defensive only; a 'ranked'
    row is always produced from this group's own members."""
    try:
        rankings = json.loads(rankings_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return
    top = next((r for r in rankings if r.get("rank") == 1), None)
    if not top:
        return
    valid_ids = {img["id"] for img in group["images"]}
    if top.get("image_id") not in valid_ids:
        return
    group["best_image_id"] = top["image_id"]
    group["best_reason"] = top.get("reason") or group["best_reason"]
    group["best_is_ai_pick"] = True


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GroupHeroRequest(BaseModel):
    group_image_ids: list[int]
    hero_image_id: int


class RankBurstRequest(BaseModel):
    member_ids: list[int]


class PrerankGroupsRequest(BaseModel):
    # Each inner list is the member ids of one similarity group. Order
    # within a group doesn't matter; rank_burst sorts before hashing.
    groups: list[list[int]]
    # Optional override of the queue cap. Frontend usually leaves it
    # unset and accepts the module-level default (20).
    max_groups: int | None = None


class PullLlmModelRequest(BaseModel):
    # Optional. Defaults to the recommended vision model (qwen2.5vl:7b).
    name: str | None = None


class ManualGroupAssignment(BaseModel):
    image_id: int
    manual_group_id: str | None  # None = clear, str = exact uuid to restore


class SetManualGroupRequest(BaseModel):
    """Move N photos into/out of a manually-anchored group.

    mode = "new_group":  generate one fresh uuid, assign to all image_ids.
                         They become one anchored bucket regardless of cosine.
    mode = "singletons": each image_id gets its own fresh uuid → each one
                         becomes a singleton anchored bucket (drops out of
                         /similarity-groups since the ≥2 rule applies).
    mode = "join_group": all image_ids inherit the manual_group_id of
                         target_image_id. If target_image_id has a NULL
                         manual_group_id, the backend mints a new uuid
                         and assigns it to BOTH the target and the
                         incoming image_ids (collapsing them into one
                         anchored bucket).
    mode = "clear":      set manual_group_id = NULL for all image_ids →
                         return to auto-cluster behaviour.
    mode = "restore_assignments":
                         restore an exact prior state. `assignments` is a
                         per-photo {image_id, manual_group_id} list — used
                         only by undo to roll back a previous mutation that
                         changed multiple photos to different groups.
                         `image_ids` is ignored when this mode is used.
    """
    image_ids: list[int]
    mode: str  # see docstring
    target_image_id: int | None = None
    assignments: list[ManualGroupAssignment] | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/model-info")
def personal_model_info(corpus: str = "folder"):
    """
    Return the current personal model status and decision-count progress.

    `corpus` selects which model to report on ('folder' | 'photos'); each
    trains on its own slice of training_samples and has its own pickle.

    Fields:
      ready              — True once trained at least once
      training_size      — number of samples the model was last trained on
      min_decisions      — minimum samples required to train (30)
      decided_count      — how many durable training samples exist right now
                           (read from training_samples, not decisions — these
                           persist across Clear Analysis / folder moves).
                           Scoped to corpus='folder': this model never sees
                           Photos-album decisions.
      photos_sample_count— Photos-album decisions recorded so far. Kept in a
                           separate corpus and not trained on yet; surfaced so
                           Settings can show the corpus growing.
      trained_at         — ISO-8601 timestamp of last training run (or null)
      top_features       — all feature importances, descending [{name, importance}]
      auto_running       — bool, true if a background train is in flight
      last_auto_train_at — ISO-8601 timestamp of last auto-train completion
      retrain_delta      — how many new samples auto-train waits for (10)
      pending_samples    — decided_count − training_size; the queued delta
                           the next auto-train will pick up
    """
    with get_db() as conn:
        decided_count = conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE corpus = ?", (corpus,)
        ).fetchone()[0]
        photos_sample_count = conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE corpus = 'photos'"
        ).fetchone()[0]
    info = model_for(corpus).info()
    info["decided_count"] = decided_count
    info["photos_sample_count"] = photos_sample_count
    info.update(_auto_train_snapshot(corpus))
    info["pending_samples"] = max(0, decided_count - (info.get("training_size") or 0))
    return info


@router.post("/train-model")
def train_personal_model(corpus: str = "folder"):
    """
    Manual override: force an immediate retrain from the durable
    training_samples corpus. Used by Settings → "Retrain now" — the user
    rarely needs this because auto-training kicks in every RETRAIN_DELTA
    new decisions.

    Returns 400 if the corpus is below MIN_DECISIONS, 409 if an auto-train
    is currently running (race avoidance — caller can retry in a moment).
    """
    try:
        meta = force_train_sync(model_for(corpus))
        return {"status": "trained", **meta}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Training failed: {exc}")


@router.post("/reset-personal-model")
def reset_personal_model(corpus: str = "folder"):
    """
    Destructive: clear all personal-model training progress.

    Deletes every folder-corpus row in training_samples (Photos-album samples
    are kept — they belong to a separate corpus) and all pairwise_comparisons, removes
    the persisted personal_model.pkl, and resets the in-memory model so the
    banner falls back to 0/50. Decisions on photos (K/M/R) and analysis
    results are preserved — only the learned model and its inputs are wiped.

    Returns the deleted counts so the UI can show a confirmation toast.
    """
    try:
        with get_db() as conn:
            # Scoped to the corpus this model actually trains on. Resetting the
            # camera model must not discard the Photos corpus being collected
            # for a future second model.
            cur = conn.execute("DELETE FROM training_samples WHERE corpus = ?", (corpus,))
            samples_removed = cur.rowcount
            cur = conn.execute("DELETE FROM pairwise_comparisons")
            pairwise_removed = cur.rowcount
            # An absolute-mode corpus derives its cutoffs from its own fit, so
            # they are model state, not user settings — a reset must drop them
            # or the next model inherits the old one's score scale.
            conn.execute(
                "DELETE FROM settings WHERE key IN (?, ?)",
                (f"{corpus}:keep_threshold", f"{corpus}:maybe_threshold"),
            )
            conn.commit()
        model_for(corpus).reset()
        logger.info(
            "Personal model reset: %d training_samples + %d pairwise_comparisons removed",
            samples_removed, pairwise_removed,
        )
        return {
            "status": "reset",
            "samples_removed":  samples_removed,
            "pairwise_removed": pairwise_removed,
        }
    except Exception as exc:
        logger.exception("Personal model reset failed")
        raise HTTPException(status_code=500, detail=f"Reset failed: {exc}")


# ---------------------------------------------------------------------------
# Backup / export / import of the personal taste model
#
# The durable source of truth is the training_samples corpus (one row per
# MANUAL decision, with a name-keyed frozen feature vector) plus
# pairwise_comparisons and the tunable settings. The trained pickle is a
# derived artifact — import rebuilds it by retraining. Exporting the corpus as
# JSON (not the pickle) keeps backups portable across feature-schema versions:
# features_from_json re-aligns rows by feature name and NaN-pads missing cols.
# ---------------------------------------------------------------------------

_BACKUP_FORMAT = "kamera-taste-model"
# v2 (2026-09-18): training_samples rows carry `corpus`. A v1 backup has no
# such field and its rows are read as corpus="folder", which is correct —
# v1 predates the Photos source entirely. The version is bumped so an older
# app REFUSES a v2 backup rather than silently folding Photos samples into
# the camera corpus.
_BACKUP_FORMAT_VERSION = 2
_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB — a 100k-sample corpus is well under this


@router.get("/export-taste-model")
def export_taste_model():
    """
    Export the personal taste model's durable state as a portable JSON backup.

    Contents: every training_samples row across ALL corpora (each tagged with
    its `corpus`, so a restore puts folder and Photos samples back where they
    belong) + pairwise_comparisons + the
    user-tunable settings (decision thresholds/weights). The trained pickle is
    intentionally excluded — import rebuilds it via retrain. Returns a
    downloadable attachment; an empty corpus is a valid (never 404) export.
    """
    keys = tunable_setting_keys()
    placeholders = ",".join("?" * len(keys))
    with get_db() as conn:
        samples = [
            dict(r) for r in conn.execute(
                "SELECT sample_uuid, decision, features_json, overall_score, "
                "schema_version, corpus, decided_at FROM training_samples"
            ).fetchall()
        ]
        pairwise = [
            dict(r) for r in conn.execute(
                "SELECT winner_id, loser_id, source_folder, decided_at "
                "FROM pairwise_comparisons"
            ).fetchall()
        ]
        setting_rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            keys,
        ).fetchall()

    payload = {
        "format":         _BACKUP_FORMAT,
        "format_version": _BACKUP_FORMAT_VERSION,
        "exported_at":    datetime.now().isoformat(timespec="seconds"),
        "app": {
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "min_decisions":          MIN_DECISIONS,
        },
        "training_samples":     samples,
        "pairwise_comparisons": pairwise,
        "settings":             {r["key"]: r["value"] for r in setting_rows},
    }

    filename = f"kamera-taste-model-{len(samples)}-samples.json"
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import-taste-model")
async def import_taste_model(request: Request, mode: str = "merge"):
    """
    Restore a personal-taste-model backup produced by /export-taste-model.

    The backup JSON is sent as the raw request body (application/json); `mode`
    is a query parameter. This keeps the endpoint dependency-free — the browser
    reads the picked file's text and POSTs it directly, no multipart needed.

    mode="merge"   → upsert samples by sample_uuid (taste evolution wins),
                     apply settings additively; never clears existing state.
    mode="replace" → wipe the corpus AND tunable settings first, then restore
                     the backup exactly (a true snapshot restore).

    After the DB restore, the model is retrained so the on-disk pickle reflects
    the imported corpus (skipped when the corpus is below MIN_DECISIONS).
    """
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode must be 'merge' or 'replace'")

    raw = await request.body()
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Backup file too large (limit 50 MB)")

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="File is not valid JSON")

    if not isinstance(payload, dict) or payload.get("format") != _BACKUP_FORMAT:
        raise HTTPException(status_code=400, detail="Not a KaMeRa taste-model backup")
    fmt_version = payload.get("format_version")
    if not isinstance(fmt_version, int) or fmt_version > _BACKUP_FORMAT_VERSION:
        raise HTTPException(
            status_code=400,
            detail="Unsupported backup version — export was made by a newer app",
        )

    samples  = payload.get("training_samples", [])
    pairwise = payload.get("pairwise_comparisons", [])
    settings = payload.get("settings", {})
    if not isinstance(samples, list) or not isinstance(pairwise, list) \
            or not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail="Backup structure is malformed")

    # Schema-mismatch is a warning, never a blocker: features_from_json re-aligns
    # rows by name and the SimpleImputer fills any missing columns at retrain.
    backup_fsv = (payload.get("app") or {}).get("feature_schema_version")
    schema_warning = None
    if backup_fsv != FEATURE_SCHEMA_VERSION:
        schema_warning = (
            f"Backup was made on feature schema {backup_fsv}; this app is on "
            f"{FEATURE_SCHEMA_VERSION}. Samples will be re-aligned by name and "
            f"any missing features filled in when the model retrains."
        )

    allowed_keys = set(tunable_setting_keys())

    # Pre-filter samples: a row needs a non-empty uuid and a valid decision.
    valid_samples, skipped = [], 0
    for s in samples:
        uuid = s.get("sample_uuid")
        if not uuid or s.get("decision") not in ("keep", "maybe", "reject"):
            skipped += 1
            continue
        valid_samples.append(s)

    try:
        with get_db() as conn:
            if mode == "replace":
                conn.execute("DELETE FROM training_samples")
                conn.execute("DELETE FROM pairwise_comparisons")
                if allowed_keys:
                    ph = ",".join("?" * len(allowed_keys))
                    conn.execute(
                        f"DELETE FROM settings WHERE key IN ({ph})",
                        tuple(allowed_keys),
                    )

            # Upsert samples — mirror the live decision path's ON CONFLICT, but
            # preserve the backup's decided_at so recency weighting stays faithful.
            conn.executemany(
                """
                INSERT INTO training_samples
                    (sample_uuid, decision, features_json, overall_score,
                     schema_version, corpus, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sample_uuid) DO UPDATE SET
                    decision       = excluded.decision,
                    features_json  = excluded.features_json,
                    overall_score  = excluded.overall_score,
                    schema_version = excluded.schema_version,
                    corpus         = excluded.corpus,
                    decided_at     = excluded.decided_at
                """,
                [
                    (
                        s["sample_uuid"],
                        s["decision"],
                        s.get("features_json"),
                        s.get("overall_score"),
                        s.get("schema_version", FEATURE_SCHEMA_VERSION),
                        # v1 backups predate the Photos source — folder is right.
                        s.get("corpus") or "folder",
                        s.get("decided_at"),
                    )
                    for s in valid_samples
                ],
            )

            # Pairwise has no unique key; in merge mode dedup so repeat imports
            # stay idempotent. Replace mode already wiped the table.
            pairwise_imported = 0
            for p in pairwise:
                if p.get("winner_id") is None or p.get("loser_id") is None:
                    continue
                params = (
                    p["winner_id"], p["loser_id"],
                    p.get("source_folder"), p.get("decided_at"),
                )
                if mode == "merge":
                    cur = conn.execute(
                        """
                        INSERT INTO pairwise_comparisons
                            (winner_id, loser_id, source_folder, decided_at)
                        SELECT ?, ?, ?, ?
                        WHERE NOT EXISTS (
                            SELECT 1 FROM pairwise_comparisons
                            WHERE winner_id = ? AND loser_id = ?
                              AND IFNULL(decided_at,'') = IFNULL(?,'')
                        )
                        """,
                        params + (p["winner_id"], p["loser_id"], p.get("decided_at")),
                    )
                    pairwise_imported += cur.rowcount
                else:
                    conn.execute(
                        "INSERT INTO pairwise_comparisons "
                        "(winner_id, loser_id, source_folder, decided_at) "
                        "VALUES (?, ?, ?, ?)",
                        params,
                    )
                    pairwise_imported += 1

            # Settings: only accept known tunable keys (drop unknown/hostile).
            settings_applied = 0
            for key, value in settings.items():
                if key in allowed_keys:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (key, str(value)),
                    )
                    settings_applied += 1
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Taste-model import failed during DB restore")
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}")

    # ── Rebuild the derived pickle from the restored corpus ──────────────────
    clear_all_caches()
    # Replace can shrink the corpus below the on-disk fit; save()'s shrink guard
    # would then silently refuse the overwrite and leave a stale pickle. reset()
    # deletes the pickle first so the (possibly smaller) new fit persists cleanly.
    # Every corpus is reset: a replace rewrites training_samples wholesale, so a
    # pickle left behind for another corpus would be fit on deleted rows.
    if mode == "replace":
        for _model in _personal_models.values():
            _model.reset()

    retrained = False
    retrain_deferred = False
    retrain_skipped_reason = None
    with get_db() as conn:
        corpus_size = conn.execute(
            "SELECT COUNT(*) FROM training_samples WHERE corpus = 'folder'"
        ).fetchone()[0]

    if corpus_size < MIN_DECISIONS:
        retrain_skipped_reason = "below_min_decisions"
    else:
        try:
            force_train_sync(_personal_model)
            retrained = True
        except ValueError:
            retrain_skipped_reason = "below_min_decisions"
        except RuntimeError:
            # An auto-train is mid-flight — don't fail the import; the running
            # train (or the next one) will pick up the freshly imported corpus.
            retrain_deferred = True
        except Exception:
            logger.exception("Retrain after import failed")
            retrain_skipped_reason = "retrain_error"

    logger.info(
        "Taste-model import (%s): %d samples, %d skipped, %d pairwise, %d settings; "
        "retrained=%s deferred=%s",
        mode, len(valid_samples), skipped, pairwise_imported, settings_applied,
        retrained, retrain_deferred,
    )
    return {
        "status":                 "imported",
        "mode":                   mode,
        "samples_imported":       len(valid_samples),
        "samples_skipped":        skipped,
        "pairwise_imported":      pairwise_imported,
        "settings_applied":       settings_applied,
        "retrained":              retrained,
        "retrain_deferred":       retrain_deferred,
        "retrain_skipped_reason": retrain_skipped_reason,
        "schema_warning":         schema_warning,
    }


@router.get("/similarity-groups")
def similarity_groups(threshold: float = 0.90, time_gap_seconds: float = 60.0):
    """
    Cluster all analyzed images by capture-time gap then semantic similarity.

    How it works:
      1. Load every image that has a stored SigLIP embedding from SQLite.
      2. Deserialize embeddings from JSON text → float lists.
      3. Sort by shot_at and split into segments wherever the inter-frame
         gap exceeds time_gap_seconds. Untimed photos form their own bucket.
      4. Inside each segment, run cosine-similarity Union-Find at `threshold`.
      5. For each group, pick the "best" image (highest overall_score).
      6. Return groups sorted by size descending (largest burst first).

    The time-gap primary split mirrors what every mature culling tool does
    (Aftershoot, Narrative Select, Lightroom Auto-Stack). Pure visual
    similarity collapses unrelated moments at the same scene into one giant
    cluster; segmenting by capture-time gap first recovers the moments.

    Query params:
      threshold         float  0.0–1.0   cosine similarity cutoff (default 0.90)
      time_gap_seconds  float  ≥0        max gap in seconds between frames in
                                         the same segment (default 120.0).
                                         Set to a very large value (e.g. 1e9)
                                         to disable the time split.
    """
    with get_db() as conn:
        # Manually-anchored photos (manual_group_id IS NOT NULL) bypass the
        # cosine threshold entirely — they form a group by user fiat, not
        # similarity. Surfacing them here doesn't require an embedding: the
        # anchor-bucket phase in group_by_similarity() never reads the vector
        # for anchored items. We still pull them so the grouper can include
        # them even if SigLIP hasn't run yet (or failed).
        rows = conn.execute(
            """
            SELECT i.id, i.filename, i.file_path, i.preview_path, i.overall_score,
                   i.embedding, i.face_embedding, i.scene, i.manual_group_id,
                   i.sharpness_score, i.face_detected, i.face_sharpness_score,
                   i.eyes_open, i.iqa_score, i.aesthetic_score, i.shot_at,
                   d.decision
            FROM images i
            LEFT JOIN decisions d ON d.image_id = i.id
            WHERE (i.embedding IS NOT NULL OR i.manual_group_id IS NOT NULL)
              AND i.analysis_status = 'done'
            ORDER BY i.id
            """
        ).fetchall()

    # Parse embeddings. Anchored rows without an embedding get a zero-vector
    # placeholder — it never reaches _cluster_segment because the anchor
    # phase short-circuits them into their own buckets first.
    # 4-tuple shape: (id, embedding, shot_at, manual_group_id). The grouper
    # routes non-NULL manual_group_id values through the anchor-bucket phase
    # before time-segment + cosine union-find.
    items: list[tuple[int, list[float], float | None, str | None]] = []
    meta: dict[int, dict] = {}
    extras: dict[int, dict] = {}
    _ANCHOR_PLACEHOLDER = [0.0] * 768
    for row in rows:
        vec = _json_to_embedding(row["embedding"])
        if vec is None and row["manual_group_id"] is not None:
            vec = _ANCHOR_PLACEHOLDER
        if vec is not None:
            items.append((
                row["id"], vec,
                _shot_at_to_unix(row["shot_at"]),
                row["manual_group_id"],
            ))
            meta[row["id"]] = {
                "id": row["id"],
                "filename": row["filename"],
                "file_path": row["file_path"],
                "overall_score": row["overall_score"],
                "sharpness_score": row["sharpness_score"],
                "face_detected": row["face_detected"],
                "face_sharpness_score": row["face_sharpness_score"],
                "eyes_open": row["eyes_open"],
                "iqa_score": row["iqa_score"],
                "aesthetic_score": row["aesthetic_score"],
                "decision": row["decision"],
            }
            # Layered-gate inputs. Prefer the cached preview JPEG over the
            # source for histogram reads — avoids demosaicing a RAW just to
            # count colors. Falls back to file_path so freshly-analysed
            # JPEGs (no preview cache) still gate correctly.
            extras[row["id"]] = {
                "face_embedding": _json_to_embedding(row["face_embedding"]),
                "scene": row["scene"],
                "file_path": row["preview_path"] or row["file_path"],
            }

    raw_groups = _group_by_similarity(
        items, threshold=threshold, time_gap_seconds=time_gap_seconds,
        extras=extras,
    )

    # Batch-load any persisted hero overrides from the settings table
    fingerprints = [
        "hero:" + ",".join(str(i) for i in sorted(grp))
        for grp in raw_groups
    ]
    if fingerprints:
        with get_db() as conn:
            placeholders = ",".join("?" * len(fingerprints))
            override_rows = conn.execute(
                f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
                fingerprints,
            ).fetchall()
        overrides = {r["key"]: int(r["value"]) for r in override_rows}
    else:
        overrides = {}

    groups = []
    for group_ids in sorted(raw_groups, key=len, reverse=True):
        images = [meta[gid] for gid in group_ids if gid in meta]
        fingerprint = "hero:" + ",".join(str(i) for i in sorted(group_ids))
        override_id = overrides.get(fingerprint)
        valid_ids = {img["id"] for img in images}
        user_override_used = bool(override_id and override_id in valid_ids)
        if user_override_used:
            best_id = override_id
        else:
            best_id = max(images, key=lambda img: img["overall_score"] or 0)["id"]
        best_img = next((img for img in images if img["id"] == best_id), images[0])
        best_reason = compute_best_reason(best_img, images, user_override_used)
        groups.append({
            "images": images,
            "best_image_id": best_id,
            "best_reason": best_reason,
            "best_is_ai_pick": False,
            "_user_override": user_override_used,
            "size": len(images),
        })

    # Tag each group with its prerank state so the grid can show "pending /
    # in_progress / ready / not_applicable" markers without a second poll.
    # Also promotes best_image_id/best_reason to the LLM's rank-1 pick
    # wherever prerank_state comes back "ready" (see _annotate_prerank_state).
    # Done inside a short-lived connection — the heavy SQL above has already
    # closed its `with get_db()` block, so this needs its own.
    with get_db() as conn:
        _annotate_prerank_state(groups, conn)

    return {"threshold": threshold, "groups": groups}


@router.get("/face-groups")
def face_groups(threshold: float = 0.50, time_gap_seconds: float | None = None):
    """
    Cluster photos by person identity using FaceNet face-identity embeddings.

    Uses dedicated 512-dim face embeddings produced by InceptionResnetV1
    (vggface2-pretrained) — not the full-photo SigLIP embeddings. The
    difference matters: SigLIP describes the scene (background, clothing,
    lighting), so two pictures of Aunt Jane in different rooms collapsed
    on environment instead of identity. FaceNet was trained specifically
    on face crops to map identity into a stable space, so the same person
    clusters together across moments, expressions, and lighting.

    The cosine cutoff is much lower than for full-photo similarity:
      ≥0.50  = standard "same person" (FaceNet on VGGFace2)
      ≥0.65  = stricter (only very confident matches)
      ≥0.35  = looser (catches profile shots, more variance, more false merges)

    Photos analysed before schema v38 have NULL face_embedding and won't
    appear here until re-analysed. The endpoint silently filters them.

    Time-gap split defaults to OFF: people-clustering wants to track the
    same face across separate moments, so segmenting by capture time would
    defeat the purpose. Callers may still pass time_gap_seconds to enable it.

    Returns the same shape as GET /similarity-groups so the frontend can
    render the groups identically via GroupTile + GroupLoupe.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT i.id, i.filename, i.file_path, i.overall_score,
                   i.face_embedding,
                   i.sharpness_score, i.face_detected, i.face_sharpness_score,
                   i.eyes_open, i.iqa_score, i.aesthetic_score, i.shot_at,
                   d.decision
            FROM images i
            LEFT JOIN decisions d ON d.image_id = i.id
            WHERE i.face_embedding IS NOT NULL
              AND i.analysis_status = 'done'
              AND i.face_detected = 1
            ORDER BY i.id
            """
        ).fetchall()

        # Photos with a detected face but no FaceNet embedding yet — typically
        # rows analysed before schema v38. The frontend uses this to surface
        # a "re-analyse to enable People mode" hint without us needing a
        # separate endpoint.
        pending_row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM images
            WHERE face_detected = 1
              AND analysis_status = 'done'
              AND face_embedding IS NULL
            """
        ).fetchone()
        pending_reanalysis = int(pending_row["n"]) if pending_row else 0

    items: list[tuple[int, list[float], float | None]] = []
    meta: dict[int, dict] = {}
    for row in rows:
        vec = _json_to_face_embedding(row["face_embedding"])
        if vec is not None:
            items.append((row["id"], vec, _shot_at_to_unix(row["shot_at"])))
            meta[row["id"]] = {
                "id": row["id"],
                "filename": row["filename"],
                "file_path": row["file_path"],
                "overall_score": row["overall_score"],
                "sharpness_score": row["sharpness_score"],
                "face_detected": row["face_detected"],
                "face_sharpness_score": row["face_sharpness_score"],
                "eyes_open": row["eyes_open"],
                "iqa_score": row["iqa_score"],
                "aesthetic_score": row["aesthetic_score"],
                "decision": row["decision"],
            }

    raw_groups = _group_by_similarity(
        items, threshold=threshold, time_gap_seconds=time_gap_seconds,
    )

    groups = []
    for group_ids in sorted(raw_groups, key=len, reverse=True):
        images = [meta[gid] for gid in group_ids if gid in meta]
        best_id = max(images, key=lambda img: img["overall_score"] or 0)["id"]
        best_img = next((img for img in images if img["id"] == best_id), images[0])
        best_reason = compute_best_reason(best_img, images, False)
        groups.append({
            "images": images,
            "best_image_id": best_id,
            "best_reason": best_reason,
            "best_is_ai_pick": False,
            "_user_override": False,
            "size": len(images),
        })

    # Same prerank-state annotation as /similarity-groups. People-mode
    # groups share the burst_rankings cache because the rank key is the
    # sorted membership hash, not the grouping algorithm.
    with get_db() as conn:
        _annotate_prerank_state(groups, conn)

    return {
        "threshold": threshold,
        "groups": groups,
        "pending_reanalysis": pending_reanalysis,
    }


@router.post("/group-hero")
def set_group_hero(request: GroupHeroRequest):
    """Store a hero override for a similarity group in the settings table."""
    key = "hero:" + ",".join(str(i) for i in sorted(request.group_image_ids))
    set_setting(key, str(request.hero_image_id))
    return {"status": "ok", "hero_image_id": request.hero_image_id}


def _reclaim_stale_singleton_anchors(conn, anchors: set[str]) -> None:
    """Null out manual_group_id for any of `anchors` left with exactly one
    member after a mutation.

    A manual anchor bucket only means something as a *group* — the ≥2 rule
    in group_by_similarity() already hides size-1 buckets from
    /similarity-groups. Without this sweep, moving one photo out of a
    2-member anchored group (e.g. GroupLoupe's "Remove from group") leaves
    the other member pinned to the old anchor forever: it can't be seen
    (size 1 never renders) and it can't rejoin auto-clustering either
    (a non-NULL manual_group_id always short-circuits into the anchor
    phase). Clearing it restores normal auto-cluster behaviour, matching
    what already happens to an auto-cluster member that ends up alone.
    """
    anchors = {a for a in anchors if a}
    if not anchors:
        return
    placeholders = ",".join("?" * len(anchors))
    rows = conn.execute(
        f"SELECT manual_group_id, COUNT(*) AS n FROM images "
        f"WHERE manual_group_id IN ({placeholders}) GROUP BY manual_group_id",
        list(anchors),
    ).fetchall()
    stale = [r["manual_group_id"] for r in rows if r["n"] == 1]
    if stale:
        placeholders2 = ",".join("?" * len(stale))
        conn.execute(
            f"UPDATE images SET manual_group_id = NULL "
            f"WHERE manual_group_id IN ({placeholders2})",
            stale,
        )


@router.post("/set-manual-group")
def set_manual_group(request: SetManualGroupRequest):
    """Move photos in/out of manually-anchored groups. See
    SetManualGroupRequest for the four modes.

    All writes happen in a single transaction. Returns the new anchor id
    (or None for clear/singletons) plus the count of rows updated.
    """
    import uuid as _uuid

    mode = request.mode
    if mode not in ("new_group", "singletons", "join_group", "clear", "restore_assignments"):
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    if mode == "restore_assignments":
        if not request.assignments:
            raise HTTPException(status_code=400, detail="assignments required for restore_assignments")
        with get_db() as conn:
            for a in request.assignments:
                conn.execute(
                    "UPDATE images SET manual_group_id = ? WHERE id = ?",
                    (a.manual_group_id, a.image_id),
                )
            conn.commit()
        return {"updated": len(request.assignments), "manual_group_id": None}

    image_ids = request.image_ids
    if not image_ids:
        raise HTTPException(status_code=400, detail="image_ids must not be empty")

    if mode == "join_group" and request.target_image_id is None:
        raise HTTPException(
            status_code=400,
            detail="target_image_id is required when mode == 'join_group'",
        )

    with get_db() as conn:
        # Snapshot the anchors image_ids currently belong to BEFORE mutating.
        # Every mode below can pull photos out of whatever bucket they were
        # previously in, so these are the buckets that need a post-mutation
        # stale-singleton sweep (see _reclaim_stale_singleton_anchors).
        placeholders0 = ",".join("?" * len(image_ids))
        prior_anchor_rows = conn.execute(
            f"SELECT manual_group_id FROM images WHERE id IN ({placeholders0})",
            image_ids,
        ).fetchall()
        prior_anchors = {r["manual_group_id"] for r in prior_anchor_rows}

        if mode == "new_group":
            anchor = _uuid.uuid4().hex
            placeholders = ",".join("?" * len(image_ids))
            conn.execute(
                f"UPDATE images SET manual_group_id = ? WHERE id IN ({placeholders})",
                [anchor, *image_ids],
            )
            _reclaim_stale_singleton_anchors(conn, prior_anchors)
            conn.commit()
            return {"updated": len(image_ids), "manual_group_id": anchor}

        if mode == "singletons":
            # One fresh uuid per image_id; do it in one transaction.
            for image_id in image_ids:
                anchor = _uuid.uuid4().hex
                conn.execute(
                    "UPDATE images SET manual_group_id = ? WHERE id = ?",
                    (anchor, image_id),
                )
            _reclaim_stale_singleton_anchors(conn, prior_anchors)
            conn.commit()
            return {"updated": len(image_ids), "manual_group_id": None}

        if mode == "join_group":
            target_id = request.target_image_id
            row = conn.execute(
                "SELECT manual_group_id FROM images WHERE id = ?",
                (target_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"target_image_id {target_id} not found",
                )
            anchor = row["manual_group_id"]
            if anchor is None:
                # Target wasn't anchored yet — mint a fresh uuid and assign
                # to BOTH the target and the incoming image_ids, so the
                # whole set becomes one anchored bucket.
                anchor = _uuid.uuid4().hex
                all_ids = [target_id, *(i for i in image_ids if i != target_id)]
            else:
                # Target already has an anchor — incoming photos inherit it.
                all_ids = [i for i in image_ids if i != target_id]
                if not all_ids:
                    # Caller asked to "join_group" with target being the
                    # only image_id (no-op since the target is already in
                    # the group it's the target of). Treat as success.
                    return {"updated": 0, "manual_group_id": anchor}
            placeholders = ",".join("?" * len(all_ids))
            conn.execute(
                f"UPDATE images SET manual_group_id = ? WHERE id IN ({placeholders})",
                [anchor, *all_ids],
            )
            _reclaim_stale_singleton_anchors(conn, prior_anchors)
            conn.commit()
            return {"updated": len(all_ids), "manual_group_id": anchor}

        # mode == "clear"
        placeholders = ",".join("?" * len(image_ids))
        conn.execute(
            f"UPDATE images SET manual_group_id = NULL WHERE id IN ({placeholders})",
            image_ids,
        )
        _reclaim_stale_singleton_anchors(conn, prior_anchors)
        conn.commit()
        return {"updated": len(image_ids), "manual_group_id": None}


@router.post("/pull-llm-model")
def pull_llm_model_endpoint(request: PullLlmModelRequest):
    """Pull an Ollama model from inside the app (lazy, background thread).

    Returns `{"status": "started"}` once the background pull has begun;
    progress then flows through `/model-status` (same surface as SigLIP /
    TOPIQ / LAION / FaceNet downloads).

    Pre-flight check: if Ollama itself isn't installed or the daemon isn't
    running, we return that immediately instead of kicking off a thread
    that will fail. Without this, the frontend's `usePullModel` polls for
    30 minutes seeing no in-flight entry, then times out with a generic
    "Pull failed". Same model_status surface, but the fail-fast contract
    means the button shows the right error within a second.

    Response shape:
      success:  {"status": "started",       "model": <name>}
      fast-fail:{"status": "not_installed", "model": <name>, "detail": "..."}
                {"status": "not_running",   "model": <name>, "detail": "..."}
    """
    import threading
    from phase2_quality.llm_explainer import (
        pull_model as _pull_model,
        DEFAULT_VISION_MODEL,
        _ollama_installed,
        _is_daemon_reachable,
    )

    name = (request.name or DEFAULT_VISION_MODEL).strip()
    if not name:
        raise HTTPException(status_code=400, detail="model name required")

    # Fail-fast pre-flight. `pull_model()` performs these same checks
    # internally and returns early, but its return value is buried in the
    # background thread where the UI can't see it. Hoisting them here
    # turns a 30-minute silent timeout into an immediate, actionable error.
    if not _ollama_installed():
        return {
            "status": "not_installed", "model": name,
            "detail": "Ollama isn't installed yet — install it first (ollama.com or `brew install ollama`).",
        }
    if not _is_daemon_reachable():
        return {
            "status": "not_running", "model": name,
            "detail": "Ollama is installed but the daemon isn't running. Try restarting the app.",
        }

    # Fire-and-forget. pull_model registers + clears its model_status entry
    # internally, so the polling UI sees the lifecycle without our help.
    def _run():
        try:
            result = _pull_model(name)
            logger.info("ollama pull %s → %s", name, result.get("status"))
        except Exception:
            logger.exception("ollama pull %s crashed", name)

    threading.Thread(target=_run, name=f"ollama-pull-{name}", daemon=True).start()
    return {"status": "started", "model": name}


@router.delete("/ollama-model/{name:path}")
def delete_llm_model_endpoint(name: str):
    """Delete a locally-installed Ollama model.

    Used by the Settings panel's "Remove unused" affordance when the user
    upgrades from one vision model to another (e.g. moondream → qwen2.5vl)
    and wants to reclaim disk without dropping to Terminal.

    The `{name:path}` converter is important: Ollama model names contain
    a colon (`qwen2.5vl:7b`), which would otherwise be parsed as a path
    parameter delimiter.
    """
    from phase2_quality.llm_explainer import delete_model as _delete_model

    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="model name required")
    result = _delete_model(name.strip())
    if result.get("status") == "ok":
        logger.info("ollama delete %s → ok", name)
        return result
    # All other statuses are reported as 200 with the failure detail so the
    # frontend can show a meaningful message; HTTPException would swallow it.
    logger.warning("ollama delete %s → %s (%s)", name, result.get("status"), result.get("detail"))
    return result


@router.post("/rank-burst")
def rank_burst_endpoint(request: RankBurstRequest):
    """Rank a burst of similar photos using a vision LLM (lazy + cached).

    Called by GroupLoupe when the user opens a group. Returns instantly when
    a cached ranking exists for the evaluated set; otherwise calls Ollama with
    the preview JPEGs in one chat completion and caches the result.

    Large bursts (>12 members) are pre-filtered server-side to the top-12 by
    `backend.group_scoring.top_n_candidates` (face_sharpness → eyes_open →
    sharpness → IQA → aesthetic → overall_score) before the LLM sees them.
    The non-evaluated photos remain in the group at their score-based standing.

    Response shape:
      {
        "status":        "ranked" | "no_vision_model" | "too_few" | "error",
        "model":         str | None,
        "members_hash":  str | None,        # hash of the EVALUATED (post-filter) set
        "cached":        bool,
        "rankings":      [{"image_id": int, "rank": int, "reason": str}, ...],
        "evaluated_ids": list[int],         # (≤12) sorted ids actually ranked
        "filtered_from": int,               # original input count (≥ len(evaluated_ids))
      }
    """
    from phase2_quality.burst_ranker import rank_burst

    with get_db() as conn:
        result = rank_burst(request.member_ids, conn)
    return result


@router.post("/prerank-groups")
def prerank_groups_endpoint(request: PrerankGroupsRequest):
    """Queue similarity groups for background pre-ranking.

    The user just finished an analysis. The natural next step is opening a
    similarity group — the burst-rank LLM call inside the loupe takes 30-90 s,
    most of it the vision encoder. Running those calls in the background as
    soon as analysis completes means the result is already cached by the
    time the user clicks, so the amber ring appears with no spinner.

    The endpoint never blocks: it enqueues and returns the current snapshot.
    Poll /prerank-status for progress. Cancellation is fire-and-forget via
    POST /prerank-cancel.

    Behaviour:
      - Groups smaller than 3 are skipped (LLM rank wouldn't change the score
        pick meaningfully — same threshold as `_MIN_GROUP_SIZE` in prerank.py).
      - Sorted largest-first, capped at max_groups (default 20).
      - Idempotent w.r.t. the burst_rankings cache: groups that already have
        a cache row return cached=true from rank_burst and count as `skipped`.
    """
    from phase2_quality import prerank
    snap = prerank.enqueue_groups(
        request.groups,
        max_groups=request.max_groups or 20,
    )
    return snap


@router.get("/prerank-status")
def prerank_status_endpoint():
    """Read-only snapshot of the background pre-rank worker. Frontend may
    poll this to show progress ("12 of 20 groups ranked") or stay silent
    and just rely on the cache being warm when the user opens a loupe."""
    from phase2_quality import prerank
    return prerank.snapshot()


@router.post("/prerank-cancel")
def prerank_cancel_endpoint():
    """Signal the background worker to stop after its current item. Used
    when the user changes folder, closes the app, or re-clusters at a new
    threshold (in which case the existing queue's hashes are stale)."""
    from phase2_quality import prerank
    prerank.cancel()
    return prerank.snapshot()


@router.get("/model-status")
def model_status():
    """
    Return the loading state of all AI models.

    Polled by the frontend every second to show a download/load toast.
    """
    return _model_status_snapshot()


@router.post("/reload-models")
def reload_models():
    """
    Evict all cached ML models from RAM and reload them in background threads.

    Use this after updating model weights on disk OR after a load failure
    (e.g. the meta-tensor race that previously broke aesthetic_scorer for
    the whole session). Resets _load_failed flags so the warm-up retries.

    Returns immediately; loading happens in background (watch /model-status
    for progress).
    """
    import phase2_quality.aesthetic_scorer as _aes
    import phase2_quality.iqa_scorer as _iqa
    import phase2_quality.similarity_scorer as _sim

    _iqa._metric       = None
    _iqa._load_failed  = False
    _aes._model        = None
    _aes._processor    = None
    _aes._head         = None
    _aes._load_failed  = False
    _sim._model        = None
    _sim._processor    = None

    # Import _warm_models from analysis router to avoid duplication
    from backend.routers.analysis import _warm_models
    _warm_models()
    return {"status": "reloading"}
