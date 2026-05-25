#!/usr/bin/env bash
# KaMeRa — launcher (macOS / Linux)
#
# Normal use:  ./start.sh          → production mode (pre-built frontend)
# Dev use:     ./start.sh --dev    → Vite dev server on :5173 (hot reload)

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Python venv ───────────────────────────────────────────────────────────────
VENV="$ROOT/venv"
if [ ! -d "$VENV" ]; then
  echo "Creating virtual environment..."
  python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"
pip install -q -r "$ROOT/requirements.txt"

# facenet-pytorch can't go in requirements.txt — its setup.py pins an old
# Pillow that breaks pip's resolver on Python 3.13. Install with --no-deps;
# its runtime needs (torch / numpy / Pillow / requests) are already in the
# venv from the requirements above. See requirements.txt for the full reason.
python -c "import facenet_pytorch" 2>/dev/null || \
  pip install -q --no-deps facenet-pytorch

# ── Launch ────────────────────────────────────────────────────────────────────
python "$ROOT/launcher.py" "$@"
