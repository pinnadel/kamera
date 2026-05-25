# 06 — Known risks (the author's honest list)

**This is the file to read first if you only have 30 minutes.** It's the author's pre-emptive concession of where the design is weakest. A reviewer should test these claims, then push beyond them.

Risks are tagged with severity: **🔴 critical**, **🟡 moderate**, **🟢 minor**. Every entry has a "where to check" pointer for evidence.

---

## ML / scoring stack

### 🔴 The personal model has never been trained on real data
The whole Phase 3 layer is wired but unproven. The author has not yet labelled ≥20 of his own photos to actually train it. So **the most ambitious claim of the project — "it learns your taste" — is currently aspirational.**
- Where to check: `data/models/personal_model.pkl` does not exist on the author's machine.
- What this invalidates: any judgement about predictive quality, learning rate, feature importance ordering.

### 🔴 17 features × 200 trees × ~20 samples = severe overfit risk
GBR with 200 estimators and `max_depth=3` has plenty of capacity to memorise a small training set. The only regulariser is `subsample=0.8`. No validation split, no early stopping, no held-out R² check.
- Where to check: `phase3_learning/personal_model.py:104–114`.
- Mitigation that exists: the `±25` delta cap prevents catastrophic re-ordering even if the model is garbage.
- Mitigation that doesn't: there's no signal to the user that "your model is currently 12 trees deep on 22 samples and will probably reverse itself when you add 5 more decisions."

### 🔴 `overall_score` is in the feature vector AND in the additive base
This is double-counting. The model can learn "I like overall_score" → adds a positive delta proportional to overall_score → applied on top of overall_score. The cap papers over it but doesn't fix it.
- Where to check: `phase3_learning/feature_extractor.py:28–46` (feature 3 is `overall_score`); `phase3_learning/personal_model.py:135–144` (`personal_score = overall_score + delta`).
- Honest fix: drop `overall_score` from the feature vector, since it's a linear combination of `sharpness_score` (feature 1) and `exposure_score` (feature 2) anyway.

### 🟡 NaN imputation produces silently-wrong feature values
`SimpleImputer(strategy='mean')` fills missing `eye_openness_ratio` with the mean across decided photos. If the user has only made decisions on photos with faces, the mean is "0.7-ish" — and a no-face photo gets imputed as "eyes mostly open." The model learns a wrong correlation.
- Where to check: `phase3_learning/feature_extractor.py` (no missingness indicator features) + `phase3_learning/personal_model.py:104` (Imputer config).
- Honest fix: add binary "feature_was_present" companion features.

### 🟡 TOPIQ runs on CPU only, costing ~1.5–3 s per photo
Pinned to CPU because of MPS `adaptive_avg_pool2d` incompatibility. This dominates the per-photo wall-clock time.
- Where to check: `phase2_quality/iqa_scorer.py:25–38`.
- Mitigation: lazy-load (only loads on first analysis) + `_MAX_SIDE = 1024` downscale.
- A reviewer should question whether **a faster alternative IQA** (BRISQUE, NIQE, MANIQA) would be a fair substitution given that IQA is one feature among 17.

### 🟡 LAION aesthetic head is a tiny re-projection of CLIP
Five Linear layers on a frozen CLIP-L/14 embedding cannot learn anything CLIP didn't already encode. So the aesthetic score is essentially "what CLIP thinks of beauty" filtered through a lens trained on AVA + LAION-aesthetic.
- Where to check: `phase2_quality/aesthetic_scorer.py:92–113` (head architecture).
- Effect: documentary / street / harsh-light photography scores poorly. The Phase 3 personal-taste layer is supposed to compensate; whether it does is untested (see first risk).

### 🟡 SigLIP threshold (0.90) is global
One cosine threshold for all clusters. Wedding sequences cluster correctly; landscape sequences with similar skies cluster falsely. Hero selection within a cluster uses `overall_score` only — ignoring aesthetic + IQA.
- Where to check: `phase2_quality/similarity_scorer.py:210–278`.
- UI mitigation: user-adjustable slider (0.80–0.99) per session. Not persisted per-folder.

### 🟢 Multi-face aggregation is "worst case" — could hide a great photo
Two-person portrait, one blinks: `eyes_open` is `False`, the photo is flagged for instant-reject. This is correct for "is anyone blinking?" but means a portrait of two people with one blinking gets the same treatment as a solo portrait of someone blinking.
- Where to check: `phase2_quality/face_analyzer.py` aggregation logic.

---

## Architecture

### 🔴 Stop-analysis is best-effort, not a hard cancel
The user clicks Stop → 2–5 s tail while the in-flight RAW decode finishes. The UI honestly shows "Stopping…" but a reviewer should question whether 5 s is OK for a workflow tool.
- Where to check: `phase1_technical/quality_analyzer.py:140–142`, `194–208`.
- Why it can't be improved: rawpy's `postprocess()` doesn't take a cancellation token.

### 🟡 `backend/main.py` is 2119 lines
HTTP endpoints, batch loop, auto-cull rules, three module-level globals. Single file. No tests.
- Where to check: `wc -l backend/main.py`.
- Risk surface: a refactor without tests has nothing to catch regressions.

### 🟡 `frontend/src/App.jsx` is 1986 lines
15+ `useEffect` hooks with non-trivial polling/persistence/dropdown interactions. The active-tab derivation is clever but means setters must always go through `updateActiveTab`. A new contributor can introduce drift between `tabs` and the derived UI state by writing to `setImages` directly (currently nothing prevents that).
- Where to check: `frontend/src/App.jsx`.

### 🟡 No tests anywhere
Zero coverage. The substitute is a manual "smoke test" of curling each module from the Vite dev server (memorised pattern: `feedback_smoke_test_strategy`). For a single-user personal tool this is defensible; for a peer review it's a flag.
- Where to check: `find . -name 'test_*' -o -name '*_test.py' -o -name '*.test.jsx'` returns nothing.

### 🟡 Personal-score cache invalidation has two paths
1. `_personal_model.invalidate(image_id)` after `/analyze` and `/analyze-folder`.
2. `_personal_model._cache.clear()` after `/train-model`.

Plus an implicit dependency: when the user changes `sharpness_weight` in Settings (which re-scores `overall_score` for all rows), the personal score becomes stale because `overall_score` is the additive base. The codepath that handles this is in `POST /settings`.
- Where to check: `backend/main.py` settings endpoint + `phase3_learning/personal_model.py:60–64`.
- Risk: if a future code path mutates `overall_score` without invalidating the personal cache, scores drift silently.

### 🟢 SQLite on a single connection during batch
The batch loop holds one connection and commits per photo. If the FastAPI process crashes mid-photo, the DB is fine (transaction rolls back) but the file may already be written to a `_Keeps` folder (no — moves only happen on `/decision`, which is request-scoped).
- Where to check: `backend/main.py:797–990`.

---

## UX / interaction

### 🔴 Auto-cull modal underplays "rule-driven cliff" failures
The preview shows aggregate counts (12 keep / 8 maybe / 30 reject). It does not surface "27 of those rejects came from the closed-eyes rule alone." The user clicks "Run" and the system makes 30 file moves; if the closed-eyes detector misfired, dozens of legitimate keepers go to the trash folder.
- Where to check: `frontend/src/modals/AutoCullModal.jsx` + `backend/main.py::_compute_auto_decision`.
- The decisions are reversible (files can be moved back manually) but the *trust* damage is not.

### 🟡 The Phase 2 first-load is ~25 seconds with no fallback
First analysis triggers ~1.2 GB of HuggingFace + 100 MB pyiqa downloads. Mitigated by DownloadToast, but there's no "analyse one photo with what's already loaded" mode.

### 🟡 Settings → Model tab is dense
9 numeric thresholds + 4 boolean toggles + Personal model section + Danger zone. Sticky-footer Apply is the right pattern but the surface is heavy.
- Where to check: `frontend/src/modals/SettingsModal.jsx` (~700 lines).

### 🟡 No way to inspect "what changed" between two personal-model versions
After retraining, the user has no view into "your top-3 features used to be X, Y, Z — now they're A, B, C." The model just silently updates.

### 🟢 Decision K hotkey vs typed-K-in-a-search-box conflict
react-hotkeys-hook's defaults handle this (no firing while focused on input/textarea), but the *filename search box* is a borderline case. Currently works; adding a global Cmd+K palette later would conflict.

### 🟢 `tabFoldersOpen` dropdown closes on tab switch (correct) but loses unsaved state
If the user opens the Folders pill, types a custom destination, then accidentally clicks another tab, their typing is gone. No warning. Mitigation: pill is informational, the FolderInput uses native picker (no typing).

---

## Privacy / safety

### 🟢 Generated explanations leak scores back to the LLM
The LM Studio prompt includes the actual numbers (overall, sharpness, IQA, aesthetic, face signals) as instructional context. So the narrative *agrees with* the score because we showed it the score. This is documented as deliberate (a reviewer might call it confirmation-biased).

### 🟢 No content moderation on stored explanations
Whatever the user's local LLM produces gets stored in `images.explanation` (TEXT). For a single-user tool this is fine; for shared use it would need filtering.

### 🟢 `data/app.log` may contain RAW file paths
Rotating log captures errors with file paths — could expose folder structure if shared. 1 MB × 4 files cap. No PII beyond path.

---

## How to attack this list

If you're a reviewer:

1. **Start with the 🔴 entries.** Verify each by going to the cited file and running the cited check.
2. **Pick ONE of the moderate (🟡) entries** to deep-dive; the value of a peer review is *depth*, not coverage.
3. **Push past this list.** Anything *not* listed here is a candidate for a finding the author has missed. Especially watch:
   - Edge cases not enumerated (very long folder paths, Unicode filenames, non-Latin EXIF).
   - Performance under high N (500+ photos, 5000+ photos).
   - Concurrent state bugs (two tabs analysing? two `/decision` requests on the same `image_id`?).
   - Threading hazards in `quality_analyzer.py` that the author hasn't proved are absent.

The "What's surprisingly good" axis is also welcome — a peer review that only finds problems gives the author no signal about which design moves are working.
