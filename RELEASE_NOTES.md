# Release Notes

## v0.1 — Phase 1 MVP (2026-04-22)

### What's in this release

Phase 1 establishes the technical quality foundation: every photo gets a scored, reviewable result before any human decision is made.

**Image analysis**
- Sharpness scoring via Laplacian variance (OpenCV) with per-format thresholds
- Exposure analysis via histogram — detects highlights clipping and shadows crush
- Camera shake heuristics from EXIF shutter speed and focal length
- Burst detection by timestamp clustering (< 3 s gap = same burst)
- Combined quality score: `(sharpness × 0.65) + (exposure × 0.35)`

**File support**
- RAF (Fujifilm X100VI, X Half) via rawpy
- NEF (Nikon Z6III) via rawpy
- JPEG / PNG
- EXIF extraction (camera, lens, ISO, shutter speed, aperture, focal length)
- Portrait auto-rotation via EXIF orientation tag

**Backend (FastAPI)**
- 10 REST endpoints: analyze, batch-analyze, previews, decisions, folder watch, health
- SQLite persistence — images, bursts, burst_members, decisions tables
- Batch folder analysis with live progress + ETA polling
- macOS native folder picker (AppleScript)
- File watcher (watchdog) for automatic analysis of new imports

**Frontend (React + Tailwind v4)**
- Photo grid with quality score badges
- Detail view: full preview + EXIF panel + score breakdown
- Keyboard shortcuts: K (keep), X (reject), M (maybe), arrow keys for navigation
- Sticky header with batch-analyze trigger
- Full-grid arrow-key navigation

**Data safety**
- RAW files are never modified
- All writes go to SQLite or XMP sidecar only
- UUID written to XMP sidecar on ingest for cross-tool tracking

### Known limitations

- fastdup not yet integrated — burst detection uses timestamp clustering only
- No XMP write verification against Luminar Neo yet (manual check pending)
- macOS only (AppleScript folder picker)
- No authentication — local use only

### Compatibility

- Python 3.10+
- Node.js 18+
- macOS (tested on macOS 15 Sequoia)
- Cameras: Fujifilm X100VI · Fujifilm X Half · Nikon Z6III
