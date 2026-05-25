# KaMeRa — Peer Review Package

**Version:** 0.9 · **Snapshot date:** 2026-05-04 · **License:** personal/non-commercial
**Repository root:** `..` (relative to this folder)

This directory is a **self-contained briefing** intended to be shared with third-party reviewers (humans or AIs) who have no prior context on the project. Every claim is grounded in source files; every algorithm choice and threshold is named with the file it lives in. The package is organised so a reviewer can answer three review questions independently:

1. **Architecture** — Is the system decomposed sanely? Are the boundaries between processes, threads, and storage layers defensible?
2. **Models** — Are the chosen ML components appropriate, well-integrated, and honestly scored? Where are the calibration hazards?
3. **UX** — Does the interaction model match the problem (high-volume RAW culling)? Are there hidden friction points?

---

## How to read this package

| File | Purpose | Format |
|---|---|---|
| `README.md` | This index — start here. | Markdown |
| `00-overview.md` | One-page TL;DR of what the app does + glossary + invariants. | Markdown |
| `01-architecture.md` | Process model, data flow, threading, storage. With ASCII diagrams. | Markdown |
| `02-models.md` | Every ML model: weights, preprocessing, output range, known calibration risks. | Markdown |
| `03-scoring.md` | Phase 1 algorithms (sharpness fusion, exposure), Phase 3 personal model math. | Markdown |
| `04-ux.md` | UX inventory: views, modals, keyboard model, empty states, design system. | Markdown |
| `05-decisions-log.md` | Notable past trade-offs (what was tried, what was dropped, and why). | Markdown |
| `06-known-risks.md` | The author's own list of known weak spots — start your review *here* if you only have 30 minutes. | Markdown |
| `07-review-prompts.md` | Pre-written prompts a reviewer can paste into another LLM to attack a specific axis. | Markdown |
| `manifest.json` | Machine-readable index of every doc file + content hashes + cross-links. | JSON |
| `endpoints.json` | All 37 HTTP endpoints — method, path, purpose, side-effects. | JSON |
| `schema.sql` | The full SQLite schema (CREATE TABLE) with column-level comments. | SQL |
| `features.json` | The 17-dim Phase 3 feature vector — exact order, source column, semantics. | JSON |
| `dependencies.json` | All Python + JS dependencies with versions and licenses. | JSON |

> **Reviewers in a hurry**: read `00-overview.md` → `06-known-risks.md` → `07-review-prompts.md`. That triplet is ~15 minutes and gives you everything you need to challenge the design.

---

## Ground truth — what's real and what's aspiration

This package describes the **state on 2026-05-04**, not a target. Specifically:

- **Implemented and shipping:** Everything in `01-architecture.md`, `02-models.md` §Phase 1 + §Phase 2, and `04-ux.md`. The five Phase 2 scorers (face, IQA, aesthetic, similarity, narrative) all run on every analysed photo and persist to SQLite.
- **Implemented but lightly tested:** Phase 3 personal model (`PersonalModel`) is wired end-to-end but the author has not yet trained it on a real corpus of ≥20 of his own decisions. Treat any claim about real-world predictive quality as untested.
- **Not implemented:** Anything in `05-decisions-log.md` § "Future / explicitly deferred". Most prominently: A/B pairwise training, sample-weight recency decay, multi-watcher live mode.

---

## Repository layout (canonical)

```
kamera/
├── backend/                  Python · FastAPI · SQLite
│   ├── main.py               37 HTTP endpoints, batch loop, model-status registry
│   ├── database.py           SQLite schema + migrations + settings/folder_settings helpers
│   ├── file_mover.py         Post-decision RAW + .xmp + .hif moves with collision suffixes
│   └── file_watcher.py       watchdog observer (single-folder, non-recursive)
├── phase1_technical/         Pillar A — pixel-level scoring (RAW-aware)
│   ├── quality_analyzer.py   Orchestrator; ThreadPoolExecutor fan-out across all scorers
│   ├── batch_sharpness_analyzer.py   Tile-p90 fusion of Laplacian/Tenengrad/Modified-Laplacian
│   ├── exposure.py           Histogram-based exposure score + clipping pcts
│   ├── camera_shake.py       EXIF rule + gradient-direction + FFT elongation
│   ├── burst_detection.py    Timestamp clustering (kept after fastdup was dropped)
│   └── exif_parser.py · utils.py
├── phase2_quality/           Pillar B — perceptual / semantic scoring (PyTorch)
│   ├── face_analyzer.py      MediaPipe FaceLandmarker + BlazeFace fallback
│   ├── iqa_scorer.py         TOPIQ no-reference IQA (CPU-pinned)
│   ├── aesthetic_scorer.py   CLIP ViT-L/14 + LAION linear MLP head
│   ├── similarity_scorer.py  SigLIP base — 768-dim L2-normed embeddings + Union-Find clustering
│   ├── llm_explainer.py      LM Studio (OpenAI-compatible) narrative — 2–3 sentence prose
│   ├── model_status.py       Thread-safe begin/end registry feeding the download toast
│   └── device.py             MPS > CUDA > CPU resolver
├── phase3_learning/          Pillar C — personal taste delta
│   ├── feature_extractor.py  17-dim float32 vector from a single DB row
│   └── personal_model.py     Imputer → Scaler → GradientBoostingRegressor, MIN_DECISIONS=20
├── frontend/                 React 19 · Tailwind v4 · Vite
│   └── src/
│       ├── App.jsx           ~2000 lines: tab state, polling, hotkeys, grid orchestrator
│       ├── views/            DetailView (~1000 LOC), GroupStrip, TabBar, TrainingModeView, …
│       ├── modals/           SettingsModal (~700 LOC), AutoCullModal, ConflictModal, …
│       └── ui/               primitives, toasts, FolderInput, format helpers
├── data/                     Runtime state (gitignored)
│   ├── pca.db                SQLite — see schema.sql
│   ├── previews/             *.jpg cache, regenerated on demand
│   ├── models/               face_landmarker.task, blaze_face_short_range.tflite, personal_model.pkl
│   └── app.log               RotatingFileHandler — 1 MB × 4 files
└── docs/peer-review/         (you are here)
```

---

## What this package deliberately does **not** include

- **Source code** — reviewers should `cd ..` and read it. Pasting code into the package would duplicate truth.
- **Author bias / first-person rationalisation** — the goal is to enable challenge, not to defend.
- **Marketing copy** — there are no benchmarks vs. competitors, no claims of state-of-the-art, no growth numbers. This is a personal tool.
- **Recipe to reproduce a training run** — the personal model has not been trained on real data yet; there is nothing to reproduce.

---

## Provenance & integrity

`manifest.json` includes a SHA-256 hash for every doc file in this package. A reviewer can verify they are reading the version the author intended:

```bash
cd docs/peer-review
shasum -a 256 -c <(jq -r '.files[] | "\(.sha256)  \(.path)"' manifest.json)
```

If hashes drift, the package was edited after publication — read with appropriate scepticism.
