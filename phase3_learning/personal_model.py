"""
PersonalModel: learns the user's keep/reject/maybe preferences as a
signed delta applied on top of the base overall_score.

Decision → training label mapping:
    keep   → +1.0  (user would add score)
    maybe  →  0.0  (neutral)
    reject → -1.0  (user would subtract score)

sklearn Pipeline:
    SimpleImputer(strategy='mean')   fill NaN features with training-set mean
    StandardScaler                   zero-mean unit-variance (GBR doesn't need
                                     this, but it makes debugging easier)
    GradientBoostingRegressor        predict raw delta in roughly [-1, +1]

Feature vector (17 dims): sharpness, exposure, iqa, aesthetic,
highlight_clip_pct, shadow_clip_pct, shake_detected, face_present
(computed binary — never NaN), face_detected, face_count,
face_sharpness_score, eyes_open, eye_openness_ratio, face_size_ratio,
focal_length_mm, aperture, iso.
overall_score is excluded from the feature vector (it is a linear combo
of sharpness + exposure already present) but is still used as the delta
base in the prediction formula.

Predicted delta is multiplied by DELTA_SCALE before adding to overall_score:
    personal_score = clamp(overall_score + delta × 25, 0, 100)

GBR hyperparameters are conservative (shallow trees, low learning rate,
200 estimators) to avoid overfitting on small datasets (20–200 decisions).

Saved to data/models/personal_model.pkl as a plain dict so it can be
loaded without importing this module (forward-compatible if the class moves).
"""

import pickle
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from phase3_learning.feature_extractor import (
    FEATURE_SCHEMA_VERSION,
    extract,
    extract_batch,
    feature_names,
)

logger = logging.getLogger(__name__)

# Bumped 20 → 30 on 2026-05-05. With 17 features and a 3-class label, 20 is
# barely enough to fit GBR without it being noise; 30 gives the bootstrap
# validator (4–6 test samples per fold) a chance to produce a meaningful
# beats_baseline verdict. See docs/PROJECT_WIKI.md "model trust tiers".
MIN_DECISIONS: int  = 30      # refuse to train below this — too little signal
DELTA_SCALE: float  = 25.0    # max ± adjustment in score points
_MODEL_PATH = Path(__file__).parent.parent / "data" / "models" / "personal_model.pkl"

# Uncertainty ensemble: N sub-sampled GBR pipelines whose prediction spread
# proxies per-photo uncertainty for the Auto-cull "Uncertain → Maybe" routing.
# 20 trees × n_estimators=50 keeps total train cost ~5× the main pipeline
# (~30s on a typical ~few-hundred-sample training set per project memory).
_ENSEMBLE_N: int = 20
_ENSEMBLE_SUBSAMPLE: float = 0.7

_LABEL: dict[str, float] = {"keep": 1.0, "maybe": 0.0, "reject": -1.0}

# Fixed cutoffs used only in bootstrap validation — close enough to defaults
# (keep ≥ 70, maybe ≥ 50) that they give a reliable go/no-go signal without
# needing to import from backend/.
_VAL_KEEP_CUTOFF:  float = 70.0
_VAL_MAYBE_CUTOFF: float = 50.0
_VAL_MIN_MARGIN:   float = 0.05   # model must beat baseline by ≥5 pp to count


def _bootstrap_validate(
    pipeline: Pipeline,
    X: np.ndarray,
    y: np.ndarray,
    rows: list[dict],
    n_iter: int = 10,
) -> dict:
    """
    Bootstrap validation: estimate model ordinal accuracy vs. threshold baseline.

    Runs n_iter 80/20 train/test splits (sampled without replacement for the
    test set so each test image is seen at most once per iteration).  For each
    split a fresh GBR pipeline is re-fitted on the train portion and evaluated
    on the held-out test portion.

    Ordinal accuracy = fraction of test samples where the predicted decision
    label (keep / maybe / reject derived from personal_score thresholds) matches
    the user's actual decision.

    Returns a dict with:
        model_accuracy   — mean accuracy across iterations (0–1)
        baseline_accuracy — mean accuracy of simple overall_score threshold
        beats_baseline   — True if margin >= _VAL_MIN_MARGIN
        margin           — model_accuracy − baseline_accuracy
    """
    rng = np.random.default_rng(42)
    n = len(X)
    model_accs: list[float] = []
    baseline_accs: list[float] = []

    # Limit iterations when data is very small so we don't re-use the same
    # test samples repeatedly (no useful signal from that).
    effective_iters = min(n_iter, max(1, n // 5))

    for _ in range(effective_iters):
        test_size = max(1, n // 5)
        test_idx = rng.choice(n, size=test_size, replace=False)
        train_idx = np.setdiff1d(np.arange(n), test_idx)
        if len(train_idx) < 5:
            continue

        # Re-fit a lightweight pipeline on the train split only.
        val_pipeline = Pipeline([
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler",  StandardScaler()),
            ("gbr",     GradientBoostingRegressor(
                n_estimators=50,   # fewer estimators — speed over accuracy
                max_depth=3,
                learning_rate=0.1,
                random_state=42,
            )),
        ])
        val_pipeline.fit(X[train_idx], y[train_idx])

        raw_preds = val_pipeline.predict(X[test_idx])
        model_correct = 0
        baseline_correct = 0

        for j, idx in enumerate(test_idx):
            true_label = float(y[idx])   # +1.0, 0.0, or -1.0
            row = rows[idx]
            overall = float(row.get("overall_score") or 0.0)

            # Model decision: delta → personal_score → label
            raw = float(raw_preds[j])
            delta = float(np.clip(raw * DELTA_SCALE, -DELTA_SCALE, DELTA_SCALE))
            personal_score = float(np.clip(overall + delta, 0.0, 100.0))
            if personal_score >= _VAL_KEEP_CUTOFF:
                model_pred = 1.0
            elif personal_score >= _VAL_MAYBE_CUTOFF:
                model_pred = 0.0
            else:
                model_pred = -1.0

            # Baseline decision: threshold on overall_score directly
            if overall >= _VAL_KEEP_CUTOFF:
                base_pred = 1.0
            elif overall >= _VAL_MAYBE_CUTOFF:
                base_pred = 0.0
            else:
                base_pred = -1.0

            model_correct    += int(model_pred == true_label)
            baseline_correct += int(base_pred  == true_label)

        model_accs.append(model_correct / test_size)
        baseline_accs.append(baseline_correct / test_size)

    if not model_accs:
        return {
            "model_accuracy":    0.0,
            "baseline_accuracy": 0.0,
            "beats_baseline":    False,
            "margin":            0.0,
        }

    model_acc    = float(np.mean(model_accs))
    baseline_acc = float(np.mean(baseline_accs))
    margin       = model_acc - baseline_acc

    return {
        "model_accuracy":    round(model_acc,    3),
        "baseline_accuracy": round(baseline_acc, 3),
        "beats_baseline":    margin >= _VAL_MIN_MARGIN,
        "margin":            round(margin, 3),
    }


def _fit_uncertainty_ensemble(
    X: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray | None = None,
) -> list[Pipeline]:
    """Fit _ENSEMBLE_N sub-sampled GBRs for prediction-variance estimation.

    Each member sees a different random 70% slice of the training rows
    (sklearn's GBR `subsample` does this internally per-tree; we also seed
    each member with a different random_state so they diverge meaningfully).
    n_estimators=50 (vs 200 on the main pipeline) keeps total train cost
    manageable — ensemble adds roughly 5× the main-pipeline cost.

    sample_weight is passed through unchanged when provided so recency-decayed
    and pairwise-weighted training propagates to the ensemble too.
    """
    members: list[Pipeline] = []
    for i in range(_ENSEMBLE_N):
        member = Pipeline([
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler",  StandardScaler()),
            ("gbr",     GradientBoostingRegressor(
                n_estimators=50,
                max_depth=3,
                learning_rate=0.1,
                subsample=_ENSEMBLE_SUBSAMPLE,
                random_state=42 + i,
            )),
        ])
        if sample_weight is not None:
            member.fit(X, y, gbr__sample_weight=sample_weight)
        else:
            member.fit(X, y)
        members.append(member)
    return members


class PersonalModel:
    """
    Wraps the sklearn pipeline for training, predicting, saving, and loading.

    One global instance is created at backend startup and shared across
    all requests (no locking needed — the model is only written during
    POST /train-model, which is a synchronous endpoint that blocks).
    """

    def __init__(self) -> None:
        self._pipeline: Optional[Pipeline] = None
        self._meta: dict = {}
        # Per-image score cache: image_id → personal_score (or None for rows
        # missing overall_score). Cleared on every train() so a freshly
        # retrained model never serves stale predictions. Re-analysis of a
        # single photo evicts that one id via invalidate().
        self._cache: dict[int, Optional[float]] = {}
        # Uncertainty ensemble — N sub-sampled GBR pipelines whose per-row
        # std_dev proxies prediction uncertainty. Empty list = unavailable
        # (old pickle, or model not yet retrained on this code path).
        self._uncertainty_ensemble: list[Pipeline] = []

    # ── State ────────────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        """True once the model has been trained at least once."""
        return self._pipeline is not None

    @property
    def training_size(self) -> int:
        return self._meta.get("training_size", 0)

    @property
    def trained_at(self) -> Optional[str]:
        return self._meta.get("trained_at")

    @property
    def beats_baseline(self) -> bool:
        """True if the last training run validated better than the threshold baseline."""
        v = self._meta.get("validation", {})
        return bool(v.get("beats_baseline", False))

    @property
    def validation_info(self) -> dict:
        """Return the validation metadata dict from the last training run."""
        return self._meta.get("validation", {})

    # ── Train ────────────────────────────────────────────────────────────────

    def train(self, rows: list[dict], decisions: list[str]) -> dict:
        """
        Fit the pipeline on all decided photos.

        rows       — list of images-table dicts (all columns present)
        decisions  — parallel list of 'keep' | 'maybe' | 'reject'

        Returns a metadata dict (training_size, trained_at, feature_importances).
        Raises ValueError when fewer than MIN_DECISIONS samples are provided.
        """
        if len(rows) < MIN_DECISIONS:
            raise ValueError(
                f"Need at least {MIN_DECISIONS} decisions to train "
                f"(currently have {len(rows)}). Keep culling!"
            )
        if len(rows) != len(decisions):
            raise ValueError("rows and decisions must have the same length")

        X = extract_batch(rows)                                     # (N, len(_COLUMNS))
        y = np.array([_LABEL[d] for d in decisions], dtype=np.float32)  # (N,)

        pipeline = Pipeline([
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler",  StandardScaler()),
            ("gbr",     GradientBoostingRegressor(
                n_estimators=200,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.8,
                random_state=42,
            )),
        ])
        pipeline.fit(X, y)

        importances = dict(zip(
            feature_names(),
            pipeline.named_steps["gbr"].feature_importances_.tolist(),
        ))

        # Bootstrap validation: compare model vs. simple threshold baseline.
        # Runs 10 lightweight 80/20 splits (~0.5 s on 25 samples).
        validation = _bootstrap_validate(pipeline, X, y, rows, n_iter=10)
        logger.info(
            "Personal model validation — model: %.3f  baseline: %.3f  beats: %s",
            validation["model_accuracy"],
            validation["baseline_accuracy"],
            validation["beats_baseline"],
        )

        self._pipeline = pipeline
        self._uncertainty_ensemble = _fit_uncertainty_ensemble(X, y)
        self._meta = {
            "training_size":          len(rows),
            "trained_at":             datetime.now().isoformat(timespec="seconds"),
            "feature_importances":    importances,
            "validation":             validation,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
        }
        # Predictions from the previous model are now wrong by definition.
        self._cache.clear()
        logger.info(
            "Personal model trained on %d decisions (ensemble: %d members)",
            len(rows), len(self._uncertainty_ensemble),
        )
        return dict(self._meta)

    def train_from_samples(
        self,
        feature_vectors: np.ndarray,
        decisions: list[str],
        overall_scores: list[float | None],
        *,
        decided_at: list[str | None] | None = None,
        recency_half_life_days: float = 180.0,
        base_weights: list[float] | None = None,
    ) -> dict:
        """
        Train directly from pre-extracted feature vectors. Used by the
        /train-model endpoint when reading from the durable training_samples
        table — features were frozen at decision time and may include NaN
        padding for newer feature columns.

        feature_vectors  — (N, len(_COLUMNS)) float32 array
        decisions        — parallel list of 'keep' | 'maybe' | 'reject'
        overall_scores   — parallel list of base scores (used by the
                           bootstrap validator's threshold baseline)
        """
        if feature_vectors.shape[0] < MIN_DECISIONS:
            raise ValueError(
                f"Need at least {MIN_DECISIONS} decisions to train "
                f"(currently have {feature_vectors.shape[0]}). Keep culling!"
            )
        if feature_vectors.shape[0] != len(decisions):
            raise ValueError("feature_vectors and decisions must have the same length")
        if feature_vectors.shape[0] != len(overall_scores):
            raise ValueError("feature_vectors and overall_scores must have the same length")

        X = feature_vectors
        y = np.array([_LABEL[d] for d in decisions], dtype=np.float32)

        # Recency weights — exponential decay so recent decisions matter more
        # than decisions made months ago. Half-life: every `recency_half_life_days`
        # days, a decision's weight halves. Decisions with missing timestamps
        # get weight 1.0 (no penalty). Disabled automatically when
        # recency_half_life_days <= 0 or decided_at is None.
        # Build sample weights as product of recency decay and base weight.
        # base_weights carries the pairwise-vs-explicit distinction (0.4 vs 1.0).
        # Recency decay further down-weights older decisions.
        n = len(decisions)
        base_w = np.array(base_weights, dtype=np.float32) if base_weights else np.ones(n, dtype=np.float32)

        sample_weight: np.ndarray | None = None
        if decided_at and recency_half_life_days > 0:
            now = datetime.now()
            lam = np.log(2.0) / recency_half_life_days
            recency: list[float] = []
            for ts in decided_at:
                if not ts:
                    recency.append(1.0)
                    continue
                try:
                    dt = datetime.fromisoformat(ts.replace(" ", "T"))
                    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
                    recency.append(float(np.exp(-lam * age_days)))
                except (ValueError, TypeError):
                    recency.append(1.0)
            w_arr = base_w * np.array(recency, dtype=np.float32)
            total = w_arr.sum()
            if total > 0:
                sample_weight = w_arr * (n / total)
        elif base_weights and any(w != 1.0 for w in base_weights):
            # No recency decay but pairwise weights are present — still apply them.
            total = base_w.sum()
            if total > 0:
                sample_weight = base_w * (n / total)

        pipeline = Pipeline([
            ("imputer", SimpleImputer(strategy="mean")),
            ("scaler",  StandardScaler()),
            ("gbr",     GradientBoostingRegressor(
                n_estimators=200,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.8,
                random_state=42,
            )),
        ])
        fit_kwargs: dict = {}
        if sample_weight is not None:
            fit_kwargs["gbr__sample_weight"] = sample_weight
        pipeline.fit(X, y, **fit_kwargs)

        importances = dict(zip(
            feature_names(),
            pipeline.named_steps["gbr"].feature_importances_.tolist(),
        ))

        # Build minimal "rows" stand-ins so _bootstrap_validate can read
        # overall_score (the only field it needs from the row dicts).
        synthetic_rows = [{"overall_score": s if s is not None else 0.0} for s in overall_scores]
        validation = _bootstrap_validate(pipeline, X, y, synthetic_rows, n_iter=10)
        logger.info(
            "Personal model validation — model: %.3f  baseline: %.3f  beats: %s",
            validation["model_accuracy"],
            validation["baseline_accuracy"],
            validation["beats_baseline"],
        )

        self._pipeline = pipeline
        self._uncertainty_ensemble = _fit_uncertainty_ensemble(X, y, sample_weight=sample_weight)
        self._meta = {
            "training_size":          feature_vectors.shape[0],
            "trained_at":             datetime.now().isoformat(timespec="seconds"),
            "feature_importances":    importances,
            "validation":             validation,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
        }
        self._cache.clear()
        logger.info(
            "Personal model trained on %d durable samples (ensemble: %d members)",
            feature_vectors.shape[0], len(self._uncertainty_ensemble),
        )
        return dict(self._meta)

    # ── Predict ──────────────────────────────────────────────────────────────

    def predict_personal_score(self, row: dict) -> Optional[float]:
        """Single-row prediction. Returns None if model isn't ready."""
        if not self.ready:
            return None
        base = row.get("overall_score")
        if base is None:
            return None
        raw   = float(self._pipeline.predict(extract(row).reshape(1, -1))[0])
        delta = float(np.clip(raw * DELTA_SCALE, -DELTA_SCALE, DELTA_SCALE))
        return float(np.clip(base + delta, 0.0, 100.0))

    def predict_batch(self, rows: list[dict]) -> list[Optional[float]]:
        """
        Batch prediction — much faster than N × predict_personal_score().

        Read-through cache: rows whose image_id is already cached return the
        stored score immediately; only cache misses go through pipeline.predict.
        Rows without an `id` key (callers passing synthetic dicts) bypass the
        cache entirely. Returns None for rows where overall_score is NULL.
        """
        if not self.ready or not rows:
            return [None] * len(rows)

        results: list[Optional[float]] = [None] * len(rows)
        miss_indices: list[int] = []
        miss_rows: list[dict] = []

        for i, row in enumerate(rows):
            image_id = row.get("id")
            if image_id is not None and image_id in self._cache:
                results[i] = self._cache[image_id]
            else:
                miss_indices.append(i)
                miss_rows.append(row)

        if miss_rows:
            X       = extract_batch(miss_rows)        # (M, len(_COLUMNS))
            raw_arr = self._pipeline.predict(X)       # (M,)
            for i, row, raw in zip(miss_indices, miss_rows, raw_arr):
                base = row.get("overall_score")
                if base is None:
                    score = None
                else:
                    delta = float(np.clip(float(raw) * DELTA_SCALE, -DELTA_SCALE, DELTA_SCALE))
                    score = float(np.clip(base + delta, 0.0, 100.0))
                results[i] = score
                image_id = row.get("id")
                if image_id is not None:
                    self._cache[image_id] = score

        return results

    # ── Uncertainty (ensemble) ───────────────────────────────────────────────

    def predict_with_uncertainty(self, row: dict) -> Optional[tuple[float, float]]:
        """Single-row prediction + ensemble std_dev. Returns None if model not
        ready or overall_score missing. Returns (score, 0.0) if the model is
        ready but the ensemble is empty (loaded from a pre-PR3 pickle that
        hasn't been retrained yet) — caller treats 0.0 as "no signal" and
        falls back to the hard decision.
        """
        if not self.ready:
            return None
        base = row.get("overall_score")
        if base is None:
            return None
        feats = extract(row).reshape(1, -1)
        raw = float(self._pipeline.predict(feats)[0])
        delta = float(np.clip(raw * DELTA_SCALE, -DELTA_SCALE, DELTA_SCALE))
        score = float(np.clip(base + delta, 0.0, 100.0))

        if not self._uncertainty_ensemble:
            return (score, 0.0)

        # Ensemble predictions, mapped to personal-score space the same way
        # the main pipeline is. std_dev is computed over the personal-score
        # values so the threshold setting is in user-facing 0–100 units.
        member_scores: list[float] = []
        for member in self._uncertainty_ensemble:
            raw_m = float(member.predict(feats)[0])
            delta_m = float(np.clip(raw_m * DELTA_SCALE, -DELTA_SCALE, DELTA_SCALE))
            member_scores.append(float(np.clip(base + delta_m, 0.0, 100.0)))
        std = float(np.std(member_scores))
        return (score, std)

    def predict_batch_with_uncertainty(
        self, rows: list[dict]
    ) -> list[Optional[tuple[float, float]]]:
        """Batch counterpart for predict_with_uncertainty. Used by auto-cull
        paths only — does NOT use the personal_score cache (which stores only
        the main-pipeline score, not std_dev).
        """
        if not self.ready or not rows:
            return [None] * len(rows)

        X = extract_batch(rows)            # (N, len(_COLUMNS))
        raw_arr = self._pipeline.predict(X)  # (N,)
        # Ensemble predictions: (E, N) array
        if self._uncertainty_ensemble:
            ensemble_raw = np.stack(
                [m.predict(X) for m in self._uncertainty_ensemble], axis=0
            )
        else:
            ensemble_raw = None

        results: list[Optional[tuple[float, float]]] = [None] * len(rows)
        for i, row in enumerate(rows):
            base = row.get("overall_score")
            if base is None:
                results[i] = None
                continue
            raw = float(raw_arr[i])
            delta = float(np.clip(raw * DELTA_SCALE, -DELTA_SCALE, DELTA_SCALE))
            score = float(np.clip(base + delta, 0.0, 100.0))
            if ensemble_raw is None:
                results[i] = (score, 0.0)
                continue
            member_scores: list[float] = []
            for e in range(ensemble_raw.shape[0]):
                raw_m = float(ensemble_raw[e, i])
                delta_m = float(np.clip(raw_m * DELTA_SCALE, -DELTA_SCALE, DELTA_SCALE))
                member_scores.append(float(np.clip(base + delta_m, 0.0, 100.0)))
            results[i] = (score, float(np.std(member_scores)))
        return results

    # ── Cache invalidation ───────────────────────────────────────────────────

    def invalidate(self, image_id: int) -> None:
        """Drop the cached score for one image — call after re-analysis."""
        self._cache.pop(image_id, None)

    def invalidate_many(self, image_ids: list[int]) -> None:
        """Drop cached scores for multiple images — call after a folder clear."""
        for image_id in image_ids:
            self._cache.pop(image_id, None)

    def clear_cache(self) -> None:
        """Drop the entire cache — call after a full /clear."""
        self._cache.clear()

    def reset(self, path: Path = _MODEL_PATH) -> None:
        """Wipe all learned state in-place and delete the on-disk pickle.

        After this the model behaves like a fresh PersonalModel(): `ready`
        is False, `training_size` is 0, no cached predictions, no ensemble.
        Call after the training_samples table has been cleared so the next
        auto-train pass starts from zero.
        """
        self._pipeline = None
        self._meta = {}
        self._uncertainty_ensemble = []
        self._cache.clear()
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    # ── Persist ──────────────────────────────────────────────────────────────

    def save(self, path: Path = _MODEL_PATH) -> None:
        """Persist the fitted pipeline atomically.

        Writes to a sibling `<name>.tmp` and then `os.replace()`s into place.
        os.replace is atomic on POSIX (and on Windows for files on the same
        volume), so a Force Quit during the save can't leave a half-written
        pickle that fails to load on next launch. With auto-training writing
        every ~10 decisions in the background, this guard matters.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            pickle.dump({
                "pipeline":             self._pipeline,
                "meta":                 self._meta,
                "uncertainty_ensemble": self._uncertainty_ensemble,
            }, f)
            f.flush()
            import os as _os
            _os.fsync(f.fileno())
        import os as _os
        _os.replace(tmp, path)
        logger.info("Personal model saved → %s (%d samples)", path, self.training_size)

    def load(self, path: Path = _MODEL_PATH) -> bool:
        """Load a previously saved model. Returns True on success, False if file
        missing or schema-incompatible.

        Schema-incompatible = the pickled pipeline was fit on a different number
        of features than the current feature_extractor produces. This happens
        whenever new feature columns get added to feature_extractor._COLUMNS.
        Rather than crash later inside pipeline.predict(), we refuse to load
        the stale model — `_personal_model.ready` stays False, the user sees
        the "Train" button, and one click rebuilds the model on the new schema.
        """
        if not path.exists():
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            pipeline = data["pipeline"]
            meta     = data.get("meta", {})

            expected = len(feature_names())
            actual   = getattr(pipeline, "n_features_in_", None)
            if actual is not None and actual != expected:
                logger.warning(
                    "Personal model on disk was trained on %d features but the "
                    "current schema has %d. Ignoring saved model — please retrain.",
                    actual, expected,
                )
                return False

            # Reject pickles whose feature schema is older than the current one.
            # Catches cases where the feature *count* is unchanged but a scorer's
            # output distribution has been swapped (e.g. LAION → TOPIQ-IAA in v5)
            # — the old pipeline would silently produce wrong predictions on new
            # data otherwise. Absent field = pre-v5 pickle, also refused.
            saved_version = meta.get("feature_schema_version")
            if saved_version != FEATURE_SCHEMA_VERSION:
                logger.warning(
                    "Personal model on disk has feature_schema_version=%s but the "
                    "current schema is %d. Ignoring saved model — please retrain.",
                    saved_version, FEATURE_SCHEMA_VERSION,
                )
                return False

            self._pipeline = pipeline
            self._meta     = meta
            # Ensemble is optional — pre-PR3 pickles don't have the key.
            # predict_with_uncertainty returns (score, 0.0) when empty,
            # which the auto-cull boundary router treats as "no signal".
            self._uncertainty_ensemble = data.get("uncertainty_ensemble", []) or []
            logger.info(
                "Personal model loaded ← %s (%d samples, %d ensemble members)",
                path, self.training_size, len(self._uncertainty_ensemble),
            )
            return True
        except Exception as exc:
            logger.warning("Could not load personal model: %s", exc)
            return False

    # ── Info ─────────────────────────────────────────────────────────────────

    def info(self) -> dict:
        """JSON-serialisable summary for GET /model-info."""
        if not self.ready:
            return {
                "ready":          False,
                "training_size":  0,
                "min_decisions":  MIN_DECISIONS,
                "model_status":   "untrained",
                "beats_baseline": False,
                "validation":     {},
            }
        top = sorted(
            self._meta.get("feature_importances", {}).items(),
            key=lambda kv: kv[1],
            reverse=True,
        )

        # Determine model_status:
        #   "learning"        — not enough data for validation to be reliable
        #   "ready"           — >= 50 decisions AND beats the baseline
        #   "underperforming" — >= 50 decisions but does NOT beat the baseline
        if self.training_size < 50:
            model_status = "learning"
        elif self.beats_baseline:
            model_status = "ready"
        else:
            model_status = "underperforming"

        return {
            "ready":          True,
            "training_size":  self.training_size,
            "trained_at":     self.trained_at,
            "min_decisions":  MIN_DECISIONS,
            "model_status":   model_status,
            "beats_baseline": self.beats_baseline,
            "validation":     self.validation_info,
            "top_features":   [
                {"name": name, "importance": round(imp, 4)}
                for name, imp in top
            ],
        }
