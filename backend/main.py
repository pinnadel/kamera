"""
FastAPI application entry point for KaMeRa.

Start the server:
    uvicorn backend.main:app --reload --port 8000

Then test it:
    curl http://localhost:8000/health
"""

import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Hide the Python interpreter from the macOS dock. The UI lives in a Chrome
# PWA window; the backend should not present a second icon. Must run before
# any AppKit-touching import (torch/MediaPipe pull in CoreFoundation).
if sys.platform == "darwin":
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Logging — must be configured before any module that calls logging.getLogger.
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
_log_path = Path(__file__).parent.parent / "data" / "app.log"
_file_handler = logging.handlers.RotatingFileHandler(
    _log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.getLogger().addHandler(_file_handler)
logger = logging.getLogger("photo_culling")

# ---------------------------------------------------------------------------
# sys.path — make the project root importable (phase1_technical, phase2_quality,
# phase3_learning) regardless of where uvicorn is launched from.
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# HEIF/HIF decoder — register as a Pillow plugin once at import time so that
# PIL.Image.open() handles .heic/.hif transparently everywhere in the process.
# ---------------------------------------------------------------------------

try:
    from pillow_heif import register_heif_opener as _register_heif_opener
    _register_heif_opener()
except Exception:  # pragma: no cover — degrade gracefully if pillow-heif is missing
    pass

# ---------------------------------------------------------------------------
# App-level imports (after logging + sys.path are set)
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.database import create_tables
from backend.state import _personal_model, watcher
from backend.routers.analysis import _backfill_histograms, _warm_models
from phase2_quality.llm_explainer import ensure_daemon_running, shutdown_daemon
from backend.routers import (
    analysis,
    dashboard,
    decisions,
    images,
    model,
    pairwise,
    search,
    settings,
    system,
    watch,
)

# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database, load the personal model, and clean up on shutdown."""
    create_tables()
    _personal_model.load()   # no-op if personal_model.pkl doesn't exist yet
    _warm_models()           # load ML models in background; ready before first Analyze click
    _backfill_histograms()   # fill histogram_json for pre-existing rows; ~50ms/photo, non-blocking
    ensure_daemon_running()  # start ollama serve if installed but not yet running
    yield
    watcher.stop()
    shutdown_daemon()        # stop ollama only if we spawned it


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="KaMeRa API",
    version="0.1.0",
    description="Local-only backend for analysing and culling RAW photos.",
    lifespan=lifespan,
)

# CORS: allow the React dev server (port 5173 for Vite, 3000 for CRA) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(analysis.router)
app.include_router(dashboard.router)
app.include_router(decisions.router)
app.include_router(images.router)
app.include_router(model.router)
app.include_router(pairwise.router)
app.include_router(search.router)
app.include_router(settings.router)
app.include_router(system.router)
app.include_router(watch.router)

# ---------------------------------------------------------------------------
# Static frontend — only active when frontend/dist exists (production mode).
# In dev mode (vite dev server on :5173) this block is skipped entirely.
# ---------------------------------------------------------------------------

_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    # Serve JS/CSS/assets directly.
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    # The HTML shell must never be cached: its <script> tag points at a
    # content-hashed bundle (main-<hash>.js) that changes on every build. If
    # the browser (or the installed PWA's app_mode_loader) serves a stale
    # index.html, it references a bundle that no longer exists on disk (404),
    # and navigations fall back to that stale shell — which is exactly how an
    # export/download ends up saving index.html instead of the real response.
    # no-store forces a fresh shell on every launch; the hashed assets under
    # /assets stay immutably cacheable via StaticFiles.
    _NO_STORE = {"Cache-Control": "no-store, must-revalidate"}

    # Every non-API path returns index.html so React Router works client-side.
    # Exception: if a real file exists in the dist root at that path (e.g.
    # `mobile.html` from the multi-entry build), serve it directly so the
    # mobile companion bundle is reachable at /mobile.html.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        if full_path:
            candidate = (_DIST / full_path).resolve()
            try:
                candidate.relative_to(_DIST.resolve())
            except ValueError:
                # Path escaped the dist root — refuse and fall through.
                candidate = None
            if candidate is not None and candidate.is_file():
                # HTML shells (index/mobile) must not be cached; other real
                # files (favicons, manifest) are fine to cache normally.
                hdrs = _NO_STORE if candidate.suffix == ".html" else None
                return FileResponse(candidate, headers=hdrs)
        return FileResponse(_DIST / "index.html", headers=_NO_STORE)
