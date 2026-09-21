#!/usr/bin/env bash
# Install (or refresh) the KaMeRa backend LaunchAgent.
#
# Run once after cloning, and again whenever
# launcher/com.philippklein.kamera.backend.plist changes. Idempotent: an
# already-bootstrapped job is unloaded first so the new definition takes.
#
# See the plist's own comment for why the backend has to live in launchd's
# gui/<uid> domain instead of being spawned by the KaMeRa.app applet.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.philippklein.kamera.backend"
SRC="$ROOT/launcher/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

[ -f "$SRC" ] || { echo "Missing $SRC" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"

# launchd gives a job /usr/bin:/bin:/usr/sbin:/sbin and nothing else, so
# Homebrew binaries the backend shells out to (exiftool, ollama) are invisible
# to it. Bake THIS shell's PATH into the plist — the installer runs from a
# terminal, so it has the real one. Homebrew prefixes are appended defensively
# in case the installing shell is itself minimal.
AGENT_PATH="$PATH"
for extra in /opt/homebrew/bin /usr/local/bin; do
    case ":$AGENT_PATH:" in
        *":$extra:"*) ;;
        *) [ -d "$extra" ] && AGENT_PATH="$AGENT_PATH:$extra" ;;
    esac
done

if ! command -v exiftool >/dev/null 2>&1; then
    echo "WARNING: exiftool is not on this shell's PATH." >&2
    echo "         RAF/HIF files will be analysed without any EXIF." >&2
fi

# The plist carries a __KAMERA_PATH__ placeholder; | is safe as the sed
# delimiter because PATH entries are absolute paths containing /.
sed "s|__KAMERA_PATH__|$AGENT_PATH|" "$SRC" > "$DEST"
echo "Installed $DEST"
echo "  PATH = $AGENT_PATH"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST"
echo "Bootstrapped $DOMAIN/$LABEL"
echo "Start it with:  launchctl kickstart $DOMAIN/$LABEL"
