-- ============================================================================
-- Photo Culling App — SQLite schema (snapshot 2026-05-04)
-- Source: backend/database.py
-- Database file: data/pca.db (auto-created on first run)
--
-- This is the schema as the running database has it after all migrations
-- have applied. The migrations themselves are inline ALTER TABLEs in
-- database.py (no Alembic; OperationalError silenced when column already
-- exists).
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- images: one row per photo seen by the app.
-- Filled progressively: source_folder + filename are written first, scores
-- arrive as Phase 1 + Phase 2 finish, decisions live in a separate table.
-- ----------------------------------------------------------------------------
CREATE TABLE images (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identity ---------------------------------------------------------------
    file_path               TEXT NOT NULL UNIQUE,   -- absolute path; updated on /decision file move
    filename                TEXT NOT NULL,          -- e.g. DSC_0042.RAF
    uuid                    TEXT UNIQUE,            -- stable ID written into the .xmp sidecar at ingest
    xmp_sidecar_path        TEXT,                   -- absolute path to the sidecar (DSC_0042.xmp)
    source_folder           TEXT,                   -- parent dir of the file at ingest; NEVER updated by /decision moves

    -- EXIF ------------------------------------------------------------------
    camera                  TEXT,                   -- 'X100VI' | 'X Half' | 'Z6III' | other
    format                  TEXT,                   -- 'RAF' | 'NEF' | 'JPG' | 'PNG' | …
    shot_at                 DATETIME,               -- EXIF DateTimeOriginal
    focal_length_mm         REAL,
    aperture                REAL,                   -- f-number
    shutter_speed           REAL,                   -- seconds (e.g. 0.004 = 1/250s)
    iso                     INTEGER,

    -- Preview cache ---------------------------------------------------------
    preview_path            TEXT,                   -- data/previews/<id>.jpg; null until first GET /previews

    -- Phase 1 scores --------------------------------------------------------
    sharpness_score         REAL,                   -- 0..100  (tile-p90 multi-measure fusion)
    exposure_score          REAL,                   -- 0..100
    overall_score           REAL,                   -- 0..100  = sharpness*w + exposure*(1-w), w=0.65 default
    shake_detected          INTEGER,                -- 0|1; fused EXIF + pixel signals
    highlight_clip_pct      REAL,                   -- 0..100, % of pixels at 255 in any channel
    shadow_clip_pct         REAL,                   -- 0..100, % of pixels at 0

    -- Phase 2 — face signals (NULL when face_detected = 0) ------------------
    face_detected           INTEGER,                -- 0|1
    face_count              INTEGER,
    face_sharpness_score    REAL,                   -- Laplacian variance on the face crop only
    eyes_open               INTEGER,                -- 0|1|NULL — NULL means BlazeFace fallback (no eye data)
    eye_openness_ratio      REAL,                   -- 0..1; 1 = fully open
    face_size_ratio         REAL,                   -- 0..1; bbox area / total image area
    face_center_offset_x    REAL,                   -- -1..1; 0 = horizontally centred
    face_center_offset_y    REAL,                   -- -1..1; 0 = vertically centred

    -- Phase 2 — perceptual / semantic ML scores -----------------------------
    iqa_score               REAL,                   -- 0..100, TOPIQ no-reference IQA (CPU-pinned)
    aesthetic_score         REAL,                   -- 0..100, LAION Aesthetic Predictor V2 linearMSE
    embedding               TEXT,                   -- compact JSON of 768 floats from SigLIP, L2-normed.
                                                    -- Excluded from GET /images (size); used by /similarity-groups.

    -- Phase 2 — narrative explanation (opt-in, on-demand) -------------------
    explanation             TEXT,                   -- 2-3 sentence prose from LM Studio. NULL until generated.

    -- Bookkeeping -----------------------------------------------------------
    analysis_status         TEXT DEFAULT 'pending', -- 'pending' | 'done' | 'error'
    imported_at             DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_images_source_folder ON images(source_folder);
CREATE INDEX idx_images_status ON images(analysis_status);

-- ----------------------------------------------------------------------------
-- decisions: one row per K/M/R action. Cascade-deleted with the image.
-- A row in this table is the persistence proof of "the file is at its
-- decision destination on disk" — the file move happens before INSERT.
-- ----------------------------------------------------------------------------
CREATE TABLE decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id    INTEGER NOT NULL UNIQUE
                REFERENCES images(id) ON DELETE CASCADE,
    decision    TEXT NOT NULL CHECK (decision IN ('keep', 'reject', 'maybe')),
    decided_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- bursts + burst_members: legacy timestamp-clustered burst groups.
-- Inherited from the fastdup era. NEW analyses do not write rows here;
-- similarity grouping moved to SigLIP cosine clusters computed live in
-- /similarity-groups. Kept for old data + the /group-hero endpoint.
-- ----------------------------------------------------------------------------
CREATE TABLE bursts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hero_image_id   INTEGER REFERENCES images(id) ON DELETE SET NULL,
    image_count     INTEGER,
    detected_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE burst_members (
    burst_id    INTEGER REFERENCES bursts(id) ON DELETE CASCADE,
    image_id    INTEGER REFERENCES images(id) ON DELETE CASCADE,
    PRIMARY KEY (burst_id, image_id),
    UNIQUE (image_id)                       -- each image in at most one burst
);

-- ----------------------------------------------------------------------------
-- settings: key-value store for global thresholds + toggles.
-- Numeric & boolean keys are validated by registries in backend/main.py:
--   _NUMERIC_SETTINGS (9 keys) and _BOOL_SETTINGS (4 keys).
-- All thresholds are read fresh on every _compute_auto_decision call —
-- there is intentionally no in-memory caching for these.
-- ----------------------------------------------------------------------------
CREATE TABLE settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- Known keys (defaults applied at read-time when row missing):
--   sharpness_weight             0..1, default 0.65
--   keep_threshold               0..100, default 70   (personal-model mode)
--   maybe_threshold              0..100, default 45   (personal-model mode; must be < keep_threshold)
--   fallback_keep                0..100, default 60   (threshold mode)
--   fallback_maybe               0..100, default 40   (threshold mode; must be < fallback_keep)
--   fallback_sharpness_floor     0..100, default 40   (below this → reject if reject_blurry_frame)
--   face_sharpness_floor         0..100, default 20   (face crop below this → reject if reject_soft_face)
--   reject_soft_face             0|1,    default 0
--   reject_blurry_frame          0|1,    default 0
--   reject_closed_eyes           0|1,    default 0
--   prefer_sidecar_preview       0|1,    default 0    (Settings → Display)
--   group_hero:<signature>       int     (manual hero overrides for similarity groups)

-- ----------------------------------------------------------------------------
-- folder_settings: per-source-folder K/M/X destination overrides
-- (added 2026-05-04, replaced the global keeps_folder/maybes_folder/trash_folder
-- rows in `settings`). One-shot migration lifted legacy globals into rows here.
-- A missing row means: use the default subfolder of source_folder
-- ({source}/_Keeps, {source}/_Maybes, {source}/_Trash).
-- ----------------------------------------------------------------------------
CREATE TABLE folder_settings (
    source_folder   TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('keep', 'maybe', 'reject')),
    dest_folder     TEXT NOT NULL,
    PRIMARY KEY (source_folder, decision)
);

-- ============================================================================
-- Notes for a peer reviewer
-- ============================================================================
-- 1. There is no `personal_score` column. Personal score is computed at
--    request-time in phase3_learning/personal_model.py and cached in RAM
--    only. Cache invalidation is keyed on image_id and cleared wholesale on
--    /train-model.
--
-- 2. `embedding` is stored as JSON TEXT (compact `[0.0123, ...]`). Each row
--    is ~5 KB. Total for 5000 photos: ~25 MB. Reasonable for SQLite, but
--    cosine-similarity computation requires materialising the entire matrix
--    in Python — N^2 memory growth.
--
-- 3. `images.source_folder` is captured at ingest and never updated. After
--    a /decision moves the RAW into a _Keeps/ subfolder, file_path updates
--    but source_folder still points to the original parent. This is the
--    contract that makes per-folder workspace state work.
--
-- 4. Migrations live in backend/database.py as inline `try: ALTER TABLE …
--    except sqlite3.OperationalError: pass` blocks. Migration order matters
--    on a fresh install but no migration history is recorded.
-- ============================================================================
