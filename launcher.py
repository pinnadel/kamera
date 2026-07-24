"""
KaMeRa — launcher.

Starts the FastAPI backend silently, waits until it is healthy, then opens
the app in the default browser. A menu-bar / tray icon lets the user open
the app again or quit cleanly.

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
ICNS_PATH    = ROOT / "launcher" / "icon.icns"  # icns for rumps (macOS)

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

# The KaMeRa UI is shown as an installed Chrome PWA (its own Dock icon). The
# macOS launcher applet opens it; this launcher only owns the backend. So the
# menu-bar "Quit" must ALSO close the PWA window — otherwise the backend dies
# but the Chrome app-window keeps its Dock icon lit.
#
# The PWA runs as a Chrome "app_mode_loader" launched from its shim bundle. A
# clean Apple-event quit (osascript / NSRunningApplication.terminate) does NOT
# close it — Chrome ignores that for app-mode windows. The reliable, safe lever
# is pkill matched on the shim's UNIQUE executable path: the regular Chrome
# browser runs from a different path (Google Chrome.app/…/Google Chrome), so
# this never touches the user's browser or other PWAs. The app-id / _crx_
# marker is NOT in the loader's matchable args, so don't match on those.
PWA_LOADER_PATH = "Chrome Apps.localized/KaMeRa.app/Contents/MacOS/app_mode_loader"


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _close_pwa_window():
    """Close the KaMeRa Chrome PWA window (macOS only; no-op elsewhere).

    Kills the app_mode_loader by its unique shim path so the Dock icon clears.
    Scoped to KaMeRa's own bundle path — never the user's regular Chrome.
    """
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["pkill", "-f", PWA_LOADER_PATH],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

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
# Icon image — written to disk so macOS rumps can find it
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
# Menu-bar icon — rumps (macOS) or pystray (Windows / Linux)
# ---------------------------------------------------------------------------

def _run_menubar_macos(processes: list, app_url: str):
    """Native macOS menu-bar app using rumps. Blocks until Quit.

    Runs as an "accessory" app (LSUIElement): a menu-bar icon with NO Dock
    icon. Without this, rumps' NSApplication defaults to a regular app and
    shows a generic Python "rocket" icon in the Dock next to the real KaMeRa
    launcher-applet icon. Setting the activation policy to Accessory before
    the run loop starts keeps only the menu-bar presence.
    """
    import rumps
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
    )

    # Must be set before rumps builds the app / enters its run loop.
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )

    icon_path = _ensure_icon()

    class PCAApp(rumps.App):
        def __init__(self):
            icns = str(ICNS_PATH) if ICNS_PATH.exists() else icon_path
            super().__init__("KaMeRa", icon=icns, quit_button=None)
            self.menu = [
                rumps.MenuItem("Open KaMeRa", callback=self.open_app),
                None,  # separator
                rumps.MenuItem("Quit", callback=self.quit_app),
            ]

        def open_app(self, _):
            webbrowser.open(app_url)

        def quit_app(self, _):
            _close_pwa_window()   # close the Chrome PWA window too (clears its Dock icon)
            for p in processes:
                try:
                    p.terminate()
                except Exception:
                    pass
            rumps.quit_application()

    PCAApp().run()


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


def _run_icon(processes: list, app_url: str):
    try:
        if sys.platform == "darwin":
            _run_menubar_macos(processes, app_url)
        else:
            _run_tray_pystray(processes, app_url)
    except Exception as e:
        print(f"Menu-bar icon unavailable ({e}). Press Ctrl+C to quit.")
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
        _run_icon(processes, app_url)

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
