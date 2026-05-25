"""
Converts a SQLite images row into a fixed-length numpy feature vector
for the Phase 3 personal scoring model.

Each column maps to one feature dimension. Missing values (NULL in SQLite,
None in Python) become np.nan — the sklearn SimpleImputer in the training
pipeline fills them with the training-set mean, so we never train on garbage.

Why these features?
  Technical quality:  sharpness, exposure, iqa, aesthetic
                      NOTE: overall_score is deliberately excluded — it is a
                      linear combination of sharpness and exposure already in
                      the vector, so including it would let the GBR double-weight
                      the technical axis without adding new signal.
  Clipping:           highlight_clip_pct, shadow_clip_pct
                      (raw amounts of blown highlights / crushed shadows;
                      finer-grained than the composite exposure_score and
                      can capture taste like "I keep moody low-key shots")
  Camera physics:     focal_length_mm, aperture, iso
                      (personal taste is often lens/setting specific)
  Face signals:       face_present (computed), face_detected, face_count,
                      face_sharpness, eyes_open, eye_openness_ratio,
                      face_size_ratio
                      face_present is a hard binary flag so the model knows
                      when the other face columns are meaningful vs. imputed.
                      Without it, a non-portrait shot gets "average face"
                      from SimpleImputer — face_present short-circuits that.
  Shake:              shake_detected (hard reject signal you may override
                      for artistic blur shots)

Feature count: 31 — sharpness/exposure/clipping (6) + face block (8) + expressions (2)
+ camera physics (3) + scene one-hots (8) + SigLIP content axes (4)
"""

import json
import numpy as np

# Schema version of the feature vector. BUMP THIS when _COLUMNS changes
# (add/remove/reorder a feature) OR when a scorer's output distribution
# changes meaningfully (e.g. swapping LAION aesthetic head → TOPIQ-IAA in v5,
# or SigLIP-1 → SigLIP-2 in v6).
# Older training_samples rows are padded with NaN for any new/invalidated
# column at training time; the SimpleImputer fills NaN with the column mean,
# so old labels stay usable across feature-schema bumps.
#
# A persisted personal_model.pkl whose meta.feature_schema_version is older
# than this constant is refused by PersonalModel.load() and forces a retrain.
FEATURE_SCHEMA_VERSION: int = 6


# Ordered feature names.
# Order is stable — never re-order once the model is trained or saved
# predictions will be wrong.
#
# "face_present" is a COMPUTED feature (not a DB column): 1.0 if
# face_detected == 1, else 0.0.  It is never NaN.
_COLUMNS: list[str] = [
    "sharpness_score",
    "exposure_score",
    # "overall_score" intentionally omitted — linear combo of sharpness + exposure
    "iqa_score",
    "aesthetic_score",
    "highlight_clip_pct",
    "shadow_clip_pct",
    "shake_detected",
    "face_present",         # computed: 1.0 if face_detected else 0.0
    "face_detected",
    "face_count",
    "face_sharpness_score",
    "eyes_open",
    "eye_openness_ratio",
    "face_size_ratio",
    "smile_score",
    "mouth_open_score",
    "focal_length_mm",
    "aperture",
    "iso",
    # Zero-shot scene classification (computed from scene TEXT column).
    # Binary one-hot: 1.0 when the photo's scene label matches, else 0.0.
    # Never NaN — photos without a scene tag (analyzed before v0.12) get 0.0
    # for all scene features so the imputer never has to fill these.
    "scene_is_portrait",
    "scene_is_landscape",
    "scene_is_street",
    "scene_is_night",
    "scene_is_macro",
    "scene_is_indoor",
    "scene_is_action",
    "scene_is_water",
    # SigLIP zero-shot content axes (FEATURE_SCHEMA_VERSION=4, schema v39).
    # Each is 0.0–1.0 or NaN. eye_contact is NaN for non-portrait shots —
    # the SimpleImputer fills that with the training-set mean, but the model
    # also sees face_present as a hard 0/1 flag and can downweight eye_contact
    # in non-face contexts on its own.
    "subject_prominence_score",
    "background_distraction_score",
    "eye_contact_score",
    "decisive_moment_score",
]

# Scene label → feature column name (for computed extraction below).
_SCENE_FEATURE_LABELS: tuple[str, ...] = (
    "portrait", "landscape", "street", "night", "macro", "indoor", "action", "water",
)


def feature_names() -> list[str]:
    """Return the ordered list of feature names (for importances display)."""
    return list(_COLUMNS)


def extract(row: dict) -> np.ndarray:
    """
    Convert one images row (dict) to a 1-D float32 array of length len(_COLUMNS).

    sqlite3.Row objects should be converted with dict(row) before passing here.
    Missing values → np.nan (pipeline's SimpleImputer handles them).

    Special case: "face_present" is computed from "face_detected" — it is
    always 0.0 or 1.0, never NaN, so the model always knows whether the face
    feature columns contain real data or imputed means.
    """
    scene = row.get("scene")  # TEXT column, e.g. "portrait" or None

    values: list[float] = []
    for col in _COLUMNS:
        if col == "face_present":
            val = 1.0 if row.get("face_detected") == 1 else 0.0
        elif col.startswith("scene_is_"):
            label = col[len("scene_is_"):]  # e.g. "portrait"
            val = 1.0 if scene == label else 0.0
        else:
            raw = row.get(col)
            val = np.nan if raw is None else float(raw)
        values.append(val)
    return np.array(values, dtype=np.float32)


def extract_batch(rows: list[dict]) -> np.ndarray:
    """
    Convert a list of rows to a 2-D array of shape (N, len(_COLUMNS)).

    Used for batch prediction in GET /images so we call pipeline.predict()
    once for all N images instead of N separate calls.
    """
    if not rows:
        return np.empty((0, len(_COLUMNS)), dtype=np.float32)
    return np.vstack([extract(r) for r in rows])


def serialize_features(row: dict) -> str:
    """
    Freeze one image row's feature vector as a JSON dict {name: value}.

    Used at decision time to write a stable training-sample snapshot into
    `training_samples.features_json`. Storing as a name-keyed dict (not a
    bare array) keeps old samples loadable even if `_COLUMNS` is reordered
    later — the loader keys by name, not position.

    NaN is encoded as `null` so the JSON stays valid (Python's json module
    rejects float('nan') by default with allow_nan=False; we keep the same
    contract on read).
    """
    arr = extract(row)
    payload: dict[str, float | None] = {}
    for name, val in zip(_COLUMNS, arr.tolist()):
        payload[name] = None if (val is None or (isinstance(val, float) and np.isnan(val))) else float(val)
    return json.dumps(payload, separators=(",", ":"))


def features_from_json(features_json: str) -> np.ndarray:
    """
    Inverse of `serialize_features`. Returns a 1-D float32 array of length
    len(_COLUMNS), padding any missing key with NaN so older schema rows
    survive feature-schema growth.

    Unknown keys (features dropped from a newer schema) are silently ignored.
    Missing keys (features added since the row was saved) become NaN — the
    SimpleImputer fills them with the training-set mean at fit time.
    """
    data = json.loads(features_json)
    values: list[float] = []
    for col in _COLUMNS:
        v = data.get(col)
        values.append(np.nan if v is None else float(v))
    return np.array(values, dtype=np.float32)
