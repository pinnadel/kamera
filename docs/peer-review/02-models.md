# 02 — Models

This file inventories every ML model the app loads, plus the deterministic algorithms that fill the gap. For each model: what it is, where the weights live, the input pipeline, the output range, the integration code, and the **calibration risks** the author has identified or suspects.

Per-model summary table:

| # | Model | Phase | Size | Device | Output | Loaded from |
|---|---|---|---|---|---|---|
| 1 | MediaPipe FaceLandmarker | 2 | 3.6 MB | CPU | 478 landmarks + blendshapes | `data/models/face_landmarker.task` |
| 2 | MediaPipe BlazeFace (short-range) | 2 | 224 KB | CPU | bbox only | `data/models/blaze_face_short_range.tflite` |
| 3 | TOPIQ (no-reference IQA) | 2 | ~100 MB | **CPU pinned** | scalar [0, 1] → [0, 100] | `~/.cache/pyiqa/` |
| 4 | CLIP ViT-L/14 (LAION backbone) | 2 | ~890 MB | MPS/CUDA/CPU | 768-dim embedding | `~/.cache/huggingface/openai/clip-vit-large-patch14` |
| 5 | LAION Aesthetic Predictor V2 (linearMSE head) | 2 | 3.7 MB | MPS/CUDA/CPU | scalar [1, 10] → [0, 100] | `~/.cache/huggingface/camenduru/improved-aesthetic-predictor` |
| 6 | SigLIP base (`siglip-base-patch16-224`) | 2 | ~300 MB | MPS/CUDA/CPU | 768-dim L2-normed embedding | `~/.cache/huggingface/google/siglip-base-patch16-224` |
| 7 | LM Studio vision model (user-supplied) | 2 (opt-in) | varies | varies | 2–3 sentence prose | `localhost:1234` (OpenAI-compat API) |
| 8 | scikit-learn `GradientBoostingRegressor` | 3 | <1 MB | CPU | scalar [-1, +1] → ±25 delta | `data/models/personal_model.pkl` (after first train) |

---

## 1 — MediaPipe FaceLandmarker (Phase 2, primary face detector)

**Code:** `phase2_quality/face_analyzer.py`

**Why it's here:** Face quality (eyes open? face soft? face centred?) is decisive in portrait culling. MediaPipe's blendshape output gives an eye-closure score directly without needing a custom classifier.

**Input pipeline:**
1. Image downscaled to ≤2000 px on the longest side.
2. Pass 1: standard confidence threshold (0.5) on the raw RGB array.
3. Pass 2 (only if pass 1 finds zero faces): CLAHE contrast-boosted copy, confidence 0.3. Recovers backlit + low-contrast faces.
4. Final fallback: BlazeFace short-range (bbox only — no eye data).

**Outputs (per face, aggregated by `analyze_faces_array`):**
- `face_count` (int)
- `face_sharpness_score` — Laplacian variance computed *only on the face crop*, normalized into 0–100 by the same scale as global sharpness.
- `eyes_open` (bool) — derived from blendshapes `eyeBlinkLeft` + `eyeBlinkRight`. Threshold: closed if mean blink > 0.4.
- `eye_openness_ratio` (float, 0–1) — `1 − mean(left_blink, right_blink)`.
- `face_size_ratio` — bbox area / total image area.
- `face_center_offset_x`, `face_center_offset_y` — (-1, +1), (0, 0) is dead-centre.

**Calibration risks:**
- **Eye threshold of 0.4 is a single hard cutoff.** Real photos have squinting, smiling, partial blinks. The author observed false-positives ("eyes closed!") on ~1 in 50 portraits; the **`reject_closed_eyes`** instant-reject toggle defaults *off* for exactly this reason. A reviewer should question whether a soft probability would be better than a bool at the data layer.
- **No multi-face aggregation policy.** When `face_count > 1`, the analyser averages — but for a portrait of two people where one blinked, "mean blink = 0.5" gives a misleading single number. Currently mitigated by surfacing `eyes_open` as the worst case across faces (line ~190 of `face_analyzer.py`).
- **No quality measure of detection confidence itself.** A 0.3-confidence pass-2 detection looks identical downstream to a 0.95-confidence pass-1 detection.
- **MediaPipe's training distribution skews adult, well-lit, frontal.** Infants, side-profiles, heavily backlit faces are where the BlazeFace fallback kicks in — and those faces lose all eye/blendshape data.

**Failure modes handled:**
- Both detectors return zero faces → all face columns set to `NULL` in DB. Downstream UI hides the face section entirely.
- MediaPipe import error → silent skip, `face_detected=0`. Logged to `data/app.log`.

---

## 2 — TOPIQ (Phase 2, no-reference perceptual IQA)

**Code:** `phase2_quality/iqa_scorer.py`. Loaded via `pyiqa.create_metric('topiq_nr')`.

**Why it's here:** Sharpness alone misses noise, banding, mild motion blur, halo artefacts. TOPIQ is trained on human MOS ratings and gives a single scalar that correlates with "looks good to a person".

**Input pipeline:**
1. RAF/NEF decoded via rawpy (or HE\* fallback to embedded JPEG).
2. Downscaled to 1024 px on the longest side (constant `_MAX_SIDE = 1024`).
3. Converted to PIL RGB, then to the `pyiqa`-expected tensor.

**Output:**
- Raw TOPIQ score in roughly `[0, 1]` (sometimes drifts above 1 on extremely high-quality images — clamped).
- Clamped + scaled to `[0, 100]` for storage in `images.iqa_score`.

**Threshold mapping in UI (`frontend/src/ui/format.js::iqaLabel`):**
- `≥ 75` → "Excellent"
- `≥ 55` → "Good"
- `≥ 35` → "Fair"
- `< 35` → "Poor"

**Calibration risks:**
- **CPU-pinned by necessity.** `phase2_quality/iqa_scorer.py:25–38` pins TOPIQ to CPU because its `adaptive_avg_pool2d` with non-divisor kernels does not run on Apple's MPS backend. This costs ~1.5–3 s per photo on M-series. A reviewer should question whether a faster IQA (BRISQUE, NIQE, MANIQA) would be a defensible substitution, given that "perceptual quality" is one of two main perceptual signals into the scoring formula's *future* expansion (it isn't in `overall_score` today — see §`03-scoring.md`).
- **Trained on cleaned, mostly-JPEG datasets (KonIQ-10k, etc.).** Behaviour on demosaiced RAW is undocumented. Author has not validated against a held-out RAW set.
- **The 1024-px downscale destroys fine-grain noise that the model was *trained* to detect on JPEG.** Smaller downscale = faster + safer for memory, but possibly a category mismatch.
- **No A/B with a second IQA model.** Trust in TOPIQ is by reputation, not by local validation.

---

## 3 — LAION Aesthetic Predictor V2 (Phase 2, "is this beautiful?")

**Code:** `phase2_quality/aesthetic_scorer.py`

**Architecture (two-stage):**
1. **Backbone:** OpenAI CLIP ViT-L/14 (~890 MB, downloaded from HF). Produces a 768-dim image embedding.
2. **Head:** a tiny MLP trained on AVA + LAION human ratings (the "linearMSE" version):
   - `Linear(768 → 1024)` → Dropout(0.2) → `Linear(1024 → 128)` → Dropout(0.2) → `Linear(128 → 64)` → Dropout(0.1) → `Linear(64 → 16)` → `Linear(16 → 1)`

**Critical detail:** the CLIP embedding is **L2-normalised before the head**. Without this, raw CLIP embeddings produce out-of-range scores like ~−13 instead of ~3–8. The original LAION repo did this; some forks dropped it; this code restores it explicitly at `aesthetic_scorer.py:199–200`.

**Input pipeline:**
- RAW or JPEG decoded; downscaled to 512 px longest side; L2-normed CLIP embedding; head; clamped to `[1, 10]`; rescaled to `[0, 100]`.

**Why the camenduru mirror?** The original LAION account on HuggingFace was suspended in 2025. `camenduru/improved-aesthetic-predictor` mirrors the same `sac+logos+ava1-l14-linearMSE.pth` weights. The author considered switching to `discus0434/aesthetic-predictor-v2-5` but that release is **AGPL** — incompatible with any future commercial intent (see `project_model_licenses` in author memory).

**Calibration risks:**
- **The aesthetic distribution is "generic Instagram beauty."** AVA + LAION-aesthetic skew toward warm-toned landscapes, well-lit portraits, and predictable composition. The model **scores documentary / street / harsh-light photography badly** — categories where the user might *want* to keep an image precisely because it's "ugly." This is the strongest argument for the Phase 3 personal-taste delta layer existing at all.
- **CLIP ViT-L/14 has known biases** around whiteness, age, gender expression, indoor/outdoor lighting — those propagate into the aesthetic score.
- **The MLP is tiny.** Five Linear layers on top of a frozen embedding cannot learn anything CLIP didn't already encode. So the aesthetic head essentially **re-projects CLIP**, which is itself a controversial-quality signal for this kind of task.

**Where the score is used:**
- Stored as `images.aesthetic_score` (0–100 scalar).
- **Not** included in `overall_score` (which is only sharpness + exposure).
- Feature index 5 of the Phase 3 17-vector — fed to the personal model.

---

## 4 — SigLIP (Phase 2, near-duplicate / burst clustering)

**Code:** `phase2_quality/similarity_scorer.py`. Model: `google/siglip-base-patch16-224`.

**Why SigLIP and not CLIP?** The author wants embedding similarity to be **bounded and uniform across the unit hypersphere** so a single cosine threshold (0.90) gives stable cluster behaviour. SigLIP's sigmoid loss produces tighter, more uniform embeddings than CLIP's contrastive loss for this use case.

**Input pipeline:**
- Decoded → downscaled to 384 px longest side → processor's 224×224 resize → vision encoder → 768-dim float vector → **L2-normalised** (so cosine = dot product).

**Storage:** as compact JSON `[0.0123, ...]` in `images.embedding` (TEXT). ~5 KB per row.

**Clustering algorithm (`similarity_scorer.group_by_similarity`):**
1. Stack all embeddings into an `(N, 768)` matrix.
2. Cosine similarity matrix = `M @ M.T` (already normalised).
3. Union-Find: merge any pair with `sim >= threshold` (default 0.90; UI slider 0.80–0.99).
4. Return only groups of size ≥ 2.

**Hero selection (which photo is "best of group"):**
1. The user can override via `POST /group-hero` (stored as a `settings` row keyed by group signature).
2. Otherwise: highest `overall_score`, ties broken by `iqa_score`.

**Calibration risks:**
- **One global threshold for all clusters.** A wedding sequence at 0.90 might split correctly into "same scene" but a landscape sequence may merge unrelated photos that share sky+horizon embeddings. Per-scene thresholds aren't supported.
- **Embeddings are computed once and stored.** If the user re-orients a Fuji X Half (which produces vertical 17×24 mm RAFs) the embedding doesn't update without re-analysis.
- **No deduplication beyond pairs.** Three near-identical frames cluster correctly via Union-Find, but the *order* in the strip is hash-order, not chronological — see `04-ux.md` § GroupStrip for the consequences.
- **Embedding is excluded from `GET /images`.** A grid of 500 photos × 5 KB = 2.5 MB JSON over the wire. The cluster computation happens server-side via `GET /similarity-groups`.

---

## 5 — LM Studio narrative explanation (Phase 2, optional)

**Code:** `phase2_quality/llm_explainer.py`

**Architecture:** the app does **not** ship its own LLM. It hits an OpenAI-compatible API at `localhost:1234/v1` (LM Studio's default). Whatever vision model the user has loaded is what they get.

**When it runs:**
- **Never** automatically during analysis. Only on `POST /generate-explanation` from the DetailView "Generate explanation" button (or auto-generate-on-open if the Display setting is enabled).
- Times out silently after 30 s. Returns `None` on any error — analysis is unaffected.

**Prompt:** image (base64 JPEG of the preview) + camera + EXIF + scores + face signals + instruction "2–3 sentences, plain prose, reference actual numbers."

**Calibration risks (for the *integration*, not the model):**
- **Whatever the user's model says is what gets stored.** No moderation, no schema check, no length cap on the persisted text.
- **The prompt leaks scores.** The narrative will agree with the numbers because we showed it the numbers — it's a confirmation bias machine. Useful for "explain why this scored low," misleading for "is this actually good." The author considers this a known feature, not a bug, but a reviewer should note it.

---

## 6 — Personal model (Phase 3, `GradientBoostingRegressor`)

**Code:** `phase3_learning/personal_model.py` + `phase3_learning/feature_extractor.py`.

**Pipeline:**
```
SimpleImputer(strategy='mean')
  → StandardScaler()
  → GradientBoostingRegressor(
        n_estimators=200,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )
```

**Label mapping:**
- `keep`  → `+1.0`
- `maybe` → `0.0`
- `reject` → `-1.0`

**Feature vector (17 dims, exact order — see `features.json`):**
1. `sharpness_score`
2. `exposure_score`
3. `overall_score`
4. `iqa_score`
5. `aesthetic_score`
6. `highlight_clip_pct`
7. `shadow_clip_pct`
8. `shake_detected`
9. `face_detected`
10. `face_count`
11. `face_sharpness_score`
12. `eyes_open`
13. `eye_openness_ratio`
14. `face_size_ratio`
15. `focal_length_mm`
16. `aperture`
17. `iso`

**Score formula:**
```
delta = clamp(model.predict(features) * 25, -25, +25)
personal_score = clamp(overall_score + delta, 0, 100)
```

**Why "delta over overall" instead of "predict the score directly"?**
- Anchoring the personal score to the technical score keeps the Phase 1 ground truth visible.
- A user who has only made 20 decisions has a fragile model; capping at ±25 prevents a small training set from violently re-ordering the grid.
- The author can show the *delta* in the UI (`LearnedSignals` stacked bar in DetailView) — "the model thinks +12, mostly driven by face sharpness and aesthetic score" — which is interpretable.

**Constraints:**
- `MIN_DECISIONS = 20`. `train()` raises `ValueError` below this.
- No sample-weight decay. A decision from week 1 weights identically to a decision from today.
- The pickle is loaded with a `n_features_in_` schema check; if a future migration adds a feature, old pickles are rejected and the user re-trains.

**Calibration risks (the big ones):**
- **The model is untested at scale.** As of 2026-05-04 the author has not actually trained on a real corpus of his own decisions. The pipeline runs end-to-end on synthetic data. **A reviewer cannot evaluate predictive quality from this codebase.**
- **17 dims, GBR with 200 trees, ~20 samples.** Severe risk of overfit. `subsample=0.8` is the only regularisation lever pulled; no `max_features`, no early stopping.
- **`overall_score` is an input feature *and* the additive base.** This double-counts sharpness + exposure: the model can learn "I like overall_score" and the formula then adds that on top of the raw `overall_score`. The cap at ±25 papers over this but doesn't fix it.
- **Eye-openness and face-detected are correlated boolean features.** The Imputer's mean strategy converts NaN to a population average that is *not* "no face" — it's "average face-having-ness." A row with `face_detected=NULL` will get an imputed `eye_openness_ratio = 0.7` or so, which the model reads as "eyes mostly open" — wrong.
- **Maybe = 0.0 in label mapping.** This treats Maybe as exactly halfway between Keep and Reject. In practice "Maybe" often means "I genuinely cannot tell" — a label of `nan` (and ignoring those rows) might be more honest. The cost: many users only have a few real decisions and discarding "Maybe" rows could push them under MIN_DECISIONS.

---

## Phase 1 algorithms (deterministic, not ML)

For completeness — these are described in detail in `03-scoring.md`. Brief inventory:

| Algorithm | File | Output |
|---|---|---|
| Tile-p90 multi-measure sharpness fusion | `phase1_technical/batch_sharpness_analyzer.py` | 0–100 score |
| Histogram-based exposure | `phase1_technical/exposure.py` | 0–100 score + clipping % |
| EXIF + gradient + FFT camera shake | `phase1_technical/camera_shake.py` | bool + diagnostic dict |
| Timestamp burst clustering (legacy) | `phase1_technical/burst_detection.py` | cluster IDs |

These are **intentionally deterministic and explainable.** The user-facing scoring formula (`overall_score`) uses *only* these — no ML. The Phase 2 ML scores live alongside as supplementary signals into Phase 3.

---

## Where the model stack is strained

1. **No held-out validation.** The author has not built a "ground truth" set of his own decisions and measured Spearman ρ between `personal_score` and human labels. Without this, the Phase 3 layer is a black box even to the author.
2. **TOPIQ + LAION + SigLIP all share an unmodifiable training distribution.** Three ML signals, three "trained on internet photography" priors — diversity is illusory.
3. **The aesthetic head is a known-poor signal for serious photography** but is given equal feature weight as `face_sharpness_score` in the Phase 3 model. Implicit equal-weight assumption is wrong.
4. **No guardrails on personal model output.** If Phase 3 predicts `+25` on every photo, the grid sort becomes meaningless. There's no "is this model actually informative?" check (e.g., predict-on-train residual, hold-out R²).
5. **The narrative LLM sees the scores.** It cannot disagree usefully because we anchored its prompt to the numbers we want it to explain.
