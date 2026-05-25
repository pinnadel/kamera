"""
Semantic search endpoint — powered by SigLIP text + image embeddings.

GET /search?q=<query>&source_folder=<path>&limit=50

Encodes the query as a SigLIP text embedding and ranks all analyzed photos
by cosine similarity with their cached image embeddings. Returns image IDs
in descending relevance order.

Requires:
  - Images must have been analyzed (embedding column populated).
  - SigLIP vision model must be loaded (happens during analysis warmup).
  - SigLIP text model loads on first search call (~1s one-time cost).
"""

import logging

import numpy as np
from fastapi import APIRouter, HTTPException

from backend.database import get_db
from phase2_quality.similarity_scorer import embed_text, json_to_embedding

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/search")
def semantic_search(
    q: str,
    source_folder: str | None = None,
    limit: int = 50,
    threshold: float = 0.20,
):
    """
    Rank photos by semantic similarity to a free-text query.

    Args:
        q:             Natural-language query (e.g. "smiling person outdoors").
        source_folder: Restrict search to one analyzed folder. Omit to search
                       all folders.
        limit:         Max results to return (default 50).
        threshold:     Minimum cosine similarity to include a result (default
                       0.20). For SigLIP L2-normalised embeddings, relevant
                       image-text pairs score ~0.22–0.35; unrelated pairs
                       score ~0.10–0.18. Raise to 0.25+ for stricter results.

    Returns:
        {
            "query":   str,
            "total":   int,        # images with embeddings considered
            "results": [
                {"image_id": int, "score": float},  # sorted best-first
                ...
            ]
        }
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q must not be empty")
    limit = max(1, min(limit, 500))
    threshold = max(0.0, min(threshold, 1.0))

    # Encode query. Text model loads lazily on first call (~1s warmup).
    text_vec = embed_text(q)
    if text_vec is None:
        raise HTTPException(status_code=503, detail="Text embedding failed — check server logs")

    text_arr = np.array(text_vec, dtype=np.float32)  # 768-dim

    # Load all image embeddings for the requested scope.
    with get_db() as conn:
        if source_folder:
            rows = conn.execute(
                "SELECT id, embedding FROM images WHERE embedding IS NOT NULL AND source_folder = ?",
                (source_folder,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, embedding FROM images WHERE embedding IS NOT NULL",
            ).fetchall()

    ids: list[int] = []
    vecs: list[list[float]] = []
    for row in rows:
        emb = json_to_embedding(row["embedding"])
        if emb is None:
            continue
        ids.append(row["id"])
        vecs.append(emb)

    if not vecs:
        return {"query": q, "total": 0, "results": []}

    matrix = np.array(vecs, dtype=np.float32)   # N × 768
    scores = matrix @ text_arr                   # cosine similarity (both L2-normalised)

    ranked_indices = np.argsort(-scores)         # highest similarity first

    results = [
        {"image_id": ids[i], "score": round(float(scores[i]), 4)}
        for i in ranked_indices[:limit]
        if scores[i] >= threshold
    ]

    return {"query": q, "total": len(vecs), "results": results}
