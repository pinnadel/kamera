#!/usr/bin/env bash
# Rebuild /Applications/KaMeRa.app's launcher script from source.
#
# The KaMeRa.app bundle is an osacompile'd AppleScript applet — there is no
# Xcode project. This script recompiles launcher/main.applescript into the
# bundle's main.scpt in place, so the launcher fix in main.applescript is the
# single source of truth. See launcher/main.applescript for why the launch
# command is shaped the way it is.
#
# Usage:  launcher/build_app.sh
#
# If the whole .app is ever missing, recreate it once with:
#     osacompile -o "/Applications/KaMeRa.app" launcher/main.applescript
#   then copy launcher/icon.icns to Contents/Resources/applet.icns and set the
#   CFBundleName/CFBundleDisplayName to "KaMeRa" in Contents/Info.plist.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/launcher/main.applescript"
APP="/Applications/KaMeRa.app"
SCPT="$APP/Contents/Resources/Scripts/main.scpt"

if [ ! -f "$SRC" ]; then
  echo "Source not found: $SRC" >&2
  exit 1
fi

if [ ! -d "$APP" ]; then
  echo "App bundle not found at $APP." >&2
  echo "Create it first (one-time):" >&2
  echo "  osacompile -o \"$APP\" \"$SRC\"" >&2
  exit 1
fi

echo "Compiling $SRC → $SCPT"
osacompile -o "$SCPT" "$SRC"

# osacompile writes a self-contained .scpt; the bundle's Info.plist / icon are
# untouched. If the bundle was code-signed, the signature is now invalid —
# strip it so Gatekeeper falls back to the ad-hoc/unsigned path.
if [ -d "$APP/Contents/_CodeSignature" ]; then
  echo "Stripping stale _CodeSignature"
  rm -rf "$APP/Contents/_CodeSignature"
fi

# Clear the quarantine/translocation cache so Finder picks up the new script.
touch "$APP"

echo "Done. Relaunch KaMeRa from Finder / Applications."
