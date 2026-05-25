# 05 — Decisions log

A reviewer can save a lot of time by knowing what's already been tried. Each entry: what was tried, what happened, what's in place now.

## ML-stack decisions

### fastdup (dropped, permanent)
- **Goal:** burst / near-duplicate detection.
- **Outcome:** `pip install fastdup` failed on Python 3.13 (binary wheel incompatibility, no source build). Author held off upgrading the rest of the stack to keep fastdup, then dropped it after multiple attempts.
- **Replacement:** SigLIP embeddings + Union-Find clustering. Plus the legacy timestamp `bursts` table is still queried for old data but no new rows are created.

### `simple-aesthetics-predictor` (dropped, replaced)
- **Goal:** LAION aesthetic predictor wrapper.
- **Outcome:** The upstream `shunk031` HuggingFace account was suspended in 2025. Wrapper became unloadable.
- **Replacement:** Direct load from `camenduru/improved-aesthetic-predictor` (mirror of original LAION weights) using `transformers` + `huggingface_hub`. No new dependency. Reconstructed the MLP head architecture from the model card.

### `discus0434/aesthetic-predictor-v2-5` (rejected — license)
- **Goal:** newer, better-trained aesthetic head.
- **Outcome:** Released under **AGPL**. Incompatible with any future commercial intent, even local-only. Author memorised this in `project_model_licenses`.
- **Replacement:** sticking with V2 linearMSE.

### TOPIQ on MPS (tried, abandoned)
- **Goal:** Apple Silicon GPU acceleration for IQA (~1.5–3 s on CPU).
- **Outcome:** TOPIQ uses `adaptive_avg_pool2d` with non-divisor kernel sizes. Apple's MPS backend doesn't support that case (PyTorch issue tracked since 2023, still open). Falls through to a wrong-result computation rather than raising.
- **Replacement:** **CPU-pinned** at `phase2_quality/iqa_scorer.py:25–38` with an explicit comment.

### CLIP ViT-B/32 backbone (rejected — quality)
- **Goal:** smaller LAION backbone (~150 MB instead of ~890 MB).
- **Outcome:** Aesthetic predictions were noticeably worse on portraiture; head was trained on ViT-L/14 features.
- **Replacement:** ViT-L/14 even though it's 6× the size.

### SigLIP so400m (~1.7 GB, considered)
- **Goal:** larger SigLIP variant for tighter embeddings.
- **Outcome:** marginal improvement on synthetic test, +1.4 GB cache footprint not justified for the use case.
- **Replacement:** `siglip-base-patch16-224` (300 MB).

### Z6III HE\* NEF support
- **Tried:** every available LibRaw-based tool. None can decode Nikon's "High Efficiency\*" compression.
- **Outcome:** the open-source LibRaw decoder doesn't exist for HE\*; Adobe and Capture One have it under license.
- **Workaround:** `quality_analyzer.py` catches `LibRawFileUnsupportedError` and falls back to `extract_thumb()` — Nikon embeds a full-resolution 6048×4032 JPEG in every NEF, which is sufficient for sharpness, face, IQA, aesthetic, and embedding scoring.
- **Memorised:** `project_z6iii_he_nef`. Don't propose libraw upgrades or DNG conversion.

### Pickle for personal model (intentional, not ideal)
- **Tried:** considered `joblib`, ONNX, JSON-of-tree-thresholds.
- **Outcome:** sklearn pickle is the fastest path; the model is trained and consumed by the same Python process; no security boundary (single-user local app).
- **Risk acknowledged:** schema-version-skew between pickled pipeline and current `feature_extractor`. Mitigated by `n_features_in_` check on load.

### Per-camera sharpness calibration (deferred)
- **Considered:** different `_LAP_SCALE` etc. constants per camera (X100VI vs Z6III have very different per-pixel statistics).
- **Outcome:** would require either a calibration ritual at first launch or a hardcoded camera→constants map. Not worth complexity until a user reports ranking issues.
- **Current:** one global constant set, calibrated on the author's RAFs.

---

## Architecture decisions

### Synchronous FastAPI (not async)
- **Reason:** Phase 2 inference is CPU/MPS-bound; async would not help and complicates threading.
- **Cost:** long endpoints block other endpoints. Mitigated by polling-only client with a 400 ms cadence.
- **Reviewer challenge welcomed.**

### One SQLite file, no migration tool (Alembic etc.)
- **Reason:** single user, single machine; migrations live as inline `try: ALTER TABLE … except OperationalError: pass` blocks in `database.py`.
- **Cost:** schema history is lossy; fresh installs run the migration sequence even though they don't need to.
- **Memorised pattern:** `feedback_smoke_test_strategy` covers how to verify migrations didn't break the schema after touching `database.py`.

### All frontend state in `App.jsx`, no Redux
- **Reason:** project size <2k LOC at start; one-developer.
- **Cost:** orchestrator file is now 1986 lines. Already a constraint. Will need to split when it doubles.
- **Already split off:** DetailView, GroupStrip, TabBar, modals/, ui/.

### Multi-tab via derived state, not duplicated
- **Tried:** find-and-replace 30 call sites that read `images`/`selectedIdx`/etc. as global state.
- **Outcome:** considered too risky; chose to derive `images` etc. from `activeTab` and wrap setters.
- **Memorised:** `feedback_refactor_strategy` — "When many call-sites read a global, derive it locally and wrap setters; don't find-and-replace 30 sites."

### Dropdowns: document mousedown listener, not `<div fixed inset-0>` backdrop
- **Tried:** transparent backdrop divs to catch outside-click.
- **Outcome:** backdrop fails when an ancestor creates a stacking context (e.g., the bottom toolbar uses `transform`, which establishes one).
- **Replacement:** document-level `mousedown` listener with `data-dropdown="true"` markers on each dropdown wrapper. Memorised in `feedback_dropdown_outside_click`.

### Per-folder K/M/X destinations (replaced global)
- **Migration date:** 2026-05-04.
- **Before:** three global `settings` keys (`keeps_folder`, `maybes_folder`, `trash_folder`) used for every folder.
- **Reason for change:** users (the author) cull multiple shoots concurrently. A wedding shoot and a documentary project should not share `_Keeps`.
- **After:** `folder_settings` table keyed by `source_folder + decision`. Missing row = default subfolder of source folder. SettingsModal lost its "Folders" tab; the per-tab `TabFoldersPopover` pill replaced it.
- **Memorised:** `project_per_folder_destinations`.

### No tests (acknowledged)
- **Reason:** single-user, prototyping speed.
- **Substitute:** smoke-test pattern documented in `feedback_smoke_test_strategy` — `curl` every src module from the Vite dev server and grep for transform errors. Stronger than `vite build` alone, weaker than real tests.
- **`POST /analyze-folder` is destructive** — never use as a smoke test. Memorised in `feedback_analyze_folder_not_smoke_test`.

---

## UX decisions

### "Watch live" as a per-tab flag (not a separate tab kind)
- **Before:** `kind: 'live'` as a tab discriminator with `makeLiveTab()` factory.
- **Outcome:** doubled the tab-state surface (every consumer had to handle both kinds). UX-wise, the user wanted "this folder, but auto-update."
- **After:** `tab.watchLive: boolean` on every (ready) tab. Toggling on tab X turns it off everywhere else. Tab strip shows pulsing cyan dot on the watching tab. Memorised in `project_watch_live_architecture`.

### One-step new analysis (folder picker on click)
- **Before:** clicking "+ New analysis" opened an empty tab with a folder field + Analyze + Watch live buttons. Two clicks to start.
- **After (2026-05-03):** clicking the trailing tab opens the macOS folder picker directly. Confirming picks runs busy/overwrite guards and starts analysis immediately.
- **AppleScript caveat:** `choose folder` OK-button label is locked by macOS. Only the prompt caption is settable — so the dialog says "Start analysis" in the title bar, not in the OK button. Memorised in `feedback_applescript_button_labels`.

### Hero empty-state rejected
- **Tried:** centred filled-cyan "Pick a folder" button on empty tabs (marketing-style hero).
- **Outcome:** felt out of character. The app is a tool, not a product page. Filled cyan is reserved for state ("active selection"), not for affordance fills.
- **Memorised:** `feedback_hero_empty_state` — "Make X prominent" → lift existing layout, don't center as a marketing hero."

### Don't disable buttons unless strictly necessary
- **Tried:** disabling Apply when `maybe_threshold >= keep_threshold`.
- **Outcome:** disabled state hides the *reason*. User clicked, nothing happened, no feedback.
- **Replacement:** keep Apply enabled, show the validation error inline only on click attempt. Memorised in `feedback_button_states`.

### Helper-text contrast pass
- **Before:** secondary descriptive text in `#4a4a4a` — almost unreadable.
- **After:** `#9c9c9d` for ~25 paragraphs. `#4a4a4a` reserved for placeholders, separators, hover-from base colors.
- **Lesson:** WCAG would have caught this if there were tooling.

---

## Future / explicitly deferred

These are **not implemented** and a reviewer shouldn't be surprised by their absence.

- **Sample-weight decay** in personal model (recency bias).
- **Background warm-up** of personal-score cache after train.
- **A/B pairwise comparison mode** in Train (Elo / Bradley-Terry). Different data model from the unary K/M/R signal, so deferred.
- **Taste-evolution context in ConflictModal** ("you decided this 3 weeks ago, you're now saying X").
- **Multi-folder concurrent analysis.** Backend is sequential by design.
- **Watch-multiple-folders simultaneously.**
- **Custom scoring formulas** (user-edited weights for IQA / aesthetic / face into `overall_score`).
- **Per-camera sharpness calibration.**
- **A "is the personal model actually informative?" check** before the auto-cull mode switches from threshold rules to model rules at decision 20.
- **Held-out validation set** for the personal model.
- **Windows / Linux support.** AppleScript folder picker + macOS Trash semantics.

---

## Discarded prototypes worth knowing about

- **Day 2 global Laplacian sharpness experiment** (`phase1_technical/archive/sharpness_day2.py`). Replaced by tile-p90 fusion. Kept in archive as a reference for the day-9 reviewer note (2026-04-23) where the multi-measure scales were calibrated.
- **Donut-style sharpness/exposure visualisation** in DetailView. Replaced by standard `ScoreBar` to match AI Quality block (2026-05-03).
- **All-caps 11px bronze section labels.** Replaced by 14px Inter 500 in `--color-text-secondary`. Cleaner, more readable, less "form-style" — see `docs/DESIGN.md` §2.
