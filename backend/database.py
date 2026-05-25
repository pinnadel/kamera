"""
SQLite schema and connection helpers for KaMeRa.

Three core tables:
  images        — one row per photo file, holds EXIF + analysis scores
  bursts        — one row per burst group, tracks which shot is the hero
  burst_members — join table linking images to bursts (many-to-many bridge)
  decisions     — user's keep/reject/maybe decision per image

Connection pattern: always use get_db() as a context manager so the
connection is closed and changes are committed automatically.

Schema versioning:
  The schema_version table records which migrations have been applied.
  _migrate() is safe to call on both fresh DBs and existing ones:
  - Fresh DB: all migrations run and are recorded.
  - Existing DB (pre-versioning): ALTER TABLE steps are tried with
    try/except OperationalError (column already exists silently passes),
    and every version row is inserted regardless — seeding schema_version
    up to the current level in a single startup.
  - Future DBs: only migrations with version > MAX(schema_version.version)
    are executed, so each step runs exactly once.
"""

import sqlite3
import contextlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default DB location: kamera/data/pca.db
# Callers can override by passing a different path to create_tables() / get_db().
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "pca.db"


@contextlib.contextmanager
def get_db(db_path: Path = DEFAULT_DB_PATH):
    """
    Context manager that opens a SQLite connection and closes it cleanly.

    Usage:
        with get_db() as conn:
            conn.execute("SELECT * FROM images")

    - Commits automatically on clean exit.
    - Rolls back and re-raises on any exception so partial writes never persist.
    - Row factory is set to sqlite3.Row so columns are accessible by name:
        row["sharpness_score"]  ← works, not just row[9]
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # lets you do row["column_name"]
    conn.execute("PRAGMA foreign_keys = ON")  # enforce FK constraints
    # WAL mode lets the batch thread commit per-photo without blocking the
    # frontend's /images poll, and vice versa. synchronous=NORMAL drops the
    # per-commit fsync from ~5–15ms to <1ms on macOS SSDs (live-batch evidence
    # 2026-05-05: 741 commits × ~7ms = ~5s of pure fsync stall). With WAL +
    # NORMAL, durability degrades only at the WAL-checkpoint boundary
    # (~1 MB of writes); the worst case on power loss is replaying the
    # last few photos — RAW files on disk are the source of truth, not
    # pca.db. SQLite's WAL is journal-of-record, not the data itself.
    # Both PRAGMAs are cheap to set on every open (no-op if already set
    # on this DB file).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Create all tables if they don't exist yet. Safe to call at every app startup.

    Uses IF NOT EXISTS so running this twice never destroys data.
    Runs _migrate() afterwards to add any columns missing from existing DBs.
    """
    with get_db(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        _migrate(conn)


def get_schema_version(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Return the current schema version number (0 if none applied yet)."""
    with get_db(db_path) as conn:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] if row["v"] is not None else 0


# ---------------------------------------------------------------------------
# Migration step helpers (defined before _MIGRATIONS so they can be referenced)
# ---------------------------------------------------------------------------

def _step_v17_backfill_source_folder(conn: sqlite3.Connection) -> None:
    """Backfill source_folder for images ingested before the column was added."""
    conn.execute("""
        UPDATE images
        SET source_folder = substr(file_path, 1, length(file_path) - length(filename) - 1)
        WHERE source_folder IS NULL
    """)


def _step_v25_backfill_shooting_log(conn: sqlite3.Connection) -> None:
    """Seed shooting_log from rows currently in the images table.

    Idempotent — the NOT IN guard skips uuids already present so re-running
    or upgrading on a partially-populated DB never duplicates. lens_model and
    film_simulation aren't in the images table, so they stay NULL for
    pre-upgrade rows; the dashboard renders those as 'Unknown' buckets.

    Defensive against pre-existing DBs whose v1 ADD COLUMN uuid migration
    silently no-op'd (the runner catches OperationalError and seeds the
    schema_version row anyway). If `images` lacks uuid, we have nothing to
    backfill from — the table stays empty and forward writes populate it
    from there. PRAGMA table_info is the cheapest way to detect the column.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(images)").fetchall()}
    if "uuid" not in cols:
        return
    conn.execute("""
        INSERT INTO shooting_log
            (sample_uuid, analyzed_at, shot_at, camera, format,
             focal_length_mm, aperture, shutter_speed, iso, overall_score)
        SELECT uuid, imported_at, shot_at, camera, format,
               focal_length_mm, aperture, shutter_speed, iso, overall_score
        FROM images
        WHERE uuid IS NOT NULL
          AND uuid NOT IN (SELECT sample_uuid FROM shooting_log WHERE sample_uuid IS NOT NULL)
    """)


def _step_v42_null_aesthetic_in_training_samples(conn: sqlite3.Connection) -> None:
    """Drop aesthetic_score values from every training_samples row.

    Run once when the aesthetic scorer is swapped (PR1: LAION CLIP+SAC head →
    pyiqa TOPIQ-IAA, FEATURE_SCHEMA_VERSION 4 → 5). Old-scale aesthetic values
    would otherwise mix with new-scale values when the model retrains, silently
    corrupting predictions. NULLing the key drops the old value; the imputer
    fills missing values with the training-set mean once new-scale samples
    arrive. All other features in the row (sharpness, face, scene, etc.) are
    preserved.

    Idempotent: rows whose features_json is missing or malformed are skipped.
    """
    import json as _json
    rows = conn.execute("SELECT id, features_json FROM training_samples").fetchall()
    for row in rows:
        raw = row["features_json"]
        if not raw:
            continue
        try:
            data = _json.loads(raw)
        except (ValueError, TypeError):
            continue
        if "aesthetic_score" not in data:
            continue
        data["aesthetic_score"] = None
        conn.execute(
            "UPDATE training_samples SET features_json = ?, schema_version = ? WHERE id = ?",
            (_json.dumps(data, separators=(",", ":")), 5, row["id"]),
        )


def _step_v44_null_siglip_features_in_training_samples(conn: sqlite3.Connection) -> None:
    """Drop the four SigLIP zero-shot content axis values from every
    training_samples row.

    Run once when SigLIP-1 → SigLIP-2 (PR2, FEATURE_SCHEMA_VERSION 5 → 6).
    The four affected feature keys are computed from SigLIP image embeddings
    via sigmoid-of-cosine against text prompts; SigLIP-2's embedding space is
    different, so old-scale axis values would mix with new-scale ones at the
    next retrain. NULLing the keys drops them; SimpleImputer fills with the
    training-set mean once new-scale samples arrive. All other features
    (sharpness, face, scene one-hots, aesthetic, etc.) survive intact.

    Note: scene_is_* one-hots are NOT nulled. They derive from the `scene`
    TEXT column, and zero-shot scene classification across SigLIP-1 and
    SigLIP-2 typically converges on the same coarse label (portrait /
    landscape / etc.) — the mismatch is mild and self-corrects after a few
    new decisions.

    Idempotent: rows whose features_json is missing or malformed are skipped.
    """
    import json as _json
    _SIGLIP_KEYS = (
        "subject_prominence_score",
        "background_distraction_score",
        "eye_contact_score",
        "decisive_moment_score",
    )
    rows = conn.execute("SELECT id, features_json FROM training_samples").fetchall()
    for row in rows:
        raw = row["features_json"]
        if not raw:
            continue
        try:
            data = _json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not any(k in data for k in _SIGLIP_KEYS):
            continue
        for k in _SIGLIP_KEYS:
            if k in data:
                data[k] = None
        conn.execute(
            "UPDATE training_samples SET features_json = ?, schema_version = ? WHERE id = ?",
            (_json.dumps(data, separators=(",", ":")), 6, row["id"]),
        )


def _migrate_folder_settings_global_to_per_folder(conn: sqlite3.Connection) -> None:
    """One-shot migration: lift global keeps/maybes/trash folder settings into
    folder_settings, scoped to every source_folder currently known. Then delete
    the global rows so the new code path is the only source of truth."""
    legacy_keys = {
        "keeps_folder":  "keep",
        "maybes_folder": "maybe",
        "trash_folder":  "reject",
    }
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('keeps_folder', 'maybes_folder', 'trash_folder')"
    ).fetchall()
    if not rows:
        return
    folders = [r["source_folder"] for r in conn.execute(
        "SELECT DISTINCT source_folder FROM images WHERE source_folder IS NOT NULL"
    ).fetchall()]
    for r in rows:
        decision = legacy_keys[r["key"]]
        value = (r["value"] or "").strip()
        if not value:
            continue
        for folder in folders:
            conn.execute(
                "INSERT OR IGNORE INTO folder_settings (source_folder, decision, dest_folder) VALUES (?, ?, ?)",
                (folder, decision, value),
            )
    conn.execute(
        "DELETE FROM settings WHERE key IN ('keeps_folder', 'maybes_folder', 'trash_folder')"
    )


# ---------------------------------------------------------------------------
# Versioned migration registry
#
# Each entry is a (version, description, sql_or_callable) tuple:
#   version     — integer, must be strictly increasing, gaps are allowed
#   description — human-readable label recorded in schema_version.description
#   sql_or_callable — either a SQL string (executed directly) or a callable
#                     that receives the open sqlite3.Connection
#
# Runner behaviour (see _migrate):
#   1. Read MAX(version) from schema_version (NULL → 0 for a brand-new DB).
#   2. For every entry with version > current:
#      a. If the step is a SQL string, execute it inside try/except
#         OperationalError so "duplicate column" never aborts the run.
#      b. If the step is a callable, call it with the connection.
#      c. INSERT the version row into schema_version.
#   3. On first run against a pre-versioning DB all existing columns are
#      already present, so the ALTER TABLE silently no-ops and the version
#      rows are inserted, seeding the table in one startup pass.
# ---------------------------------------------------------------------------

_MIGRATIONS: list[tuple[int, str, str | object]] = [
    # v1–v9: Phase 2 face detection columns
    (1,  "Add uuid column",
         "ALTER TABLE images ADD COLUMN uuid TEXT UNIQUE"),
    (2,  "Add face_detected column",
         "ALTER TABLE images ADD COLUMN face_detected INTEGER"),
    (3,  "Add face_count column",
         "ALTER TABLE images ADD COLUMN face_count INTEGER"),
    (4,  "Add face_sharpness_score column",
         "ALTER TABLE images ADD COLUMN face_sharpness_score REAL"),
    (5,  "Add eyes_open column",
         "ALTER TABLE images ADD COLUMN eyes_open INTEGER"),
    (6,  "Add eye_openness_ratio column",
         "ALTER TABLE images ADD COLUMN eye_openness_ratio REAL"),
    (7,  "Add face_size_ratio column",
         "ALTER TABLE images ADD COLUMN face_size_ratio REAL"),
    (8,  "Add face_center_offset_x column",
         "ALTER TABLE images ADD COLUMN face_center_offset_x REAL"),
    (9,  "Add face_center_offset_y column",
         "ALTER TABLE images ADD COLUMN face_center_offset_y REAL"),
    # v10: Phase 2 module 2 — perceptual IQA via TOPIQ
    (10, "Add iqa_score column",
         "ALTER TABLE images ADD COLUMN iqa_score REAL"),
    # v11: Phase 2 module 3 — aesthetic appeal via LAION Aesthetic Predictor
    (11, "Add aesthetic_score column",
         "ALTER TABLE images ADD COLUMN aesthetic_score REAL"),
    # v12: Phase 2 module 4 — SigLIP semantic embedding (768-dim JSON text)
    (12, "Add embedding column",
         "ALTER TABLE images ADD COLUMN embedding TEXT"),
    # v13: Post-decision file moves — original source directory
    (13, "Add source_folder column",
         "ALTER TABLE images ADD COLUMN source_folder TEXT"),
    # v14: LLM narrative explanation (lazy-generated, stored for instant display)
    (14, "Add explanation column",
         "ALTER TABLE images ADD COLUMN explanation TEXT"),
    # v15–v16: Per-image clipping percentages for Phase 3 feature vector
    (15, "Add highlight_clip_pct column",
         "ALTER TABLE images ADD COLUMN highlight_clip_pct REAL"),
    (16, "Add shadow_clip_pct column",
         "ALTER TABLE images ADD COLUMN shadow_clip_pct REAL"),
    # v17: Backfill source_folder for rows ingested before v13
    (17, "Backfill source_folder from file_path",
         _step_v17_backfill_source_folder),
    # v18: Migrate global K/M/X folder settings to per-folder rows
    (18, "Migrate global folder settings to per-folder",
         _migrate_folder_settings_global_to_per_folder),
    # v19: Indexes for hot read paths.
    # source_folder is filtered in /images, /auto-cull/preview, /auto-cull,
    # /clear-folder, /sync-folder, and /folders (GROUP BY). Without an index,
    # every poll of /images during analysis triggers a full table scan.
    (19, "Index on images.source_folder",
         "CREATE INDEX IF NOT EXISTS idx_images_source_folder ON images(source_folder)"),
    # v20: Partial index on analysis_status='done'. Plain index on a 3-value
    # column has poor selectivity; partial index is small, dense, and
    # reliably picked by the planner for the dominant query shape.
    (20, "Partial index on analysis_status='done'",
         "CREATE INDEX IF NOT EXISTS idx_images_status_done ON images(id) WHERE analysis_status = 'done'"),
    # v21: Durable training corpus. Each row freezes one (uuid, decision,
    # feature vector) tuple at decision time so the personal model can be
    # retrained even after Clear Analysis or after the photo file is moved
    # off-disk. No FK to images — survives every cascade.
    (21, "Create training_samples table",
         """
         CREATE TABLE IF NOT EXISTS training_samples (
             id              INTEGER PRIMARY KEY AUTOINCREMENT,
             sample_uuid     TEXT    UNIQUE,
             decided_at      DATETIME NOT NULL DEFAULT (DATETIME('now')),
             decision        TEXT    NOT NULL CHECK (decision IN ('keep', 'maybe', 'reject')),
             features_json   TEXT    NOT NULL,
             overall_score   REAL,
             schema_version  INTEGER NOT NULL
         )
         """),
    (22, "Index on training_samples.sample_uuid",
         "CREATE INDEX IF NOT EXISTS idx_training_samples_uuid ON training_samples(sample_uuid)"),
    # v23–v25: Persistent shooting_log for the Dashboard. Lifecycle is fully
    # decoupled from analysis tabs — survives /clear-folder, /clear, and the
    # photo file being deleted. Keyed by sample_uuid (no FK to images), same
    # survival semantics as training_samples.
    (23, "Create shooting_log table",
         """
         CREATE TABLE IF NOT EXISTS shooting_log (
             id              INTEGER PRIMARY KEY AUTOINCREMENT,
             sample_uuid     TEXT    UNIQUE,
             analyzed_at     DATETIME NOT NULL DEFAULT (DATETIME('now')),
             shot_at         DATETIME,
             camera          TEXT,
             lens_model      TEXT,
             film_simulation TEXT,
             format          TEXT,
             focal_length_mm REAL,
             aperture        REAL,
             shutter_speed   REAL,
             iso             INTEGER,
             overall_score   REAL
         )
         """),
    (24, "Indexes on shooting_log",
         # Three indexes in one step — SQLite executes statements separately
         # from executescript, but we only have one slot per migration entry,
         # so collapse them with a callable.
         lambda conn: [
             conn.execute("CREATE INDEX IF NOT EXISTS idx_shooting_log_uuid ON shooting_log(sample_uuid)"),
             conn.execute("CREATE INDEX IF NOT EXISTS idx_shooting_log_shot_at ON shooting_log(shot_at)"),
             conn.execute("CREATE INDEX IF NOT EXISTS idx_shooting_log_camera ON shooting_log(camera)"),
         ]),
    (25, "Backfill shooting_log from images",
         _step_v25_backfill_shooting_log),
    # v26 was a previous, broken attempt at this. Don't reuse the number;
    # some live DBs already have it recorded in schema_version which would
    # cause the runner to skip the re-issue.
    (26, "(deprecated, replaced by v28)", lambda conn: None),
    (27, "(deprecated, replaced by v28)", lambda conn: None),
    # v28: Defensive uuid column add. v1 ("Add uuid column") was a UNIQUE
    # ALTER, which SQLite refuses on an existing table ("Cannot add a UNIQUE
    # column"). On some DBs that error was swallowed silently — schema_version
    # was bumped to v1 anyway, leaving the column AWOL. training_samples and
    # shooting_log both rely on it. We re-issue the ALTER without UNIQUE
    # (SQLite-safe), then enforce uniqueness via a separate index.
    (28, "Ensure images.uuid column exists (UNIQUE-safe)",
         "ALTER TABLE images ADD COLUMN uuid TEXT"),
    (29, "Unique index on images.uuid",
         "CREATE UNIQUE INDEX IF NOT EXISTS uq_images_uuid ON images(uuid) WHERE uuid IS NOT NULL"),
    # v30: Persist the per-image histogram so DetailView's Histogram section
    # is instant. The endpoint previously decoded the cached preview JPEG and
    # ran np.bincount on every open (~50ms locally, but observed as multi-second
    # under contention with the LM Studio explanation request). Compute once at
    # analyze time on rgb_full (already in memory for scoring) and store the
    # JSON shape that compute_histogram returns. Per-image payload ~5 KB.
    (30, "Add histogram_json column",
         "ALTER TABLE images ADD COLUMN histogram_json TEXT"),
    (31, "Add smile_score column for expression scoring",
         "ALTER TABLE images ADD COLUMN smile_score REAL"),
    (32, "Add mouth_open_score column for expression scoring",
         "ALTER TABLE images ADD COLUMN mouth_open_score REAL"),
    (33, "Add scene column for zero-shot scene tagging",
         "ALTER TABLE images ADD COLUMN scene TEXT"),
    (34, "Add scene_confidence column for zero-shot scene tagging",
         "ALTER TABLE images ADD COLUMN scene_confidence REAL"),
    (35, "Create pairwise_comparisons table for A/B training mode",
         """
         CREATE TABLE IF NOT EXISTS pairwise_comparisons (
             id            INTEGER PRIMARY KEY AUTOINCREMENT,
             winner_id     INTEGER NOT NULL,
             loser_id      INTEGER NOT NULL,
             source_folder TEXT,
             decided_at    DATETIME NOT NULL DEFAULT (DATETIME('now'))
         )
         """),
    (36, "Index pairwise_comparisons on winner_id and loser_id",
         "CREATE INDEX IF NOT EXISTS idx_pairwise_winner ON pairwise_comparisons(winner_id)"),
    (37, "Add faces_eyes_open_json for per-face eye state in group shots",
         "ALTER TABLE images ADD COLUMN faces_eyes_open_json TEXT"),
    (38, "Add face_embedding for FaceNet identity vectors (People-mode clustering)",
         "ALTER TABLE images ADD COLUMN face_embedding TEXT"),
    # v39: SigLIP zero-shot content axes — scored at analyze time from the
    # cached image embedding. Each value is 0.0–1.0 or NULL (pre-v39 rows;
    # eye_contact is also NULL when no face is detected). Reanalyze a folder
    # to backfill — no separate migration script.
    (39, "Add subject_prominence_score (SigLIP zero-shot content axis)",
         "ALTER TABLE images ADD COLUMN subject_prominence_score REAL"),
    (39, "Add background_distraction_score (SigLIP zero-shot content axis, higher = more distracting)",
         "ALTER TABLE images ADD COLUMN background_distraction_score REAL"),
    (39, "Add eye_contact_score (SigLIP zero-shot, NULL when no face detected)",
         "ALTER TABLE images ADD COLUMN eye_contact_score REAL"),
    (39, "Add decisive_moment_score (SigLIP zero-shot content axis)",
         "ALTER TABLE images ADD COLUMN decisive_moment_score REAL"),
    # v40: Burst-ranking cache keyed by sorted-members hash. A single row
    # represents one ranking of one exact set of photos. Cache survives
    # cluster-threshold changes (members_hash is content-stable) and only
    # invalidates when the group's membership genuinely changes.
    (40, "Create burst_rankings table for cached LLM burst rankings",
         """
         CREATE TABLE IF NOT EXISTS burst_rankings (
             id            INTEGER PRIMARY KEY AUTOINCREMENT,
             members_hash  TEXT NOT NULL UNIQUE,
             member_ids    TEXT NOT NULL,
             rankings_json TEXT NOT NULL,
             model         TEXT,
             created_at    DATETIME NOT NULL DEFAULT (DATETIME('now'))
         )
         """),
    (40, "Index burst_rankings on members_hash for fast cache lookup",
         "CREATE INDEX IF NOT EXISTS idx_burst_rankings_hash ON burst_rankings(members_hash)"),
    # v41: Remember the XMP rating/label that existed BEFORE our decision
    # write, so undo can restore it instead of clearing. Without this, an
    # Undo after K/M/X on a photo that already had a Lightroom rating wipes
    # the LR rating — a silent data-loss bug. NULL for decisions made on
    # photos with no prior rating, or for legacy rows from before this
    # migration (those will continue to clear on undo, matching old behavior).
    (41, "Add prior_xmp_rating column to decisions",
         "ALTER TABLE decisions ADD COLUMN prior_xmp_rating TEXT"),
    (41, "Add prior_xmp_label column to decisions",
         "ALTER TABLE decisions ADD COLUMN prior_xmp_label TEXT"),
    # v42: PR1 aesthetic scorer swap (LAION CLIP+SAC → pyiqa TOPIQ-IAA).
    # FEATURE_SCHEMA_VERSION bumped 4 → 5 in phase3_learning/feature_extractor.py.
    # NULL the aesthetic_score key in every training_samples.features_json row
    # so the next retrain sees only new-scale aesthetic values + imputed
    # means for historic decisions. All other features in those rows survive.
    (42, "NULL aesthetic_score in training_samples (LAION → TOPIQ-IAA swap)",
         _step_v42_null_aesthetic_in_training_samples),
    # v43: PR1.5 — also NULL aesthetic_score in the live `images` table.
    # Without this, photos analyzed before PR1 still hold old-scale aesthetic
    # values that would mix with new-scale predictions from the retrained
    # personal model. UI already hides the aesthetic chip when null
    # (image.aesthetic_score != null gates in DetailView / SignalStrip);
    # re-analysis refills the column with TOPIQ-IAA-scale values.
    (43, "NULL aesthetic_score in images (force re-analysis after TOPIQ-IAA swap)",
         "UPDATE images SET aesthetic_score = NULL WHERE aesthetic_score IS NOT NULL"),
    # v44: PR2 SigLIP-1 → SigLIP-2 swap. FEATURE_SCHEMA_VERSION bumped 5 → 6.
    # NULL the four SigLIP-derived content-axis values in every
    # training_samples row so the next retrain doesn't mix SigLIP-1 and
    # SigLIP-2 scale data. Scene one-hots intentionally left in place — see
    # _step_v44_null_siglip_features_in_training_samples docstring.
    (44, "NULL SigLIP content-axis features in training_samples (SigLIP-1 → SigLIP-2)",
         _step_v44_null_siglip_features_in_training_samples),
    # v45: NULL the SigLIP-derived columns in `images` so re-analysis refills
    # them with SigLIP-2-space values. Affected: embedding (768-dim text JSON
    # used for burst grouping + semantic search), the four content axes, and
    # the zero-shot scene tag + confidence (also produced by SigLIP).
    # Same UI-gate pattern as v43: existing `!= null` checks already hide the
    # affected chips/bars until the column is refilled.
    (45, "NULL embedding in images (force burst-grouping refresh after SigLIP-2)",
         "UPDATE images SET embedding = NULL WHERE embedding IS NOT NULL"),
    (45, "NULL subject_prominence_score in images (SigLIP-2 distribution shift)",
         "UPDATE images SET subject_prominence_score = NULL WHERE subject_prominence_score IS NOT NULL"),
    (45, "NULL background_distraction_score in images (SigLIP-2 distribution shift)",
         "UPDATE images SET background_distraction_score = NULL WHERE background_distraction_score IS NOT NULL"),
    (45, "NULL eye_contact_score in images (SigLIP-2 distribution shift)",
         "UPDATE images SET eye_contact_score = NULL WHERE eye_contact_score IS NOT NULL"),
    (45, "NULL decisive_moment_score in images (SigLIP-2 distribution shift)",
         "UPDATE images SET decisive_moment_score = NULL WHERE decisive_moment_score IS NOT NULL"),
    (45, "NULL scene tag in images (SigLIP-2 zero-shot classification)",
         "UPDATE images SET scene = NULL WHERE scene IS NOT NULL"),
    (45, "NULL scene_confidence in images (SigLIP-2 zero-shot classification)",
         "UPDATE images SET scene_confidence = NULL WHERE scene_confidence IS NOT NULL"),
    # 2026-05-14 — Manual group composition. NULL = "no manual override,
    # photo goes through auto-clustering as today." Photos sharing a
    # non-NULL value are anchored together regardless of cosine; photos
    # with a unique non-NULL value are anchored to a singleton bucket.
    # See phase2_quality/similarity_scorer.py::group_by_similarity for the
    # two-phase reconciliation that consumes this column.
    (46, "Add manual_group_id for user-anchored group membership",
         "ALTER TABLE images ADD COLUMN manual_group_id TEXT"),
    (46, "Index for manual_group_id buckets in similarity-groups",
         "CREATE INDEX IF NOT EXISTS idx_images_manual_group_id ON images(manual_group_id) WHERE manual_group_id IS NOT NULL"),
    # v47: Record a non-rank outcome alongside cached rankings so the UI can
    # distinguish "AI determined the burst is near-duplicate frames; LLM
    # comparison is meaningless" from "pending, not ranked yet" or "ranked
    # successfully." Default 'ranked' so all existing rows mean what they did
    # before; new rows with outcome='near_duplicates' carry rankings_json='[]'
    # to indicate no per-photo rank was produced.
    (47, "Add outcome column to burst_rankings",
         "ALTER TABLE burst_rankings ADD COLUMN outcome TEXT NOT NULL DEFAULT 'ranked'"),
    # v48: XMP sidecar writes removed. We no longer track sidecar paths, and
    # the prior-rating snapshot is meaningless without a restore step on undo.
    # Drop the three columns to keep the schema honest. SQLite supports
    # ALTER TABLE DROP COLUMN since 3.35 (2021); the wrapping try/except on
    # OperationalError below covers re-runs on already-migrated DBs.
    (48, "Drop xmp_sidecar_path from images",
         "ALTER TABLE images DROP COLUMN xmp_sidecar_path"),
    (48, "Drop prior_xmp_rating from decisions",
         "ALTER TABLE decisions DROP COLUMN prior_xmp_rating"),
    (48, "Drop prior_xmp_label from decisions",
         "ALTER TABLE decisions DROP COLUMN prior_xmp_label"),
    # Drop the toggle row so the next read falls through to the (removed)
    # default. No-op if it was never set.
    (48, "Drop write_xmp_ratings setting row",
         "DELETE FROM settings WHERE key = 'write_xmp_ratings'"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Run any pending versioned migrations against the open connection.

    Safe to call on both fresh and existing databases — see module docstring
    for the full seeding/idempotency guarantee.
    """
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current_version: int = row["v"] if row["v"] is not None else 0

    for version, description, step in _MIGRATIONS:
        if version <= current_version:
            continue  # already applied

        if callable(step):
            step(conn)
        else:
            try:
                conn.execute(step)
            except sqlite3.OperationalError:
                pass  # column already exists (pre-versioning DB)

        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )


# ---------------------------------------------------------------------------
# Schema SQL — kept as a module-level constant so it's easy to read and audit
# without digging through function calls.
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str | None = None, db_path: Path = DEFAULT_DB_PATH) -> str | None:
    """Return the value for key from the settings table, or default if not set."""
    with get_db(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Insert or replace a setting value."""
    with get_db(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def write_shooting_log(
    conn: sqlite3.Connection,
    *,
    sample_uuid: str | None,
    shot_at: str | None,
    camera: str | None,
    lens_model: str | None,
    film_simulation: str | None,
    format: str | None,
    focal_length_mm: float | None,
    aperture: float | None,
    shutter_speed: float | None,
    iso: int | None,
    overall_score: float | None,
) -> None:
    """Append (or refresh) a shooting_log row for one analyzed photo.

    Called inside the same transaction as the images INSERT so the per-photo
    commit invariant (live /images polls see progress) is preserved. UUID is
    the only stable anchor — re-analyzing the same photo (different image_id,
    same uuid) replaces the row via ON CONFLICT instead of duplicating.

    Skips silently when sample_uuid is None — same forgiving behaviour as
    training_samples for photos analyzed before UUID assignment.
    """
    if not sample_uuid:
        return
    conn.execute(
        """
        INSERT INTO shooting_log
            (sample_uuid, analyzed_at, shot_at, camera, lens_model,
             film_simulation, format, focal_length_mm, aperture,
             shutter_speed, iso, overall_score)
        VALUES (?, DATETIME('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sample_uuid) DO UPDATE SET
            analyzed_at     = excluded.analyzed_at,
            shot_at         = excluded.shot_at,
            camera          = excluded.camera,
            lens_model      = excluded.lens_model,
            film_simulation = excluded.film_simulation,
            format          = excluded.format,
            focal_length_mm = excluded.focal_length_mm,
            aperture        = excluded.aperture,
            shutter_speed   = excluded.shutter_speed,
            iso             = excluded.iso,
            overall_score   = excluded.overall_score
        """,
        (
            sample_uuid,
            shot_at,
            camera,
            lens_model,
            film_simulation,
            format,
            focal_length_mm,
            aperture,
            shutter_speed,
            iso,
            overall_score,
        ),
    )


# ---------------------------------------------------------------------------
# Per-folder destination overrides (2026-05-04)
# Replaces the old global keeps_folder/maybes_folder/trash_folder rows in
# `settings`. Each analysis can now override its own destinations; missing
# rows fall back to default subfolders inside the source folder.
# ---------------------------------------------------------------------------

_DECISION_TO_OVERRIDE_KEY: dict[str, str] = {
    "keep":   "keeps_folder",
    "maybe":  "maybes_folder",
    "reject": "trash_folder",
}


def get_folder_overrides(source_folder: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, str]:
    """Return active per-decision overrides for one source folder.

    Result keys are the legacy override names (`keeps_folder`, `maybes_folder`,
    `trash_folder`) so this slots straight into `resolve_dest_folder`. Missing
    keys mean "use default subfolder."
    """
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT decision, dest_folder FROM folder_settings WHERE source_folder = ?",
            (source_folder,),
        ).fetchall()
    return {
        _DECISION_TO_OVERRIDE_KEY[r["decision"]]: r["dest_folder"]
        for r in rows
        if r["decision"] in _DECISION_TO_OVERRIDE_KEY
    }


def set_folder_override(
    source_folder: str,
    decision: str,
    dest_folder: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Set or clear a per-folder destination override.

    `dest_folder=None` (or empty/whitespace) deletes the row, reverting that
    decision to the default subfolder behaviour.
    """
    if decision not in _DECISION_TO_OVERRIDE_KEY:
        raise ValueError(f"decision must be one of {list(_DECISION_TO_OVERRIDE_KEY)}, got {decision!r}")
    with get_db(db_path) as conn:
        if dest_folder is None or not dest_folder.strip():
            conn.execute(
                "DELETE FROM folder_settings WHERE source_folder = ? AND decision = ?",
                (source_folder, decision),
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO folder_settings (source_folder, decision, dest_folder) VALUES (?, ?, ?)",
                (source_folder, decision, dest_folder.strip()),
            )


_SCHEMA_SQL = """

-- ============================================================
-- schema_version
-- Records which versioned migrations have been applied.
-- Runner inserts one row per step; MAX(version) is the current
-- schema level. Empty table means no migrations have run yet.
-- ============================================================
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  DATETIME NOT NULL DEFAULT (DATETIME('now')),
    description TEXT
);

-- ============================================================
-- images
-- One row per photo file. Created when PCA first sees the file.
-- Analysis columns (sharpness_score, exposure_score, etc.) start
-- as NULL and are filled in by the analysis pipeline.
-- ============================================================
CREATE TABLE IF NOT EXISTS images (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- File identity
    file_path           TEXT    NOT NULL UNIQUE,   -- absolute path to RAW/JPG
    filename            TEXT    NOT NULL,           -- e.g. DSC_0042.RAF
    uuid                TEXT    UNIQUE,             -- stable per-photo identifier (assigned on ingest)

    -- Camera metadata (from EXIF)
    camera              TEXT,                       -- 'X100VI', 'Z6III', 'X Half'
    format              TEXT,                       -- 'RAF', 'NEF', 'JPG'
    shot_at             DATETIME,                   -- EXIF DateTimeOriginal
    focal_length_mm     REAL,
    aperture            REAL,                       -- f-number, e.g. 2.8
    shutter_speed       REAL,                       -- in seconds, e.g. 0.0333 = 1/30s
    iso                 INTEGER,

    -- Generated preview
    preview_path        TEXT,                       -- path to generated JPEG thumbnail

    -- Phase 1 analysis scores (0–100, NULL until analyzed)
    sharpness_score     REAL,
    exposure_score      REAL,
    overall_score       REAL,                       -- weighted: sharpness 65% + exposure 35%
    shake_detected      INTEGER DEFAULT 0,          -- 1 = likely camera shake (SQLite has no BOOL)

    -- Pipeline state
    analysis_status     TEXT    NOT NULL DEFAULT 'pending',  -- 'pending' | 'done' | 'error'
    imported_at         DATETIME NOT NULL DEFAULT (DATETIME('now'))
);

-- ============================================================
-- bursts
-- One row per burst group (shots taken <2 seconds apart).
-- hero_image_id points to the best shot in the group and CAN be
-- changed by the user — that's why it lives here, not in images.
-- ============================================================
CREATE TABLE IF NOT EXISTS bursts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hero_image_id   INTEGER REFERENCES images(id) ON DELETE SET NULL,
    image_count     INTEGER NOT NULL DEFAULT 0,
    detected_at     DATETIME NOT NULL DEFAULT (DATETIME('now'))
);

-- ============================================================
-- burst_members
-- Join table: each row says "this image belongs to this burst".
-- An image can only belong to one burst (enforced by UNIQUE on image_id).
-- ============================================================
CREATE TABLE IF NOT EXISTS burst_members (
    burst_id    INTEGER NOT NULL REFERENCES bursts(id)  ON DELETE CASCADE,
    image_id    INTEGER NOT NULL REFERENCES images(id)  ON DELETE CASCADE,
    PRIMARY KEY (burst_id, image_id)
);

-- One image can't be in two bursts
CREATE UNIQUE INDEX IF NOT EXISTS uq_burst_members_image
    ON burst_members(image_id);

-- ============================================================
-- decisions
-- Stores the user's keep / reject / maybe choice per image.
-- One row per image (UNIQUE on image_id). Use INSERT OR REPLACE
-- to update an existing decision without creating duplicates.
-- Separate from images so decisions survive analysis re-runs.
-- ============================================================
CREATE TABLE IF NOT EXISTS decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id          INTEGER NOT NULL UNIQUE REFERENCES images(id) ON DELETE CASCADE,
    decision          TEXT    NOT NULL CHECK (decision IN ('keep', 'reject', 'maybe')),
    decided_at        DATETIME NOT NULL DEFAULT (DATETIME('now'))
);

-- ============================================================
-- settings
-- Key/value store for user-configurable app preferences.
-- Holds scoring weights and decision thresholds (sharpness_weight,
-- keep_threshold, etc.). Folder destination overrides used to live
-- here too but moved to `folder_settings` (per-source_folder) on
-- 2026-05-04.
-- ============================================================
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- ============================================================
-- training_samples
-- Durable corpus for the Phase 3 personal scoring model.
-- One row per decision, captured the moment the user pressed K/M/X.
-- INTENTIONALLY has no foreign key to images — survives Clear Analysis,
-- folder moves, and even the photo file being deleted from disk. The
-- model can always be retrained from this table alone.
--
-- features_json freezes the 17-dim feature vector at decision time so
-- re-analysis later (with possibly different scorers) never invalidates
-- past labels. schema_version records which feature schema produced the
-- vector, so old rows can be padded with NaN when the schema grows.
-- sample_uuid lets re-decisions on the same photo replace prior labels
-- ("taste evolution wins").
-- ============================================================
CREATE TABLE IF NOT EXISTS training_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_uuid     TEXT    UNIQUE,
    decided_at      DATETIME NOT NULL DEFAULT (DATETIME('now')),
    decision        TEXT    NOT NULL CHECK (decision IN ('keep', 'maybe', 'reject')),
    features_json   TEXT    NOT NULL,
    overall_score   REAL,
    schema_version  INTEGER NOT NULL
);

-- ============================================================
-- folder_settings
-- Per-source_folder overrides for K/M/X destination paths.
-- Missing row = use default subfolder (_Keeps / _Maybes / _Trash)
-- inside the source_folder. Keyed by (source_folder, decision) so
-- each analysis carries its own routing without affecting others.
-- ============================================================
CREATE TABLE IF NOT EXISTS folder_settings (
    source_folder  TEXT NOT NULL,
    decision       TEXT NOT NULL CHECK (decision IN ('keep', 'maybe', 'reject')),
    dest_folder    TEXT NOT NULL,
    PRIMARY KEY (source_folder, decision)
);

"""
