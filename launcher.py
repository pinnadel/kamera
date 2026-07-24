"""
KaMeRa — launcher.

Starts the FastAPI backend silently, waits until it is healthy, then opens
the app in the default browser. On macOS the launcher then runs headless,
watching the KaMeRa PWA window and quitting the backend when it closes. On
Windows/Linux a tray icon lets the user open the app again or quit cleanly.

Usage:
    python launcher.py          # normal launch (pre-built frontend)
    python launcher.py --dev    # use Vite dev server on :5173 (hot reload)
"""

import argparse
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT         = Path(__file__).parent
VENV         = ROOT / "venv"
FRONTEND     = ROOT / "frontend"
DIST         = FRONTEND / "dist"
REQUIREMENTS = ROOT / "requirements.txt"
ICON_PATH    = ROOT / "launcher" / "icon.png"   # PNG for pystray (Windows/Linux)

if sys.platform == "win32":
    PYTHON  = VENV / "Scripts" / "python.exe"
    UVICORN = VENV / "Scripts" / "uvicorn.exe"
    NPM     = "npm.cmd"
else:
    PYTHON  = VENV / "bin" / "python"
    UVICORN = VENV / "bin" / "uvicorn"
    NPM     = "npm"

BACKEND_PORT = 8000
BACKEND_URL  = f"http://localhost:{BACKEND_PORT}"
HEALTH_URL   = f"{BACKEND_URL}/health"
FRONTEND_URL = "http://localhost:5173"

# The KaMeRa UI is shown as an installed Chrome PWA with its own Dock icon,
# running as a Chrome "app_mode_loader" launched from its shim bundle. The
# launcher only owns the backend process, so it watches this process to know
# when the user has quit the app (see _watch_pwa_macos below) and tears the
# backend down in response. Matched on the shim's UNIQUE executable path: the
# regular Chrome browser runs from a different path (Google Chrome.app/…/
# Google Chrome), so this never matches the user's browser or other PWAs.
PWA_LOADER_PATH = "Chrome Apps.localized/KaMeRa.app/Contents/MacOS/app_mode_loader"


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _pwa_is_running() -> bool:
    """True while the KaMeRa PWA window's app_mode_loader process is alive."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", PWA_LOADER_PATH],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return True  # can't tell — assume alive rather than tearing down early

def _ensure_venv():
    if PYTHON.exists():
        return
    print("Creating Python virtual environment (first run — takes a minute)…")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    print("Installing Python dependencies…")
    subprocess.run(
        [str(PYTHON), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
        check=True,
    )


def _ensure_frontend_deps():
    if (FRONTEND / "node_modules").is_dir():
        return
    print("Installing frontend dependencies…")
    subprocess.run([NPM, "install"], cwd=str(FRONTEND), check=True)


def _build_frontend():
    print("Building frontend…")
    subprocess.run([NPM, "run", "build"], cwd=str(FRONTEND), check=True)


def _start_backend() -> subprocess.Popen:
    cmd = [str(UVICORN), "backend.main:app",
           "--port", str(BACKEND_PORT), "--host", "127.0.0.1"]
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **kwargs)


def _start_vite() -> subprocess.Popen:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen([NPM, "run", "dev"], cwd=str(FRONTEND),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **kwargs)


def _wait_for_backend(timeout: int = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Icon image — written to disk so pystray (Windows/Linux) can find it
# ---------------------------------------------------------------------------

def _ensure_icon() -> str:
    """Return the path to a PNG icon, generating one if none exists."""
    ICON_PATH.parent.mkdir(exist_ok=True)
    if not ICON_PATH.exists():
        _generate_icon(ICON_PATH)
    return str(ICON_PATH)


def _generate_icon(path: Path):
    """Draw a simple cyan-on-dark circle as the menu-bar icon."""
    from PIL import Image, ImageDraw
    size = 22  # macOS menu bar icon is 22×22 pt
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, size - 2, size - 2], fill="#07080a")
    r = size // 4
    cx = cy = size // 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#5BB8D4")
    img.save(str(path))


# ---------------------------------------------------------------------------
# Supervisor — headless PWA watcher (macOS) or pystray tray icon (Windows / Linux)
# ---------------------------------------------------------------------------

PWA_POLL_SECONDS = 2

def _watch_pwa_macos(processes: list, app_url: str):
    """Headless supervisor: no menu-bar icon, no Dock icon of its own.

    KaMeRa opens as an installed Chrome PWA with its OWN Dock icon — quitting
    the app from the Dock (right-click → Quit, or Cmd+Q on the PWA window) is
    the natural way a user ends a mac app. This loop polls whether that PWA
    process is still alive; once it's gone, the backend has no reason to keep
    running, so we terminate it and exit. This replaces an earlier menu-bar
    icon (rumps) that duplicated "Quit" as a separate, easy-to-miss control
    and left a redundant "Open KaMeRa" that just spawned a second window.

    First poll waits PWA_POLL_SECONDS before checking, giving the just-opened
    PWA process time to actually launch.
    """
    while True:
        time.sleep(PWA_POLL_SECONDS)
        if not _pwa_is_running():
            break

    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass


def _run_tray_pystray(processes: list, app_url: str):
    """Cross-platform tray icon using pystray (Windows / Linux)."""
    import pystray
    from pystray import MenuItem as Item
    from PIL import Image

    _ensure_icon()
    img = Image.open(str(ICON_PATH)).convert("RGBA").resize((64, 64))

    def on_open(icon, item):
        webbrowser.open(app_url)

    def on_quit(icon, item):
        icon.stop()
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass

    icon = pystray.Icon(
        "photo_culling", img, "KaMeRa",
        menu=pystray.Menu(
            Item("Open", on_open, default=True),
            Item("Quit", on_quit),
        ),
    )
    icon.run()


def _run_supervisor(processes: list, app_url: str):
    """Block until the app should quit, then terminate `processes`.

    macOS: headless — watches the PWA window's process (see _watch_pwa_macos).
    Windows/Linux: no installed-PWA equivalent, so keep the pystray tray icon
    with a manual Quit.
    """
    try:
        if sys.platform == "darwin":
            _watch_pwa_macos(processes, app_url)
        else:
            _run_tray_pystray(processes, app_url)
    except Exception as e:
        print(f"Supervisor unavailable ({e}). Press Ctrl+C to quit.")
        try:
            processes[0].wait()
        except (KeyboardInterrupt, IndexError):
            pass
        finally:
            for p in processes:
                try:
                    p.terminate()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="KaMeRa launcher")
    parser.add_argument("--dev", action="store_true",
                        help="Use Vite dev server instead of pre-built frontend")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open the app after start (for callers like "
                             "Provenance that open their own window)")
    args = parser.parse_args()

    processes: list[subprocess.Popen] = []

    try:
        _ensure_venv()
        _ensure_frontend_deps()

        if args.dev:
            app_url = FRONTEND_URL
            processes.append(_start_vite())
        else:
            if not DIST.is_dir():
                _build_frontend()
            app_url = BACKEND_URL

        print("Starting backend…")
        processes.append(_start_backend())

        print("Waiting for backend…", end="", flush=True)
        if not _wait_for_backend():
            print("\nBackend did not start. Check data/app.log for errors.")
            for p in processes:
                p.terminate()
            sys.exit(1)
        print(" ready.")

        if not args.no_browser:
            webbrowser.open(app_url)
        _run_supervisor(processes, app_url)

    except KeyboardInterrupt:
        pass
    finally:
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
