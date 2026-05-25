"""
System / utility / debug endpoints: health, Ollama status, explanation,
and debug helpers.
"""

import logging
import os
import signal
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database import get_db
from backend.state import _last_errors, watcher
from phase2_quality.llm_explainer import (
    generate_explanation as _generate_explanation,
    get_loaded_model,
    get_status as _get_ollama_status,
)

logger = logging.getLogger(__name__)

# Path to the rotating log file — used by /debug/log-path.
_LOG_PATH = Path(__file__).parent.parent.parent / "data" / "app.log"

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GenerateExplanationRequest(BaseModel):
    image_id: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
def health():
    """
    Quick liveness + database check.

    Returns 200 if the server is running and can query SQLite.
    Returns 500 if the DB is unreachable.
    """
    try:
        with get_db() as conn:
            image_count = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        return {
            "status": "ok",
            "db": "connected",
            "images_in_db": image_count,
            "watching": watcher.watching,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")


@router.get("/lm-status")
def lm_status():
    """
    Return rich Ollama status so the frontend can show context-aware guidance.

    Response shape:
      {
        "available": bool,            # True when status == "ready"
        "model":     str | None,      # auto-picked vision-preferred model
        "status":    "not_installed" | "not_running" | "no_models" | "ready",
        "models":    [str, ...],      # all installed Ollama model names
        # Optional human hints for whichever non-ready state we're in:
        "install_hint": str?,
        "start_hint":   str?,
        "pull_hint":    str?,
      }

    Endpoint name kept as `/lm-status` for frontend compatibility — it now
    reflects Ollama state instead of LM Studio.
    """
    s = _get_ollama_status()
    return {"available": s.get("status") == "ready", **s}


@router.post("/generate-explanation")
def generate_explanation_endpoint(request: GenerateExplanationRequest):
    """
    On-demand narrative explanation for a photo.

    Fetches the stored scores from DB, calls Ollama, persists the result.
    Returns 404 if the image doesn't exist.
    Returns {"explanation": null} if Ollama is not running or generation fails.
    """
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, file_path, filename, camera, focal_length_mm, aperture,
                   shutter_speed, iso, sharpness_score, exposure_score, overall_score,
                   iqa_score, aesthetic_score, face_detected, face_count,
                   eyes_open, face_sharpness_score, preview_path
            FROM images WHERE id = ?
            """,
            (request.image_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Image {request.image_id} not found")

    image_data = dict(row)
    # Map overall_score → overall_quality_score so _build_prompt finds it
    image_data["overall_quality_score"] = image_data.get("overall_score")

    file_path    = image_data.get("file_path", "")
    preview_path = image_data.pop("preview_path", None)

    # JPEG/PNG previews are served on-the-fly without being saved to disk, so
    # preview_path stays NULL. Fall back to the original file so the vision
    # model still gets an image instead of a text-only prompt.
    if not preview_path and file_path:
        fp = Path(file_path)
        if fp.suffix.upper() in {".JPG", ".JPEG", ".PNG", ".HIF", ".HEIF", ".HEIC"}:
            if fp.exists():
                preview_path = str(fp)

    explanation = _generate_explanation(image_data, preview_path=preview_path)

    if explanation:
        with get_db() as conn:
            conn.execute(
                "UPDATE images SET explanation = ? WHERE id = ?",
                (explanation, request.image_id),
            )

    return {"image_id": request.image_id, "explanation": explanation}


@router.delete("/explanation/{image_id}")
def clear_explanation(image_id: int):
    """
    Clear the stored narrative explanation for one image (sets it to NULL).
    Idempotent — succeeds whether or not an explanation existed.
    Returns 404 only when the image row itself doesn't exist.
    """
    with get_db() as conn:
        row = conn.execute("SELECT id FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Image {image_id} not found")
        conn.execute("UPDATE images SET explanation = NULL WHERE id = ?", (image_id,))
    return {"image_id": image_id, "explanation": None}


@router.get("/debug/last-errors")
def debug_last_errors():
    """Return the last 50 per-file errors from batch analysis runs."""
    return {"errors": _last_errors}


@router.get("/debug/log-path")
def debug_log_path():
    """Return the absolute path to the rotating log file."""
    return {"path": str(_LOG_PATH)}


@router.post("/quit")
def quit_server():
    """Gracefully shut down the uvicorn process after a short delay."""
    def _shutdown():
        import time
        time.sleep(0.3)  # let the HTTP response return first
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_shutdown, daemon=True).start()
    return {"status": "shutting_down"}
