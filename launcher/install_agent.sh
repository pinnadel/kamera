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
cp "$SRC" "$DEST"
echo "Installed $DEST"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST"
echo "Bootstrapped $DOMAIN/$LABEL"
echo "Start it with:  launchctl kickstart $DOMAIN/$LABEL"
