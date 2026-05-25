# KaMeRa — FAQ

A running list of "how does this actually work?" questions about the app's
behaviour. Living document — entries get revised when shipped features change
the answer. If a feature ships that invalidates an entry, update or remove it
in the same commit.

**Convention:** each entry starts with a short summary, then drills into the
mechanics. Cross-link to source files using `[file.py:line](relative/path.py#Lline)`
so curious readers can verify against current code instead of trusting prose.

---

## Scoring & auto-culling

### What role do the AI Quality values play in auto-culling?

**Short answer:** Most AI Quality signals influence auto-cull *only* through
the trained personal model (as four of its 31 input features). Below readiness
they're informational. Background distraction is the one exception — it has a
direct instant-reject rule outside the model.

The "AI Quality" panel in DetailView shows four signals:

- **TOPIQ** (`iqa_score`) — perceptual quality
- **TOPIQ-IAA Aesthetic** (`aesthetic_score`) — composition / mood appeal (AVA-trained)
- **Smile + mouth-open** (`smile_score`, `mouth_open_score`) — expression from MediaPipe blendshapes
- **Background distraction** (`background_distraction_score`) — SigLIP-derived "is the backdrop busy?"

**Direct rules (always on, regardless of personal model):**

- Background distraction has an instant-reject threshold. When
  `reject_high_background_distraction` is enabled and the score crosses
  `background_distraction_ceiling` (default `0.65`), face photos are auto-rejected
  ([decisions.py:161](backend/routers/decisions.py#L161)). Faces only — clean-background
  landscapes don't trip this rule. Default was lowered from 0.85 in PR2 after
  SigLIP-2's narrower sigmoid distribution made 0.85 effectively unreachable;
  a 60-photo audit verified 0.65 trips on ~2% of photos (the genuinely busy tail).

**Indirect — only when the personal model is "ready":**

- The model is "ready" when it has ≥50 training samples *and* validated to
  beat the threshold baseline by the bootstrap margin
  ([decisions.py:171](backend/routers/decisions.py#L171)).
- Once ready, IQA / aesthetic / smile / mouth-open / background distraction all feed
  the 31-dimensional feature vector that produces `personal_score`. Auto-cull
  then compares `personal_score` to `keep_threshold` / `maybe_threshold`
  ([decisions.py:177](backend/routers/decisions.py#L177)). The full feature
  list (with column-by-column rationale) lives in
  [feature_extractor.py:51](phase3_learning/feature_extractor.py#L51).
- Below readiness, auto-cull falls back to `overall_score = sharpness×0.65 + exposure×0.35`.
  In that mode **IQA, Aesthetic, Smile, and Mouth-open play no role at all** —
  they're rendered in the UI so you can see what the model is learning, but
  they don't affect any automated K/M/X assignment.

Net: AI Quality is a *taste signal*, not a *cull rule*. It earns its vote by
training the model.

### What's the difference between `overall_score` and `personal_score`?

- `overall_score` is the deterministic technical score:
  `sharpness × w + exposure × (1 - w)`, with `w` defaulting to `0.65`. Identical
  for everyone, never changes after analysis.
- `personal_score` is `overall_score + delta×25` (clamped 0–100), where `delta`
  comes from a Gradient Boosting Regressor trained on your K/M/X decisions
  (31 features — see
  [feature_extractor.py:51](phase3_learning/feature_extractor.py#L51)).
  Always rendered after the model loads, but only *drives* auto-cull once the
  model has crossed the readiness gate.

### When does the personal model start influencing auto-cull?

After ≥50 decisions **and** validated to beat the threshold baseline. Below 50
the banner is informational only. The `model_status` field on `GET /model-info`
flips from `learning` → `ready` at the gate.

### What changes when I retrain?

Personal-score cache invalidates entirely. Next `GET /images` recomputes scores
in batch via `predict_batch()`. New decisions made before the retrain that
hadn't been folded in yet are now part of the model.

### What does the "Uncertain — routed to Maybe" line in Auto-cull mean?

Each retrain fits two models in parallel: the main pipeline that drives
`personal_score` everywhere in the UI, *and* a 20-member ensemble of
sub-sampled GBR pipelines whose prediction spread proxies "how confident is
the model on this photo?". When the per-photo standard deviation across the
ensemble is high AND the score lands within ±std of a decision boundary
(Keep cutoff or Maybe cutoff), Auto-cull routes the photo to Maybe with reason
`uncertain` instead of committing to a hard Keep or Reject it might get wrong.

Two settings in Settings → Model → Personal-model cutoffs:

- **Route uncertain decisions to Maybe** (toggle, default on) — turn the
  routing off if you'd rather see the model's hard call.
- **Uncertainty threshold** (default `8.0`, in personal_score units) — the
  minimum std_dev that counts as "uncertain". Roughly one tier of
  disagreement among ensemble members. Higher = more confident the model
  has to be before routing kicks in; lower = more photos routed to Maybe.

Instant-reject rules (closed eyes, blurry frame, etc.) fire before this
routing — a closed-eyes photo with high uncertainty is still rejected, not
routed. The routing only fires when the personal model is in "Ready" state
([decisions.py:208](backend/routers/decisions.py#L208)). Below readiness the
ensemble isn't consulted.

---

## Files & workflow

### What gets written to disk on every K/M/X decision?

1. **The RAW + sidecar move** to `_Keeps/`, `_Maybes/`, or `_Trash/` (or
   per-folder overrides). The Fuji `.HIF` companion moves with the RAW when
   present.
2. **`XMP:Rating` (5/3/1) and `XMP:Label` (Green/Yellow/Red)** to a `.xmp`
   sidecar next to the photo, so Lightroom Classic / Capture One / Bridge /
   Luminar Neo see the decision on import. Toggle in Settings → Display
   ("Write decisions to XMP sidecars", default ON). Best-effort — XMP failures
   log but never abort the decision.
3. **A row in `decisions`** + a frozen feature snapshot in `training_samples`
   (durable, no FK to `images`, survives Clear Analysis).

If `reject_to_system_trash` is enabled, the reject branch routes through
`send2trash` instead of `_Trash/` and skips the XMP write (the file is no
longer addressable).

### What happens if I move my photos in Finder after analyzing?

The DB row's `file_path` is now stale. Next decision attempt returns 409
(file not found). The `training_samples` row survives because it's keyed by
UUID, not path — your taste history is safe even if the photos vanish from
their analyzed location. UUID-on-ingest XMP write is on the backlog; until it
ships, UUIDs only live in SQLite.

### Does Clear Analysis delete the trained model?

No. `POST /clear` and `POST /clear-folder` only wipe SQLite rows + cached
preview JPEGs. Model caches (`~/.cache/pyiqa/`, `~/.cache/huggingface/`,
`data/models/personal_model.pkl`) are never touched. Training samples
(`training_samples` table) also survive Clear Analysis by design. To wipe
the personal model, see the next entry.

### How do I reset the personal taste model or the Dashboard?

Settings → Model tab → Danger Zone has two separate destructive buttons,
each with a two-step confirm:

- **Reset personal taste model** — `POST /reset-personal-model` in
  [backend/routers/model.py:214](../backend/routers/model.py#L214). Wipes
  `training_samples`, `pairwise_comparisons`, and
  `data/models/personal_model.pkl`, then calls
  [PersonalModel.reset()](../phase3_learning/personal_model.py#L602) to
  drop the in-memory pipeline + ensemble + score cache. Banner returns to
  0 / 50. Your K/M/R decisions on photos are kept.
- **Reset dashboard** — `POST /dashboard/reset` in
  [backend/routers/dashboard.py:325](../backend/routers/dashboard.py#L325).
  Wipes `shooting_log` only — clears the Dashboard's camera, lens,
  film-sim, focal-length, aperture, ISO, and shots-per-week cards. Does
  NOT touch `training_samples`, so the model and its decision-history
  cards keep their data. `shooting_log` rebuilds as you analyze new
  photos.

The two are independent because the Dashboard sources from both
`shooting_log` (shooting cards) and `training_samples` (decision cards).
Resetting only one preserves the other half of the dashboard.

### Why is a freshly-analyzed card briefly empty before its thumbnail appears?

RAW previews are generated **lazily** on the first `/previews/<id>` request,
not during analyze. So a card can appear in the grid (because its row hit
`/images`) a second or two before its `<img>` actually loads — during that
window the trailing skeleton slot has already collapsed but the per-card
demosaic is still in flight (~1–3s for a RAF). To avoid a dark empty tile,
[ImageCard.jsx:63-72](../frontend/src/views/ImageCard.jsx#L63-L72) keeps a
shimmer underlay until the `<img>` fires `onLoad` (or `onError` on missing
files). The `loading="lazy"` attr is preserved so offscreen cards still
defer; preview generation runs at `GET /previews/{image_id}` in
[backend/routers/analysis.py:868](../backend/routers/analysis.py#L868).

### How do I diagnose a slow analyze batch?

The backend logs one INFO line per finished photo:
`analyze — [42/152] DSCF0131.RAF done in 87.3s` (see
[backend/routers/analysis.py:638-645](../backend/routers/analysis.py#L638-L645)).
Tail `data/app.log` during the batch to spot outliers — anything above the
[baseline](../docs/PROJECT_WIKI.md) (~17s/RAF, ~1.7s/JPG) is worth
investigating. `GET /analyze-progress` also exposes a live
`elapsed_seconds` field that grows in real time while the batch runs and
freezes at the final wall-clock total once `running` flips to `false`.

---

## Grouping & comparison

### What's the difference between Bursts and People modes in groups?

- **Bursts** uses SigLIP image embeddings, a capture-time gap split (default
  60s), and a layered union gate: face-identity → scene-tag blocker → color
  histogram → SigLIP cosine. The layered gate prevents two visually similar
  photos from different scenes (e.g. baby-in-pastels indoor vs outdoor) from
  fusing into one cluster even when SigLIP cosine alone would bridge them.
  See [phase2_quality/similarity_scorer.py](../phase2_quality/similarity_scorer.py)
  `group_by_similarity` and `_pairwise_can_union`.
- **People** uses dedicated **FaceNet identity embeddings** (`face_embedding`
  column, schema v38), with no time-gap segmentation. Same person clusters
  across moments / lighting / scenes. Photos analyzed before v38 won't appear
  in People groups (the Tab settings nudges a re-analysis for those).

### How do I manually move photos in and out of groups?

Auto-clustering won't always get it right — sometimes a group fuses two
scenes, sometimes a photo that obviously belongs to a burst was missed.
You can fix both with multi-select + move:

- **In the grid**: cmd/ctrl+click any photo to enter select mode. Shift+click
  another tile to range-select. Drag any selected photo onto a target
  GroupTile to fold them in, or use "Move into group…" in the action bar
  (then click a target). "New group from selection" makes a fresh group
  from the selected photos. Esc exits select mode.
- **In the loupe**: the left rail shows all your groups as compact tiles.
  Click "Select" in the bottom pill (or cmd+click a tile), then drag photos
  onto a rail tile to move them into that group, or drop them on the coral
  "Make singletons" zone at the bottom of the rail to remove them from the
  current group. "Split into new group" peels the selected photos off into
  their own burst.

Manual moves persist in `images.manual_group_id` and survive every
re-cluster: anchored photos never blend back into auto-clusters, and
photos anchored to the same id are guaranteed grouped together regardless
of cosine. See [backend/routers/model.py](../backend/routers/model.py)
`set_manual_group` and
[phase2_quality/similarity_scorer.py](../phase2_quality/similarity_scorer.py)
`group_by_similarity` (manual-anchor reconciliation phase).

### The grouping defaults look wrong on my library. Can I tune them?

Yes — Settings → Model → Grouping section has the Similarity threshold
(0.80–0.99, default 0.90) and Time gap (15s–10min, default 60s) sliders.
Slider changes take effect immediately and re-cluster the whole library.
This is a power-user escape; for most "this photo doesn't belong here"
cases, the manual move workflow above is faster and more legible than
hunting for a slider value that fixes one group without breaking three
others.

### What does the AI pick (amber star) actually mean?

The hero tile in a group is selected by `personal_score` when the model is
ready, otherwise by `overall_score`. When a vision model is installed in Ollama,
opening GroupLoupe fires a comparative LLM rank (see "How does AI burst ranking
work?" below) and the rank-1 photo from that overrides the score-based pick.
Amber ring + corner star in both Survey and Loupe filmstrip mark the pick.

### How does AI burst ranking work?

When you open a similarity group, the app sends up to 12 preview JPEGs in one
chat call to a local vision model (qwen2.5vl:7b by default, via Ollama) and
asks it to rank them best-to-worst with a one-line reason per photo. The
result is cached in the `burst_rankings` SQLite table keyed by the sha1 of
the sorted member-ids — so re-opening the same group is instant. Re-clustering
with a different threshold changes membership → new cache key → ranking re-runs.

For bursts larger than 12 photos, the backend pre-filters the input down to
the top-12 candidates by face sharpness → eyes-open → frame sharpness → IQA
→ aesthetic → overall score (the same priority used to pick the score-based
hero). Only those 12 go to the LLM; the rest stay in the group at their
score-based standing. The loupe surfaces this with an "AI ranked top 12 of N"
chip — click it to see which photos the model evaluated; tiles the model
didn't see get a small muted dot.

This is intentionally separate from per-photo numeric scoring: within a burst
all the technical/aesthetic scores barely move (the photos are by definition
similar). The LLM is the only tool that can compare micro-moments — peak smile
vs 80 ms before, eyes fully open vs mid-blink — because it sees all frames
in one context and reasons relatively.

See [phase2_quality/burst_ranker.py](../phase2_quality/burst_ranker.py),
[backend/group_scoring.py](../backend/group_scoring.py) for the pre-filter helper,
[backend/routers/model.py](../backend/routers/model.py) `rank_burst_endpoint`,
[frontend/src/views/GroupLoupe.jsx](../frontend/src/views/GroupLoupe.jsx) `BurstRankStatus`.

### When does AI burst ranking start running for a freshly analyzed folder?

Two triggers:

1. **Mid-batch**, every 50 photos. The polling tick that drives the progress bar also re-fetches `/similarity-groups` and POSTs the new memberships to `/prerank-groups`. So as soon as enough photos are scored to form recognisable bursts, the AI worker starts ranking them in the background — you don't have to wait for the batch to finish. See [frontend/src/hooks/usePolling.js](../frontend/src/hooks/usePolling.js) `MID_BATCH_PRERANK_EVERY`.
2. **At batch completion**, one final pass to catch bursts that only stabilised in the last 50 photos.

Re-posting growing supersets is safe — the backend's `enqueue_groups` skips any membership that already has a `burst_rankings` cache row, so the worker never re-ranks the same group.

If a burst hits an analyzed RAF or NEF whose preview hasn't been generated yet (Z6III HE\* NEFs always; RAFs whose viewer cache wasn't warmed), the ranker now generates one on the fly using the same `_generate_preview` path as `/previews/<id>` and writes it back to the cache — so the viewer benefits too. See [phase2_quality/burst_ranker.py](../phase2_quality/burst_ranker.py) `_resolve_preview_bytes`.

### How do I install the qwen2.5vl vision model?

Two paths, both end at the same result:

1. **In-app (recommended).** When the app detects no vision model is installed,
   the GroupLoupe top bar and the Settings → AI explainer panel both show a
   "Pull qwen2.5vl now (≈6 GB)" button. Click it, the download runs in a
   background thread and reports progress through the same model-status banner
   that shows SigLIP/TOPIQ-NR/TOPIQ-IAA/FaceNet downloads. The model is reused
   forever after.
2. **Terminal.** `ollama pull qwen2.5vl:7b`. Same result, no app interaction.

Users who previously had `moondream` installed can free the disk with
`ollama rm moondream` — the app no longer uses it. (Moondream was removed
because it returned prose captions instead of the structured JSON the burst
ranker needs, failing virtually every comparative-ranking call.)

Ollama itself (the daemon + CLI) is a separate one-time install via
[ollama.com](https://ollama.com) or `brew install ollama` — not bundled with
this app, same as Python isn't bundled with start.sh. After Ollama is on PATH,
the app's `ensure_daemon_running()` auto-starts the daemon on every launch.

### What are the four "Content signals" in DetailView?

Four SigLIP zero-shot perception axes scored from the cached image embedding
(no extra decode — same matrix-multiply trick the scene tagger uses):

- **Subject prominence** — how clearly the subject reads as the focal point
- **Background** — how clean/uncluttered (higher is better; bar inverts the raw
  `background_distraction_score` so the visual ladder reads "higher = better"
  across all four)
- **Eye contact** — only shown when a face is detected; whether the subject is
  looking at the camera (distinct from `eyes_open`)
- **Decisive moment** — fleeting gesture/action vs static/posed

All four feed the personal model's 31-dim feature vector
([phase3_learning/feature_extractor.py:_COLUMNS](../phase3_learning/feature_extractor.py)),
so the GBR learns to weight them against your own decisions. Opt-in instant
reject for high background-distraction lives in Settings → Model → Decision
thresholds (default OFF; only fires on photos with a detected face).

### Why does a second Python icon appear when I open the folder picker?

The folder dialog runs in a fresh Python subprocess rather than in the
backend's request handler. The reason is platform-specific: macOS Tk
requires the process main thread to show a window, and uvicorn request
handlers run in worker threads. Calling `tk.Tk()` directly from a worker
makes the dialog open invisible to the user — the Python dock icon just
spins forever waiting for an app context that never arrives.

Running the picker in a subprocess gives Tk its own main thread and its
own clean GUI app context, so the dialog actually appears. The second
dock icon is that subprocess; it disappears as soon as you choose a
folder (or cancel). See
[backend/routers/watch.py](../backend/routers/watch.py) `pick_folder`.

## DetailView

### How do I resize the filmstrip?

Grab the 1.5 px strip at the top edge of the filmstrip toolbar (the bar
with the collapse chevron) and drag up to grow thumbs, down to shrink.
Thumbs are clamped to 80–260 px. Dragging below 80 px collapses the
strip entirely; dragging back up during the same gesture re-expands at
80 px and grows from there. Clicking the chevron toggles the same
collapse, and remembers the previous expanded size in
`pca.stripThumbAtExpand` so the next un-collapse restores it. See
[frontend/src/App.jsx](../frontend/src/App.jsx) `startStripResize` and
[frontend/src/ui/Filmstrip.jsx](../frontend/src/ui/Filmstrip.jsx).

### Why doesn't the filmstrip scroll when I click a different thumb?

By design. Auto-centering fires once on initial open of DetailView
(focused thumb centers in the strip viewport). After that, clicking a
thumb, pressing arrow keys, or stepping prev/next never moves the
strip. If you want a particular thumb in view, scroll the strip
manually. The user explicitly asked for this — clicking shouldn't yank
the scroll position around. See `hasCenteredRef` in
[frontend/src/ui/Filmstrip.jsx](../frontend/src/ui/Filmstrip.jsx).

### What does F do in DetailView?

Toggles "focus mode": collapses both the side info panel AND the
filmstrip so the photo fills the viewport. Pressing F again restores
the exact prior state of both panels (each can be independently
collapsed/expanded normally — F snapshots both at toggle-on and
restores from the snapshot at toggle-off). See `toggleFocusMode` in
[frontend/src/views/DetailView.jsx](../frontend/src/views/DetailView.jsx).

### How does the click-to-zoom cycle work?

Click the photo in DetailView to enter fullscreen mode. Inside
fullscreen, each subsequent click cycles `100% → 150% → 300% → exit
fullscreen`. The 4th click takes you back to DetailView rather than
looping to 1×. Drag to pan while zoomed; trackpad two-finger scroll
also pans. Z hotkey cycles the same scales. Esc or the × button exits.
Scales defined in `FS_ZOOM_SCALES` at
[frontend/src/views/DetailView.jsx:27](../frontend/src/views/DetailView.jsx#L27).

### Why doesn't clicking the black area around the photo close DetailView?

Removed deliberately. Letterbox clicks used to dismiss the panel, which
was a frequent surprise-close when the user just meant to focus the
preview pane. Now only the X button (hover-visible, top-left of the
picture pane) or Esc close DetailView.

---

*Last updated: 2026-05-15*
