"""
Ollama narrative explanation layer.

Calls the locally-running Ollama daemon (OpenAI-compatible API at
http://localhost:11434/v1) to generate a 2–3 sentence human-readable
explanation for why a photo scored the way it did. Vision models receive the
preview JPEG directly; text-only models get structured scores as a prose
prompt.

Migrated from LM Studio (2026-05-06): Ollama is daemon-based with no GUI to
start, has a simpler install (brew install ollama), and is easier for less
tech-savvy users. The OpenAI SDK calls are unchanged — only the base URL.

Degrades gracefully: every public function returns None / False on any
failure so callers never need to guard against exceptions from this module.
"""

import base64
import logging
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Subprocess handle set only when *we* spawned ollama — never when the user
# started it independently. Checked by shutdown_daemon() to avoid killing a
# daemon the user wants to keep running.
_ollama_proc: subprocess.Popen | None = None

_OLLAMA_BASE   = "http://localhost:11434/v1"
_OLLAMA_NATIVE = "http://localhost:11434"   # native API (used for /api/tags)
_OLLAMA_KEY    = "ollama"                    # Ollama ignores the key but openai client requires one
_TIMEOUT       = 180                          # seconds — vision models on Apple-silicon MPS/CPU take 30–90 s for a 4–12 image burst rank after preview downscaling; first call after runner load adds ~20 s for weight init. 180 s gives headroom without making a user wait too long for a definite-failure case.
_MAX_TOKENS    = 160

# Vision-capable Ollama model name prefixes. When picking a default model the
# explainer prefers these so vision input actually informs the response.
_VISION_PREFIXES = ("qwen2.5vl", "llava", "bakllava", "llama3.2-vision", "minicpm-v", "qwen2-vision")


def _is_daemon_reachable() -> bool:
    """Quick TCP probe — much faster than an HTTP request when nothing is listening."""
    try:
        with socket.create_connection(("localhost", 11434), timeout=0.5):
            return True
    except OSError:
        return False


# Standard install locations for the Ollama CLI. shutil.which() only checks
# the inherited PATH, which on macOS is minimal when the app is launched from
# Finder/Dock (Homebrew's /opt/homebrew/bin is missing). Falling back to
# these known paths means the app works regardless of how it was launched.
_OLLAMA_FALLBACK_PATHS = (
    "/opt/homebrew/bin/ollama",   # Apple-silicon Homebrew
    "/usr/local/bin/ollama",      # Intel Homebrew + manual installs
)


def _resolve_ollama_path() -> str | None:
    """Return an absolute path to the `ollama` CLI, or None if not found.

    Tries `shutil.which` first (covers user-customised PATHs), then falls
    back to the well-known Homebrew install locations so GUI-launched
    processes — which inherit a minimal PATH on macOS — still find it.
    """
    found = shutil.which("ollama")
    if found:
        return found
    for path in _OLLAMA_FALLBACK_PATHS:
        if Path(path).exists():
            return path
    return None


def _ollama_installed() -> bool:
    """True if the `ollama` CLI is available (PATH or a known fallback location)."""
    return _resolve_ollama_path() is not None


def list_models() -> list[str]:
    """Return all locally-pulled Ollama model names. Empty list if daemon is down."""
    if not _is_daemon_reachable():
        return []
    try:
        with httpx.Client(timeout=2) as client:
            r = client.get(f"{_OLLAMA_NATIVE}/api/tags")
            if r.status_code != 200:
                return []
            data = r.json()
            return [m["name"] for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def _pick_model() -> str | None:
    """Pick the best installed model for explanations. Vision-capable wins; falls
    back to first available text model. None when no models are pulled."""
    models = list_models()
    if not models:
        return None
    for m in models:
        name = m.lower()
        if any(name.startswith(p) for p in _VISION_PREFIXES):
            return m
    return models[0]


def _is_vision_capable(model_name: str | None) -> bool:
    """True iff `model_name` starts with one of the known vision-model prefixes.
    Centralised here so callers don't reinvent the check."""
    if not model_name:
        return False
    lower = model_name.lower()
    return any(lower.startswith(p) for p in _VISION_PREFIXES)


def get_status() -> dict:
    """
    Rich status for the frontend so the Explanation UI can show context-aware
    guidance instead of a generic "unavailable" string.

    Possible status values:
        not_installed   — `ollama` CLI not on PATH
        not_running     — daemon not reachable (user needs to `ollama serve`)
        no_models       — daemon up but no models pulled
        ready           — at least one model installed; `model` field set.
                          `vision_capable` flags whether that picked model can
                          see the photo (vs. text-only fallback like phi3).

    The `ready` state still returns `status: "ready"` even when only text-only
    models are installed — they CAN generate explanations, just from the
    numeric scores rather than the image. The frontend uses `vision_capable`
    to decide whether to nudge the user toward pulling a vision model.
    """
    if not _ollama_installed():
        return {
            "status": "not_installed",
            "model": None,
            "models": [],
            "vision_capable": False,
            "install_url": "https://ollama.com",
            "install_hint": "Install Ollama from ollama.com (or `brew install ollama`)",
        }
    if not _is_daemon_reachable():
        return {
            "status": "not_running",
            "model": None,
            "models": [],
            "vision_capable": False,
            "start_hint": "Run `ollama serve` in a Terminal, then click Try again",
        }
    models = list_models()
    if not models:
        return {
            "status": "no_models",
            "model": None,
            "models": [],
            "vision_capable": False,
            "pull_hint": "Run `ollama pull qwen2.5vl:7b` to enable vision-aware explanations (≈6 GB)",
        }
    picked = _pick_model()
    vision_capable = _is_vision_capable(picked)
    return {
        "status": "ready",
        "model": picked,
        "models": models,
        "vision_capable": vision_capable,
        # When only text-only models are installed, suggest the upgrade path
        # so the UI can surface the pull button alongside the connected state.
        "pull_hint": (
            None if vision_capable
            else "Pull qwen2.5vl:7b to enable vision-aware ranking and explanations (≈6 GB)"
        ),
    }


def is_ollama_available() -> bool:
    """True when the daemon is up AND at least one model is pulled."""
    return get_status().get("status") == "ready"


def ensure_daemon_running() -> None:
    """Start `ollama serve` if Ollama is installed but the daemon isn't running.

    Only spawns a child process when the daemon isn't already reachable — so
    users who run `brew services start ollama` or start it manually won't get
    a second instance. The handle is stored in _ollama_proc so shutdown_daemon()
    can terminate only the process we own.
    """
    global _ollama_proc
    ollama_bin = _resolve_ollama_path()
    if ollama_bin is None:
        return
    if _is_daemon_reachable():
        return  # already running — not our process to manage

    logger.info("Ollama not running — starting daemon automatically (%s)", ollama_bin)
    try:
        # Use the resolved absolute path rather than the bare command so this
        # works even when the parent process has a minimal PATH (e.g. when
        # the app is launched from Finder on macOS).
        _ollama_proc = subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait up to 5 s for the daemon to accept connections
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _is_daemon_reachable():
                logger.info("Ollama daemon ready (pid %d)", _ollama_proc.pid)
                return
            time.sleep(0.2)
        logger.warning("Ollama started (pid %d) but didn't respond within 5 s", _ollama_proc.pid)
    except Exception:
        logger.exception("Failed to start Ollama daemon")
        _ollama_proc = None


def shutdown_daemon() -> None:
    """Terminate the Ollama daemon if *we* started it."""
    global _ollama_proc
    if _ollama_proc is None:
        return
    proc = _ollama_proc
    _ollama_proc = None
    if proc.poll() is None:
        logger.info("Stopping Ollama daemon (pid %d)", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _recover_wedged_runner(model_id: str) -> bool:
    """Clear a wedged Ollama model runner so the next chat call gets a fresh one.

    Failure mode this fixes: the daemon stays reachable (`/api/tags` returns 200
    instantly) but its model *runner* subprocess hangs — `ollama ps` shows it
    stuck in "Stopping…" — so every `/api/chat` blocks until the full timeout.
    Seen after a hardware/OS change (model pulled on an old machine, Metal
    runner wedges on first load on the new one).

    The fix is an unload request (`keep_alive: 0`): the daemon drops the stuck
    runner and spawns a clean one on the next request. This is a soft recovery
    — it never kills the daemon (which the app owns; see ensure_daemon_running),
    only the per-model runner. Returns True if the unload call was accepted.
    """
    if not _is_daemon_reachable():
        return False
    try:
        # Short timeout: a healthy unload returns near-instantly. If THIS hangs
        # too, the daemon itself is wedged (not just the runner) and a retry
        # wouldn't help anyway — let it fail and fall through to graceful None.
        r = httpx.post(
            f"{_OLLAMA_NATIVE}/api/generate",
            json={"model": model_id, "keep_alive": 0},
            timeout=15,
        )
        ok = r.status_code == 200
        if ok:
            logger.info("Recovered wedged Ollama runner for %s (unloaded)", model_id)
        return ok
    except Exception:
        logger.warning("Could not unload wedged Ollama runner for %s", model_id, exc_info=True)
        return False


def get_loaded_model() -> str | None:
    """Return the chosen Ollama model name (vision-preferred), or None if not ready."""
    s = get_status()
    return s.get("model") if s.get("status") == "ready" else None


# Default vision model we suggest everywhere. ~6 GB, native multi-image
# attention and trained for structured JSON output (the burst-rank prompt
# requires this — moondream and llava:13b both fail it). If you change the
# default name, also update the copy in SettingsModal / DetailView /
# GroupLoupe / the pull button / the FAQ entry.
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"

# Approximate download sizes (MB) for the pull-progress copy. Used purely
# for UI formatting ("Downloading ~6 GB"), not for any real percentage —
# Ollama's /api/pull stream doesn't expose total size. Fallback 4000 MB
# is a sane middle-ground for an arbitrary unknown model.
_PULL_SIZE_MB = {
    "qwen2.5vl": 6000,
    "llava": 4700,
    "bakllava": 4700,
    "minicpm-v": 5500,
}


def _estimate_size_mb(name: str) -> int:
    """Pick a display size for a model name, stripping any `:tag` suffix."""
    base = name.split(":", 1)[0].lower()
    return _PULL_SIZE_MB.get(base, 4000)


def delete_model(name: str) -> dict:
    """Delete a locally-installed Ollama model.

    Thin wrapper around the native `DELETE /api/delete` endpoint. Sync —
    deletion is fast (just removes manifest + GC's blobs), so we don't
    bother with the model_status thread plumbing the pull flow uses.

    Returns:
        {"status": "ok" | "not_installed" | "not_running" | "not_found" | "error",
         "model":  the name we attempted to delete,
         "detail": optional human-readable string on failure}
    """
    import httpx

    if not _ollama_installed():
        return {"status": "not_installed", "model": name,
                "detail": "Ollama isn't installed."}
    if not _is_daemon_reachable():
        return {"status": "not_running", "model": name,
                "detail": "Ollama daemon is not running."}

    try:
        with httpx.Client(timeout=10) as client:
            r = client.request(
                "DELETE", f"{_OLLAMA_NATIVE}/api/delete",
                json={"name": name},
            )
            if r.status_code == 404:
                return {"status": "not_found", "model": name,
                        "detail": f"Model {name!r} is not installed."}
            if r.status_code != 200:
                return {"status": "error", "model": name,
                        "detail": f"Ollama returned HTTP {r.status_code}"}
        return {"status": "ok", "model": name}
    except Exception as exc:
        logger.exception("ollama delete failed for %s", name)
        return {"status": "error", "model": name, "detail": str(exc)}


def pull_model(name: str = DEFAULT_VISION_MODEL) -> dict:
    """Pull an Ollama model from the registry, reporting progress through
    the shared `model_status` registry the rest of the app already polls.

    Mirrors how SigLIP / TOPIQ / LAION / FaceNet handle their first-time
    weight downloads — register a `begin(... downloading=True)` entry while
    bytes are in flight, `end()` when done. The /model-status endpoint
    already streams this to the frontend every second, so no new polling
    machinery is needed.

    Blocking call. Run from a background thread for non-blocking UX.

    Returns:
        {"status": "ok" | "not_running" | "not_installed" | "error",
         "model":  the name we attempted to pull,
         "detail": optional human-readable string on failure}
    """
    import httpx
    from phase2_quality.model_status import (
        begin as _begin,
        end as _end,
        update_progress as _update_progress,
    )

    if not _ollama_installed():
        return {"status": "not_installed", "model": name,
                "detail": "Ollama CLI is not on PATH. Install from ollama.com first."}
    if not _is_daemon_reachable():
        return {"status": "not_running", "model": name,
                "detail": "Ollama daemon is not running. Try restarting the app."}

    # Register in the shared status table so /model-status surfaces the
    # download to the frontend the same way SigLIP/TOPIQ etc. do. The UI
    # only uses this number to format "Downloading ~N GB" copy, not to draw
    # a real percentage. See _estimate_size_mb for the per-model lookup.
    model_id = f"ollama:{name}"
    _begin(model_id, f"Ollama: {name}", _estimate_size_mb(name), downloading=True)
    try:
        # POST /api/pull streams newline-delimited JSON until the model is
        # fully pulled. We consume the stream so the call blocks until the
        # daemon reports completion (or an error). The JSON content itself
        # isn't surfaced — model_status only tracks the binary "in progress
        # vs done" state — but consuming the stream is what keeps the HTTP
        # connection alive while the daemon does the work.
        with httpx.stream(
            "POST",
            f"{_OLLAMA_NATIVE}/api/pull",
            json={"name": name, "stream": True},
            timeout=None,  # downloads can take many minutes on slow links
        ) as r:
            if r.status_code != 200:
                return {"status": "error", "model": name,
                        "detail": f"Ollama returned HTTP {r.status_code}"}
            last_status: str | None = None
            # Throttle the model_status writes to ~4/s so the lock contention
            # is negligible — Ollama emits a status line every few hundred
            # bytes during downloads.
            _last_progress_at = 0.0
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    import json as _json
                    payload = _json.loads(line)
                except Exception:
                    continue
                last_status = payload.get("status") or last_status
                # Surface byte-level progress when Ollama provides it (the
                # "pulling <digest>" lines include completed + total). The
                # final "verifying"/"writing"/"success" lines have neither,
                # which is correct — UI can show "Finalizing…" until end().
                completed = payload.get("completed")
                total     = payload.get("total")
                if isinstance(completed, (int, float)):
                    now = time.monotonic()
                    if now - _last_progress_at >= 0.25:
                        _last_progress_at = now
                        _update_progress(
                            model_id,
                            current_mb=completed / (1024 * 1024),
                            total_mb=(total / (1024 * 1024)) if isinstance(total, (int, float)) else None,
                        )
                # If Ollama explicitly reports an error mid-stream, abort.
                if payload.get("error"):
                    return {"status": "error", "model": name,
                            "detail": str(payload.get("error"))}
            # Success when the last status line says "success".
            if last_status and "success" in last_status.lower():
                return {"status": "ok", "model": name}
            return {"status": "error", "model": name,
                    "detail": f"Pull ended without success (last status: {last_status!r})"}
    except Exception as exc:
        logger.exception("ollama pull failed for %s", name)
        return {"status": "error", "model": name, "detail": str(exc)}
    finally:
        _end(model_id)


def generate_explanation(image_data: dict[str, Any], preview_path: str | None = None) -> str | None:
    """
    Generate a 2–3 sentence narrative explanation for a photo's quality rating.

    image_data should include the keys produced by analyze_photo_quality() plus
    EXIF fields (camera, focal_length_mm, aperture, shutter_speed, iso).

    If preview_path points to an existing JPEG, it is base64-encoded and sent as
    a vision message so the model can also comment on composition and mood.
    Returns None on any failure (Ollama down, timeout, model error).
    """
    # Native /api/chat (not the OpenAI shim) — the shim silently drops
    # options.num_ctx, leaving qwen2.5vl at its 4K default. One 150 KB
    # preview blows past that and Ollama thrashes prompt-processing for
    # minutes. Same fix as burst_ranker.py:477-519.
    try:
        model_id = get_loaded_model()
        if not model_id:
            return None

        prompt_text = _build_prompt(image_data)

        user_msg: dict[str, Any] = {"role": "user", "content": prompt_text}
        if preview_path and Path(preview_path).exists():
            jpeg_b64 = base64.b64encode(Path(preview_path).read_bytes()).decode()
            user_msg["images"] = [jpeg_b64]

        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a photography curator reviewing shots for a professional photographer. "
                        "Given technical analysis scores and optionally the image itself, write exactly "
                        "2–3 sentences explaining the quality judgment. Be specific — reference the actual "
                        "numbers and what they mean for real-world usability of the shot. "
                        "Do not use bullet points or headers. Plain prose only."
                    ),
                },
                user_msg,
            ],
            "stream": False,
            "options": {
                "num_ctx": 8192,
                "temperature": 0.4,
                "num_predict": _MAX_TOKENS,
            },
        }

        def _chat() -> str | None:
            r = httpx.post(f"{_OLLAMA_NATIVE}/api/chat", json=payload, timeout=_TIMEOUT)
            if r.status_code != 200:
                logger.warning("Ollama /api/chat returned HTTP %d: %s", r.status_code, r.text[:300])
                return None
            text = (r.json().get("message") or {}).get("content")
            return text.strip() if text else None

        try:
            return _chat()
        except httpx.ReadTimeout:
            # A timeout (not an HTTP error) is the signature of a wedged runner:
            # the daemon answered the connect but the model never produced a
            # token. Unload the stuck runner once and retry on a fresh one
            # rather than surfacing "unavailable" the user can't act on.
            logger.warning("Ollama chat timed out — attempting wedged-runner recovery")
            if _recover_wedged_runner(model_id):
                return _chat()  # one clean retry; if it times out too, the outer except returns None
            return None

    except Exception:
        logger.warning("Ollama explanation failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt(val: float | None, decimals: int = 1) -> str:
    return f"{val:.{decimals}f}" if val is not None else "N/A"


def _score_label(val: float | None, thresholds: tuple[float, float] = (40, 70)) -> str:
    if val is None:
        return "N/A"
    lo, hi = thresholds
    if val >= hi:
        return "good"
    if val >= lo:
        return "borderline"
    return "poor"


def _build_prompt(d: dict[str, Any]) -> str:
    overall     = d.get("overall_quality_score") or d.get("overall_score")
    sharpness   = d.get("sharpness_score") or (d.get("sharpness") or {}).get("sharpness_score")
    exposure    = d.get("exposure_score") or (d.get("exposure") or {}).get("exposure_score")
    iqa         = d.get("iqa_score") or (d.get("iqa") or {}).get("iqa_score")
    aesthetic   = d.get("aesthetic_score") or (d.get("aesthetic") or {}).get("aesthetic_score")

    face_detected = d.get("face_detected")
    face_count    = d.get("face_count", 0)
    eyes_open     = d.get("eyes_open")
    face_sharp    = d.get("face_sharpness_score")

    camera      = d.get("camera") or "unknown camera"
    focal       = d.get("focal_length_mm")
    aperture    = d.get("aperture")
    shutter     = d.get("shutter_speed")
    iso         = d.get("iso")

    # Format shutter as fraction for readability
    shutter_str = "N/A"
    if shutter:
        if shutter >= 1:
            shutter_str = f"{shutter:.0f}s"
        else:
            shutter_str = f"1/{round(1/shutter)}s"

    lines = [
        f"Camera: {camera}",
        f"Settings: {_fmt(focal, 0)}mm · f/{_fmt(aperture, 1)} · {shutter_str} · ISO {iso or 'N/A'}",
        f"",
        f"Technical scores (0–100):",
        f"  Overall: {_fmt(overall, 1)} ({_score_label(overall)})",
        f"  Sharpness: {_fmt(sharpness, 1)} ({_score_label(sharpness)})",
        f"  Exposure: {_fmt(exposure, 1)} ({_score_label(exposure)})",
    ]

    if iqa is not None:
        lines.append(f"  Perceptual quality (TOPIQ): {_fmt(iqa, 1)} ({_score_label(iqa, (35, 55))})")
    if aesthetic is not None:
        lines.append(f"  Aesthetic appeal (LAION): {_fmt(aesthetic, 1)} ({_score_label(aesthetic, (30, 50))})")

    if face_detected:
        face_info = f"  {face_count} face(s) detected"
        if eyes_open is not None:
            face_info += f", eyes {'open' if eyes_open else 'CLOSED'}"
        if face_sharp is not None:
            face_info += f", face sharpness {_fmt(face_sharp, 0)}"
        lines.append("")
        lines.append("Faces:")
        lines.append(face_info)
    else:
        lines.append("")
        lines.append("Faces: none detected")

    lines.append("")
    lines.append("Write your 2–3 sentence narrative explanation now:")

    return "\n".join(lines)
