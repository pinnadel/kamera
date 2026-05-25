"""
File-watcher and folder-picker endpoints.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.state import watcher

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WatchRequest(BaseModel):
    folder_path: str


class PickFolderRequest(BaseModel):
    start_path: str = ""   # optional; empty string → home directory
    prompt: str = ""       # optional caption shown above the file list


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/pick-folder")
def pick_folder(request: PickFolderRequest):
    """
    Open a native folder-picker dialog and return the chosen path.

    Implementation note (macOS): tkinter requires the process main thread to
    show a window. Uvicorn request handlers run in a worker thread, so calling
    `tk.Tk()` directly here hangs the dialog (it never gains focus — the
    Python dock icon just spins forever waiting for an app context that won't
    come). We sidestep that by running the picker in a clean Python subprocess
    that has its own main thread and gets a fresh GUI app context from the
    window-server. Same approach works identically on Windows and Linux.

    Returns {"path": "/chosen/folder"} on success,
            {"path": null}             if the user cancelled or timed out.
    """
    start = request.start_path.strip()
    if not start or not Path(start).is_dir():
        start = os.path.expanduser("~")
    title = request.prompt or "Choose a folder"

    # The script is stdlib-only and runs in a fresh interpreter — no
    # backend imports, no SigLIP/TOPIQ load, just Tk. Cancel returns empty
    # stdout, which we map to {"path": null} below.
    # repr() escaping handles paths with spaces, quotes, unicode, etc. so
    # there's no f-string / shell-quoting surface to get wrong.
    script = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "root.update_idletasks()\n"
        "root.lift()\n"
        "root.focus_force()\n"
        f"chosen = filedialog.askdirectory(title={title!r}, initialdir={start!r})\n"
        "root.destroy()\n"
        "sys.stdout.write(chosen or '')\n"
    )

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes — generous for users mulling the dialog
        )
    except subprocess.TimeoutExpired:
        logger.warning("Folder picker subprocess timed out after 5 minutes")
        return {"path": None}
    except Exception:
        logger.exception("Folder picker subprocess failed to launch")
        raise HTTPException(status_code=500, detail="Folder picker unavailable")

    if result.returncode != 0:
        # Tk error inside the subprocess — most commonly a missing _tkinter
        # extension. Surface the stderr so the cause shows up in app.log.
        logger.error(
            "Folder picker subprocess exited %d: %s",
            result.returncode, result.stderr.strip()
        )
        raise HTTPException(status_code=500, detail="Folder picker failed (see app.log)")

    chosen = result.stdout.strip()
    if not chosen:
        return {"path": None}
    return {"path": Path(chosen).as_posix().rstrip("/")}


@router.get("/watch")
def get_watch():
    """Return the currently watched folder, or null if none."""
    return {"folder": watcher.watching}


@router.post("/watch")
def start_watch(request: WatchRequest):
    """Start watching a folder for new photos. Replaces any existing watch."""
    try:
        watcher.start(request.folder_path)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "watching", "folder": request.folder_path}


@router.delete("/watch")
def stop_watch():
    """Stop the folder watcher."""
    watcher.stop()
    return {"status": "stopped"}
