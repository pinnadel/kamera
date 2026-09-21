"""
Dashboard endpoints — lifecycle is detached from analysis tabs.

All queries source from durable tables (training_samples, shooting_log,
personal_model.pkl) that survive Clear Analysis and folder moves. Tabs come
and go; the dashboard accumulates forever.

Six endpoints:
  GET /dashboard/model-card                 — delegates to /model-info
  GET /dashboard/decisions/timeline         — K/M/X buckets per week/month
  GET /dashboard/decisions/feature-deltas   — kept-vs-rejected medians for the 17 features
  GET /dashboard/shooting/cameras           — count + last_shot per camera
  GET /dashboard/shooting/distributions     — focal length, aperture, ISO, film sim, lens histograms
  GET /dashboard/shooting/timeline          — shots per week/month

Two filter dimensions, and they do NOT cover the same endpoints:

  `corpus`  ('folder' | 'photos') applies to the three model/decision
            endpoints only. Since the 2026-09-19 corpus split each corpus
            trains its own model on its own slice of training_samples;
            pooling them here would average the two incompatible
            distributions the split exists to keep apart.

  `since`   applies to every endpoint EXCEPT the model's own facts.
            training_size / trained_at / accuracy describe one pickle that
            was fit on everything — there is no 30-day model to report.
            model-card therefore answers `since` with `window_sample_count`
            (how many decisions landed inside the window) and leaves the
            model facts all-time, so the frontend can label them honestly
            instead of implying the number moved.

The shooting endpoints are deliberately corpus-agnostic: shooting_log has no
source_type column, and a phone frame is still a frame you shot. The frontend
labels that section "all sources" rather than letting the corpus switch
silently appear to filter it.
"""

import logging
from datetime import datetime, timedelta

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from backend.database import get_db
from backend.routers.model import personal_model_info
from phase3_learning.feature_extractor import feature_names, features_from_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard")


# Bucket modes for timeline endpoints. SQLite strftime accepts %Y-%W (ISO week)
# and %Y-%m (month). Reject other values defensively.
_BUCKETS = {"week": "%Y-W%W", "month": "%Y-%m"}

# How far to step to reach the next bucket. Weeks are exactly 7 days; months
# are walked by clamping to the 1st and adding 32 days, which lands inside the
# following month for every month length.
_BUCKET_STEP = {"week": "week", "month": "month"}


def _bucket_sequence(first: str, last: str, bucket: str) -> list[str]:
    """Every bucket label from `first` to `last` inclusive, none skipped.

    Both ends are ISO datetimes out of the table. Python's %W matches SQLite's
    exactly (verified against 400 real rows and both year boundaries), so the
    labels generated here line up with the ones the GROUP BY produced.

    This exists because omitting empty buckets is right for a data feed and
    wrong for a chart. Rendered, the gaps closed up: ten bars spanning W20 to
    W37 drew as ten adjacent columns, so eighteen weeks of history looked like
    ten consecutive ones and a three-week pause was indistinguishable from no
    pause at all. A time axis that skips time is not a time axis.
    """
    fmt = _BUCKETS[bucket]
    try:
        start = datetime.fromisoformat(first)
        end = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return []

    # Step by DAY, not by bucket.
    #
    # Stepping 7 days from an unaligned start visits one date per 7-day
    # window offset from Monday, which is not the same set as "every %W
    # week": a range starting Sat 2025-12-20 stepped weekly goes W50, W51,
    # W00 — silently skipping W52. Monday-aligning fixes that one and breaks
    # another, because %W week 00 is the partial week before the year's first
    # Monday and no Monday ever falls inside it.
    #
    # A day cursor produces every label in the range by construction, with no
    # reasoning about week numbering at all. 365 strftime calls per year of
    # range, once per request — not worth outsmarting.
    labels: set[str] = set()
    cursor = start
    # ~50 years of days, so a corrupt timestamp can never spin here.
    for _ in range(20000):
        if cursor > end:
            break
        labels.add(cursor.strftime(fmt))
        cursor += timedelta(days=1)
    labels.add(end.strftime(fmt))
    return sorted(labels)


def _parse_since(since: str | None) -> str | None:
    """Validate `since` is `YYYY-MM-DD`. Returns the original string on success,
    None when omitted, raises 400 on garbage. The validation is purely a parse
    check — the string is passed straight to SQLite's lexicographic comparison
    against the ISO-8601 `decided_at`/`shot_at` columns."""
    if since is None or since == "":
        return None
    try:
        datetime.fromisoformat(since)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"since must be YYYY-MM-DD, got {since!r}")
    return since


# Corpora that own a model + a training_samples slice. Mirrors
# state._personal_models — an unknown name is rejected rather than silently
# falling back to 'folder', because a dashboard that answers the wrong corpus
# with a straight face is worse than one that errors.
_CORPORA = ("folder", "photos")


def _parse_corpus(corpus: str | None) -> str:
    """Validate the corpus name, defaulting to 'folder'."""
    if corpus is None or corpus == "":
        return "folder"
    if corpus not in _CORPORA:
        raise HTTPException(
            status_code=400,
            detail=f"corpus must be one of {_CORPORA}, got {corpus!r}",
        )
    return corpus


# ---------------------------------------------------------------------------
# Model card
# ---------------------------------------------------------------------------


@router.get("/model-card")
def model_card(corpus: str | None = None, since: str | None = None):
    """/model-info for one corpus, plus the window count the timeframe chips
    are allowed to move.

    Kept under /dashboard/* so the frontend fetches every card through one
    prefix. No payload duplication — we call personal_model_info directly.

    `since` does NOT window the model's own facts (see the module docstring).
    It adds two fields instead:

      window_sample_count — decisions in this corpus inside the window
      window_since        — the window's start, echoed back so the frontend
                            can label the card without recomputing the date
    """
    corpus = _parse_corpus(corpus)
    since = _parse_since(since)
    info = personal_model_info(corpus)

    if since:
        with get_db() as conn:
            window_count = conn.execute(
                "SELECT COUNT(*) FROM training_samples "
                "WHERE corpus = ? AND decided_at >= ?",
                (corpus, since),
            ).fetchone()[0]
    else:
        window_count = info.get("decided_count", 0)

    info["window_sample_count"] = window_count
    info["window_since"] = since
    return info


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@router.get("/decisions/timeline")
def decisions_timeline(
    bucket: str = Query("week"),
    since: str | None = None,
    corpus: str | None = None,
):
    """K/M/X counts per time bucket from training_samples, for one corpus.

    Returns rows ordered chronologically. Empty buckets are omitted (the
    frontend renders that as a gap, which matches honest reality — we
    don't know the user wasn't culling other photos elsewhere).
    """
    fmt = _BUCKETS.get(bucket)
    if fmt is None:
        return {"bucket": bucket, "rows": [], "error": "bucket must be 'week' or 'month'"}
    since = _parse_since(since)
    corpus = _parse_corpus(corpus)

    where = "WHERE corpus = ?" + (" AND decided_at >= ?" if since else "")
    params: tuple = (fmt, corpus, since) if since else (fmt, corpus)

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT strftime(?, decided_at) AS period, decision, COUNT(*) AS n
            FROM training_samples
            {where}
            GROUP BY period, decision
            ORDER BY period
            """,
            params,
        ).fetchall()
        # Same WHERE, minus the leading strftime format argument.
        bounds = conn.execute(
            f"""
            SELECT MIN(decided_at) AS first, MAX(decided_at) AS last
            FROM training_samples
            {where}
            """,
            params[1:],
        ).fetchone()

    pivot: dict[str, dict[str, int]] = {}
    for r in rows:
        period = r["period"]
        if period is None:
            continue
        slot = pivot.setdefault(period, {"keep": 0, "maybe": 0, "reject": 0})
        slot[r["decision"]] = r["n"]

    # Zero-fill the quiet buckets so the axis represents elapsed time rather
    # than "weeks in which something happened". A week with no decisions is a
    # fact about the week, not an absence of data.
    if pivot and bounds and bounds["first"]:
        for label in _bucket_sequence(bounds["first"], bounds["last"], bucket):
            pivot.setdefault(label, {"keep": 0, "maybe": 0, "reject": 0})

    return {
        "bucket": bucket,
        "corpus": corpus,
        "rows": [
            {"period": p, **counts}
            for p, counts in sorted(pivot.items())
        ],
    }


@router.get("/decisions/feature-deltas")
def decisions_feature_deltas(since: str | None = None, corpus: str | None = None):
    """For each feature, the median value among kept photos vs the median
    among rejected photos, within one corpus. SQLite has no median aggregate,
    so we load feature_json blobs and compute in numpy.

    Per-feature rows carry kept_median, rejected_median, n_kept, n_rejected —
    where the two n's count the *non-NaN* values in that column, which is what
    the median was actually taken over.

    The response also carries top-level `n_kept_photos` / `n_rejected_photos`:
    the number of decisions on each side. The frontend's header shows photos,
    not column counts — a feature that is NaN for half the corpus (e.g. face
    metrics on landscapes) would otherwise make the header under-report how
    many photos the user has judged.
    """
    names = feature_names()
    since = _parse_since(since)
    corpus = _parse_corpus(corpus)

    sql = (
        "SELECT decision, features_json FROM training_samples "
        "WHERE decision IN ('keep', 'reject') AND corpus = ?"
    )
    params: tuple = (corpus,)
    if since:
        sql += " AND decided_at >= ?"
        params = (corpus, since)

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return {
            "corpus": corpus,
            "n_kept_photos": 0,
            "n_rejected_photos": 0,
            "features": [
                {"feature": n, "kept_median": None, "rejected_median": None,
                 "n_kept": 0, "n_rejected": 0}
                for n in names
            ],
        }

    kept_vecs:    list[np.ndarray] = []
    rejected_vecs: list[np.ndarray] = []
    for r in rows:
        try:
            vec = features_from_json(r["features_json"])
        except Exception:
            # Malformed JSON shouldn't break the endpoint — skip the row.
            logger.exception("feature-deltas: skipping malformed features_json")
            continue
        if r["decision"] == "keep":
            kept_vecs.append(vec)
        else:
            rejected_vecs.append(vec)

    kept_arr     = np.vstack(kept_vecs)     if kept_vecs     else np.empty((0, len(names)))
    rejected_arr = np.vstack(rejected_vecs) if rejected_vecs else np.empty((0, len(names)))

    out = []
    for i, name in enumerate(names):
        kept_col = kept_arr[:, i]     if kept_arr.size     else kept_arr
        rej_col  = rejected_arr[:, i] if rejected_arr.size else rejected_arr
        kept_clean = kept_col[~np.isnan(kept_col)] if kept_col.size else kept_col
        rej_clean  = rej_col[~np.isnan(rej_col)]   if rej_col.size  else rej_col
        out.append({
            "feature":         name,
            "kept_median":     float(np.median(kept_clean)) if kept_clean.size else None,
            "rejected_median": float(np.median(rej_clean))  if rej_clean.size  else None,
            # Means exist for the 0/1 features, where a median is a majority
            # vote and reads as nonsense: "face present, kept 0.00 vs rejected
            # 1.00" tells you which side won, not by how much. The mean of a
            # binary column IS the share, so the frontend can say "faces
            # appear in 12% of keeps and 68% of rejects" instead.
            "kept_mean":       float(np.mean(kept_clean)) if kept_clean.size else None,
            "rejected_mean":   float(np.mean(rej_clean))  if rej_clean.size  else None,
            "n_kept":          int(kept_clean.size),
            "n_rejected":      int(rej_clean.size),
        })

    return {
        "corpus": corpus,
        "n_kept_photos": len(kept_vecs),
        "n_rejected_photos": len(rejected_vecs),
        "features": out,
    }


# ---------------------------------------------------------------------------
# Shooting behavior — sourced from shooting_log (permanent, decoupled from
# the images table).
#
# Deliberately NOT corpus-filtered: shooting_log has no source_type column,
# and the question these answer ("what do I shoot with?") doesn't split along
# the model's corpus boundary — a phone frame is still a frame you shot. The
# frontend labels the section "all sources" so the corpus switch never looks
# like it filters something it doesn't.
# ---------------------------------------------------------------------------


@router.get("/shooting/cameras")
def shooting_cameras(since: str | None = None):
    """Camera-by-camera shot counts, with most-recent shot for each."""
    since = _parse_since(since)
    extra = " AND shot_at >= ?" if since else ""
    params: tuple = (since,) if since else ()
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT camera, COUNT(*) AS count, MAX(shot_at) AS last_shot_at
            FROM shooting_log
            WHERE camera IS NOT NULL{extra}
            GROUP BY camera
            ORDER BY count DESC
            """,
            params,
        ).fetchall()
    return {"cameras": [dict(r) for r in rows]}


@router.get("/shooting/distributions")
def shooting_distributions(since: str | None = None):
    """Five histograms over the shooting_log corpus.

    Buckets are server-side via CASE so we don't ship raw rows. Buckets are
    chosen for photographic legibility (full-stop apertures, common ISO doublings,
    full-frame focal-length classes).
    """
    since = _parse_since(since)
    # Composed once and embedded inside the inner SELECTs (the bucketing
    # subqueries) so the filter shrinks the rowset before CASE classification.
    inner_where = "WHERE shot_at >= ?" if since else ""
    params: tuple = (since,) if since else ()

    out: dict[str, list[dict]] = {}

    with get_db() as conn:
        # Focal length — full-frame style classes; numbers in mm.
        focal = conn.execute(
            f"""
            SELECT bucket, COUNT(*) AS count FROM (
              SELECT CASE
                WHEN focal_length_mm IS NULL THEN 'Unknown'
                WHEN focal_length_mm < 21  THEN '<21mm'
                WHEN focal_length_mm < 28  THEN '21–27mm'
                WHEN focal_length_mm < 35  THEN '28–34mm'
                WHEN focal_length_mm < 50  THEN '35–49mm'
                WHEN focal_length_mm < 70  THEN '50–69mm'
                WHEN focal_length_mm < 105 THEN '70–104mm'
                WHEN focal_length_mm < 200 THEN '105–199mm'
                ELSE                            '200mm+'
              END AS bucket
              FROM shooting_log
              {inner_where}
            )
            GROUP BY bucket
            ORDER BY MIN(CASE bucket
              WHEN '<21mm'      THEN 0
              WHEN '21–27mm'    THEN 1
              WHEN '28–34mm'    THEN 2
              WHEN '35–49mm'    THEN 3
              WHEN '50–69mm'    THEN 4
              WHEN '70–104mm'   THEN 5
              WHEN '105–199mm'  THEN 6
              WHEN '200mm+'     THEN 7
              ELSE                   8
            END)
            """,
            params,
        ).fetchall()
        out["focal_length"] = [dict(r) for r in focal]

        aperture = conn.execute(
            f"""
            SELECT bucket, COUNT(*) AS count FROM (
              SELECT CASE
                WHEN aperture IS NULL    THEN 'Unknown'
                WHEN aperture < 1.6      THEN 'f/1.4'
                WHEN aperture < 2.2      THEN 'f/2'
                WHEN aperture < 3.2      THEN 'f/2.8'
                WHEN aperture < 4.5      THEN 'f/4'
                WHEN aperture < 6.4      THEN 'f/5.6'
                WHEN aperture < 9        THEN 'f/8'
                WHEN aperture < 13       THEN 'f/11'
                ELSE                          'f/16+'
              END AS bucket
              FROM shooting_log
              {inner_where}
            )
            GROUP BY bucket
            ORDER BY MIN(CASE bucket
              WHEN 'f/1.4'  THEN 0 WHEN 'f/2'    THEN 1 WHEN 'f/2.8'  THEN 2
              WHEN 'f/4'    THEN 3 WHEN 'f/5.6'  THEN 4 WHEN 'f/8'    THEN 5
              WHEN 'f/11'   THEN 6 WHEN 'f/16+'  THEN 7 ELSE              8
            END)
            """,
            params,
        ).fetchall()
        out["aperture"] = [dict(r) for r in aperture]

        iso = conn.execute(
            f"""
            SELECT bucket, COUNT(*) AS count FROM (
              SELECT CASE
                WHEN iso IS NULL  THEN 'Unknown'
                WHEN iso < 200    THEN '100'
                WHEN iso < 400    THEN '200'
                WHEN iso < 800    THEN '400'
                WHEN iso < 1600   THEN '800'
                WHEN iso < 3200   THEN '1600'
                WHEN iso < 6400   THEN '3200'
                WHEN iso < 12800  THEN '6400'
                ELSE                   '12800+'
              END AS bucket
              FROM shooting_log
              {inner_where}
            )
            GROUP BY bucket
            ORDER BY MIN(CASE bucket
              WHEN '100'    THEN 0 WHEN '200'    THEN 1 WHEN '400'    THEN 2
              WHEN '800'    THEN 3 WHEN '1600'   THEN 4 WHEN '3200'   THEN 5
              WHEN '6400'   THEN 6 WHEN '12800+' THEN 7 ELSE               8
            END)
            """,
            params,
        ).fetchall()
        out["iso"] = [dict(r) for r in iso]

        # Film simulation / picture control — Fuji writes "Velvia"/"Acros" etc.,
        # Nikon writes picture-control names like "STANDARD". We surface them
        # together since both answer "what look did I shoot with".
        film = conn.execute(
            f"""
            SELECT COALESCE(film_simulation, 'Unknown') AS bucket, COUNT(*) AS count
            FROM shooting_log
            {inner_where}
            GROUP BY bucket
            ORDER BY count DESC
            """,
            params,
        ).fetchall()
        out["film_simulation"] = [dict(r) for r in film]

        lens = conn.execute(
            f"""
            SELECT COALESCE(lens_model, 'Unknown') AS bucket, COUNT(*) AS count
            FROM shooting_log
            {inner_where}
            GROUP BY bucket
            ORDER BY count DESC
            """,
            params,
        ).fetchall()
        out["lens_model"] = [dict(r) for r in lens]

    return out


@router.get("/shooting/timeline")
def shooting_timeline(bucket: str = Query("week"), since: str | None = None):
    """Shots-per-bucket from shooting_log.shot_at."""
    fmt = _BUCKETS.get(bucket)
    if fmt is None:
        return {"bucket": bucket, "rows": [], "error": "bucket must be 'week' or 'month'"}
    since = _parse_since(since)

    extra = " AND shot_at >= ?" if since else ""
    params: tuple = (fmt, since) if since else (fmt,)

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT strftime(?, shot_at) AS period, COUNT(*) AS count
            FROM shooting_log
            WHERE shot_at IS NOT NULL{extra}
            GROUP BY period
            ORDER BY period
            """,
            params,
        ).fetchall()
        bounds = conn.execute(
            f"""
            SELECT MIN(shot_at) AS first, MAX(shot_at) AS last
            FROM shooting_log
            WHERE shot_at IS NOT NULL{extra}
            """,
            params[1:],
        ).fetchone()

    counts = {r["period"]: r["count"] for r in rows if r["period"]}
    # Zero-fill, same reasoning as the decisions timeline: a week you didn't
    # shoot is a fact about that week. Omitting it makes a three-month gap
    # render as one bar-width of nothing.
    if counts and bounds and bounds["first"]:
        for label in _bucket_sequence(bounds["first"], bounds["last"], bucket):
            counts.setdefault(label, 0)

    return {
        "bucket": bucket,
        "rows": [{"period": p, "count": n} for p, n in sorted(counts.items())],
    }


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


@router.post("/reset")
def reset_dashboard():
    """
    Destructive: clear the shooting history that drives the Dashboard's
    camera, lens, film-sim, focal-length, aperture, ISO, and timeline cards.

    Wipes shooting_log only. training_samples is untouched, so the personal
    model and its decision-timeline / feature-delta cards keep their data.
    Use 'Reset personal taste model' in addition if you also want to wipe
    the model.
    """
    try:
        with get_db() as conn:
            cur = conn.execute("DELETE FROM shooting_log")
            shooting_removed = cur.rowcount
            conn.commit()
        logger.info(
            "Dashboard reset: %d shooting_log rows removed",
            shooting_removed,
        )
        return {
            "status": "reset",
            "shooting_removed": shooting_removed,
        }
    except Exception as exc:
        logger.exception("Dashboard reset failed")
        raise HTTPException(status_code=500, detail=f"Reset failed: {exc}")
