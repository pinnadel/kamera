"""
Background auto-training for the personal model.

Each K/M/R decision calls `maybe_train_async()`. The trigger checks two
gates against the durable `training_samples` table:

  1. Floor gate    — at least MIN_DECISIONS samples exist (skip until then).
  2. Freshness gate — at least RETRAIN_DELTA new samples since last fit
                      (skip if we just trained and barely anything changed).

When both pass, a daemon thread runs `train_from_samples()` on the full
corpus and saves the pickle atomically. A threading.Lock makes the trigger
single-flight: if a train is already running, additional calls return
immediately and the still-arriving samples roll into the *next* train.
That's by design — we don't queue trainings, we just guarantee the next
one will see all new data when it eventually fires.

The threshold model used elsewhere in the app stays the source of truth
for auto-cull decisions until the personal model reaches the "ready" tier.
This module just keeps the model file fresh in the background so when
"ready" arrives, it's actually ready.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

from backend.database import get_db
from phase3_learning.feature_extractor import features_from_json
from phase3_learning.personal_model import MIN_DECISIONS

if TYPE_CHECKING:
    from phase3_learning.personal_model import PersonalModel

logger = logging.getLogger(__name__)

# Auto-train fires whenever there are this many new samples since the last
# successful fit. Kept low enough that the user feels the model evolving,
# high enough that we're not retraining on every single decision (each
# train is a few seconds of CPU; doing it per-decision would burn cycles
# while the user is still culling).
RETRAIN_DELTA: int = 10

# Module-level state. Wrapped in a lock so concurrent decision posts don't
# race the freshness check.
_lock = threading.Lock()
_running = False
_last_auto_train_at: datetime | None = None


def status_snapshot() -> dict:
    """JSON-serialisable peek at auto-train state for /model-info."""
    return {
        "auto_running":         bool(_running),
        "last_auto_train_at":   _last_auto_train_at.isoformat(timespec="seconds")
                                 if _last_auto_train_at else None,
        "retrain_delta":        RETRAIN_DELTA,
    }


def _count_samples() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM training_samples").fetchone()[0]


def _pairwise_synthetic_samples() -> tuple[np.ndarray, list[str], list[float | None], list[str | None]]:
    """Build synthetic training samples from pairwise A/B comparisons.

    Each comparison generates two soft labels:
      winner → fractional "keep"  label (+0.7, weight 0.4)
      loser  → fractional "reject" label (-0.7, weight 0.4)

    Images with more wins than losses trend toward keep; those with more
    losses trend toward reject. The fractional label ±0.7 is weaker than
    an explicit K (+1.0) or X (-1.0), reflecting that the user expressed
    a relative preference rather than an absolute verdict.

    Returns (feature_vectors, decisions, overall_scores, decided_at) —
    same shape as _load_corpus so the two sets can be concatenated.
    The 'decisions' list uses 'keep' / 'reject' labels to match the GBR
    label encoding; caller applies sample_weight=0.4 for all synthetic rows.
    """
    with get_db() as conn:
        pairs = conn.execute(
            "SELECT winner_id, loser_id, decided_at FROM pairwise_comparisons"
        ).fetchall()

    if not pairs:
        empty = np.empty((0, 0), dtype=np.float32)
        return empty, [], [], []

    # Tally wins / losses per image
    from collections import defaultdict
    wins:   dict[int, int] = defaultdict(int)
    losses: dict[int, int] = defaultdict(int)
    latest: dict[int, str] = {}
    for row in pairs:
        w_id, l_id, ts = row["winner_id"], row["loser_id"], row["decided_at"]
        wins[w_id]   += 1
        losses[l_id] += 1
        if ts:
            if w_id not in latest or ts > latest[w_id]:
                latest[w_id] = ts
            if l_id not in latest or ts > latest[l_id]:
                latest[l_id] = ts

    all_ids = set(wins) | set(losses)
    if not all_ids:
        empty = np.empty((0, 0), dtype=np.float32)
        return empty, [], [], []

    # Load feature vectors for all involved images
    with get_db() as conn:
        placeholders = ",".join("?" * len(all_ids))
        img_rows = conn.execute(
            f"SELECT * FROM images WHERE id IN ({placeholders})",
            list(all_ids),
        ).fetchall()

    from phase3_learning.feature_extractor import extract

    vecs, decisions_out, scores_out, dates_out = [], [], [], []
    for row in img_rows:
        img_id = row["id"]
        w = wins[img_id]
        l = losses[img_id]
        total = w + l
        if total == 0:
            continue
        net = (w - l) / total  # [-1, 1]
        label = "keep" if net >= 0 else "reject"
        vecs.append(extract(dict(row)))
        decisions_out.append(label)
        scores_out.append(row["overall_score"])
        dates_out.append(latest.get(img_id))

    if not vecs:
        empty = np.empty((0, 0), dtype=np.float32)
        return empty, [], [], []

    return np.vstack(vecs), decisions_out, scores_out, dates_out


def _load_corpus() -> tuple[np.ndarray, list[str], list[float | None], list[str | None], list[float]] | None:
    """Read every training_samples row and rebuild the feature matrix.

    Also augments with synthetic samples from pairwise A/B comparisons
    (winner → soft-keep, loser → soft-reject, base weight 0.4).

    Returns None if the corpus is empty or under the floor.

    Return tuple: (feature_vectors, decisions, overall_scores, decided_at, base_weights).
    `base_weights` is 1.0 for K/M/X decisions and 0.4 for pairwise synthetics;
    recency weighting inside train_from_samples multiplies these together.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT decision, features_json, overall_score, decided_at FROM training_samples"
        ).fetchall()
    if len(rows) < MIN_DECISIONS:
        return None
    feature_vectors = np.vstack([features_from_json(r["features_json"]) for r in rows])
    decisions       = [r["decision"]      for r in rows]
    overall_scores  = [r["overall_score"] for r in rows]
    decided_at      = [r["decided_at"]    for r in rows]
    base_weights    = [1.0] * len(rows)

    # Augment with pairwise synthetic samples at 0.4× weight.
    p_vecs, p_decisions, p_scores, p_dates = _pairwise_synthetic_samples()
    if p_vecs.shape[0] > 0:
        n_feats = feature_vectors.shape[1]
        if p_vecs.shape[1] < n_feats:
            pad = np.full((p_vecs.shape[0], n_feats - p_vecs.shape[1]), np.nan, dtype=np.float32)
            p_vecs = np.hstack([p_vecs, pad])
        elif p_vecs.shape[1] > n_feats:
            p_vecs = p_vecs[:, :n_feats]
        feature_vectors = np.vstack([feature_vectors, p_vecs])
        decisions       = decisions + p_decisions
        overall_scores  = overall_scores + p_scores
        decided_at      = decided_at + p_dates
        base_weights    = base_weights + [0.4] * p_vecs.shape[0]

    return feature_vectors, decisions, overall_scores, decided_at, base_weights


def _train_worker(model: "PersonalModel") -> None:
    """Run inside the daemon thread. Trains, saves, updates state, releases lock."""
    global _running, _last_auto_train_at
    try:
        corpus = _load_corpus()
        if corpus is None:
            logger.info("Auto-train skipped — corpus below MIN_DECISIONS")
            return
        X, decisions, overall_scores, decided_at, base_weights = corpus
        prior_size = model.training_size if model.ready else 0
        logger.info(
            "Auto-train starting on %d samples (prior fit: %d)",
            X.shape[0], prior_size,
        )
        if X.shape[0] < prior_size:
            logger.warning(
                "Auto-train corpus (%d) is SMALLER than the prior fit (%d) — "
                "the training_samples table appears to have shrunk. save() will "
                "refuse the overwrite; investigate the corpus before forcing.",
                X.shape[0], prior_size,
            )
        model.train_from_samples(X, decisions, overall_scores, decided_at=decided_at,
                                 base_weights=base_weights)
        model.save()
        _last_auto_train_at = datetime.now()
        logger.info("Auto-train complete at %s", _last_auto_train_at.isoformat(timespec="seconds"))
    except Exception:
        # Never let a background failure crash the server. The user will
        # just keep using thresholds until the next trigger succeeds.
        logger.exception("Auto-train failed")
    finally:
        with _lock:
            _running = False


def maybe_train_async(model: "PersonalModel") -> bool:
    """Fire-and-forget trigger called after each decision.

    Returns True if a training thread was actually started, False if either
    gate said no (already running, below floor, or no new data since last
    fit). Callers don't wait — the model becomes ready when it becomes ready.

    Trigger logic:
      • If a train is already in flight, return False. The next call after
        it finishes will see the additional samples and decide for itself.
      • If sample_count < MIN_DECISIONS, return False (still in "untrained"
        phase, the banner shows N / 30 progress).
      • If model has never been trained AND we just crossed the floor → train.
      • If model has been trained AND samples - training_size >= RETRAIN_DELTA
        → train.
      • Otherwise no-op.
    """
    global _running

    with _lock:
        if _running:
            return False

        sample_count = _count_samples()
        if sample_count < MIN_DECISIONS:
            return False

        prior_size = model.training_size if model.ready else 0
        delta = sample_count - prior_size

        # First-train special case: prior_size == 0 means model has never been
        # fit. Any delta >= floor (which we just verified) qualifies.
        # Subsequent trains require RETRAIN_DELTA new samples.
        if prior_size > 0 and delta < RETRAIN_DELTA:
            return False

        _running = True

    thread = threading.Thread(
        target=_train_worker,
        args=(model,),
        daemon=True,
        name="personal-model-auto-train",
    )
    thread.start()
    return True


def force_train_sync(model: "PersonalModel") -> dict:
    """Manual override — used by Settings → Retrain now.

    Blocks the caller until training finishes. Raises ValueError if the
    floor isn't met (so the API can return 400). Respects the in-flight
    lock: if an auto-train is mid-flight, this raises so the user knows
    to wait a few seconds.
    """
    with _lock:
        if _running:
            raise RuntimeError("Auto-train already in progress — try again in a moment.")

    corpus = _load_corpus()
    if corpus is None:
        raise ValueError(
            f"Need at least {MIN_DECISIONS} decisions to train (current corpus is smaller)."
        )
    X, decisions, overall_scores, decided_at, base_weights = corpus
    meta = model.train_from_samples(X, decisions, overall_scores, decided_at=decided_at,
                                    base_weights=base_weights)
    model.save()

    global _last_auto_train_at
    _last_auto_train_at = datetime.now()
    return meta
