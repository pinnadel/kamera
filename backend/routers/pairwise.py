"""
A/B pairwise comparison endpoints.

POST /pairwise           — record one winner/loser comparison
GET  /pairwise-candidates — return N pairs of image IDs for the A/B training UI

Pairwise data feeds into the personal model at training time: each comparison
is converted to a synthetic training sample with a fractional label (+0.7 for
winner, -0.7 for loser) at half the weight of an explicit K/M/X decision.
This lets the model learn fine-grained relative preferences without forcing
the user to make an absolute keep/reject call on every photo.
"""

import logging
import random

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database import get_db
from phase3_learning.auto_trainer import maybe_train_async
from backend.state import _personal_model

logger = logging.getLogger(__name__)

router = APIRouter()


class PairwiseRequest(BaseModel):
    winner_id:     int
    loser_id:      int
    source_folder: str | None = None


@router.post("/pairwise")
def record_pairwise(request: PairwiseRequest):
    """Record one A/B comparison. Triggers the auto-train daemon."""
    if request.winner_id == request.loser_id:
        raise HTTPException(status_code=400, detail="winner_id and loser_id must differ")

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO pairwise_comparisons (winner_id, loser_id, source_folder)
            VALUES (?, ?, ?)
            """,
            (request.winner_id, request.loser_id, request.source_folder),
        )

    maybe_train_async(_personal_model)
    return {"status": "ok", "winner_id": request.winner_id, "loser_id": request.loser_id}


@router.get("/pairwise-candidates")
def pairwise_candidates(
    source_folder: str | None = None,
    n: int = 30,
):
    """
    Return N pairs of image IDs for the A/B training UI.

    Pair selection strategy:
      1. Load undecided analyzed photos for the folder.
      2. If the personal model is ready, sort by proximity to the K/M
         threshold boundary (most uncertain photos first) — these comparisons
         give the model the highest information gain.
      3. If no scores: random shuffle.
      4. Pair consecutive: (photos[0], photos[1]), (photos[2], photos[3]), …
      5. Already-compared pairs are filtered out so the user sees fresh ones.

    Returns:
        { "pairs": [[a_id, b_id], ...] }
    """
    n = max(1, min(n, 200))

    with get_db() as conn:
        if source_folder:
            rows = conn.execute(
                """
                SELECT i.id, i.overall_score
                FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                  AND i.source_folder = ?
                """,
                (source_folder,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT i.id, i.overall_score
                FROM images i
                LEFT JOIN decisions d ON d.image_id = i.id
                WHERE i.analysis_status = 'done' AND d.decision IS NULL
                """
            ).fetchall()

        # Load already-compared pairs so we don't repeat them
        done_pairs: set[frozenset] = set()
        pairs_rows = conn.execute(
            "SELECT winner_id, loser_id FROM pairwise_comparisons"
        ).fetchall()
        for pr in pairs_rows:
            done_pairs.add(frozenset([pr["winner_id"], pr["loser_id"]]))

    photos = [dict(r) for r in rows]
    if len(photos) < 2:
        return {"pairs": []}

    # Sort by uncertainty (distance to keep/maybe threshold midpoints) so
    # the user compares the most ambiguous pairs first — maximises information
    # gain per comparison. overall_score is used as the uncertainty proxy.
    has_scores = any(p["overall_score"] is not None for p in photos)
    if has_scores:
        keep_t  = 70.0
        maybe_t = 45.0

        def uncertainty(p):
            s = p.get("overall_score") or 50.0
            return min(abs(s - keep_t), abs(s - maybe_t))

        photos.sort(key=uncertainty)
    else:
        random.shuffle(photos)

    # Generate pairs (consecutive in the sorted order) skipping seen pairs
    pairs: list[list[int]] = []
    i = 0
    attempts = 0
    while len(pairs) < n and attempts < len(photos) * 4:
        if i + 1 >= len(photos):
            random.shuffle(photos)
            i = 0
        a_id = photos[i]["id"]
        b_id = photos[i + 1]["id"]
        if frozenset([a_id, b_id]) not in done_pairs:
            pairs.append([a_id, b_id])
            done_pairs.add(frozenset([a_id, b_id]))
        i += 2
        attempts += 1

    return {"pairs": pairs}
