# 00 — Overview

## What the app is

A **local-only desktop photo-culling assistant** for working photographers. The user points it at a folder of RAW files (Fujifilm RAF, Nikon NEF) plus JPEGs/PNGs; the app analyses each photo, surfaces near-duplicates, and supports a fast keyboard-driven Keep / Maybe / Reject workflow. Decisions immediately move RAW + sidecar files into per-folder `_Keeps/_Maybes/_Trash` directories and write XMP star ratings that downstream apps (Luminar Neo, Lightroom, Capture One) read.

Three decisions define the project:

1. **Local-only** — no cloud uploads, no remote inference. All weights are downloaded once, then stay on the machine. RAW files never leave disk.
2. **RAW-respecting** — the RAW file itself is never modified. Decisions persist as XMP sidecar ratings + database rows + filesystem moves.
3. **Personal taste, not population taste** — Phase 3 trains a per-user model that nudges scores by ±25 points based on the user's own keep/reject history. There is no shared model.

## What the user actually does

```
1. Open the app                          → empty trailing tab visible
2. "+ New analysis" → pick folder        → background batch starts (RAF + NEF + JPEG)
3. Watch progress bar / skim incoming    → live grid refresh as photos finish
4. Hit K / M / R on each photo           → file moves to _Keeps/_Maybes/_Trash + XMP rating
5. Switch to Groups view (optional)      → see SigLIP near-duplicate clusters → "Reject all but best"
6. Switch to Train mode (optional)       → score-blind queue → after 20 decisions, train personal model
7. Tab back to grid → personal_score now shifts the sort order
```

Total ergonomic budget per photo: **<1 second** (single keystroke + autoadvance).

## Glossary (terms used throughout this package)

| Term | Meaning |
|---|---|
| **Phase 1** | "Pixel-level technical scoring" — sharpness, exposure, shake, bursts. CPU-only, no DL. |
| **Phase 2** | "Perceptual / semantic scoring" — face landmarks, TOPIQ IQA, LAION aesthetic, SigLIP embeddings, optional LLM narrative. PyTorch + MediaPipe. |
| **Phase 3** | "Personal-taste delta" — a user-trained gradient-boosted regressor that produces a `personal_score` from the 17 Phase 1+2 features. |
| **overall_score** | `sharpness × w + exposure × (1 − w)`, default `w=0.65`. The Phase-1-only score. Stored in DB. |
| **personal_score** | `clamp(overall_score + Δ × 25, 0, 100)`. Computed at request-time, cached in RAM. |
| **Tab** | A workspace bound to one source folder, with its own image list / progress / selection. The rightmost tab is always an empty "New analysis" stub. |
| **Watch live** | A per-tab boolean that turns the source folder into a watched directory. Only one tab can have it on. |
| **Decision** | One of `keep` / `maybe` / `reject`. Moves the RAW file + sidecars, writes XMP rating, persists to `decisions` table. |
| **Burst / similarity group** | A SigLIP-cosine cluster of ≥2 images at threshold 0.90 (default; user-adjustable 0.80–0.99). |
| **Hero** | The auto- or user-chosen "best" image of a similarity group. Used by "Reject all but best." |
| **HE\*** | Nikon's High-Efficiency NEF compression (Z6III/Z8/Z9). LibRaw cannot demosaic it — pipeline falls back to the embedded JPEG. |

## System invariants (these must always hold)

| # | Invariant | Where enforced |
|---|---|---|
| 1 | RAW files are never modified by this app. | All write paths go to SQLite, XMP sidecars, or `data/previews/`. No `cv2.imwrite` or `rawpy.write`. |
| 2 | All ML inference runs locally; no network calls during analysis. | Phase 2 scorers use HuggingFace cache only. LM Studio call is opt-in and to `localhost:1234`. |
| 3 | A `decisions` row implies the file is at its decision destination on disk. | `POST /decision` moves the file *before* committing the row. Failure raises 5xx and rolls back. |
| 4 | `personal_score` is never persisted; it is recomputed on every `GET /images`. | No `personal_score` column. Only the cache in `phase3_learning/personal_model.py`. |
| 5 | The active tab's `selectedIdx` is bounded by `images.length`. | Derived setter in `App.jsx::updateActiveTab` clamps when image list shrinks. |
| 6 | Only one folder watcher runs at a time. | `backend/main.py::_watcher` is a singleton; toggling `watch_live` on tab X disables it elsewhere. |
| 7 | The last tab is always the empty "New analysis" stub. | `App.jsx::tabs` derivation appends a stub if missing. |
| 8 | Stop-analysis honours the *next* scorer boundary, not the current pixel op. | `_stop_event: threading.Event` checked in `quality_analyzer.py` between scorer futures; UI shows "Stopping…" while the in-flight RAW decode finishes. |
| 9 | Model weight caches are immune to "Clear analysis." | `POST /clear` only wipes SQLite rows + `data/previews/`. `~/.cache/pyiqa/` and `~/.cache/huggingface/` are untouched. |
| 10 | Decision thresholds are read fresh on every `_compute_auto_decision` call. | No in-memory copy in the auto-cull path; all 9 thresholds round-trip to SQLite each time. |

## Hardware / runtime profile

- **Target:** Apple Silicon (M-series) MacBook with ≥16 GB unified memory. macOS only (folder picker uses AppleScript `osascript`).
- **Cold start:** 0 seconds for the app itself. First Phase 2 analysis triggers ~2 GB of HuggingFace + pyiqa weight downloads (LAION CLIP backbone ~890 MB, SigLIP ~300 MB, TOPIQ ~100 MB, MediaPipe ~4 MB) — surfaced in a download toast.
- **Per-photo wall-clock budget (warm cache, RAF, M-series):** ~3–6 s including RAW decode. Sharpness + exposure: <500 ms. Phase 2 scorers run in parallel on a 6-worker `ThreadPoolExecutor`.
- **Inference device:** TOPIQ is **pinned to CPU** (MPS adaptive_avg_pool2d incompatibility). LAION + SigLIP use `phase2_quality/device.py::get_device()` — MPS > CUDA > CPU.

## What this app is **not**

- **Not** a RAW developer / converter — there is no exposure adjustment, no white-balance UI, no export pipeline.
- **Not** a DAM (Digital Asset Manager) — no tags, no collections, no search beyond filename.
- **Not** a multi-user system — single SQLite file, no auth, no concurrent-edit handling.
- **Not** Windows / Linux compatible — the folder picker is `osascript`-only and several paths assume macOS Trash semantics (`send2trash`).
- **Not** a benchmark or research tool — the Phase 1 sharpness scales were calibrated against ~50 of the author's own RAFs, not against a public dataset.
