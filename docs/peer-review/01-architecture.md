# 01 — Architecture

## Process model

Two long-running processes plus optional auxiliaries:

```
┌────────────────────────────────────────────────────────────────────────┐
│  macOS host                                                            │
│                                                                        │
│  ┌──────────────────────┐         HTTP (localhost:8000)                │
│  │  Vite dev server     │──────────────────────┐                       │
│  │  :5173               │                      │                       │
│  │  React 19 SPA        │                      ▼                       │
│  └──────────────────────┘            ┌────────────────────────────┐    │
│         ▲                            │  FastAPI / uvicorn :8000   │    │
│         │ fetch() polling            │  - 37 endpoints            │    │
│         └────────────────────────────│  - batch loop (1 thread)   │    │
│                                      │  - ThreadPoolExecutor pool │    │
│                                      │  - watchdog Observer (opt) │    │
│                                      └─────────────┬──────────────┘    │
│                                                    │                   │
│                                  ┌─────────────────┼──────────────┐    │
│                                  ▼                 ▼              ▼    │
│                         ┌────────────────┐ ┌───────────────┐ ┌───────┐ │
│                         │ data/pca.db    │ │ ~/.cache/...  │ │ RAW   │ │
│                         │ (SQLite)       │ │ HuggingFace   │ │ files │ │
│                         └────────────────┘ │ pyiqa, MP     │ │ on    │ │
│                                            └───────────────┘ │ disk  │ │
│                                                              └───────┘ │
│                                                                        │
│  ┌──────────────────────┐  optional, opt-in                            │
│  │  LM Studio :1234     │  ◀───── POST /generate-explanation ─────┐    │
│  │  (any vision model)  │                                         │    │
│  └──────────────────────┘                                         │    │
└────────────────────────────────────────────────────────────────────────┘
```

There is no separate worker process; the FastAPI process owns:

1. The **HTTP request thread** (uvicorn).
2. **One batch-analysis thread** (`backend/main.py:797–990`, started by `POST /analyze-folder`). Holds a single SQLite connection, commits per-photo so the frontend grid sees live progress.
3. A **`ThreadPoolExecutor` per photo** (size 6 for RAW, 4 for JPEG) that fans Phase 2 scorers out in parallel — `phase1_technical/quality_analyzer.py:181`.
4. An optional **`watchdog.Observer`** thread (`backend/file_watcher.py`) when "Watch live" is on for one tab.

This keeps state simple: there is no IPC, no message broker, no shared-memory dance. The cost is that long Phase 2 model loads (~25 s the first time) block other endpoints — surfaced in the UI via a download toast and the "Stopping…" honesty pattern.

## Data flow — single photo through the system

```
┌─────────────────┐
│ User selects    │  POST /pick-folder (osascript)
│ folder          │
└────────┬────────┘
         │ folder_path
         ▼
┌────────────────────────────────────────────────────────┐
│  POST /analyze-folder?path=...                         │
│  - launches background thread                          │
│  - sets _progress dict + _stop_event                   │
│  - skips files where (file_path, mtime) already in DB  │
└────────┬───────────────────────────────────────────────┘
         │  for each file:
         ▼
┌────────────────────────────────────────────────────────┐
│  quality_analyzer.analyze_photo_quality(path, …)       │
│                                                        │
│  IF RAW (.raf/.nef):                                   │
│    1. rawpy decode (uncancellable, ~2-5s tail)         │
│    2. fallback to extract_thumb() if HE* NEF           │
│    3. share decoded numpy RGB array with all scorers   │
│  IF JPEG/PNG: pillow load                              │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │ ThreadPoolExecutor (6 workers for RAW)           │  │
│  │   ├─ batch_sharpness_analyzer.detect_sharpness   │  │
│  │   ├─ exposure.analyze_exposure                   │  │
│  │   ├─ face_analyzer.analyze_faces_array           │  │
│  │   ├─ iqa_scorer.score_image_pil    (CPU pinned)  │  │
│  │   ├─ aesthetic_scorer.score_image_pil            │  │
│  │   └─ similarity_scorer.embed_image_pil           │  │
│  │                                                  │  │
│  │ stop_event checked between scorers (StopRequested│  │
│  │ raised → pool.shutdown(cancel_futures=True))     │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│  overall = sharpness * w + exposure * (1-w), w=0.65    │
└────────┬───────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────┐
│  exif_parser.extract_exif()                            │
│  → camera, lens, focal_length, aperture, shutter, ISO  │
└────────┬───────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────┐
│  database.upsert_image(...)                            │
│  - INSERT OR REPLACE into images                       │
│  - source_folder = parent dir of file_path             │
│  - embedding stored as compact JSON in TEXT column     │
└────────┬───────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────┐
│  xmp_writer.ensure_uuid(file_path)                     │
│  - reads .xmp sidecar (or creates one)                 │
│  - writes UUID for stable ID across rename/move        │
└────────────────────────────────────────────────────────┘
```

When the user later hits **K / M / R**, a separate path runs:

```
POST /decision { image_id, decision }
  ├─ resolve dest_folder via folder_settings (per-folder K/M/X overrides)
  ├─ file_mover.move_photo(current_path, dest_folder)
  │     ├─ moves RAW
  │     ├─ moves matching .xmp sidecar (case-insensitive)
  │     ├─ moves matching .hif companion (Fuji RAW+HIF mode)
  │     └─ collision suffix _1/_2/... if filename exists
  ├─ xmp_writer.write_rating(decision)   # 5★ keep, 3★ maybe, 0★ reject
  ├─ UPDATE images SET file_path = new_path
  ├─ INSERT OR REPLACE INTO decisions (image_id, decision, decided_at)
  └─ personal_model.invalidate(image_id)   # so next /images recomputes
```

## Threading & concurrency contract

| Actor | Lives in | Owns |
|---|---|---|
| uvicorn worker | request thread | `_progress`, `_last_errors`, `_personal_model` cache, `_watcher` ref |
| Batch loop | one daemon thread | per-photo SQLite transactions, `_stop_event` setter |
| Scorer pool | `ThreadPoolExecutor` per photo | wraps Phase 1 + Phase 2 scorers; `cancel_futures=True` on stop |
| Watcher | watchdog `Observer` | `on_created`/`on_moved`/`on_deleted` → spawns short daemon thread per event with 2s settle delay |
| Frontend | browser | poll loops at 400 ms (`/analyze-progress`), 1 s (`/model-status`), 5 s (`/images?source_folder=…` for live tab + `/debug/last-errors`) |

**No async/await on the backend** — every endpoint is synchronous. The choice is deliberate: Phase 2 inference is CPU/MPS-bound, not I/O-bound, so async would not help and would complicate the threading story.

## Storage layers

```
┌────────────────────────────────────────────────────────────────────┐
│ data/pca.db      SQLite, ~10 KB per photo (mostly the embedding)   │
├────────────────────────────────────────────────────────────────────┤
│ images           one row per photo (see schema.sql)                │
│ decisions        one row per K/M/R, ON DELETE CASCADE from images  │
│ bursts           legacy timestamp clusters (kept after fastdup     │
│                  was dropped — used only for old data)             │
│ burst_members    join table                                        │
│ settings         9 numeric thresholds + 4 booleans, key-value      │
│ folder_settings  per-source-folder K/M/X destination overrides     │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ data/previews/{image_id}.jpg     RAW demosaic cache, ~500 KB each  │
├────────────────────────────────────────────────────────────────────┤
│ JPEG/PNG sources:  served live from disk, no preview generated     │
│ RAF/NEF sources:   regenerated on first GET /previews/{id}         │
│ Sidecar mode on:   substitutes sibling .HIF/.JPG/.JPEG (Fuji)      │
│ Cache invalidation: full wipe on prefer_sidecar_preview toggle     │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ data/models/                                                       │
├────────────────────────────────────────────────────────────────────┤
│ face_landmarker.task            ~3.6 MB, MediaPipe, auto-downloaded│
│ blaze_face_short_range.tflite   ~224 KB, fallback face detector   │
│ personal_model.pkl              created after first /train-model   │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ ~/.cache/                       NOT inside the project — global   │
├────────────────────────────────────────────────────────────────────┤
│ ~/.cache/pyiqa/                 ~100 MB TOPIQ                     │
│ ~/.cache/huggingface/           ~1.2 GB CLIP-L/14 + SigLIP-base   │
│                                 NEVER touched by POST /clear      │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ data/app.log                    RotatingFileHandler 1 MB × 4      │
└────────────────────────────────────────────────────────────────────┘
```

## Frontend state model

The frontend is a single-page React 19 app with **all state in `App.jsx`** (~2000 LOC). There is no Redux, Zustand, Jotai, or context provider.

The pivotal decision is the **tab-derived global state**:

```js
// App.jsx
const [tabs, setTabs] = useState([makeNewTab()]);
const [activeTabId, setActiveTabId] = useState(tabs[0].id);
const activeTab = useMemo(
  () => tabs.find(t => t.id === activeTabId) || tabs[0] || null,
  [tabs, activeTabId]
);

// Everything that used to be top-level state is now derived:
const images       = activeTab?.images       ?? [];
const selectedIdx  = activeTab?.selectedIdx  ?? 0;
const folderPath   = activeTab?.folderPath   ?? null;
const analyzing    = activeTab?.status === 'analyzing';
// ... etc
```

Setters (`setImages`, `setSelectedIdx`, …) are wrapped to call `updateActiveTab(patch)` which produces a new `tabs` array. This avoided a 30-call-site find-and-replace when multi-tab was added — see `feedback_refactor_strategy` in author memory.

**Persistence:** only the active tab's `folderPath` is in `localStorage` (`pca.activeFolderPath`). Tabs themselves are reconstructed on launch from `GET /folders` + `GET /watch`. UUIDs regenerate every launch — they're for React keys only, never sent to the backend.

**Polling cadences (all `setInterval` in `App.jsx`):**

| Endpoint | Interval | Trigger |
|---|---|---|
| `/analyze-progress` | 400 ms | Always polling — finds analyzing tab via `tabsRef` |
| `/model-status` | 1 s | Always polling — feeds DownloadToast |
| `/images?source_folder=…` | 5 s | Only when `tab.watchLive === true` |
| `/debug/last-errors` | 5 s | Only when `tab.watchLive === true` (toasts new errors) |

## Why these choices

| Choice | Reason | Trade-off accepted |
|---|---|---|
| Synchronous FastAPI, not async | Phase 2 is CPU/MPS-bound; async would not help | Long-running endpoints block other endpoints; mitigated by polling-only client |
| Single SQLite file | Single-user, single-machine; no schema-migration tooling needed | No concurrent writers; locks held briefly per photo during batch |
| Per-photo `ThreadPoolExecutor` (not a process pool) | Models are loaded once, in-process, ~2 GB RAM each. Forking would re-load them. | The GIL bottlenecks pure-Python parts, but PyTorch / MediaPipe release the GIL during inference |
| All frontend state in `App.jsx`, no store | <2000 LOC, single developer; the cost of Redux > the benefit | When the file passes ~3000 LOC this will need to split |
| Polling, no WebSocket | Polling fits the 1–5 s cadence and avoids reconnection logic | A 400 ms `/analyze-progress` poll is wasteful on idle tabs; mitigated by it being a constant-cost endpoint |
| Tab data derived, not duplicated | Avoided 30-site refactor when going multi-tab | Setters need a wrapper; debugging requires knowing which tab "owns" which UI state |

## Where the architecture is strained

- **`backend/main.py` is 2119 lines** and holds endpoints, batch loop, auto-cull logic, and three module-level globals (`_progress`, `_stop_event`, `_personal_model`). It needs splitting before it doubles.
- **`frontend/src/App.jsx` is 1986 lines.** Already split off DetailView/GroupStrip/TabBar/etc., but the orchestrator itself is dense. The 15+ `useEffect`s have non-trivial interaction (polling, persistence, dropdown closing) that resists testing.
- **No tests.** None. The "test plan" is `bash start.sh`, click around, eyeball it. This is honest given a single-user personal tool but is a major audit risk if this ever ships beyond the author.
- **Stop-analysis is best-effort.** The RAW decode itself is uncancellable, and an in-flight scorer thread can't be preempted. UI says "Stopping…" to be honest; a reviewer should still question whether 2–5 s of "we heard you" is acceptable.
- **Personal model cache invalidation is one-way.** When `/train-model` runs, `_cache.clear()` happens. But if the user changes `sharpness_weight` in Settings (which triggers a re-score of `overall_score` for all rows), the personal score cache is *also* wiped — but via a separate code path. There are two ways for the cache to get stale; both are handled, but the contract is implicit.
