# 03 — Scoring algorithms

This file documents the deterministic Phase 1 scoring formulas + the Phase 3 personal-model math at the algebraic level. Phase 2 ML models are covered in `02-models.md`.

---

## 3.1 The headline formula

```
overall_score = sharpness_score × w + exposure_score × (1 − w)
```

- `w = sharpness_weight`, default `0.65`. Read live from `settings` table on every analysis.
- Both inputs are 0–100 scalars; output is therefore 0–100.
- Range is full (0 = "blurry, 100% clipped"), 100 = "tack sharp + perfectly exposed."

**Design intent:** sharpness is a non-recoverable quality (you can't sharpen a blurry photo back). Exposure is partially recoverable in RAW. Hence sharpness gets the larger weight by default, but the user can move the slider in Settings → Model.

**What `overall_score` does and doesn't include:**

| Included | Not included |
|---|---|
| Sharpness (Phase 1) | TOPIQ IQA |
| Exposure (Phase 1) | LAION aesthetic |
|  | Face quality |
|  | Camera-shake detection (boolean only, side-channel) |
|  | Personal-model delta |

The Phase 2 perceptual scores are deliberately **side-cars**: they are stored, displayed, and fed into the Phase 3 model — but they don't change `overall_score`. This keeps Phase 1 explainable ("two numbers from the pixels, weighted") even after Phase 2 went live.

---

## 3.2 Sharpness — tile-p90 multi-measure fusion

**File:** `phase1_technical/batch_sharpness_analyzer.py`

**Algorithm summary:**
1. Decode RAW (or load JPEG); convert to grayscale.
2. Pad/crop to a multiple of 16 on each axis. Reshape to `(16, 16, h_tile, w_tile)` — i.e., a 16×16 grid of tiles, **256 tiles** total.
3. Compute three focus-measure heatmaps over the **full image** (because each is a convolution with edge effects we don't want to recompute per tile):
   - **Laplacian variance** (Pech-Pacheco 2000): per-tile variance of `cv2.Laplacian(img)`.
   - **Tenengrad / Sobel energy** (Krotkov 1987): mean of `(Sobel_x² + Sobel_y²)` per tile.
   - **Modified Laplacian** (Nayar & Nakagawa 1994): per-tile mean of `|conv(img, [-1, 2, -1])| + |conv(img, [-1, 2, -1].T)|`.
4. For each measure, take the **90th percentile of the 256 tile values** — i.e., "how sharp is the sharpest 10% of the image?"
5. Normalise each p90 by an empirically-calibrated scale:
   - `_LAP_SCALE = 12.0`
   - `_TEN_SCALE = 1200.0`
   - `_MLAP_SCALE = 7.0`
6. Fuse with **geometric mean**:
   ```
   fused = ∛( (p90_lap / 12.0) × (p90_ten / 1200.0) × (p90_mlap / 7.0) )
   ```
7. Map to 0–100:
   ```
   sharpness_score = min(100, round((fused / 2.3) × 50))
   ```
   The 2.3 threshold corresponds to "definitely sharp on this hardware/lens."

**Why p90, not mean?** A sharp photo has *some* very sharp pixels (the focal point) and many soft ones (out-of-focus background). A blurry photo has no sharp pixels anywhere. `p90` rewards the existence of a sharp region rather than the average — which would penalise legitimate shallow-DOF portraits.

**Why three measures fused with geometric mean?**
- Laplacian variance is fast but sensitive to noise.
- Tenengrad is robust to moderate noise but undersells fine high-frequency detail.
- Modified Laplacian splits row/column gradient and catches motion-blur direction-asymmetry.
- Geometric mean penalises any weak measure (one zero → fused = 0). This guards against "high Laplacian variance from JPEG noise" being read as sharpness.

**Calibration provenance:**
- Scales calibrated against ~50 of the author's own RAFs on day 9 (2026-04-23) of the project. **Not a public benchmark.**
- The 0.65/0.35 weight is design intuition, not data-driven.
- The 2.3 sharpness threshold is hardware-dependent (depends on lens MTF + sensor pixel pitch). Across the three cameras (X100VI, X Half, Z6III) the author has not separately tuned per-camera; one constant is used for all three.

**Failure modes the algorithm handles:**
- Black frames / lens-cap shots → all p90s ≈ 0, fused ≈ 0, score = 0.
- Very small images (< 16 tiles per side) → falls back to global Laplacian on the whole image.
- Decode error → returns `None`; analyzer raises and the orchestrator records `analysis_status='error'`.

**Risks a reviewer should challenge:**
- **Noise vs. detail collinearity.** All three measures conflate high-frequency *signal* with high-frequency *noise*. ISO 6400 RAFs from the X100VI score artificially high before noise reduction.
- **The 2.3 threshold is a magic number.** No published derivation, no statistical floor.
- **Geometric mean punishes asymmetric detail too hard.** A photo of a wall with a sharp graffiti corner can score lower than a fully soft portrait if Tenengrad happens to have a low p90 (since the wall has few oriented gradients).
- **No camera-specific calibration.** The Z6III is a 24 MP full-frame, X100VI is 40 MP APS-C, X Half is 17 MP half-frame. Per-pixel sharpness statistics will differ structurally.

---

## 3.3 Exposure score

**File:** `phase1_technical/exposure.py`

**Algorithm:**
1. Convert to grayscale (or sometimes use the embedded JPEG for speed — see HE\* fallback).
2. Compute mean brightness `μ` and stdev `σ` (0–255 scale).
3. Compute clipping percentages from the histogram:
   - `highlight_clip_pct` — fraction of pixels at value 255.
   - `shadow_clip_pct` — fraction of pixels at value 0.
4. Score baseline = 100, apply penalties:
   - `|μ − 128| > 80` → −40 (severely under/over-exposed)
   - `|μ − 128| > 50` → −25
   - `|μ − 128| > 30` → −10
   - `highlight_clip_pct > 10%` → −30
   - `shadow_clip_pct > 10%` → −30
   - `σ < 25` → −20 (low contrast)
5. Clamp to `[0, 100]`.

**Intentionality heuristic** (separate output, not used in score):
- `(mean < 80 AND std > 40) OR (mean > 200 AND std > 35)` → `intentional = true`.
- Surfaces in DetailView as a hint: "looks low-key on purpose" / "looks high-key on purpose." Doesn't change the score — the warning chip just becomes informational rather than negative.

**Histogram endpoint** (`GET /histogram/{image_id}`): 256-bin counts per R/G/B + Rec.709 luminance, plus per-channel clipping. Computed live from the cached preview, not from the RAW — so what the user sees in the histogram view matches what they see in the preview thumbnail.

**Risks:**
- **Score penalties are cliff-edged.** A photo at `μ = 80.1` scores 100; at `μ = 80.0` scores 60. A reviewer should question whether smooth Gaussian penalties (à la photo IQ) would be more honest.
- **No EV/exposure-compensation awareness.** The score is purely pixel statistics — it has no idea the photographer intentionally pushed +1 EV.
- **Sky bias.** Wide landscape photos with a bright sky almost always have non-trivial highlight clipping. Score punishes them by 30 points; the user usually thinks they're fine.

---

## 3.4 Camera-shake detection

**File:** `phase1_technical/camera_shake.py`

**Two independent signals fused into one boolean:**

**Signal A — EXIF rule (predicted shake):**
```
safe_shutter = 1 / (focal_length × crop_factor) × 2^IBIS_stops
predicted_shake = shutter_speed > safe_shutter
```
Per-camera IBIS:
- X100VI → 4 stops
- X Half → 0 stops
- Z6III → 5 stops

**Signal B — pixel rule (measured shake):**
- Compute gradient direction histogram over the image. **Camera shake clusters edges in one direction** (the motion direction).
- `direction_consistency` = how peaky the histogram is, in `[0, 1]`.
- FFT magnitude spectrum, look for elongated lobes (`fft_elongation`).
- Penalties:
  - `consistency > 0.7` → −50
  - `consistency > 0.5` → −35
  - `consistency > 0.3` → −15
  - `fft_elongation > 3.0` → −30

**Fusion:** the two signals output independently. If they conflict (EXIF says safe, pixel says shaky) the analyser flags `conflict=True` so Phase 3 can learn from it. The single boolean `shake_detected` is true if either signal is severe.

**Risks:**
- **`direction_consistency` mistakes legitimate scene structure for shake.** A photo of a stair railing has highly directional gradients; the algorithm reads "motion in 45°" when there is none.
- **The IBIS stop counts are one-way.** They assume the user had IBIS on. There's no EXIF tag for "IBIS active or not" that's portable across the three cameras.
- **No discrimination between subject motion blur and camera shake.** A panning shot of a cyclist scores as shake.

---

## 3.5 Auto-cull rules — `_compute_auto_decision`

**File:** `backend/main.py:1249–1297`

This is the function that turns scores into K/M/R for the auto-cull modal and the bulk operations. Reads thresholds fresh from `settings` on every call (no caching).

**Flow (in order):**

```
1. Instant-reject rules (overrides any model):
   - eyes_open == False AND reject_closed_eyes → "reject"
   - face_sharpness < face_sharpness_floor (default 20)
       AND sharpness_score >= 60 (frame is sharp; only the face is soft)
       AND reject_soft_face → "reject"

2. Personal model path (when personal_model.ready):
   - personal_score >= keep_threshold (default 70)   → "keep"
   - personal_score >= maybe_threshold (default 45)  → "maybe"
   - else → "reject"

3. Threshold fallback (when personal model not ready):
   - sharpness_score < fallback_sharpness_floor (default 40)
       AND reject_blurry_frame → "reject"
   - overall_score >= fallback_keep (default 60)     → "keep"
   - overall_score >= fallback_maybe (default 40)
       AND sharpness_score >= 60                     → "maybe"
   - else → "reject"
```

**Settings registry** (`backend/main.py:1649–1676`):
- `_NUMERIC_SETTINGS` — 9 thresholds with type + min/max + default.
- `_BOOL_SETTINGS` — 4 toggles (`reject_soft_face`, `reject_blurry_frame`, `reject_closed_eyes`, `prefer_sidecar_preview`).

The frontend `SettingsModal` uses these registries to render the threshold UI declaratively, and the backend `POST /settings` validates against them.

**Risks:**
- **The "frame sharp but face soft" reject rule (rule #2 in §1)** is an arbitrary heuristic. Why `face_sharpness < 20` and `sharpness_score >= 60`? Those are vibes, not data. A reviewer should question whether this rule is justified at all when the user can see both numbers in the DetailView.
- **The personal-model-vs-threshold mode is binary.** Either the personal model is "ready" (≥20 decisions) and *only* the personal model decides, or it's not ready and *only* the threshold rules decide. There's no blending — e.g., "use the personal model with low confidence and fall back to thresholds for ambiguous cases."
- **No "is this model better than the thresholds?" check** before switching to personal-model mode at decision 20.

---

## 3.6 Personal model — math (covered in `02-models.md`, summarised here)

```
features = [17 floats from feature_extractor]
delta_raw = pipeline.predict(features)            # ≈ [-1, +1]
delta = clamp(delta_raw × 25, -25, +25)
personal_score = clamp(overall_score + delta, 0, 100)
```

The full feature list with semantics is in `features.json`.

The pipeline is `SimpleImputer(mean) → StandardScaler → GradientBoostingRegressor(n_estimators=200, max_depth=3, lr=0.05, subsample=0.8)`.

`MIN_DECISIONS = 20` enforced in `train()`.

`predict_batch()` is read-through cached in a `dict[image_id, Optional[float]]`. Cache is cleared on `train()` and on per-image re-analysis.

---

## 3.7 Where to question the scoring stack

Open questions a peer reviewer is invited to push on:

1. **Why is `overall_score` not just `sharpness_score`?** Exposure is recoverable in RAW; sharpness isn't. Is the 0.35 weight earning its keep, or is it noise?
2. **Why isn't TOPIQ in `overall_score`?** It's a perceptually-trained score with much better noise/banding sensitivity than the deterministic Phase 1 pipeline. The author kept it out to preserve Phase 1's explainability — is that the right priority?
3. **Why a single `sharpness_weight` instead of per-camera weights?** The Z6III is much more sensitive to shutter speed than the X100VI; their sharpness *distributions* differ.
4. **Should `personal_score` be an additive delta or a learned score that *replaces* `overall_score`?** Additive prevents catastrophic re-ordering on small training sets, but caps the model's authority artificially.
5. **The auto-cull thresholds are user-tunable but the *formulas* aren't.** Should the user be able to choose, e.g., `personal_score = 0.5 × overall + 0.3 × aesthetic + 0.2 × iqa` directly?
6. **Burst clustering uses `overall_score` for hero selection** — but `overall_score` ignores aesthetic + IQA. The "best of group" might miss the aesthetically strongest frame.
