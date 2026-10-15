# KaMeRa — Changelog

Historical sprint log moved out of CLAUDE.md on 2026-05-12 to keep the project's auto-loaded context lean. Git log is the canonical source — this file is a curated narrative for skim-reading.

---

## One group view in every decided bucket (2026-09-21)

Keeps, Maybes and Rejects now draw the same group tile and it behaves the same
in all three: click peeks the whole burst in `GroupScrubPreview`, double-click
(or Enter/Space) opens it in DetailView. Keeps had no grouping at all — the
clustering pass in `displayGridItems` was gated to `reject`/`maybe`, so the one
view where every member survived was also the one where burst membership was
invisible.

**The collapse rule changed from run length to burst membership.** A photo gets
a group tile because it belongs to a multi-photo burst, not because ≥2 of its
groupmates share this bucket. A burst's lone survivor is still a window onto
its burst — and, before this, the only way to reach that burst was a chip.

**`PartnerCountChips` and `GroupPartnersOverlay` are both deleted.** The chips
("2 Keeps", "1 Maybe") counted on the tile where the rest of a burst was culled;
the peek panel already walks the whole burst and states each frame's bucket on
the frame itself, in the same palette. Two surfaces saying one thing, and on a
lone survivor the chip doubled as the only entrance. The tile footer carries the
fact instead — "Group · 1 of 4 keeps" — and the panel plus DetailView are now
the only group surfaces in the decided views. That removed 481 lines of modal.

Consequences worth naming:

- **DetailView's grid filmstrip no longer skips decided groups.** It used to
  render them as a dimmed, unclickable placeholder, which was survivable while
  only Maybes/Rejects collapsed; once Keeps collapses too, skipping would walk
  past most of the view. A decided group is now an ordinary stop that selects
  its hero — no loupe affordances, because decided photos have nothing to rank.
  `selectedGridIdx` and `filmstripIndex` both gained a second lookup for a photo
  that is only present as a *member* of a collapsed tile, so closing DetailView
  still lands the grid cursor on a cell.
- **Collapsing the Keeps view hid ImageCard's per-photo "mark reviewed" check
  button**, so `DecidedGroupTile` carries the same action for the auto-culled
  keepers it now hides (hover, top-right, with the count). Empty in every other
  view — `needsReview()` is keep-only.
- `GroupBulkBar` stays, but only on a tile holding ≥2 members of this bucket.
  Moving "all" of one photo is just a re-cull, which the panel's K/M/R does.
- The shared group-run border overlay, `groupRun`, `groupChip` and
  `ImageCard`'s `inGroupBorder` call site are gone with it: in a decided view an
  `{type:'image'}` item is now always an ungrouped photo.

**Arrows wrap around the burst** (same day). `←`/`→` in the peek panel used to
stop dead at the ends, so re-checking the first frame after reaching the last
meant holding `←` back through the whole group. `stepIndex` grew an opt-in
`{ wrap }` and the arrow keys pass it. **K/M/R deliberately still clamp**: their
step means "judged that one, show me the next", and silently jumping to the far
end of the burst after a decision would hide which frame you just acted on.
Stopping there is the honest answer — you have reached the end of the group.


## Sort direction is reachable from the sort menu (2026-09-21)

"Oldest first" had exactly one control: the small ↓ arrow on the left of the
Sort pill. The menu itself showed `Newest ↓` and `Z → A ↓` in grey next to
each field — text that reads like a control and did nothing. Clicking `Date`
while Date was already the sort re-selected Date and closed the menu, so the
one gesture a user reaches for produced no visible change at all.

**A row is now the direction toggle for its own field.** Clicking the field
that is already active flips desc↔asc and **leaves the menu open**, so
reversing is a second click on the same row; the readouts in every row update
in place. Clicking a *different* field still selects it and closes the stack,
direction preserved — the existing pick-and-go flow is untouched, and so is
the pill arrow.

The order logic itself was never broken: `compareImages` reverses strings via
`localeCompare` and numbers via subtraction, and `shot_at` is stored as
`YYYY-MM-DD HH:MM:SS`, which sorts lexicographically. This was a missing
affordance, not a comparator bug — worth stating because the bug report read
as "sorting doesn't work".

Every row (L1 Date/Name and the L2 score metrics) also gained a `title` +
`aria-label`: the flip sentence on the active row, `Sort by <field> —
<direction> first` elsewhere. The active row's direction readout brightens to
`#cecece` so it reads as live rather than disabled, and all rows now carry
`hover:opacity-70` — the active one previously had no hover response, which
was half the reason it didn't look clickable.

[frontend/src/ui/SortPill.jsx](../frontend/src/ui/SortPill.jsx)

---

## One surface: the import is visible, and an archived photo stays (2026-09-21)

The UI half of the seam, planned in `docs/PLAN-import-ui.md` and built the
same day. The rule it follows: **Provenance is the sensor and the hands,
KaMeRa is the only screen**, and feedback volume follows *reversibility*, not
importance — modal is reserved for the irreversible.

**The import is now visible while it runs.** `usePolling` reads
`/import-status` alongside the three endpoints it already polled, and
`onImportStatus` derives the run onto whichever tab's folder it is filling.
Derived every tick, never stored: tabs are rebuilt from `GET /folders`, so
there is nothing that could resurrect a finished run as a live one. A tab
appears on its own for a moving run but **does not steal focus** — if you are
culling something else, an import arrives as a tab with a chip.

**A copy bar with a real denominator.** Unlike the watch-live bar, an import
knows its total from the first second: the card was read before the copy
started. The grid holds placeholder tiles for the photos that haven't landed,
reusing the trailing-skeleton block that already existed for running batches.
The difference between "it's running" and "it'll take this long" is the
denominator; everything else is decoration.

**`lifecycle` on every photo** — `staged` → `archived` → `backed_up`, derived
per request from one cached ledger snapshot
(`provenance_link.annotate_lifecycle`), keep-only because a maybe or a reject
never leaves for the library. `staged` is the state the old completion dialog
called "archived".

**An archived photo stops vanishing.** `sync_folder` dropped every row whose
file was missing — which is exactly what Provenance's archive move produces,
seconds later. That one predicate is why an archived photo disappeared from
KaMeRa entirely, cached preview and all. It now spares archived rows, and the
tile desaturates (400ms, slower than every other transition in the app — a
departure should be visible) and names the year folder in its footer. **No new
chip:** DESIGN.md §4.2 keeps ImageCard's vocabulary deliberately tiny, and
lifecycle is not a decision, so it does not borrow decision chrome. The folder
name is the accessible channel, the desaturation the glanceable one, and the
alt text carries it too.

**"Hide archived"** is one opt-in pill, offered only in the Keep view and only
when there is something to hide. No new IA: an archived photo keeps
`decision='keep'` and belongs in the view that answers "what did I keep". The
predicate is folded into the existing combined pass, not run as a second
flatMap — sequential passes miscount group entries.

**"Mark all reviewed" → "Release to archive".** Named for the consequence
instead of the bookkeeping. The old wording described what the database
records; what actually happens is that the photo leaves KaMeRa for the Luminar
library. That gap is how a completion dialog could claim photos were archived
while they sat in `_Keeps`.

**No hotkey for releasing** — deliberately. `a` is already `runBatch('reject')`
in GroupLoupe, and a key meaning "reject all" in one view and "irreversibly
archive" in another is a hazard. More to the point, a one-keystroke,
no-confirmation, bulk, irreversible action is the loudest possible affordance
for the most dangerous operation in the app.

Guarded by `tests/test_archived_rows_survive.py` — including the narrow half:
a genuinely deleted photo is still reaped.

---

## KaMeRa can read Provenance back (2026-09-21)

The relationship with Provenance (`~/dev/provenance`) was one-way. Its
`kamera.py` knows nine of our endpoints, starts our `start.sh` and opens our
window. We knew Provenance through two code comments. That asymmetry is why an
import is invisible here while it runs, why its result has to be shouted
through a modal dialog that holds the screen for up to half an hour, and why
an archived photo simply 404s and gets its row deleted.

**`backend/provenance_link.py`** is the return channel and the only place that
touches anything of Provenance's — read-only, no HTTP, no Python import either
way. Two sources in the `~/Pictures/.kamera/` directory both apps already
share: `ledger.sqlite` (opened `mode=ro`, so a bug here cannot corrupt another
app's database) for the durable facts, and `import_status.json` for the run in
flight.

**A file, not a port.** The flow decided it: KaMeRa only ever needs to *read*,
because the one thing it asks Provenance to do — "archive this now" — is
already expressed by moving a file into `_Keeps_reviewed`, and that contract
works. Nothing justified a server, a bound port, or a second lifecycle.

**`GET /import-status`** always returns 200. `available:false` means Provenance
isn't installed, which is a normal state rather than an error — the same gating
idea as `photos_library.is_available()` off macOS.

**`images.provenance_key` (migration v57).** `images.uuid` is a `uuid4()`
assigned per row: a handle with no derivation. It already keys
`training_samples` and `shooting_log`, the two tables built to outlive the
photo — so when an archived file's row is deleted, those samples point at an id
nothing can ever resolve again. Provenance's `exif_key` is the mirror image:
derived from the bytes, recomputable forever, with nothing hanging off it. The
column joins them. Read from the ledger, never recomputed here, so the
canonical key keeps one definition in one repo. Nullable and deliberately not
UNIQUE (a RAW and its JPEG are two rows by design). Stamped at ingest at both
insert sites, because the join is on the staged path and the first K/M/R moves
the file out from under it.

**No backfill.** The 1870 existing rows stay NULL by decision — their files are
already archived or already renamed, and a wrong key is worse than none.

**Found while building it:** `is_available()` gated on the ledger alone, but
Provenance writes its first status *before* creating the ledger, so a
first-ever import would have reported "not installed" while visibly running.
`is_available()` now accepts either artifact; the ledger reads gate separately.

Guarded by `tests/test_provenance_link.py` (14 tests, no Provenance install
needed — the ledger is just another process's SQLite file).

---

## Validating the AI winner against a real manual cull (2026-09-21, evening)

The 2026-09-21 feature was justified by "the LLM picks a different winner than
the scores in 71% of groups". Different is not better. A folder of the user's
**actual** keepers from the same shoot (83 exported photos, 75 of them in the
analysed set) made the real question answerable: when auto-cull has to choose one
frame out of a burst, whose choice lands closer to his?

**Method.** For every similarity group of 3+ that contains one of his keepers and
has a cached ranking, both methods choose from the **same candidate set** — every
frame the ranker gave a standing to. Judging the heuristic on the full group
while the AI only sees its pre-filtered subset would compare two different tasks
(the same fairness trap as `_bootstrap_validate`'s fixed-vs-calibrated cutoffs).
n = 28 groups.

| | picks a photo he kept | mean rank of his pick | median |
|---|---|---|---|
| score heuristic | 5/28 (18%) | 3.29 | 3 |
| **AI ranking** | **9/28 (32%)** | **2.07** | **2** |

Top-1 agreement is directional but underpowered — 6 groups the AI alone got
right against 2 for the scores, McNemar exact **p = 0.289**. The rank of his pick
uses all 28 pairs instead of only the discordant ones and is decisive: the AI
ranks his photo higher in **17** groups, lower in 3, tied in 8 — Wilcoxon
signed-rank **p = 0.0028 two-sided**. The AI moves his chosen frame a little over
one position closer to the top.

So the answer is yes, and the honest size of it: the winner is meaningfully
closer to his taste, but it is still only the right photo a third of the time.
Within the whole cull that is worth 4 extra groups out of 37 (24% → 35%).

**Two instrument errors caught on the way, both of which had produced a clean and
completely wrong finding first:**
- The folder is `jpeg_priority`, so 1140 of its 2397 `images` rows are unscored
  RAF stubs. An embedding query filtered to `.RAF` returned 11 of 1150 vectors
  and reported "0 of 24 rejected keepers had a similar frame survive". Against
  the scored rows the truth is **19 of 24** — auto-cull kept the moment and
  chose a different frame. Only 5 moments are actually lost.
- On a cache hit, `rank_burst` returns `evaluated_ids` as the post-dedup **rep**
  set while the `rankings` payload carries the absorbed near-duplicates too.
  Testing membership against `evaluated_ids` dropped 7 groups as "your pick was
  never ranked", including groups of 4, 5 and 6 that the top-8 pre-filter cannot
  have touched — the size that gave it away. Corrected sample: 22 → 28.

Recorded because the 71% number alone would have justified this feature on
novelty. See [docs/FAQ.md](FAQ.md) for the user-facing version.

---

## Auto-cull: one denominator, and a cull that starts itself (2026-09-21, later)

Two follow-ups from using the feature above on a real 1257-photo import.

**The progress bar counted something the modal never promised.** The modal said
"50 groups need AI ranking — about 50 min"; the bar then showed `13/72`. Both
numbers were right about different things: the estimate counted groups needing
an LLM call, the bar counted every *contested* group. The gap is the
near-duplicate outcome — measured on that folder, 78 contested groups were 55
cached + 15 near-duplicate + 8 uncached, and a near-duplicate group resolves
from embeddings we already have, for free. New `autocull_rank.resolve_cached()`
is now the single definition of "groups that cost time": it splits contested
groups into resolved-now and pending, and **both** the preview's estimate and
the run's `_autocull_progress["total"]` come from that one split. The run also
stops redoing the cheap pass. Pinned by two tests, one of which asserts the
`"total": len(pending)` call site directly.

**The preview's counts are pre-ranking, and now say so.** Measured on the same
folder (1257 analysed photos, warm cache): AI winners move **11 photos (0.9%)**
between buckets and the totals by **≤2** (keep 245→246, maybe 44→45, reject
968→966). So the honest line is that ranking changes *which* photo of a group is
kept far more than *how many* — which is what the modal now says.

(The folder holds 2397 `images` rows but only 1257 carry scores: it was analysed
in `jpeg_priority` mode, so 1140 RAF rows are unscored `skipped` stubs. Measuring
against the row count instead of the analysed count understates every rate — the
first cut of this number read 0.5% and a reject total of 2108.)

**Analyse → cull, unattended.** New per-tab toggle (Tab settings → *Cull when
analysis finishes*), armed per folder in `frontend/src/autoCullArming.js`
(localStorage, keyed by folder path — tabs are rebuilt from `GET /folders` every
launch and carry no state of their own). `usePolling` gained
`onAnalysisFinished(tab)`, fired **only** on the running:true→false edge —
deliberately not `onBatchComplete`, which also fires every 50 photos mid-batch
to warm the prerank cache, and culling a folder still being analysed would judge
each burst against however much of it had arrived.

The preview call before the run is the model gate, not a formality: if
`scoring_mode !== "personal"` the cull is **skipped with a toast**, because
unattended culling on generic quality thresholds is the case most likely to
produce rejects the user disagrees with, and by definition nobody is watching. A
Photos tab refuses server-side and arrives here as a non-ok preview, skipping the
same way. Completion reporting reuses `handleAutoCullComplete`, so a stopped run
or an AI-rank fallback reads identically whether or not a human started it.
(`handleAutoCullComplete` is declared further down `App.jsx`, so the handler
reaches it through a ref — naming it in the deps array is a temporal-dead-zone
ReferenceError on every render, i.e. a blank screen.)

Suite 714/714.

---

## Auto-cull asks the AI which photo of a group is the best one (2026-09-21)

Auto-cull already grouped bursts and already picked a winner per group — the
keep-cap pass decides which of several Keeps survives, the keep-guarantee pass
decides which photo is rescued from a group it rejected entirely. Both asked
`backend/group_scoring.py` for that winner: face_sharpness → eyes_open →
sharpness → IQA → aesthetic → overall. Meanwhile the vision burst ranker
(`phase2_quality/burst_ranker.py`) has been answering the *same question*
better since May — it sees all the frames in one context and compares them —
but only when the user opened GroupLoupe or the prerank worker drained a queue.
`_annotate_prerank_state` has promoted its rank-1 over the score hero in the
grid since then; auto-cull was the one place that never asked.

**Measured before building anything.** Of the 53 cached rankings whose photos
still exist, 41 resolve through the new lookup, and on those the LLM picks a
**different winner than the score heuristic in 29 of 41 groups (71%)** — often
inverting a large `overall_score` gap (group 8461-8463: score pick 98.7,
AI pick 71.3). This is not a tiebreaker; it changes the output.

**What was built** (`backend/autocull_rank.py`, new):
- `consequential_bursts` — only groups where the winner decides something get
  ranked: ≥3 members (the prerank worker's floor, pinned by a test) AND either
  ≥2 Keeps (keep-cap picks a survivor) or 0 Keeps (keep-guarantee promotes one).
  Exactly one Keep means the per-photo scores already settled it and a ~60s call
  would be paid for and ignored.
- `resolve_winners` — one short-lived DB connection per group, progress callback,
  stop check between groups. Returns member→winner plus a stats block.
- `promote_winner` — the AI rank's ENTIRE reach: it moves the rank-1 to index 0
  of the priority order. Keep/Maybe budget gates, near-dup rep choice and
  demotion order still run on scores.
- `_guarantee_winner` in `decisions.py` carries one guard the cap pass doesn't
  need: a rank-1 whose own reject reason is physically unrecoverable (ISO
  ceiling / reciprocal rule / blurry frame / low IQA) loses to any
  subjectively-rejected member. The LLM ranks frames against *each other*, so in
  an all-mediocre burst its #1 can be the blurred one — and the whole point of
  that pass is handing the user something rescuable.

**Cost, and the two things that make it bearable.** A fresh rank is ~60s.
`rank_burst`'s cache is shared with GroupLoupe and the prerank worker, so a
group the user already looked at is free; and the consequential filter cuts the
rest. `POST /auto-cull` stays one blocking request but now publishes into
`state._autocull_progress` (groups, not photos — ranking is the only slow part),
read by the new `GET /auto-cull/progress` and polled by the modal.
`POST /auto-cull/stop` aborts **between two ranks**, which is safe precisely
because ranking runs before a single decision is applied: a stopped auto-cull is
a no-op, not a half-culled folder, and completed ranks stay in the cache for the
next run. The prerank worker is cancelled at the start of the phase — it ranks
the same groups through the same cache, but a second concurrent qwen runner OOMs
the unified memory pool.

**The preview is cache-only.** `rank_burst` gained `cached_only=True` (returns
`not_cached` instead of calling the LLM, and skips the `near_duplicates` cache
write). A modal that ranked everything would take as long as the cull it is
previewing, and this endpoint promises to write nothing. So the preview uses the
ranks that exist and reports the rest as `ai_rank.to_rank` + an estimate; the
modal states it before the user commits. Counts for already-ranked groups are
exact, a first-time rank can land elsewhere — the honest cost of not spending an
hour inside a modal.

**Fallback is silent in the decisions, loud in the report.** No vision model,
Ollama error, a rank-1 that isn't a member, or the `near_duplicates` outcome →
that group keeps its score pick. Never raises. `ai_rank.fell_back` +
`fallback_reasons` ride the response and the completion toast says
"N groups used scores (AI rank unavailable)" — a silent downgrade to the old
behaviour would be indistinguishable from the feature working.

Setting `auto_cull_ai_rank` (bool, default on, Settings → Model → Decision
thresholds) turns it off. Reverses the earlier "don't let LLM rank-1 silently
override auto-decision yet" hold — it is no longer silent.

18 new tests in `tests/test_autocull_ai_rank.py`, incl. a call-site check that
both endpoints actually pass their winner map to both passes, and a check that
the size floor still equals `prerank._MIN_GROUP_SIZE`. Suite 688/688.

**Known, pre-existing:** 12 of the 53 live cached rankings no longer resolve —
their `members_hash` is keyed on the post-dedup rep set, and re-analysis rewrote
those embeddings, so the collapse now yields different reps. GroupLoupe misses
them identically today. Correct behaviour (the old rank described a grouping
that no longer exists); it just means those groups get re-ranked.

---

## Browser swipe-navigation gestures disabled (2026-09-20)

A two-finger swipe left/right on the trackpad triggered Chrome's history
back/forward inside the app window — a browser affordance with no meaning in a
single-screen culling app, and it takes the grid cursor and any open loupe with
it. One rule in `frontend/src/index.css`: `overscroll-behavior-x: none` on
`html, body`. The gesture only fires once a horizontal overscroll reaches the
viewport, so blocking it at the scroll root also stops it chaining out of inner
horizontal scrollers (filmstrip, tab bar) when they hit their end. The mobile
bundle already did this for both axes (`mobile.css`).

---

## People mode: one group per person, not one group (2026-09-21)

`/face-groups` put **963 of 977 faces in a single group** — every adult and
every child in the library. Two hypotheses, one measured wrong:

- **Not the crop.** The suspicion was that `embed_face` feeds FaceNet an
  unaligned, non-square crop where facenet-pytorch expects an MTCNN-aligned
  one. Tested by re-embedding a 24-face sample both ways and scoring 143
  hand-labelled adult×child pairs (certainly different people): the current
  crop already separates them at median cosine 0.124 with 2.1 % above the 0.50
  cutoff; eye-aligned crops gave 0.090 / 1.4 %. Real, and far too small to
  explain a 963-face group. Hypothesis dropped.
- **It was the linkage.** Within one age group the median pair sits at 0.561 —
  above the cutoff — and the burst grouper unions on *single* linkage: any one
  pair across two clusters merges them. At an identical 0.50 threshold on
  identical embeddings: single → largest group 963, average → 271, complete →
  86. On the labelled pairs: single merged **143 of 143**, average and complete
  **0 of 143**.

`face_identity.group_faces_by_identity()` now clusters faces with average
linkage (SciPy), and `/face-groups` calls it instead of the burst grouper.
Result on this library: **63 groups instead of 4**, largest 271, in 0.57 s.
Spot-checked visually — the 271 group is the toddler across two years of hats
and goggles, the 88 group is one adult across hoods, profiles and beard states.

- Average, not complete: complete linkage requires *every* pair to clear the
  cutoff, which splits one child across the ages they grow through.
- `time_gap_seconds` is **gone** from the endpoint rather than silently ignored.
  Segmenting by capture time is right for bursts and precisely wrong here,
  where recognising a person across different days is the point.
- Above 8000 faces it falls back to the old grouper with a logged warning
  (the pairwise matrix is O(n²)); this library is at 977.
- Guarded by `tests/test_face_identity_clustering.py`, including a fixture
  self-check and a call-site test. The bridging-face test fails on single linkage.

---

## Face sharpness joins the edge-width scale, and the bbox bug behind it (2026-09-20)

Follow-up to yesterday's Photos sharpness switch. `face_sharpness_score` was
still Laplacian variance — the same noise blindness, on the signal that drives
the soft-face instant reject and one of the model's 31 features.

- **Photos face sharpness is now 0–100 edge width**, calibrated on the same
  scale as the frame score (the skin offset is applied), so "face 30 / frame 80"
  compares like with like. Was variance: p5/50/95 of 11 / 101 / 1105 across 972
  faces, now 31 / 78 / 100. Folder keeps variance.
- **`analyze_faces_array` returned its bbox in the detector's downscaled frame**
  while every caller cropped the full-resolution array it had passed in. On a
  4032 px RAW the box sat at 0.496× its true position, so **every folder RAW face
  identity embedding cropped the wrong region** — `/face-groups` clustering has
  been running on whatever occupied the top-left quadrant. Fixed at the source;
  `tests/test_face_bbox_frame.py` fails without it.
- **The JPEG branch now always `exif_transpose`s.** The face bbox comes from
  `analyze_faces()`, which decodes upright; 52 of this library's 105 Photos JPEGs
  are orientation 6/8, so the face crop landed 90° away for those too.
- **The soft-face reject nearly died silently.** It asks "frame sharp, face
  soft?", but on Photos `sharpness_score` is now `min(frame, face)`, so its
  precondition could never hold. In personal-model mode
  `_compute_auto_decision` returns on the model's verdict long before the
  threshold-fallback `reject_blurry_frame` line — so this was the *only*
  sharpness guard, not a redundant one. It now reads `edge_width_px` (frame
  only). Measured on the corpus: **13 photos fire the rule, 2 would have
  without the fix.** Guarded by `tests/test_soft_face_rule.py`, including a
  call-site check that auto-cull still selects the whole image row.
- **Edge width is recorded for every corpus** (`images.edge_width_px` /
  `face_edge_width_px`, migration v54) and written into every new training
  sample via `feature_extractor._SHADOW_COLUMNS` — present in the JSON, not in
  the model's vector, so **no feature-schema bump and no retrain**. When there
  are labelled folder photos measured on the new scale, the camera-model
  comparison can be run without re-analysing anything.
- **47 face embeddings were recomputed** (`scripts/backfill_face_embeddings.py`,
  10 RAW + 37 orientation-6/8 JPEGs of 977). Verified with a 30-row control
  group of unaffected files: their recomputed vectors match the stored ones at
  cosine 1.000, so the recomputation is faithful to the pipeline — while the 47
  affected rows moved to a **median cosine of 0.320**, i.e. they had been
  pointing at a different region entirely. All 47 moved; none stayed above 0.9.
- **Open, and separate: People-mode clustering is collapsing.** Measured after
  the fix, and identical before it (964 vs 963), so this is not caused by the
  bbox bug: at the default 0.50 cutoff, 963 of 977 faces land in ONE group.
  13 % of all face pairs score ≥ 0.50 and the pairwise p90 is 0.564, so
  union-find chains everything together; even at 0.80 the largest group is 244.
  Likely cause is the crop, not the threshold — `embed_face` feeds FaceNet a
  MediaPipe box with 0.10 + 0.15 padding, non-square, stretched to 160×160 and
  never aligned, where facenet-pytorch expects an MTCNN-aligned square crop.
  (vggface2 is also trained on adult faces, and this library is mostly a
  toddler.) Not touched in this pass.
- **The two models have names:** *camera model* and *iPhone model* (Dashboard
  switch, ModelCard, Settings, FAQ). The internal corpus keys stay
  `folder`/`photos` — renaming them is a migration with no payoff. "Camera"
  always carries the noun "model" so it never reads as the app's own name.

---

## Photos sharpness measures edge width, not gradient energy (2026-09-19)

IMG_2332 (a blurred, grainy ISO-1000 iPhone frame) scored Sharpness 100 /
Overall 99. Cause: `batch_sharpness_analyzer` measures Laplacian/Tenengrad
*energy*, which cannot tell an edge from sensor grain — the face crop read 877
Laplacian variance against a pure-noise floor of ~867 (σ≈6.6). A synthetic blur
ladder reproduced it: σ=2 px blur scores 5 clean, 97 with σ=6 noise, 100 with σ=10.

- **`phase1_technical/edge_sharpness.py`** — edge width (`contrast / (2.507 ·
  peak gradient)`) at a fixed 1600 px long side. Flat under noise, monotone in
  blur (1.25 → 2.82 px for σ_b 0 → 3), contrast-independent. Sharpest-tiles
  aggregate (p20) for bokeh; with a face, `max(frame width, face width − 0.14)`
  so a sharp background can't rescue a soft face. Featureless frames return
  `None` → fall back to the legacy metric (47 of 1868).
- **Photos corpus only.** `analyze_photo_quality(corpus="photos")`, passed by
  `_run_batch` from `source_folder_override`. Folder keeps the legacy metric: its
  thresholds are user-tuned on the old scale and its labelled images are no
  longer on disk to re-validate against.
- **Backfill:** `scripts/backfill_edge_sharpness.py` (dry run by default; `--apply`
  backs the DB up to `pca.db.pre-edge-sharpness-backup` first). Photos share ≥95:
  60 % → 24 %; Blurry <40: 17 % → 6 % (the old metric marked low-contrast sharp
  subjects — a grey spider on grey, a peony — as blurry).
- **Measured, not argued:** Spearman vs TOPIQ-NR 0.406 → 0.456 overall (no face
  0.43 → 0.60, with face 0.48 → 0.57 — the face rule costs some agreement with a
  whole-frame metric, by design).
- Also fixed: DetailView EXIF showed raw float32 (`6.764999865652793mm`).
- **Open:** `face_sharpness_score` is still Laplacian variance and feeds the
  soft-face instant reject and the GBR — same noise blindness, not touched.
  `analyze_faces_array` returns its bbox in the ≤2000 px frame it ran on, but the
  RAW branch crops `rgb_full` with it (`_embed_face`) — folder RAW face embeddings
  likely crop the wrong region. Left alone deliberately; needs its own decision.

---

## Time axes that represent time (2026-09-21)

The last item from the critique: both timeline charts were drawing geometry
that did not match their data — the same failure as the "Unknown" bar, in a
different shape.

- **Quiet buckets are zero-filled.** Omitting empty periods is right for a
  data feed and wrong for a chart. Ten bars spanning W20–W37 drew as ten
  adjacent columns, so eighteen weeks of history looked like ten consecutive
  ones and a three-week pause was indistinguishable from no pause. Both
  `/decisions/timeline` and `/shooting/timeline` now emit every bucket between
  the first and last record. Totals are unchanged (2 971 and 5 011); the
  decisions axis went 10 → 18 buckets, shooting 22 → 38.
- **A non-zero week can no longer render as nothing.** One week held a single
  decision against a peak of 1 751 — 0.1px. Any week with activity gets a 2px
  floor; a week with none gets a baseline tick instead, because "nothing
  happened" and "almost nothing happened" have to look different or the floor
  would be its own lie. The footnote now reads "Busiest week: W20 '26 with
  1 751 decisions. 8 of 18 weeks had none."
- **`_bucket_sequence` steps by day.** Stepping 7 days from an unaligned start
  visits one date per 7-day window offset from Monday, which is not the same
  set as "every `%W` week" — a range starting Sat 2025-12-20 went W50, W51,
  W00 and silently skipped **W52**. Monday-aligning fixes that one and breaks
  another, because `%W` week 00 is the partial week before the year's first
  Monday and no Monday falls inside it. A day cursor produces every label by
  construction. Caught by a test that compares generated labels against
  SQLite's own `strftime` across a year boundary, not by reading the output.
  Cost: 11ms for all three timelines on the real corpus.

Python's `%W` was verified to match SQLite's against 400 real rows and both
year boundaries before any of this was built on it.

---

## The 23% of shooting_log with no EXIF (2026-09-20)

The open question left by the readability pass had an answer, and it was not
in the dashboard.

**A LaunchAgent does not inherit the login shell's PATH.** launchd hands the
job `/usr/bin:/bin:/usr/sbin:/sbin`, and the plist ran `/bin/bash start.sh` —
neither interactive nor a login shell, so no profile is read. Homebrew's
`/opt/homebrew/bin` was never on it, and `exiftool` is the only reader for RAF
and HIF. `pyexiftool` could not find it, `_extract_via_exiftool` returned None,
and `extract_exif` fell back to exifread's all-None dict.

The photo still **scored**, so the row was written anyway — with camera,
shot_at, lens, film simulation, focal length, aperture, shutter and ISO all
NULL. 1 530 rows, 23% of `shooting_log`, no error anywhere. JPEG/NEF/3FR/HEIC
were untouched because exifread is pure Python and needs no binary, which is
exactly why it never looked like a systemic failure — just a suspiciously
large "Unknown" bar.

`ollama` was equally unreachable, so the LLM explainer had been off whenever
the app was launched normally.

Reproduced directly: the same RAF returns full EXIF under a normal PATH and an
all-None dict under the agent's.

Fixed two ways, because one of them is machine-specific:

- `launcher/install_agent.sh` substitutes the installing shell's PATH into a
  `__KAMERA_PATH__` placeholder in the plist's `EnvironmentVariables`, and
  warns if exiftool isn't on that shell's PATH either. **Re-run it after any
  plist edit** or the placeholder ships literally.
- `exif_parser._find_exiftool()` tries PATH, then `/opt/homebrew/bin`,
  `/usr/local/bin`, `/usr/bin`, and passes `executable=` to `ExifToolHelper`.
  A wrong PATH now degrades to "works" instead of to "quietly loses data".

A missing binary warns **once per process at WARNING level**. It was debug-only
to avoid flooding a batch — the right instinct for a per-photo error, the wrong
one for a condition that holds for the whole run, and the reason this stayed
invisible for months.

Guarded by four tests in `tests/test_exif.py`. Not done: the 1 530 existing
rows still carry no EXIF. They are historical `shooting_log` entries whose
`images` rows are long gone; re-analysing the source files is the only way to
recover them, and only where those files still exist.

---

## Dashboard readability pass (2026-09-20)

Follow-up to the critique. "I don't understand this" about *What you keep vs
reject* turned out to be three problems stacked, and chasing them reached two
real data bugs underneath.

- **Rows are claims, not columns.** `FEATURE · UNIT · KEPT · REJ. · Δ · ✓/✗`
  became a sentence plus a dumbbell: *"Your keeps are far sharper"* over
  `Sharpness · 0–100  ──○────────●──  33 → 80`. Nothing said "80" was a median;
  `Δ +47` restated the two numbers beside it; and the ✓/✗ legend had moved to a
  footnote at the bottom of the card, far from any row. The gap between the two
  dots **is** the delta, so the column is gone. Each feature carries a written
  label and a comparative clause; 31 of them are hand-written.
- **The binary rows were lying.** A median of a 0/1 column is a majority vote,
  so `face present · kept 0.00 · rejected 1.00` read as "my keeps never have
  faces, my rejects always do". The true shares are 47% and 57% — nearly no
  difference at all. The endpoint now also returns `kept_mean` /
  `rejected_mean` (for a binary column the mean *is* the share) and those rows
  read *"Faces appear in 47% of keeps and 57% of rejects"*.
- **Effect size decides what you see.** Rows sort by unexpected-first, then by
  how far the pair moved across the measure's own scale; anything under 5% of
  that scale hides behind "show N that barely differ" and drops its directional
  claim (*"Exposure made no real difference"* rather than *"slightly worse
  exposed"* next to `95.00 → 95.00`). This is what demoted `highlight clip
  0.07 vs 0.04`, which the old table had flagged with a red ✗ as though it were
  a finding. `face_count` got an explicit `[0, 5]` range after the adaptive
  scale turned a one-face median shift into a perfect 1.0 effect and the
  loudest sentence on the card.
- **"Unknown" left the charts.** 70% of `shooting_log` rows carry no
  `lens_model`, and that bucket was setting the scale — 911 frames on the
  135mm drew at a fifth of a bar meaning "we don't know". Missing data is now
  excluded from the bars and stated underneath ("4 612 shots (71%) have no lens
  recorded"). Film simulation was 94% unknown for the same reason. Each
  histogram also carries **its own** denominator: shares had been divided by
  the Cameras total, but `/shooting/cameras` filters `WHERE camera IS NOT NULL`
  while `/shooting/distributions` does not, so the lens tooltip said 92% where
  the truth was 70.5%.
- **Film simulation was a chart of opcodes.** pyexiftool defaults to
  `common_args=["-G", "-n"]`, and `-n` disables exiftool's print conversion:
  good for FNumber, destructive for every enum tag. `FilmMode` was stored as
  `2816` rather than `"Reala ACE"`. The parser now reads with print conversion
  on and `_to_float` handles exiftool's printed forms (`"23.0 mm"`, `"f/2.8"`,
  `"1/140"`) — a parsing job we control, versus an enum table to chase every
  time Fujifilm ships a simulation. A still-numeric value is dropped, so a
  regression shows as a blank instead of a bar chart of integers. Migration 55
  backfilled the seven historical codes; the mapping was read off real RAF
  files by running exiftool twice and pairing the output, not taken from a spec.
- **One camera, two names.** `X100VI` (2 482, JPEG) and `FUJIFILM X100VI`
  (378, RAF/HIF) were the same body, split by which parser read the file —
  exifread normalised through `canonical_camera_name`, the exiftool fallback
  built `"Make Model"` itself. Both paths now share the normaliser and Make is
  never prepended; migration 56 folded the existing rows.
- **`Segmented` gained arrow keys**, and `HoverPopover` an optional
  `className` so a portalled wrapper can be a flex child.

Open, not fixed: 1 530 of 6 541 `shooting_log` rows (23%) carry no camera,
focal length, aperture or ISO, and they are still appearing (110 in September).
That is the same 23% showing as "Unknown" in those three histograms. The cause
could not be diagnosed from this session — the sandbox cannot read the source
RAFs, and the exiftool failures observed here were that permission error, not
a real one.

---

## Dashboard critique pass (2026-09-19)

A design + interaction review of the Dashboard found that the page had quietly
started lying after the morning's corpus split, and that its chrome had drifted
from the rest of the app.

- **The dashboard is corpus-aware.** `/dashboard/model-card`,
  `/decisions/timeline` and `/decisions/feature-deltas` take `corpus`
  (`folder` | `photos`); a header `Segmented` switches between them. They used
  to pool every `training_samples` row, so the kept/rejected medians averaged
  camera RAW with iPhone HEIC — the exact mixing the split exists to prevent —
  while a card titled "Personal model" described only the folder pickle. An
  unknown corpus is a **400**, never a silent fallback to folder. The shooting
  endpoints stay deliberately shared (`shooting_log` has no `source_type`) and
  the card is labelled "all sources" so the switch never looks like it filters
  something it doesn't.
- **The timeframe tells the truth.** `since` cannot window a pickle —
  `training_size` / `trained_at` / accuracy describe one fit over everything.
  `model-card` now answers `since` with `window_sample_count`, and the card
  says "Model facts are all-time — a 30d window can't retrain the model. N
  decisions landed inside it." Previously the chips visibly governed a card
  they didn't touch.
- **One failing endpoint no longer blanks the page.** `useDashboard` was
  `Promise.all` over six calls: one rejection meant `setData` never ran and
  every card went empty, including ones whose data had already arrived. Now
  `allSettled` with per-card errors, and results merge rather than replace so
  a flaky endpoint shows a stale value with its error rather than a hole.
- **Entry point.** A four-KPI row (accuracy vs baseline · keep rate ·
  decisions · shots) sits above a 2-column grid. The four equal-weight stacked
  cards had no reading order, and the page's largest number existed only in
  the *untrained* state — once the model was ready, accuracy-vs-baseline was
  the typographically quietest thing on the page.
- **Real loading states.** Only `ModelCard` had a skeleton; the other three
  rendered their *empty* copy during the first fetch, so a returning user with
  2 971 decisions briefly read "No shooting data yet — analyze a folder".
  Every card now has shimmer-on-first-load, dim-on-refetch, and its own error
  line. Refresh spins while in flight and otherwise shows "2m ago" — it had no
  observable effect at all before.
- **Charts.** Weekly bars flex to fill the card instead of sitting as fixed
  12px columns in an `overflow-x-auto` strip that hid ~40 weeks behind an
  unmarked scrollbar. Hover moved from native `title` (500ms delay, light OS
  box on a `#07080a` canvas) to the portalled `HoverPopover`, which gained an
  optional `className` so a wrapper can be a flex child. Bars grow from zero
  over 240ms ease-out, the duration DESIGN.md already specified; skipped under
  `prefers-reduced-motion`.
- **Accessibility.** Keep/reject columns had no headers — the two medians were
  distinguished *only* by sage vs coral with the meaning in `title`. Each
  column now carries a real header row. Both charts got `role="img"` +
  `aria-label`. `Segmented` moved from SettingsModal into `ui/primitives` and
  gained the arrow-key navigation its `role="radio"` had been promising.
- **Tokens.** Card borders `#2a2b2d` → `rgba(255,255,255,0.06)` (the app's
  border, 53 other usages); bar fill `#cecece` → `#a8a8a8` per DESIGN.md (the
  old code's comment claimed it was already following the system); three bar
  geometries collapsed into one `StatBar`; 9px/10px text raised to 11px, the
  scale's floor. `min-h-screen` inside a page that already has a tab bar →
  `min-h-full`, which was guaranteeing a scrollbar over empty canvas.
- **Dead cells dropped.** Histogram blocks with no rows are omitted rather than
  rendered as a labelled "No data in this dimension yet" — *Film simulation*
  could never fill for a Nikon- or Hasselblad-only window.
- **Count fix.** The "N kept · N rejected" header read `features[0].n_kept`,
  which counts non-NaN values of one column, not photos. The backend now sends
  `n_kept_photos` / `n_rejected_photos` separately.

Guarded by five new tests in `tests/test_dashboard.py` (corpus scoping on both
decision endpoints, photo-vs-column counts, 400 on unknown corpus, and that
`since` never appears to shrink the model).

---

## Whole-app critique pass (2026-09-19)

A full-app review found three things worth fixing straight away.

- **Decision state no longer rides on colour alone.** Decided tiles on the All view carry a small ✓ / ◐ / ✕ glyph (bottom-right) and the tile's `alt` text names the decision. The top filter bar's inactive Keeps / Maybes / Rejects labels were drawn at 50% alpha (≈2.3–3.3:1) on the app's primary navigation; the label is now readable grey and the decision colour moved to the icon and count.
- **Contrast token.** ~100 uses of `#6a6b6c` as *readable* text (≈3.5:1) became `#8a8b8c` (≈5.5:1). `#6a6b6c` stays as the disabled-only token.
- **Shell.** Settings button was labelled "Model settings" (it has Model / Workflow / Display) → "Settings"; the bottom funnel is "Filter photos" so it stops sharing a name with the top decision filter. Tab bar is a real tablist (roving tabindex, arrow / Home / End, Enter / Space) — its tabs were click-only `div`s. New `hooks/useDialog.js` gives every modal Escape-to-close, a Tab trap, and focus restore, with a **stack so only the topmost dialog answers Escape** (a confirm over Settings closes alone). Applied to Settings, Confirm (alertdialog), Conflict (forced choice: no Escape), Auto-cull, Photos reconcile / album picker, Shortcuts and Move-into-group. It swallows Escape in the capture phase, so the grid's own Esc handlers no longer also fire behind an open dialog.
- **Text floor.** ~76 lines of readable prose (hints, descriptions, footnotes, status text) moved from 9–11px to 12px (`text-xs`); filenames from 10px to 11px. Left small on purpose: keycaps, chips and badges, uppercase tracking labels, chart/score-bar numerics, and the 30px tile-footer labels (a fixed two-line box — 12px would clip the filename).
- **Control placement: no move.** The top-edge = status/actions, bottom-pill = view rule was already the design; the real problem was two controls both called "Filter". The rule is now written down in DESIGN.md §4.1, and the Dashboard header toggle exposes `aria-pressed`.
- **No group-tile glyph needed.** An undecided group tile holds only undecided photos, and a decided one appears only in the Maybe / Reject filtered views, where its footer already names the decision.

---

## Settings critique pass (2026-09-19)

Review of the Settings modal found three save models living in one tab with nothing to tell them apart, and a Danger zone that mixed recoverable and irreversible actions.

- **Save behaviour is now labelled.** Presets + Decision thresholds carry "Saved with Apply" (the sticky footer owns only these); Analysis scope and AI vision carry "Saves instantly". The old claim that Analysis scope shared the Apply state was wrong — it always saved on click — so it moved to **Workflow**, and its copy shrank from ~170 words to two sentences plus a tooltip.
- **Danger zone split by reversibility.** *Reload models* and *Reset dashboard* (recoverable) moved to a neutral **Maintenance** block. *Reset taste model* — which erases the durable corpus that Clear Analysis deliberately keeps — now needs the same typed-sum challenge as *Clear analysis*. Four copy-pasted confirm blocks became one `ConfirmAction` component.
- **`Segmented` control** for every enumerated choice; *Advance cursor* was a lone flip-button and now matches. *Reject destination* states where rejects go right now.
- **Bug:** `ToggleRow` never accepted `indent`, so "Only when ALL faces are closed" rendered flush with its parent.
- **Accessibility:** `Toggle` takes a `label` (was an unnamed switch) and has a focus ring; `InfoTooltip` was `aria-hidden` yet focusable — now `role="button"`; the modal is a `role="dialog"` with Escape-to-close; tabs are a real tablist with arrow-key navigation; number inputs and sliders are named; `#6a6b6c` / `#777` hint text (3.5–4.2:1) → `#8a8b8c` (≈5.5:1) within Settings.
- Not done: presets still don't cover the burst fields (copy now says so rather than implying they do); critical explanations for the burst-dedupe rules remain tooltip-only.

---

## Vision model: qwen2.5vl:7b → qwen3-vl:30b-a3b-instruct (2026-09-19)

Default burst-rank / explainer model is now `qwen3-vl:30b-a3b-instruct` (≈20 GB). It is a
mixture-of-experts model (~3B active parameters), so it should not be slower
than the dense 7B on the M1 Max / 64 GB — unmeasured, verify on real bursts.

- **Picker honours preference.** `_pick_vision_model` and `_pick_model` used to
  return whichever vision model Ollama listed first. Both now go through
  `llm_explainer.pick_vision_from`, which walks `_VISION_PREFIXES` in order —
  `qwen3-vl` first, `qwen2.5vl` kept as fallback. Both models can coexist.
- **One frontend constant.** `VISION_MODEL` in `hooks/usePullModel.js` drives the
  pull button name, size and copy. The pull poll timeout went 30 → 90 min (20 GB).
- **Plain `qwen3-vl:30b` is the thinking build — unusable here.** First attempt
  showed "AI rank unavailable": `raw=''` in the log. The model reasons first and
  burned the whole 400-token `num_predict` on it, so `message.content` was empty;
  `think: false` was ignored. Use the `-instruct` tag. The picker prefers
  `instruct` names, and the ranker now logs the reasoning-ate-the-budget case.
- **Not yet verified:** multi-image ranking quality vs. qwen2.5vl on known bursts.

## A Re-analyse button, and the number that made it honest (2026-09-20)

"New photos are in the Smart Album again and I cannot see them — does a
re-analyse button make sense?" Yes, but on its own it would have made things
worse. Measured against the live album first: 18 new members, and **16 of them
had no local original** — still in iCloud, so analysis cannot read them. A bare
button picks up 2 of 18, says nothing about the rest, and looks broken.

The reason it would say nothing turned out to be a gap that had been there
since the Photos source shipped. `analyze_photos_album` has always returned
`missing_originals` and `skipped_videos`; `useTabs.js` stored them on the tab
as `photosSkipped`; **nothing ever rendered them**. The counts were collected
and discarded, which is why "I re-analysed and my photos are still missing" had
no answer anywhere in the UI.

So both halves shipped together:

- **Tab settings → Re-analyse album.** Routes through the existing
  `resumeAnalysisForTab`, which POSTs `/analyze-photos-album` with
  `source_folder` alone and skips everything already done. Compare with Photos
  finds the same photos through the `not_analyzed` class, but costs a full
  catalogue re-parse — 11s on this 2116-member album — and lives two levels
  deep in a settings panel. Wrong door for the thing you do most often.
- **The skip line in the result banner.** "16 photos in this album were not
  read — the original is still in iCloud, not on this Mac. Select them in
  Photos, choose Download Original, then re-analyse." Plus a muted line for the
  248 videos, which are never analysed by design.

One trap on the way: `photosSkipped` was only captured by
`runPhotosAnalysisForTab` (the album-picker path). The button goes through
`startAnalysis`, which dropped those fields — so the button's own path would
have been the one place the new explanation never appeared.

The Resume banner cannot be made to cover this, incidentally: it measures
against the `asset_count` a run recorded, so an album that grew reports zero
unfinished. The counter is written by the very action it is supposed to
prompt.

---

## Compare with Photos learned to see arrivals (2026-09-19, later)

Photos that join a Smart Album after the last analysis were invisible. The
Resume banner cannot surface them — it compares against the `asset_count` the
last run recorded, so an album that *grew* still reports zero unfinished, and
the banner meant to trigger the run needs the run to notice itself. The
reconcile's five classes were all about decisions and departures; nothing
covered an arrival.

`not_analyzed` is the sixth class: an album asset with no `images` row and no
KaMeRa keyword. A marker still means "analysed elsewhere" and stays the bare
`unmanaged_keyword` count — promoting it would re-analyse something already
judged.

Telling a new photo from a movie needed data the reconcile did not have.
`album_keyword_state` deliberately returns *every* member's keywords, movies
included, because treating a movie as absent would misreport it as
`not_in_album` and invite dropping a good row. It now also returns `members`
(`{filename, is_movie, has_original, supported}`) built from the same catalogue
read, mirroring `album_assets`'s three drop reasons — without it the test
album's 243 videos would have been reported as actionable drift alongside the
5 photos that mattered. `classify(..., members=None)` keeps the class empty, so
callers predating it are unaffected.

This is the one class that writes nothing. `action="analyze"` sets
`analysis_requested` in the response and the frontend then runs the ordinary
analyze flow, which re-enumerates the album and skips what is done. Starting
the batch inside reconcile would have meant a second copy of the batch starter
— which is exactly where the resume bug fixed an hour earlier had lived.

Measured against the real album: 1868 checked, 1868 in sync, drift 5 — and all
five are photos shot in the last two days whose originals are still in iCloud.
They are listed and flagged *not downloaded* rather than quietly omitted:
analysis reads the original, and scoring the local 768x1024 derivative would
put a meaningless sharpness number into `training_samples`.

`tests/test_photos_reconcile.py` +9, `tests/test_photos_reconcile_apply.py` +3;
full suite 623.

---

## Resuming a Photos album, which had never actually worked (2026-09-19, late)

An interrupted 1868-photo album analysis could not be picked back up. The
Resume banner was documented as folder-only and "cosmetic, because re-running
the analysis already skips finished photos". That was true of the endpoint and
false of everything downstream.

`_run_batch` opens with `queued = {str(f) for f in to_analyze} | {str(folder /
name) for name in skipped}`. For a Photos album `folder` is None. On a *fresh*
album run `skipped` is empty, and an empty set comprehension never evaluates its
body — so the `TypeError` fired only on a re-run, which is exactly and only what
a resume is. It raised above the try block, so the `finally` that clears
`_progress["running"]` never ran: the UI showed an analysis stuck at 0 of N,
forever, with no way to stop it. `queued` is read only inside the folder-rescan
branch, which was already gated on `folder is not None` — the guard existed one
level away from the line that needed it.

The UI half needed two things an album does not have. `/folders/unfinished`
counts by walking a directory, so albums now use the `asset_count` their last
run recorded (schema v53) — re-enumerating on every tab focus would mean an
osxphotos read, or AppleScript against a running Photos.app for a Smart Album,
just to draw a banner. And the banner's button needs to address the album:
`album_uuid` was never stored on a tab, so every tab restored out of
localStorage lacks it — which is the only kind of tab a resume ever happens on.
`POST /analyze-photos-album` therefore accepts `source_folder` on its own and
resolves the rest from `photos_albums`.

`tests/test_photos_resume.py` (13 tests) covers the count, the addressing and
the batch-setup regression; full suite 611.

---

## A second taste model, and a base score that could not rank (2026-09-19, evening)

Started as a status question — does culling in Photos already train anything? —
and the answer was no: decisions land in `training_samples` with
`corpus='photos'`, `auto_trainer` filters to `folder`, nothing consumes them.
So: build the second model. The build is the boring half; the measurements
changed the design twice.

**What the Photos corpus actually looks like.** 449 analyzed Photos rows against
the 2971 frozen folder vectors. `overall_score` on iPhone photos has a **median
of 93.1**, p25 of 64.7, p75 of 98.6 — half the corpus inside a six-point band.
One layer down: 50% of rows score `sharpness_score >= 95` and 26% fall below 50,
with almost nothing between. On RAW, sharpness is a gradient; through Deep
Fusion it is a binary — focus locked or it did not. A `keep_threshold` tuned to
pass 25% would have to sit at 98.6, which is slicing noise, not quality. So the
Photos model scores in a new **"absolute" mode** — `clamp(50 + raw*50)`,
`overall_score` never consulted — while folder keeps the shipped offset.

**The finding that was not about Photos.** Philipp asked whether a *blend* of
absolute and an iqa/aesthetic base would be better, and whether it would help
the camera model too. Bootstrap sweep on the real folder corpus, cutoffs
re-derived per split from train-set quantiles (otherwise you measure cutoff
mismatch, not score quality), Spearman as a cutoff-free check:

| variant (folder, 2971 samples) | accuracy | Spearman |
|---|---|---|
| `overall_score` alone | 0.703 | 0.280 |
| **shipped** `base + raw*25` | 0.713 | 0.341 |
| pure GBR absolute | **0.741** | **0.413** |

No blend beat pure GBR, on any base, at any weight — and an iqa/aesthetic base
was worse than `overall_score` everywhere (`aesthetic` alone: Spearman 0.024,
i.e. random ordering). The reason was already written in the codebase:
`feature_extractor.py` excludes `overall_score` from the vector *specifically*
to stop the GBR double-weighting the technical axis — and then
`predict_personal_score` adds that same `overall_score` back at the output. The
invariant is defended in the input layer and broken in the output layer.

**So the camera model was flipped as well.** That is not a free switch: absolute
scores are not in quality-percentage space, so `keep_threshold = 70` becomes
meaningless (folder's absolute scores have a median near 19). The flip and the
cutoff re-derivation had to land together — `folder:keep_threshold` / `:maybe_`
seeded from the fit's own label proportions, live at 39.65 / 35.56. The band is
narrow because the corpus is 72% reject and 2.9% maybe.

**A fourth copy of the formula.** `_bootstrap_validate` hardcoded
`overall + raw*25`. It stands behind `beats_baseline` → `model_status ==
"ready"` → whether auto-cull consults the model at all, so after the flip the
readiness gate was validating a formula the model no longer used. The tell was
that accuracy stayed at exactly 0.646 instead of moving; with the validator
fixed it reports 0.740, matching the independent sweep's 0.741. It now takes the
model's own `to_score` and, in absolute mode, derives cutoffs per split. Its
baseline/model cutoff asymmetry is documented and deliberately left alone —
making it fair would drop the margin below the 5pp gate and silently switch
auto-cull back to thresholds.

**Also:** thresholds are now corpus-scoped (`<corpus>:<key>`, falling back to the
bare key — no migration, `settings` is key→value). Absolute-mode corpora
self-calibrate their cutoffs from the fit's own label proportions, writing only
keys that do not exist yet so a user edit always wins. Photos' training floor is
150, not 30. Auto-cull on a Photos tab is no longer a flat 400 — it is gated on
that corpus's model reaching the ready tier, since the fallback quality
thresholds would stamp Keep on ~70% of an iPhone library.

Near-misses caught by building rather than reading: `auto_trainer._running` was
a single bool, so a folder train would have silently swallowed every Photos
trigger; `pairwise_comparisons` has no `corpus` column, so only a join on
`images.source_type` keeps camera A/B pairs out of the Photos fit; and dropping
the `overall_score is None` guard in absolute mode would have put a confident
score on every unanalyzed photo (the base is unused there, but its absence is
how the app knows a photo has not been looked at).

One bug shipped and was caught only by hitting the route over HTTP: inserting
two helper functions above `def auto_cull_preview` put them *between* the
`@router.get("/auto-cull/preview")` decorator and its function, so the route
quietly rebound to the helper and answered `200 "photos"`. Every one of the 597
unit tests still passed — nothing in the suite read the route table. There is
now a test that does.

Unrelated, found on the way: `shake_detected` is constant 0 across all 507
images *and* all 2971 training samples — dead feature, everywhere. And
`uncertainty_threshold = 8.0` fires on 0.1% of the corpus, so "Uncertain →
Maybe" has effectively never run.

`tests/test_photos_model_corpus.py` (20 tests) covers the seam; full suite 598.

---

## The AI pick stopped being a GroupLoupe secret (2026-09-19, later still)

Philipp's actual complaint: the grid shows an "AI is ranking" pulse while a
burst is being ranked, and then — nothing. No way to tell which photo won
without double-clicking into GroupLoupe. True, and worse than it sounds:
`/similarity-groups` and `/face-groups` computed `best_image_id` purely from
`overall_score`, full stop. The vision-LLM's actual rank-1 pick only ever
existed as a client-side value inside GroupLoupe (`effectiveHeroId`), built
from a `/rank-burst` call that fires on loupe-open. Everywhere else in the
app — the grid tile's "Best: filename" footer, the group-scrub peek panel,
DetailView's group banner — was silently showing the *score* pick and
implying (DetailView's copy said "Showing the AI's best…" outright) that it
was the AI's.

Fixed at the source instead of patching each surface: `_annotate_prerank_state`
(`backend/routers/model.py`) — which already queries `burst_rankings` per
group to compute `prerank_state` — now also reads `rankings_json` on a
`"ready"` hit and promotes the LLM's rank-1 onto `best_image_id`/
`best_reason`, tagging the group `best_is_ai_pick: true`. A manual hero
override (Settings' per-group pin) still wins over the AI, tracked via a
`_user_override` flag threaded through from both group-building loops and
popped before the group dict goes out over the API. Every consumer of
`best_image_id` — GroupTile's footer, the rail, DetailView, the peek panel —
inherited the fix for free; only three places needed new chrome to actually
*say* "this one's AI-confirmed":

- The group-scrub peek panel's position bar wears the app's rainbow
  `.ai-border-sm` stroke on the AI's segment — reusing the exact cue
  GroupLoupe's "ranking in progress" chip already uses, per Philipp's ask,
  rather than inventing new chrome.
- DetailView grew a small rainbow "AI pick" chip next to "Best: filename"
  (grid-filmstrip mode) and a rainbow ring on the group filmstrip's hero
  thumb (loupe/scrub mode) — replacing the plain white outline only when the
  pick is real, and rewording "Showing the AI's best of N photos" to "Showing
  the top-scoring of N" when it isn't.
- GroupLoupe now hands DetailView its own live `effectiveHeroId`/
  `effectiveHeroReason` instead of the raw `group` prop when opening it (O,
  Space, Enter, thumb double-click) — the loupe's own `/rank-burst` result
  can be fresher than the last `/similarity-groups` poll, and DetailView
  would otherwise flash the stale pick for one poll cycle.

One regression caught before it shipped: `best_image_id` doubling as a
group's "identity" for UI state (`selectedGroupId` in App.jsx) assumed it was
stable. It isn't — `useGroups.js`'s `loupeGroup` already knew this and falls
back to a membership search, but the grid's own "did my selected group leave
the visible list" cleanup effect didn't, and would have silently deselected
a tile the instant its background ranking finished. Gave it the same
membership fallback.

See `_promote_ai_pick` in
[backend/routers/model.py](../backend/routers/model.py), and
[tests/test_similarity_groups_prerank_state.py](../../tests/test_similarity_groups_prerank_state.py)
for the promotion/override/fallback cases.

---

## Smart Albums are selectable (2026-09-19, later still)

The album picker now lists user-created Smart Albums alongside regular ones. Getting there needed one finding first.

**A Smart Album is a stored query, not a stored list.** Photos keeps *no* album↔asset rows for one — verified against a Smart Album holding 2114 photos: zero rows in the join table. The predicate is evaluated live. That is why `osxphotos` does not expose Smart Albums at all, and why no amount of catalogue reading was going to work.

Photos' **AppleScript** does treat them as ordinary albums, and it is fast: 2114 member UUIDs in 0.26 s, batched counts in 0.23 s. Addressed by `album id`, never by name — album names are not unique in Photos and `album "Foo"` silently resolves to whichever comes first, which is a wrong read that looks like a right one.

How it fits together:

- `smart_albums()` finds them in the catalogue by `kind = 1507` **with a stored title**. Apple's built-ins (Favourites, Recents, Screenshots, …) share the kind range but carry no title — Photos localises those at runtime — so a title is exactly the marker for "the user made this".
- `smart_album_member_uuids()` asks Photos over AppleScript and strips the `/L0/001` suffix off each id.
- `_album_photos()` is now the single membership resolver for both kinds, so `album_assets`, `album_keyword_state` and `album_name` all gained Smart Album support without branching themselves. Photos' own ordering is preserved rather than the lookup's.
- `smart_album_counts()` batches every count into one `osascript` call, and is best-effort: the picker has to open when Photos is closed, so a missing count shows as `—` rather than failing the list.

**The one hard requirement this adds:** a running Photos.app with the library open, for Smart Albums only. Regular albums still read straight from the catalogue. `SmartAlbumUnavailable` → 503 saying exactly that, instead of a generic error.

**Worth knowing when culling one:** membership moves. A Smart Album built on `KaMeRa:*` keywords — which the app's own copy suggests — changes as you decide, so reconcile's `not_in_album` will show traffic there. It still defaults to leaving rows alone, so this is noise rather than risk. Verified live end to end: 2114 members → 1868 analysable (242 movies, 4 evicted originals), keyword state and reconcile classification both run against it.

---

## The memo that never fired, and a Settings tab that was filed by accident (2026-09-19, later still)

Two things came out of looking at grid performance, and the first one was not what was expected.

**`ImageCard`'s `memo()` was decorative.** The component has been memoised for a long time, but `memo` compares props shallowly and the call site passed `onDragStart={(e) => {…}}` — a fresh function identity on every render. The comparison failed on that one prop every single time, so **every App render re-rendered all N cards**. The place that hurts is the primary culling loop: one arrow key changes `isSelected` on exactly two tiles, and a thousand were re-rendering. `ImageCard` now hands its own `image` back to the handler (`onDragStart(e, image)`), which lets App hold one stable `useCallback`; the multi-select state it needs is read through a ref, so the callback never has to be rebuilt. Creating the small arrow *inside* the card is free — `memo` stops that function running at all when nothing about the card changed. `useGroupScrub`'s return object got the same treatment (it was a fresh literal per render, passed straight to the memoised group tiles), and `images` now falls back to a module-level constant instead of a new `[]`.

**Virtualization was considered and not built.** After the prop fix, what remains is initial render and DOM weight — real, but much smaller than the per-keystroke win, and a JS virtualizer would have to be paid for in exactly the places this grid is most intricate: roving `tabIndex`, `data-grid-idx` lookups, `.focus()` from arrow navigation, drag-and-drop, scroll-into-view. All of those address cells directly and none of them survive unmounting. `content-visibility: auto` on the cell wrapper buys most of the same benefit — the browser skips layout and paint for off-screen tiles — while leaving the DOM tree untouched, so nothing above needed changing. `contain-intrinsic-size: auto 15rem` keeps the scrollbar honest. Checked first that nothing in a cell paints outside its box (no negative offsets, no `position: fixed`, and the hover scrub panel portals to `body`), because `content-visibility` brings `contain: paint` with it.

**Settings grew a third tab.** Not for density — for meaning. **Reject destination** sat under *Display*, three sections below the interface size, and it is the switch that decides whether a rejected photo lands in a recoverable `_Rejects/` folder or in the system Trash. *Advance cursor* was there too, and *Grouping* was filed under *Model* despite being neither. The new **Workflow** tab holds those three. `Analysis scope` stays under Model on purpose: it shares the sticky-footer Apply state with the presets, and splitting it would orphan its own Apply button.

---

## Photos reconcile — the way back in (2026-09-19, later)

Decisions only ever flowed outward: KaMeRa wrote `KaMeRa:Keep|Maybe|Reject` keywords and never read them back. Three keywords deleted by hand in Photos on 2026-09-18 went unnoticed, and two photos kept a `keep` decision with nothing behind it — which is also what sent a debugging session chasing a bug that did not exist.

`GET /photos-album/diff` now reports the drift per photo in five classes, and `POST /photos-album/reconcile` applies a chosen direction per class. **Neither side is authoritative**, because they disagree for good reasons in both directions: a keyword set by hand in Photos is a real decision, and so is one made here. Only `keyword_missing` defaults to acting — there the two sides do not contradict each other, one simply never arrived. Everything else defaults to `skip`.

The classification lives in `backend/photos_reconcile.py` as a **pure function** — rows in, diff out, no library and no database. That is deliberate: the half of this feature that deletes rows must be testable on a machine with no Photos at all, not only on the developer's Mac. Every write reuses an existing path (the sink for keywords, `_apply_decision` for decisions, `_delete_image_rows` for removals) so reconcile cannot grow its own idea of what a decision is, and it re-reads the diff instead of trusting the client — an edit made in Photos while the modal sat open must not be overwritten by the picture the user was shown.

Two refusals rather than guesses: an asset carrying **two** KaMeRa keywords has no "what Photos says", so adopting it is refused and reported instead of coin-flipping; and a photo that merely left the album is never removed by the `asset_gone` action, because "gone from the library" and "gone from the album" are different facts with different consequences.

**Two robustness fixes fell out of testing it.** Forcing a catalogue re-read (reconcile's whole point is to see edits made since KaMeRa last looked) made this the first caller to hit a library that had been deleted — and osxphotos' bare `FileNotFoundError` surfaced as a 500. It is now a 404 naming the library and telling the user to pick another. Getting there required care: `Path.exists()` returns False under a TCC denial too, so a naive existence check would have reintroduced exactly the absence/denial conflation this project already lost a day to. `_library_is_really_absent()` only claims absence when it can **list the parent directory and see the package is not in it**; when the parent is unreadable it makes no claim and the caller reports an access problem, which is the one with an action attached. Separately, the library picker listed the same library twice — the directory scan and Spotlight returned it spelled differently, and a set of raw strings does not de-duplicate `/a/b` against `/a/b/`. Paths are resolved before de-duplication now.

**Not yet observed live:** no photo in the available library currently carries a `KaMeRa:*` keyword, so a non-empty keyword read has not been seen end to end. The read path is verified (315 assets and 6118 library UUIDs came back correctly); the parsing of a populated keyword list is covered only by tests.

---

## Three honesty fixes after an outside review (2026-09-19)

An external review pass (ChatGPT Codex) re-derived most of [PLAN-photos-next.md](PLAN-photos-next.md) as new findings, but three items survived verification against the code, and one more turned up while checking them.

**TCC under the LaunchAgent: verified, nothing to do.** Every TCC finding in the Photos feature had been measured against the old applet-spawned process. Re-measured against `gui/502/com.philippklein.kamera.backend`: `/photos-libraries` lists both libraries `readable: true`, `/photos-albums` opens the selected one and returns the 1248-photo album. Only `detected` stays `null` — `get_last_library_path()` is still blocked, which the explicit-choice design already routes around. No new grant is needed after installing the agent. Recorded in [PLAN-photos-source.md](PLAN-photos-source.md).

**`reject_to_system_trash` never worked.** Found while verifying the auto-cull copy below. `POST /settings` writes booleans as `"1"`/`"0"`; `sources/folder.py` read the key with a literal `== "true"` test, so the toggle evaluated False no matter how it was set. It shipped that way and went unnoticed because it fails in the *safe* direction — rejects kept landing in the recoverable `_Rejects/` folder instead of the OS Trash. Every other boolean reader in the codebase used the correct truthy set; this was the one that re-derived it. Parsing now lives in `database.get_bool_setting()` next to the writer's format, and the three already-correct duplicates read through it too, so there is one truthy set instead of four. `tests/test_bool_setting_roundtrip.py` exercises the real writer and the real reader — mocking either one is what let this hide.

**Auto-cull's modal promised something it could not keep.** It said *"Files move immediately"* in every case, but auto-cull commits through the same sink as a manual R press, so with the Trash route on one click sends N files to the OS Trash. `GET /auto-cull/preview` now resolves the real destination server-side (`reject_destination`: mode, label, `undoable`) — honouring per-folder `rejects_folder` overrides — and the modal states it before the click, including that `U` cannot take a trashed file back because it is no longer addressable. In Trash mode the confirm button turns coral-outlined (new `BTN_PRIMARY_DANGER` / `.btn-destructive`) and leads with the number that leaves the shoot: `Cull 1248 · trash 312`, not the reassuring total alone.

**`POST /trash-rejects` was not tab-scoped.** It swept every reject in the database, while the button that calls it sits in a bar labelled with the *active tab's* reject count — so pressing it in tab B trashed tab A's rejects under a number that described neither. `source_folder` is now a required body field: omitting it is a 422, not a wider sweep. The confirm line names the tab. Photos rows were already safe via `supports_bulk_trash = False`. Guarded by `tests/test_trash_rejects_scope.py`.

**Mid-batch `/images` refetching was quadratic.** The grid reloaded on *every* completed photo, and `/images` always returns the tab's whole list — measured at ~1.27 KB per row, so a 1248-photo album meant ~1248 fetches averaging ~0.8 MB, each one also re-running the tab's filter/sort memo and re-rendering every card. Payload slimming was measured and rejected: no single field dominates (`explanation` is null until DetailView asks for it), so dropping columns buys ~20% at best. The frequency was the lever — mid-batch refetches are now throttled to one per 1500 ms (`IMAGE_REFETCH_MIN_MS`). Safe because batch completion already does one unconditional full fetch, so the end state stays exact.

Not done, and deliberately separate: grid virtualization, Photos↔KaMeRa reconcile, Settings information architecture. Each needs its own decisions.

---

## macOS 27 reaps what an applet spawns (2026-09-18, later)

Four "failed to fetch" reports in one afternoon — folder picker, closing a tab, closing a tab again, ERR_CONNECTION_REFUSED on reload. One cause, and not in any of the endpoints being clicked: the backend was already dead, and the click was just the first thing to notice.

**The measurement.** Sampling the whole process family twice a second, twice in a row, to the tenth of a second:

```
18:11:29.7  applet starts
18:11:32.2  launcher.py + uvicorn up
18:11:35.9  applet exits          ← its script ended; applets quit then
18:11:36.6  launcher.py: SIGTERM
18:11:37.2  uvicorn gone
```

When the AppleScript applet finishes — it waits for `/health`, opens the PWA, and is done — macOS terminates the process group it spawned. The `perl -e 'fork and exit; POSIX::setsid(); exec @ARGV'` detach that the applet's own comment called "verified" no longer shields anything: a new session is not a new owner. `launchctl submit` was tried next and died exactly the same way, for the same reason — it runs in the caller's bootstrap context too.

**The fix is ownership, not detachment.** The backend is now a user LaunchAgent (`com.philippklein.kamera.backend`) bootstrapped into `gui/<uid>`; the applet's whole job is `launchctl kickstart`. Verified: applet exits at 19:20:42, backend still serving at 19:21:52 and beyond, `/clear-folder` round-trips, no SIGTERM in the log. Provenance's autopilot agent has lived on that same footing for months.

**Why it took four reports.** uvicorn's stdout and stderr went to `DEVNULL`, so a backend dying at any point left nothing behind — `data/app.log` carries only KaMeRa's own logger and simply stopped mid-sentence. They now go to `data/backend.log`. A second blind spot compounded it: the launcher's status `print`s were block-buffered into a redirected file, so a killed launcher took its own "Backend did not start" message with it and the log always ended mid-word at "Waiting for backend…", which read like a hang. Both fixed; the launcher also names the signal that ends it now.

**Two wrong turns worth recording.** A genuine 2-second race in `_watch_pwa_macos` was found and fixed on the way (it tore the backend down while looking for a window the AppleScript opens afterwards) — real bug, guarded by `tests/test_launcher_supervisor.py`, but not the cause of these outages. And an attempt to name the *sending* pid via `sigwaitinfo` backfired: macOS has no `sigwaitinfo`, the waiter thread died on `AttributeError` while `pthread_sigmask` stayed in effect, and the launcher became immune to SIGTERM — which then read as "macOS kills uvicorn but spares the launcher" and pointed at the wrong mechanism entirely. Instrument, then check the instrument.

Separately, the macOS 27 update reset TCC: the Photos library was unreadable until Full Disk Access was granted (that grant is itself what killed the backend the first time — macOS restarts an app's processes when its permissions change), and `~/Downloads` remains a distinct scope that a headless detached process cannot be prompted for.

---

## The launcher was killing its own backend (2026-09-18)

Three separate "failed to fetch" reports in one afternoon — folder picker, closing the last tab, then ERR_CONNECTION_REFUSED on hard reload. None of them was a bug in the endpoint being clicked. The backend was simply dead, and the click was just the first thing to notice.

`_watch_pwa_macos` slept 2 seconds, ran `pgrep` for the PWA's `app_mode_loader`, and terminated uvicorn if it found nothing. That grace period silently assumed *this process opened the window* — true on the plain path, where `main()` calls `webbrowser.open` immediately before the supervisor. The .app bundle takes the other path: it runs `start.sh --no-browser`, waits for `/health` to answer, and opens the PWA **after** that — the same instant the launcher comes out of `_wait_for_backend`. Chrome needs longer than two seconds to produce that process, so the supervisor tore the backend down before the window it was waiting for had ever existed. The window then opened against a dead server and rendered happily from the service-worker cache.

Intermittent, because `pgrep` matches *any* process carrying that path: a leftover KaMeRa window from an earlier session kept the supervisor quiet. That is why the same launch sometimes ran for six minutes through a 184-photo batch and sometimes died in seconds.

**The supervisor is now two-phase.** Wait for the window to appear (90s), and only then treat its disappearance as "the user quit". If none ever appears the backend is left running — that is the Provenance case, which drives the API with `--no-browser` and brings no window. A lingering backend is recoverable; one killed under a live window is not.

**Why it took three reports to find:** uvicorn's stdout and stderr went to `DEVNULL`, so a backend dying at any point left no trace — `data/app.log` only carries KaMeRa's own logger and simply stopped mid-sentence. They now go to `data/backend.log`, truncated per launch. And every launcher status `print` got `flush=True`: stdout redirected to a file is block-buffered, so a killed launcher took its own "Backend did not start" message with it. The launch log kept ending at "Waiting for backend…", which read like a hang and was really just an unflushed buffer.

Also aligned `_wait_for_backend`'s 60s default with the AppleScript's 120s health loop. The longer patience there could never pay off while launcher.py gave up first and terminated uvicorn — the cold-start fix had only ever been applied to one of the two places that needed it.

`tests/test_launcher_supervisor.py` covers all four cases, including the late-opening window that started this.

---

## Apple Photos as a second source type (2026-09-18)

The question was whether KaMeRa could work on the Photos library without exporting first. It splits into two: can it *read* in place, and can it express a decision *without* touching the filesystem. A spike answered both before a line was written.

Reading is easy once you have Full Disk Access — `originals/<hex>/<UUID>.ext` are real files, and `rawpy` / `pillow-heif` decode them directly (RAF 1.3 s half-size, HEIC 0.2 s). Writing was where both obvious designs died. **PhotoKit** returns `denied` instantly with no prompt, because a bare Python binary has no `NSPhotoLibraryUsageDescription`. **An album per decision** looked fine until the cost was measured: AppleScript cannot remove a photo from an album, so `photoscript` deletes and rebuilds it — 1.27 s to remove one photo from an album of six, i.e. ~0.21 s per photo already in it. One undo on a 500-photo Keeps album: ninety seconds.

Keywords are O(1) in both directions (set 0.03 s, clear 0.19 s) and Photos builds Smart Albums from them, so the album view survives — Photos maintains it instead of KaMeRa.

**What shipped:**

- **`backend/sources/`** — a `SourceSink` seam. `FolderSink` is today's file-move behaviour lifted *verbatim* out of `decisions.py` (moved, not rewritten, so behaviour is byte-identical); `PhotosSink` writes `KaMeRa:Keep` / `:Maybe` / `:Reject` keywords. Dispatch is on the new `images.source_type` column, never on parsing a path. Capability flags (`supports_auto_cull`, `supports_bulk_trash`, `supports_path_reaper`, …) let routers decline a feature rather than scattering `if source_type ==`.
- **A structural safety floor.** `file_mover._assert_outside_photos_library()` refuses any mutation on a path with a `.photoslibrary` component, checked on the *resolved* path so symlinks pointing in are caught too. It guards `move_photo` (source and destination), `trash_photo` (and every sidecar) and `resolve_dest_folder` — that last one catches a `folder_settings` override aimed into a library. Dispatch is an if-statement; this is the guarantee.
- **`training_samples.corpus`** (v52) partitions the training set. An iPhone HEIC and a camera RAF score on incompatible sharpness / aperture / ISO distributions, so folding both into one GBR teaches contradictory labels — the same reasoning as the RAW+JPEG companion's `_train=False`. Photos rows get `personal_score = null` rather than a confident number from a model that has never seen them.
- **`.heic` / `.heif` into `SUPPORTED_EXTENSIONS`.** A Photos library is ~75 % HEIC and the extension set silently excluded it, despite `pillow-heif` being registered globally and `burst_ranker` already listing the format. Side effect worth naming: HEIC is now analysable in ordinary folder tabs too.
- **A `photos` tab kind**, its own album picker (tkinter can only choose directories, so there is no `/pick-folder` equivalent), and a warning strip when Photos has a different library open. The K/M/R keys stay live there on purpose — the 409 names the library to open, which beats a silently dead keyboard.

**The wrong-library problem, and why the obvious guard is not the real one.** AppleScript writes always land in whatever library Photos.app currently has open, and `photoscript…name` returns "Photos" (the app), not the library. Comparing `get_last_library_path()` is advisory and can go stale between check and write. The binding guarantee is that asset UUIDs are per-library: looking one up in the wrong library returns *nothing*, so a mis-targeted write cannot happen. The keyword is then read back and verified before the `decisions` row is written.

**Two things the plan missed, found while implementing.** The taste-model export/import wrote `training_samples` with no `corpus`, so a backup round-trip would have re-filed every Photos sample as `folder` — the exact pollution the split exists to prevent. Fixed, with the backup format bumped to v2 so an older app *refuses* a new file rather than mis-reading it. And `trash-rejects` iterates by decision, not by source, so it needed sink dispatch plus a `skipped_unsupported` count — Photos exposes no delete at all, and a reject there must survive a sweep that could not act on it.

**Verified beyond green tests:** a real analysis run over a 6-photo album against the live library — filenames as `IMG_4012.HEIC` rather than UUIDs, `source_type='photos'`, scores 68.5–97.7, faces and scene detected, and the 37 existing folder rows untouched. Plus a single-photo proof that the full scorer pipeline reads a HEIC out of `originals/` at its true 3024×4032, not a derivative.

Setup, test-library instructions and known limitations: [docs/PLAN-photos-source.md](docs/PLAN-photos-source.md).

---

## "Only unconfirmed": one filter that makes the bucket order irrelevant (2026-09-18)

Started as a workflow question — *after an auto-cull, in what order do I walk the buckets without seeing a photo twice?* The honest answer was that no order is safe. Rejects promote to Keep, Maybes split both ways, Keeps demote; every bucket feeds the other two, so whichever you visit last collects photos from everything before it.

Except the app was already recording the thing that dissolves the problem. `decisions.reason` (migration v49) is `'manual'` after any human K/M/R and names the auto-cull rule otherwise — a per-photo "I have looked at this", independent of which bucket the photo currently sits in. It had just never left the backend: `GET /images` selected `d.decision` and nothing else, so the UI could only derive the answer for Keeps, and only from the `_Keeps_reviewed` path. One column in one SELECT was the whole gap.

**What shipped:**

- **`d.reason` in `GET /images`** ([images.py:154](backend/routers/images.py#L154)), and `isUnconfirmed()` in `ui/format.js` reading it. `null` counts as unconfirmed — that covers both undecided photos (open work, belongs in the filter) and decisions predating v49. Showing a photo one extra time is the safe direction; hiding an unseen one from a bulk trash action is not.
- **A toggle pill in the filter bar**, cyan when on, stacking with the decision segment rather than replacing it. On the All view it becomes "everything still waiting for me"; inside a bucket it's that bucket's queue. Persisted, because a culling session outlives a reload.
- **`_move_to_reviewed()` now stamps `reason = 'manual'`** ([decisions.py:566](backend/routers/decisions.py#L566)). Without it the ✓ affordance would move the file and still leave the photo in the filter — two sources of truth disagreeing about the same word. It deliberately writes no training sample: Mark reviewed means "I looked", K means "here is a label".

**The bug that would have eaten the feature:** the optimistic `setImages` patches after every decision carried `decision` and `file_path` but not `reason`, so a photo you just decided would have lingered in the filtered grid until the next `/images` poll — exactly the double-look the filter exists to prevent. All eight patch sites now mirror `reason`, including the two undo paths, which set it back to `null` because the backend deletes the whole `decisions` row.

`needsReview()` was left alone. It stays keep-only and path-based and answers the narrower Provenance question "may this be archived yet?" — a different question from "have I looked at this?", and worth keeping as a separate function rather than merging the two.

The recommended order is now in the FAQ anyway, for anyone not using the filter: **Rejects → Maybes → Keeps**, which works only because a manual keep skips straight to `_Keeps_reviewed`.

---

## Group peek: click to open, and a real grouped/ungrouped filter (2026-09-17)

Three asks in one pass. The filter one turned out not to be a bug.

**"Group photos" was never a group filter.** It's `composition: 'group'` — `face_count > 1`, i.e. *more than one person in the frame* — sitting in the composition list next to Portraits and Landscape. That's why it showed ungrouped photos: the four singles in the screenshot each have two people in them, and the one burst that came with them had a member scoring 2+ faces. The filter was doing its job; its label was claiming someone else's. Renamed to **"Multiple people"**, and the file now says in as many words that composition is about what's in the frame while grouping is about burst membership, so the next person doesn't re-merge them.

**A filter on burst membership now exists** — it didn't before. New `grouping: 'grouped' | 'ungrouped'` dimension, orthogonal to composition and AND-ed like every other category, with both directions selectable (only "show me groups" was ever expressible, and only by accident). "Grouped" means *member of a burst of ≥2*, independent of how the grid renders it — a burst whose other members are already decided still shows its last undecided member as a plain tile, and that photo is still grouped. The predicate lives outside `filterPredicate` because an image row carries no membership; the caller passes the lookup. It's applied at ITEM level in `displayGridItems`, not just per image: a group tile is grouped by definition and is dropped wholesale under "Ungrouped" — testing its members with `.some()` would have kept it, since every member individually answers "no, I'm not ungrouped" the wrong way round.

**The peek panel opens on click now, not on hover**, and the click scrolls its tile up under the sticky header so the panel gets the whole remaining viewport (it already sizes itself to the room it's given). Consequences worth knowing:

- **Most of the hover machinery is gone.** Dwell, grace period, the latch, and the document-level mousemove watcher all existed to make a panel survive a pointer that was only ever passing through. A pinned panel needs none of it. What stayed is everything about *steering*: tile sweep, position bar, arrow keys, the axis lock, the keyboard pin.
- **Dismissal is explicit**: Esc, a click anywhere outside, or a click on the tile again. The anchor's own click is deliberately not handled by the outside-click watcher — mousedown lands before click, so closing there would let the toggle re-open it a moment later.
- **A 220ms arm window** sits between the click and the panel, purely so double-click still reaches GroupLoupe / the partners overlay. Without it the first of the two clicks would open the panel *and scroll the grid* before the second arrived. It's on par with the 180ms hover dwell it replaced. A second click on a *different* tile inside that window isn't a double-click — it abandons the pending open and arms the new one.
- **Scroll-then-measure, never the reverse.** The panel is placed against the tile's rect, so it has to be measured after the scroll lands: `scrollend` where it exists, a 420ms cap where it doesn't (older WebKit, the PWA shim). The "scrolling closes the panel" rule now ignores a guarded window around our own scroll, including the momentum tail that arrives after the panel is already up.
- **The floating bottom toolbar is reserved for.** `place()` measured room against the raw viewport, so a panel hanging below put its action row — the row you actually reach for — under the toolbar. Now measured off `[data-bottom-bar]` rather than hard-coded, since the bar's height changes with what it hosts and it vanishes entirely inside DetailView.

Opening still maps the click's x onto the burst, so clicking the right-hand end of a tile opens on a late frame — same mapping the hover sweep had.

---

## The hover-scrub panel takes the keyboard (2026-09-17)

The group hover-scrub panel (2026-08-03) could already show you the whole burst behind a Reject tile and let you re-cull a frame — but only by clicking its buttons. Philipp wanted the keyboard: while the panel is up, ←/→ step the burst and K/M/R decide the shown frame and advance to the one on its right, so a group can be worked through without the pointer ever leaving the tile.

**Ownership.** The panel now binds its own hotkeys (`ui/GroupScrubPreview.jsx`) the way `GroupLoupe` does, and `useKeyboard.js` gained a `scrubOpen` gate that backs it off wholesale — arrows, K/M/R, U/Cmd+Z, Space, Enter, Esc. Without that gate both layers answer one keypress and fire two `/decision` posts against two different photos: the panel's frame and whatever the grid cursor happens to be parked on. (Same failure the DetailView gate exists to prevent.)

**One index, two gestures.** Pointer and keyboard drive the same scrub index; whichever acted last is the one showing. Two pieces make that work:
- A keyboard step pins the pointer's x (`RETAKE_PX = 6`). The tile's `mousemove` only takes the scrub back once the pointer has genuinely travelled — otherwise the smallest twitch of a resting hand snaps you back to the frame under a stationary cursor the instant you press →.
- The panel's position bar is now a scrub surface of its own (hover + click), with the hit area grown into the dead margins above and below without changing the height `place()` budgeted for. Once the pointer is inside the panel no `mousemove` reaches the tile at all, so before this the scrub was frozen exactly where the controls are.

**Axis intent — the scrub stops when the hand turns.** Sweeping the tile and then heading down into the panel means crossing the rest of the tile on the way out, and on a 13-frame burst a 40px sideways drift during that trip is three photos — you arrive at the panel looking at the wrong one. So the tracker freezes the scrub once the gesture reads as vertical, and releases it on clearly horizontal movement again (`AXIS_LOCK_RATIO` 1.0 / `AXIS_UNLOCK_RATIO` 1.3 — the asymmetry is hysteresis, or a 45° gesture flaps).

The part worth keeping is *what the direction is measured over*. The first cut used a 90ms time window and a minimum vertical travel, and it broke slow sweeps: a 4-second pass across the tile covers ~6px per window, which is the same size as the hand's own wobble, so the ratio is reading noise and it froze 90% of the sweep. Replacing the time window with a **travel window** — the direction is read over the most recent 18px of actual movement, whenever that took — normalises both speeds: a sweep reads as 18 across / 3 down whether it took 300ms or 4s, and a resting hand's jitter never accumulates 18px so it never reaches the test at all. Simulated against synthetic gestures before shipping: realistic sweeps (500ms–6s, drift up to ±20px) never lock; every exit — straight down, diagonal, slow, and the sweep-then-turn case — locks within 0.1–0.6 frames of horizontal drift.

The dwell timer needed the same guard. Re-entering the tile from the panel restarts it, and its `measure()` snaps the index to the pointer's x — which would have walked straight past the lock on the return trip. It now leaves an already-open panel alone while the axis is locked; a *first* open still goes through, since there's nothing on screen to protect yet and it has to land on some frame.

**The latch — the non-obvious half.** Deciding a photo can unmount the very tile the pointer is resting on: in the Rejects view, rescuing a frame drops it from the filtered grid, React removes the node, and the browser fires a `mouseleave` the user never performed. The panel would tear itself down on the first K. So the first key or decision *latches* the pass: the tile's own `mouseleave` stops being trusted, and a document-level `mousemove` watcher takes over the job of noticing a real departure — anywhere that isn't the panel (`[data-group-scrub]`) or a still-mounted anchor tile starts the same 240ms grace countdown, with `LATCH_SLOP = 10` of slack so the tile vanishing under a stationary cursor doesn't count as walking away. The watcher only schedules once; restarting the timer on every `mousemove` would mean a pointer that keeps moving never times out.

**Space goes full screen and comes back where you left.** Space/Enter on the panel opens the shown frame in DetailView — now carrying the burst with it as a group context, so ←/→ cycle the burst's members rather than the global grid order. Space or Esc in there closes DetailView and *rebuilds the panel on the frame DetailView ended on*, whether you got there by navigating or by culling. Hover can't rebuild that — the pointer may be nowhere near the tile by then — so the pass is parked before `detailOpen` suspends the hook (`stash()`) and replayed on the way out (`restore(imageId)`). The anchor is re-measured if it's still mounted; the parked rect is the fallback for a tile the cull has since dropped from the filtered grid.

Three things this needed underneath:
- **K/M/R in a scrub-opened DetailView doesn't close it.** Group mode normally closes on a decision, because `sendDecision` advances the *global* grid cursor and would walk out of the burst. This path gets its own handler that advances within the burst instead, and a new `closeOnDecide={false}` on DetailView — Space/Esc stays the user's own way back.
- **Esc and Space now share one exit.** Both routed through `App.closeDetail()`, which is also what the X button and the backdrop call, so the hand-back can't be bypassed. Space closing DetailView is no longer gated on grid focus (Esc already wasn't) — a full-screen surface shouldn't care what holds focus behind it.
- **The re-entry snap had to be plugged.** Dismissing DetailView re-fires `mouseenter` on the tile underneath a cursor that never moved, and the tile's dwell timer ends with a `measure()` that snaps the index to the pointer's x — the restored frame would have been overwritten ~180ms after appearing. The dwell now leaves an already-open panel alone while the index is held (axis lock or keyboard pin), and `restore()` pins at the pointer's last known x so the frame stands until a genuine sideways move.

**Decisions always post, even redundant ones.** Pressing K on a frame that is already a Keep still sends it. Only a *manual* decision writes a `training_samples` row, so confirming an auto-cull is real signal — and `move_photo` is a documented no-op when the destination folder hasn't changed. (The panel's Keep *button* stays disabled in that state; the key is the confirm gesture.)

Also folded in: `suspended` now covers CompareView and any non-grid `activeView`. Both mount full-screen surfaces with their own K/M/R bindings, and a panel left open behind one would have collided with them.

---

## Fixed: IQA + Aesthetic silently blank for a whole batch (process-wide model-load lock, 2026-08-28)

Found while verifying DNG support, fixed the same day. On a cold process the TOPIQ-NR and TOPIQ-IAA scores intermittently came back `None` for **every photo in the batch** — no crash, no toast, no user-visible error. Just two empty columns, which reads as "the model didn't have an opinion" rather than "the model never loaded."

**Root cause.** `accelerate` implements meta-device weight initialisation by monkey-patching `torch.nn.Module.register_parameter` — *global process state*, not per-model and not thread-local. `analyze_photo_quality` fans seven scorers into a 6-worker pool, so a thread inside `AutoModel.from_pretrained()` (SigLIP) is live while another thread builds a net inside `pyiqa.create_metric()`. The second thread's parameters get created on the meta device and never filled; its `.to(device)` then raises `Cannot copy out of meta tensor`. The loser latches `_load_failed`, which by design short-circuits every subsequent call — so one lost race disables that scorer for the rest of the run.

**Why the existing locks didn't catch it.** `iqa_scorer`, `aesthetic_scorer` and `similarity_scorer` each already had a `threading.Lock`, added by the 2026-07-19 watcher fix. Each correctly prevented *one module* from loading twice — but the collision is **between** modules, so no two threads ever contended for the same lock. `face_identity` had no lock at all despite also calling `.to(device)`. The asymmetry is what made the per-module locks look sufficient: loading pyiqa twice concurrently passes; loading pyiqa against SigLIP failed 2/2.

`_warm_models` loading the three torch models sequentially in one background thread is why this was rare in the running app — but that's a mitigation, not a guarantee. It only orders the loads *that thread* performs; clicking Analyze before warm-up finished put the scorer pool on the same loaders from other threads.

**Fix.** New `phase2_quality/model_loading.py` exporting a single process-wide `MODEL_LOAD_LOCK` (an `RLock`, so a loader that transitively triggers another load on the same thread can't deadlock). All four torch loaders now hold it across construction *and* `.to(device)` — the patch window spans both. Each keeps its `if _model is not None: return` fast path, so warm calls stay uncontended. MediaPipe (`face_analyzer`) deliberately stays out: it's TFLite, never touches torch module construction, and serialising it would only add first-photo latency. `_warm_models` stays sequential — now as a contention/ordering preference rather than the correctness mechanism — with its docstring corrected to say so.

**`tests/test_model_load_lock.py` (11 tests)** covers three layers: every loader references the one lock; each actually *holds* it at the moment of construction; and two loaders released simultaneously from two threads never overlap. Verified all three layers fail when a module-local lock is reintroduced — the overlap test reports `assert 2 == 1`, i.e. the constructions genuinely collided.

The behavioural tests patch each loader's own `_begin()` (the model-status toast hook, called inside the lock immediately before construction) rather than the library entry points, so they run in under 4s and never touch the network or the weight cache. Patching `transformers.AutoModel` does **not** work under pytest — `transformers` is a `_LazyModule`, and the swap reads back correctly on the module object while the loader's function-local `from transformers import AutoModel` still resolves to the real class. Worth knowing before anyone tries that approach again.

Verified on the real cold-start path that previously failed: `analyze_photo_quality` on RAF/HIF/DNG in a fresh process with no pre-warming, IQA and Aesthetic populated 3/3 runs. 441/441 tests pass.

---

## DNG support + one source of truth for RAW formats (2026-08-28)

Philipp asked for `.dng` analysis (DJI drone files) and whether `.3FR` was already supported. `.3FR` was — fully wired since 2026-06-30. `.dng` was **half-wired**, and the shape of that half is the interesting part.

`DNG`, `ARW`, `CR2` and `CR3` had been sitting in `constants.RAW_FORMATS` for a long time, so everything gated on *that* constant already handled them: preview demosaic, the RAW+JPEG pairing inference, `has_processed_sibling`'s `jpeg_priority` skip, the burst ranker's RAW fallback. But `SUPPORTED_EXTENSIONS` was a hand-written literal that never got the same additions — and that constant is what the analyzer, the file watcher, and `GET /images`' folder scan walk with. A `.dng` on disk was therefore **never discovered at all**. Had it been, three of the four scorer decode branches still read a hardcoded `('.RAF', '.NEF', '.3FR')` and would have sent it down the `cv2.imread` path, which cannot decode a RAW.

Fixed by making `RAW_FORMATS` the single source of truth rather than one of five parallel lists:

- **`backend/constants.py`** — new `RAW_DECODE_SUFFIXES` (lowercase dot-prefixed mirror, *derived* from `RAW_FORMATS`). `SUPPORTED_EXTENSIONS` is now `_PROCESSED_EXTS | RAW_DECODE_SUFFIXES`, so the RAW half can no longer drift from `RAW_FORMATS`. `_HIF_PAIRED_RAW_EXTS` and `_PROCESSED_SIBLING_EXTS` likewise derive instead of repeating.
- **Four scorer decode branches** now import that constant: `phase1_technical/utils.py::load_as_gray`, `phase1_technical/batch_sharpness_analyzer.py::detect_sharpness`, `phase2_quality/face_analyzer.py::_load_image`, and `quality_analyzer.py` (which re-exports it for `routers/analysis.py`'s RAW prefetch). Also switched `.upper()`-against-uppercase-literals to `.lower()`-against-the-constant.
- **`batch_sharpness_analyzer.py`** doubles as a standalone calibration CLI where the repo root isn't on `sys.path`, so it bootstraps the root before importing. Its folder walk now uses `SUPPORTED_EXTENSIONS` (previously a 7-entry per-casing glob list that missed `.3fr` entirely), its `THRESHOLDS` table gets a derived entry per RAW format instead of silently falling back to the JPG value, and its `[raw_threshold]` CLI argument now applies to every RAW format. It also registers `pillow_heif` in `__main__` so `.hif` doesn't get silently dropped from a calibration set.
- The three dev-CLI folder walks in `burst_detection.py` / `camera_shake.py` / `exposure.py` (all inside `__main__`) read `SUPPORTED_EXTENSIONS` too — they'd been stuck at `{jpg, jpeg, png, raf, nef}`.
- Settings → Display copy no longer enumerates "RAW/RAF/NEF/3FR".

**`tests/test_raw_format_coverage.py` (29 tests)** pins the derivation and — the part with teeth — asserts via `inspect.getsource` that the three suffix-branching functions contain no hardcoded `.RAF`/`.NEF`/`.3FR` literal. Verified the guard fails when a literal is reintroduced.

Verified end-to-end through `analyze_photo_quality` (the real call site, not `rawpy.imread`) on a DJI `8192×4608` DNG and an Adobe-converted `8000×5320` DNG: sharpness, exposure, face, IQA, aesthetic, scene, 768-dim embedding and histogram all populate; `_raw_to_pil` + `_generate_preview` produce correct-orientation JPEGs. RAF and HIF regression-checked in the same process, unchanged. 430/430 tests pass, `npm run build` green, dist rebuilt.

ARW/CR2/CR3 are wired by the same derivation but **untested** — no Sony/Canon sample files on hand.

**Unrelated pre-existing issue found while verifying:** on a cold process, the TOPIQ IQA and aesthetic metrics sometimes both returned `None` with `NotImplementedError: Cannot copy out of meta tensor`. Reproduces identically on a RAF, so it was format-independent and not caused by this change. Fixed immediately after — see the next entry.

---

## Fixed: K/M/X did nothing in the Keeps/Maybes/Rejects views until you clicked a photo (2026-07-27)

Philipp reported (flagged **absolute must-have**) that pressing `X` on a selected photo *inside the Keep view* did **nothing at all** — no re-decision to Reject, no feedback.

Diagnosis: the Keep/Maybe/Reject views are **not** separate `activeView` values — they're the same `activeView === 'grid'` with `decisionFilter` set to `'keep'`/`'maybe'`/`'reject'`, so the `activeView !== 'grid'` gate in [useKeyboard.js](../frontend/src/hooks/useKeyboard.js) was never the culprit. The real gate was `gridHasFocus()` — every decision hotkey requires DOM focus to be *inside the grid* (the composite-widget focus model). Switching to a filtered view by clicking the filter pill never moves focus into the grid: the pill deliberately declines focus (`onMouseDown` preventDefault, to *preserve* grid focus), and no effect focused a cell on filter change — so if focus was unowned (`document.body`, the common state after a load or scroll), `gridHasFocus()` stayed false and K/M/X returned early. Clicking any photo first put focus into the grid and made the keys work, which is why it looked intermittent.

Fix in [frontend/src/App.jsx](../frontend/src/App.jsx): a new effect keyed on `decisionFilter` selects and DOM-focuses the first visible photo cell when the filter switches, so decision keys work the instant you land in a filtered view. Guards mirror the existing focus-reclaim logic — it only fires on an actual filter *change* (tracked via `lastFocusedFilterRef`, never on the constant `displayGridItems` churn during a batch), only when the grid is the active surface with no overlay open, and only claims focus when it's unowned or already inside the grid (never steals from search/settings/modals). The DOM `.focus()` is deferred a frame so the new selection's roving `tabIndex`/`data-grid-idx` has committed.

Scope check confirmed this was the *only* broken surface: DetailView (`decideFromDetail`) and GroupLoupe (`decideSelectedOrFocused`) bind their own K/M/R gated on the overlay being open (`enabled: !!group` / `enableOnFormTags`), not on grid focus, so they already worked. The backend re-decide path (`sendDecision` → `POST /decision`) always re-decides `images[selectedIdx]` regardless of its current decision, moving the file from its current bucket to the new one — so the fix is purely about getting the keypress *to* that path. 401/401 tests still pass (frontend-only change); `npm run build` clean.

---

## Bulk-move a whole group in the grid + Group members overlay (2026-07-27)

Philipp: after auto-cull, inspecting a group of Maybes and deciding the whole burst should go to Rejects meant re-culling each near-identical frame one at a time. Added a one-action "move this group" in both surfaces, acting on the members *currently visible* (a survivor culled into another bucket is untouched — chosen over "every member" so the deliberate Keep isn't collateral).

- **Grid (Rejects/Maybes views):** hovering the shared group-run border reveals a small action bar at its top-left — `GroupBulkBar` in [frontend/src/ui/TileChips.jsx](../frontend/src/ui/TileChips.jsx). It offers the two decisions that aren't the current view ("Keep all" / "Reject all" on Maybes; "Keep all" / "Maybe all" on Rejects) — no no-op button for the bucket you're already in. Hover is keyed off a **named** Tailwind group (`group/run` on the cell wrapper) so it doesn't collide with ImageCard's own unnamed `group`. `displayGridItems` now hangs the run's visible image-ids on the run's first tile (`runImageIds`), alongside the existing `groupChip`.
- **"Group members" overlay:** each section header (Keeps / Maybes / Rejects) with ≥2 photos gets the same "…all" buttons — `SectionBulkBar` in [frontend/src/modals/GroupPartnersOverlay.jsx](../frontend/src/modals/GroupPartnersOverlay.jsx), wired via a new `onBulkDecide` prop.
- Both route through the existing undoable `bulkDecide` batch path (one round-trip, one Undo entry — same primitive CompareView's "Keep best · Reject rest" uses), so `U` / Cmd+Z reverses the whole move. Frontend-only; `npm run build` green, dist rebuilt. Mobile companion has no equivalent yet (desktop-is-active-surface).

---

## Renamed reject folder `_Trash` → `_Rejects` to match the UI verb (2026-07-26)

Philipp flagged a wording inconsistency: the UI calls the decision **Reject** (the `R` key, "reject list", decision counts) but the folder it moved files into was `_Trash` — and that blurred into the genuinely-separate `reject_to_system_trash` feature (the OS Trash via `send2trash`). Chose a **hard rename** (no back-compat alias) so the folder on disk matches the verb.

- **Folder name:** `_Rejects` everywhere — `constants.DECISION_SUBFOLDERS`, `file_mover._DECISION_FOLDERS`, the file-watcher skip guard, the unfinished-folder walk, and `ImageCard`'s originating-subfolder detector.
- **Override/setting key:** `trash_folder` → `rejects_folder` across `file_mover`, `database._DECISION_TO_OVERRIDE_KEY`, `settings.FolderSettingsRequest` + `get_folder_settings` (`rejects_is_default`), and `TabFoldersForm.jsx`. The legacy pre-2026-05-04 global `trash_folder` rows read by migration v18 are **left as-is** — those are historical DB keys an old DB literally contains.
- **DB migration v51:** rewrites any persisted `folder_settings` reject override whose path ends in `/_Trash` → `/_Rejects`; a genuinely custom reject destination is untouched. Live DB had 0 reject overrides, so it's a no-op there — the rename takes effect purely through the new default. Verified end-to-end (default rewritten, custom preserved, keep untouched, version bumps to 51).
- **Hard-rename caveat (owner-accepted):** existing `_Trash/` folders already on disk are no longer recognized as decision-subfolders. Philipp cleans those up manually before the next Provenance-driven import, else the watcher re-analyzes what's inside them.
- **System-Trash copy preserved:** `reject_to_system_trash` and every "sent to Trash" string that refers to the *OS* Trash stay "Trash" — that IS the macOS/Windows Trash, a different destination.
- **Provenance:** unaffected — its archive pipeline only watches `_Keeps`/`_Keeps_reviewed`, never rejects. Fixed one stale `reject→_Trash` docstring in `provenance/src/provenance/kamera.py`.

Docs (README, FAQ, CLAUDE.md) updated; historical CHANGELOG entries that describe past `_Trash` events left verbatim. Tests updated + green.

---

## Fixed: blurry photos scoring sharpness 100 — the micro-texture false-sharp bug (2026-07-26)

Philipp: DSCF0976.JPG (a completely out-of-focus string-lights shot) scored sharpness **100** and auto-culled to `_Keeps/`. Root cause: the tile-based p90 focus measures (Laplacian/Tenengrad) fire on *per-pixel contrast*, which a defocused frame full of fine, dense micro-texture (fabric weave, sensor noise) produces uniformly across the whole frame. The p90 aggregation — meant to rescue a sharp subject from bokeh — instead reads the uniform texture as uniform sharpness and saturates. DSCF0976 fused to normalized 18.1 (≈8× the 2.3 threshold → score 100) purely on this artifact, even though its TOPIQ-NR IQA was 25.6. Systemic, not isolated: 37 photos had sharpness ≥ 90 with IQA < 35, and 338/664 photos read *exactly* 100 (the old `min(100, ratio*50)` clipped hard at 2× threshold).

Three complementary fixes (A, C, D — B, a naive coarse-Tenengrad cap, was tested and rejected: it wrongly capped 20–60% of genuinely-sharp photos on the full library):

- **A — low-IQA decision guard.** `_compute_auto_decision` ([backend/routers/decisions.py](../backend/routers/decisions.py)) now instant-rejects when `iqa_score < iqa_floor` (default 30), in both personal-model and threshold modes, before the model path. This is the primary net: IQA<30 cleanly separated all 18 false-sharp cases from every genuinely-sharp photo (which sit at IQA 74–78). NULL IQA never culls (fail-safe). New settings `reject_low_iqa` (on by default) + `iqa_floor`, registered in the DB registries, `SettingsRequest`, `get_decision_thresholds`, and surfaced in Settings → Model → Instant rejects. `low_iqa` added to `_PHYSICAL_REJECT_REASONS` (an all-low-IQA burst isn't rescued) and the preview reason-bucket (folds into "blurry").
- **C — micro-texture cap.** `batch_sharpness_analyzer._score_from_gray` (shared by every scoring path) caps `normalized_score` when the fine/coarse Tenengrad ratio ≥ 6.0 — the physical fingerprint of texture-masquerading-as-focus (real focus survives a 4× downsample; fine texture collapses). Calibrated on the library: the worst false-sharp cases land at ratio 8.5–20.4, every genuinely-sharp photo stays ≤ 4.9, so 6.0 catches the extreme tail with zero true-sharp collateral. Fails open on bad input. DSCF0976 drops 100 → 30 ("Blurry").
- **D — soft-saturation curve.** `quality_analyzer.sharpness_score_from_ratio` replaces the hard `min(100, ratio*50)` clip. Keeps the linear slope-50 line **unchanged** below a knee at ratio 1.8 (→ 90) — so the label cuts (Sharp ≥ 70 at ratio 1.4, Borderline ≥ 40 at 0.8) and the threshold anchor (50 at ratio 1.0) don't move — then log-compresses the previously-clipped ratio>1.8 tail into 90–100, restoring resolution at the top (the 338-photo pile-up on exactly 100 spreads out).

Read-only re-run of the last auto-cull (threshold-fallback path, stored scores): **23 photos flip keep → reject**, all via the low-IQA guard (the full false-sharp cluster + 5 poor `.HIF` frames), DSCF0976 among them. On re-analysis, C additionally corrects the 6 worst frames at the source (100 → 30) and D spreads the rest off the 100 ceiling. Note C+D only take effect on re-analysis; A takes effect on the next auto-cull. Full suite **376 passing** (+15 new: `tests/test_sharpness_scoring.py` for the cap + curve, low-IQA cases in `tests/test_auto_decision.py`, `_PHYSICAL_REJECT_REASONS` guard updated). `frontend/dist` rebuilt.

---

## Decoupled auto-cull dedup from the grouping time-gap (2026-07-26)

Follow-up to wiring the slider into auto-cull (below): on Philipp's real library, changing the time-gap 1s↔5min barely moved the K/M/X counts, and digging in revealed *why* — the time-gap is the wrong lever for auto-cull dedup. Two reasons: (1) burst dedup only re-decides photos that were already Keeps, so most rejects (the personal model's per-photo quality calls) can't move with grouping at all; (2) more fundamentally, continuous bursts fire several frames within one EXIF second (second-resolution timestamps), so **no integer-second gap can split them** — the slider can only ever split bursts *coarser* than the camera's inter-frame spacing, which fast bursts are already below. Cosine similarity, by contrast, *can* discriminate those frames.

So auto-cull dedup is now **decoupled from the time-gap and groups by visual similarity only**. `_group_undecided_into_bursts` defaults `time_gap_seconds=None` (time split disabled); both auto-cull endpoints dropped the `time_gap_seconds` param and keep only `threshold` (Similarity); `AutoCullModal` stops sending the gap. The risk of pure-similarity grouping — distinct moments minutes apart collapsing into one giant burst — is already handled by `_cap_burst_keeps`' per-scene split at 0.94, verified: 3 moments 7 min apart merge into one group under similarity-only, then the scene split correctly re-separates them into [4, 4, 4], each with its own keep budget. The real auto-cull dedup levers are now the **Similarity threshold** + the **keep/maybe budgets**, which actually discriminate tight bursts.

UI honesty: the Settings → Grouping **Time gap** slider now carries an italic note that it affects grouped review (Bursts / GroupLoupe) only, *not* auto-cull; the **Similarity** slider's help notes it also drives auto-cull dedup. The AutoCullModal's dedup-vs-model split line was reworded ("Similarity slider + burst budgets" / "the model's own call"). The `_group_undecided_into_bursts` mechanism still honours an explicit `time_gap_seconds` when a caller passes one (tests do). Regression tests rewritten: default grouping ignores time, an explicit gap still splits, and the endpoints expose `threshold` but no longer `time_gap_seconds`. Full suite 401 passing; `frontend/dist` rebuilt.

---

## Fixed: auto-cull ignored the grouping time-gap / similarity slider (2026-07-26)

Philipp: changing the GroupLoupe clustering slider from 1s to 5min produced *identical* auto-cull K/M/X counts. Root cause: both auto-cull endpoints called `_group_undecided_into_bursts(rows_dicts)` with **no arguments**, so the burst grouping the redundancy/multi-keep post-passes operate on was hardwired to the function defaults (60s / 0.90). The slider values live only in browser `localStorage` and were only ever sent to `GET /similarity-groups` (GroupLoupe) — they never reached `/auto-cull` or `/auto-cull/preview`. So every auto-cull grouped at 60s regardless of the slider, and the whole burst-dedup family (cap, review budget, per-scene split) silently operated on the wrong groups. This orphaning existed since the first burst-cap commit (2026-07-25), not just the recent passes.

Fix: both endpoints now take `time_gap_seconds` + `threshold` query params (defaults unchanged at 60 / 0.90) and thread them into `_group_undecided_into_bursts` ([backend/routers/decisions.py](../backend/routers/decisions.py)). `AutoCullModal` sends the current slider values on both the preview and the run (matching `useGroups.js`'s convention that a non-positive gap → a large sentinel that disables the time split); `App.jsx` passes `timeGapSeconds` + `threshold` as props. Now auto-cull dedups against exactly the grouping the user sees in GroupLoupe. Regression tests in `tests/test_auto_cull_grouping_params.py` pin that a 1s gap splits what a 300s gap merges, that the 30s boundary is exact, and that both endpoint signatures expose the params (a cheap guard against the call-site plumbing being dropped again). Full suite 401 passing; `frontend/dist` rebuilt.

**Mobile follow-up (deferred):** `frontend/src/mobile/views/AutoCullView.jsx` has the same missing plumbing. Left for the `sync-mobile` skill per the desktop-is-active-surface convention.

---

## Per-scene keep budget within a burst (2026-07-26)

Follow-up to the redundancy pass: a time-gap burst can contain more than one *visual* scene — shoot angle A, pan, shoot angle B seconds apart, and the 30s time-gap glue makes them one burst. The redundancy budget then wrongly rejected angle B's keeper as a "redundant" copy of angle A, because the budget was per-burst. Now the budget is **per visual sub-scene**. `_cap_burst_keeps` in [backend/routers/decisions.py](../backend/routers/decisions.py):

- New `_scene_subclusters` splits each burst by cosine union-find at `_SCENE_SPLIT_COSINE`, and the Keep/Maybe budget logic (extracted into `_apply_burst_budget`) runs once per sub-scene with a fresh allowance. Two genuinely-different POVs in one burst each keep their own winner (+ their own Maybe budget); a single-scene burst yields one sub-cluster and behaves exactly as before. A frame without an embedding can't be placed, so it becomes its own singleton scene and keeps — consistent with "never auto-trash on missing data".
- **Threshold subtlety (important):** the split threshold is **0.94**, deliberately *stricter* than the 0.90 the grouping algorithm uses to form a burst. A burst is already a single 0.90-connected component (transitively — a chain A~B~C at 0.92 each is one burst even if A~C is 0.85), so sub-clustering at 0.90 would just re-merge it and never fire. 0.94 sits between burst formation (0.90) and near-dup collapse (0.97): a real second POV chained into the burst splits off; a slight recompose of the same shot stays one scene. Kept a module constant (no new knob) — tunable later if real bursts split too eagerly or too rarely.
- Stage A near-dup collapse still runs **burst-wide** (a near-pixel-dup is redundant regardless of sub-scene; two ≥0.97 frames are always in the same sub-cluster anyway).

Net for the headline case: a burst that's really *two* scenes can now yield 2 (or more) Keeps — one per scene — where the per-burst budget would have capped it at 1–2 total. Tests extended to 28 cases (per-scene budget isolation, two-scenes-two-keeps, single-scene-not-inflated, missing-embedding-own-scene, burst-wide near-dup-within-scene). The test fixtures gained a scene-aware embedding helper so same-scene frames cluster at ~0.96 (one sub-cluster, not near-dups) and distinct scenes at ~0. Full suite 398 passing.

---

## Burst time-gap slider — non-linear granularity (2026-07-26)

The Settings → Grouping "Time gap" slider was linear `15–600s @ 15s`, so the short end (where burst boundaries actually matter) was coarse and the long end over-detailed. Reworked to a **non-linear value ladder** in [frontend/src/modals/SettingsModal.jsx](../frontend/src/modals/SettingsModal.jsx): 1–30s in 1s steps, 30–60s in 10s steps, 60s–5min in 15s steps (49 stops, `TIME_GAP_STEPS`). A native `<input type=range>` can't vary its step, so the slider now runs over the array *index* (uniform step 1) and maps each index → gap value; `timeGapToIndex` snaps any stored value (incl. pre-ladder ones like 45s) to the nearest stop for handle placement, `formatTimeGap` handles the readout. Backend `time_gap_seconds` was already an unfloored float ≥0, so 1-second gaps are valid with no server change. Desktop only — the mobile SettingsView slider is a separate sync-on-demand port. `frontend/dist` rebuilt.

---

## Burst redundancy rejection — near-dup collapse + review budget (2026-07-26)

Follow-up to the multi-keep pass below: make a big tight burst generate *rejects*, not a wall of Maybes. The winner-relative reject rule had a blind spot — in a 30-frame burst of near-identical shots the frames cluster so tightly that none is far enough below the winner to trip `burst_keep_reject_delta`, so the whole tail routed to Maybe and flooded the review pile. `_cap_burst_keeps` in [backend/routers/decisions.py](../backend/routers/decisions.py) now runs two layered stages per burst before the existing floor pass:

- **Stage A — near-duplicate collapse.** The lower-priority frame of any cosine-near-identical pair (≥ `_NEAR_DUP_COSINE` = 0.97, the same threshold `phase2_quality/burst_ranker._collapse_near_duplicates` uses before the LLM rank) is redundant regardless of score → **Reject**, reason `burst_near_duplicate`. New in-memory helper `_near_duplicate_losers` reuses the embeddings already on the rows (L2-normalised → dot product is cosine) with no DB round-trip; a frame without an embedding can never be collapsed (can't prove it's a dup). The higher-priority frame of each pair survives as the rep; the winner is a rep by construction.
- **Stage B — review budget.** After the Keep gates (unchanged), a burst may keep at most `max_burst_maybes` Maybe alternates; every further non-surviving frame → **Reject**, reason `burst_redundant`, instead of adding yet another Maybe. Clearly-weak frames still reject on their own merits (`burst_keep_reject`) and don't consume a Maybe slot; a scoreless frame can't be "clearly weak" so it only rejects via the budget path, never gets auto-trashed on missing data.

One new setting: `max_burst_maybes` (default **2**, range 0–10; `0` rejects every non-kept frame, large values reproduce the prior all-to-Maybe behaviour). Both new reject reasons bucket as `burst_duplicate` in the preview breakdown. So a 30-frame near-identical burst now yields ~1 Keep + 2 Maybes + the rest Reject instead of 1 Keep + 29 Maybes. Settings → Model gained a "Max Maybe alternates per burst" control under the burst-dedup toggle. Tests extended to 33 cases (Stage A collapse incl. rep-survival + missing-embedding safety, Stage B budget + `=0` + clearly-weak-doesn't-consume-slot + scoreless-frame routing). Full suite 393 passing; `frontend/dist` rebuilt.

---

## Burst multi-keep with graded demotion (2026-07-26)

Reworked the burst keep-cap (added 2026-07-25, which forced exactly one auto-Keep per similarity burst) into a **multi-keep** pass so a burst can auto-cull *several* genuinely-good frames to Keep when the metrics justify it — Philipp's request. `_cap_burst_keeps` in [backend/routers/decisions.py](../backend/routers/decisions.py) now:

- Always keeps the best-by-priority member (unchanged `top_n_candidates` hero anchor).
- Walks the remaining in-burst Keeps best→worst and lets each **also** keep if **both** gates hold and we're under the max: (a) its score clears its own mode's Keep cutoff by `burst_keep_margin` (not barely over the line), and (b) it's within `burst_keep_winner_delta` of the burst winner (a genuine alternate). Score field follows the decision mode — `personal_score` for `personal_keep` members, `overall_score` for threshold-mode, matching the number the Keep verdict was actually made on.
- **Graded demotion** for non-survivors (Philipp's follow-up): a frame more than `burst_keep_reject_delta` below the winner, or below its mode's Maybe cutoff, is the clearly-weaker duplicate → **Reject** (reason `burst_keep_reject`, bucketed as `burst_duplicate` in the preview breakdown); everything else → **Maybe** (reason `burst_keep_cap`, unchanged). A member with no usable score never survives and always demotes to the safer Maybe, never auto-trashed.

Four new settings (`_NUMERIC_SETTINGS` + `SettingsRequest` + `get_decision_thresholds`, all live-read): `max_burst_keeps` (default **2**, range 1–10 — **1 reproduces the old cap-at-one behaviour exactly**), `burst_keep_margin` (8.0), `burst_keep_winner_delta` (6.0), `burst_keep_reject_delta` (20.0). The master toggle keeps its old key `auto_cull_cap_burst_keeps`; its Settings → Model label changed from "Cap Keep at one photo per burst" to "Deduplicate Keeps within a burst" with the four knobs nested beneath it. Applies to both `POST /auto-cull` and `GET /auto-cull/preview` (same helper → preview counts stay honest); the preview response gained `reject_demotions` and each `demotions` entry now carries `to: "maybe" | "reject"`. Runs before the keep-guarantee floor pass as before — the winner always stays Keep, so a burst can never reach zero Keeps here. Rewrote `tests/test_burst_keep_cap.py` (25 cases: cap-at-one, multi-keep survival, both gates, graded reject, mode-aware score field, missing-score safety). Full suite 385 passing; `frontend/dist` rebuilt.

---

## Full-codebase code review (2026-07-26)

An in-depth correctness + streamlining review of the whole application (backend routers, phase 1–3 scorers/model, frontend). Baseline was green (376 tests) with a large uncommitted WIP branch (preview caching, one-ahead RAW decode prefetch, low-IQA reject, burst keep-cap, jpeg_priority stub mirroring, GroupLoupe evaluated-filter). The WIP itself reviewed as correct and well-tested; the review surfaced three issues *outside* it, each fixed in its own commit and covered by a test:

- **`top_n_candidates` returned input order when `n >= len(images)`** ([backend/group_scoring.py](../backend/group_scoring.py)). The `if n >= len(images): return list(images)` short-circuit skipped the sort, silently breaking the "index 0 is the best photo" contract every caller relies on. The real victim was `_collapse_near_duplicates` in [phase2_quality/burst_ranker.py](../phase2_quality/burst_ranker.py), which calls `top_n_candidates(members, len(members))` precisely to keep the *best* near-duplicate frame as the burst representative — with the short-circuit it kept whichever frame was listed first instead, so a burst could surface a worse frame as its visible "best." Two existing tests passed for the wrong reason (they listed the best photo first, so input order == priority order); hardened both by reordering the input, added explicit `n>=len`/`n==len` ordering tests. Verified with a standalone reproducer before fixing.
- **Duplicated, drifted `_shot_at_to_unix`** — two copies (routers/model.py, routers/decisions.py) parsed the same `shot_at` column differently: the decisions.py "tiny copy" silently returned `None` for `Z`-suffixed UTC timestamps and integer epochs; model.py's missed the space-separator shape. Two burst-clustering call sites parsing the same column differently is a latent grouping inconsistency. Consolidated one superset implementation into [backend/constants.py](../backend/constants.py) (stdlib-only, importable from either router without a circular import — the original excuse for the copy), which is the module explicitly meant to kill this exact drift.
- **Dead `isDuplicateRank` in GroupLoupe's filmstrip** ([frontend/src/views/GroupLoupe.jsx](../frontend/src/views/GroupLoupe.jsx)). The WIP added the near-duplicate rank-tie indicator (`≈N`) to `SurveyTile` but `LoupePane` extracted `isDuplicateRank` and never rendered it, so the same photo read as a tie in the Survey grid but a plain ordinal in the filmstrip (eslint flagged the dead var). Rendered `≈N` + the near-duplicate aria-label/title in the filmstrip badge too, matching SurveyTile.

Scanned clean (no fix needed): no SQL string interpolation (all dynamic `IN (…)` clauses use `?` placeholders and guard against empty lists), no mutable default args, no bare/`pass`-swallowing excepts. The new one-ahead RAW decode prefetch is thread-safe — `decode_raw` touches only locals (independent LibRaw contexts, no shared globals), and at most one decode ever runs at a time (prefetch worker decodes N+1 while the main thread *scores* N). The PR3 uncertainty ensemble snapshots members under the lock before predicting. Deliberately *not* refactored: the double companion-lookup in `run_auto_cull`'s mirror path (micro-optimization not worth the risk in a file-moving path). Full suite 377 passing; `npm run build` clean; `frontend/dist` rebuilt.

---

## Backlog / future TODOs

- **Apply the grid's grouping styling to the Maybe and Reject views (added 2026-07-26, Philipp).** The grid view of still-undecided photos renders the similarity-grouping mechanism (group tiles / chip row / group summary via [GroupTile](../frontend/src/views/GroupTile.jsx)). The Maybe and Reject decision views don't share that treatment — they list photos flat. Want the same grouping UI/styling carried into those two views so a decided folder still reads as grouped bursts, not a flat wall. Not yet scoped — needs a design pass on how grouping interacts with a decision-filtered set before implementing.

- **Make "manual overrides of auto-culls actually train the model" observable, and reconsider learning from agreements (added 2026-07-27, Philipp).** Philipp flagged (correctly, as a principle) that *any* manual decision must train the personal model — **especially** one that overrides or confirms a prior auto-cull verdict. **First, the reassuring part, already verified in-code so a future session doesn't re-derive it:** this behaviour already exists and is already tested. `_apply_decision` in [backend/routers/decisions.py](../backend/routers/decisions.py) skips the `training_samples` write only for the auto-cull decision itself (`is_auto=True`) and mirrored RAW/JPEG companion writes (`_train=False`); a later **manual** K/M/X arrives with `is_auto=False` and `INSERT … ON CONFLICT(sample_uuid) DO UPDATE`s the sample. `tests/test_training_sample_provenance.py::test_manual_override_of_auto_cull_trains` pins exactly this (auto-reject → 0 samples, then manual keep → trains as keep), plus `test_manual_then_auto_does_not_erase_sample` guards the reverse. So there is **nothing to fix in the training path** — do not touch the `is_auto` gate ([[project_auto_cull_not_training]]). The real gaps this concern exposes: (1) **observability** — nothing surfaces whether the corpus is actually *growing* from overrides vs. stagnating. The whole worry arose because auto-Maybe photos sit in the review pile untrained until a human touches them, which is easy to never do. Build a signal (dashboard stat / model banner) from the `auto_decision_log` — a `sample_uuid` join across `is_auto=1` then `is_auto=0` rows answers "how many auto-decisions has a human since confirmed/overridden", which is precisely what that durable log was built for. (2) **Learn from agreements, not just disagreements** — today only a manual *touch* trains; silently leaving an auto-Keep in place teaches the model nothing, so the corpus is biased toward the cases the user disagreed with. Consider making "confirm this auto-decision" a first-class, low-friction action (bulk "confirm all remaining auto-Keeps"?) so agreements enter the corpus too. Related: [[feedback_kr_only_culler]] (user rarely uses Maybe manually, so the auto-Maybe pile is exactly where this stagnation bites hardest).

---

## Fixed: auto-cull left jpeg_priority RAW twins behind, undecided (2026-07-26)

Philipp noticed after the last auto-cull run that only the processed (JPEG) side of RAW+JPEG pairs had moved into `_Keeps`/`_Maybes`/`_Trash` — the RAW twin was left sitting undecided in the source folder. Root cause was collateral damage from the previous day's fix (commit `8d696a5`, "Fix auto-cull mirroring RAW+JPEG pairs to the wrong verdict"): `run_auto_cull` started passing `_mirror=False` to `_apply_decision` so two independently-scored RAW+JPEG siblings could no longer clobber each other's verdict. That reasoning only holds under `analysis_mode='full'`. Under `analysis_mode='jpeg_priority'` (Philipp's active setting), a RAW with a processed sibling never gets scored at all — it gets an EXIF-only stub row (`analysis_status='skipped'`) that exists *purely* so a decision can mirror onto it and move it (see `analyze_folder` in [backend/routers/analysis.py](../backend/routers/analysis.py)). `run_auto_cull`'s own query only selects `analysis_status='done'` rows, so these stubs are invisible to it — with mirroring off, nothing ever carried the JPEG's verdict over. 557 RAF files were affected.

Fix in [backend/routers/decisions.py](../backend/routers/decisions.py): `run_auto_cull` now checks each row's companion via the existing `_find_companion_image_id`, and mirrors *only* when that companion is a skipped stub (new `_companion_is_skipped_stub` helper, factored out of `_companion_is_hidden` which already encoded this same case for the manual K/M/X path). Independently-scored companions (full mode, or a display-mode-hidden but fully-scored sibling) still get `_mirror=False`, preserving the Jul 24 fix.

Repaired the 557 already-stuck files with a one-off script reusing the real `_apply_decision` path (mirrors the sibling's existing decision, `_mirror=False`/`_train=False` so it behaves exactly like the corrected auto-cull would have) — all moved cleanly to match their JPEG twin.

New regression test `test_auto_cull_still_mirrors_to_skipped_jpeg_priority_stub` in [tests/test_raw_jpeg_companion.py](../tests/test_raw_jpeg_companion.py), sitting right next to the test that pins the Jul 24 fix so the two can't regress each other again. 361/361 passing.

---

## Fixed: HIF previews re-decoded on every reload — now all formats are cached (2026-07-25)

Philipp noticed thumbnails taking a long time to appear after an app reload on an already-analyzed folder of 664 `.HIF` files (Fuji X100VI/X Half HIF-only shooting). Root cause in [backend/routers/analysis.py](../backend/routers/analysis.py): `GET /previews/{id}` cached only RAW formats (`preview_path` → `data/previews/*.jpg`) and re-decoded everything else on every single request, on the assumption that "not RAW = already compressed = cheap to decode." That holds for JPEG/PNG but is badly wrong for `.HIF` — HIF is a HEIF container, and pillow-heif decode is comparably expensive to a RAW demosaic. So a full-res HIF library paid that decode cost for all 664 files on every reload, with zero caching.

Fix: cache **every** format, not just RAW. Both `get_preview()` and `_load_preview_pil()` (the histogram/clipping-mask helper) now unconditionally check the disk cache first, and on a miss generate via the existing `_generate_preview()` (which already branches RAW-demosaic vs. plain-decode internally), write to `data/previews/{id}.jpg`, and persist `preview_path`. Cheap formats just get a near-instant first write; expensive ones (HIF, RAW) get the speedup RAW already had. The stale "no cache needed" fast-path branch is gone.

Cache lifecycle (per Philipp's spec — "cache cleaned when the tab is closed or the files are moved elsewhere on disk") turned out to already be fully handled by existing infrastructure, once non-RAW rows actually populate `preview_path`:
- **Tab close / re-analyze** → `POST /clear-folder` → `_delete_image_rows()` deletes the DB rows *and* their `data/previews/*.jpg` files ([backend/routers/images.py](../backend/routers/images.py)).
- **File moved/deleted outside the app** → `POST /sync-folder` (fired on tab focus) detects rows whose `file_path` no longer `.exists()` and runs the same `_delete_image_rows()` cleanup.
- **In-app K/M/R move** deliberately leaves `preview_path` untouched — the cached bytes are still valid since only the file's location changed, not its content.

One consistency fix fell out of this: the `prefer_sidecar_preview` toggle-flip in [backend/routers/settings.py](../backend/routers/settings.py) wipes the whole cache dir via `glob("*.jpg")` but previously nulled `preview_path` for RAW rows only. Now that non-RAW rows are cached too, that would strand them pointing at deleted files, so the DB-null is now unconditional (`UPDATE images SET preview_path = NULL`) to match the blanket disk wipe. (`get_preview` tolerates a dangling `preview_path` — it re-checks `.exists()` and regenerates — so this was belt-and-suspenders, not a crash.)

3 new tests in [tests/test_preview_cache.py](../tests/test_preview_cache.py): non-RAW preview is cached + `preview_path` persisted; second request serves cached bytes even after the source is changed on disk (the reload path); missing source with no cache 404s. 360/360 passing.

---

## Fixed: manually-anchored groups silently vanishing (2026-07-25)

Reported by Philipp as "groupings are getting lost after reloading the app." Root cause was NOT reload, caching, or the frontend at all — `POST /set-manual-group` ([backend/routers/model.py](../backend/routers/model.py)) could leave a manual group's remaining member pinned to a stale `manual_group_id` after removing photos from it.

`group_by_similarity()` ([phase2_quality/similarity_scorer.py](../phase2_quality/similarity_scorer.py)) correctly hides any bucket (manual or auto) with fewer than 2 members — that's an intentional, tested rule (`test_manual_anchor_singleton_drops_from_results`). But GroupLoupe's "Remove from group" button only reassigns the *removed* photos to fresh singleton anchors (`mode: 'singletons'`); it never checked whether the photo(s) left behind in the *old* bucket now numbered exactly one. A stranded singleton anchor is a dead end: it can never render again (size-1 buckets are always hidden) and it can never fall back into auto-clustering either, since a non-NULL `manual_group_id` always short-circuits straight into the anchor phase, bypassing cosine/time clustering entirely. The photo would just quietly disappear from every future `/similarity-groups` response — including the very next reload, which is why it read as a reload bug.

All four mutating modes (`new_group`, `singletons`, `join_group`, `clear`) can strand a source bucket this way, not just `singletons`. Fix: a new `_reclaim_stale_singleton_anchors()` helper snapshots each affected photo's prior anchor before the mutation, then — after the UPDATE — sweeps any of those anchors now sitting at exactly one member and nulls them back to `NULL`, matching the behavior an unattached auto-cluster member already had. `restore_assignments` (the undo path) didn't need the same treatment — it replays a snapshot captured *after* a prior mutation already ran through the sweep, so it can't reintroduce a stale anchor.

5 new regression tests in [tests/test_manual_group.py](../tests/test_manual_group.py) covering all four modes stranding a 2-member bucket down to 1, plus a control test confirming a 3→2 shrink leaves the surviving pair's shared anchor untouched. 357/357 passing.

---

## Burst keep-cap post-pass for /auto-cull (2026-07-25)

User ran auto-cull on a 664-photo import and found dozens of near-identical burst frames (same pose, same beach scene, seconds apart) all independently landing in Keep or Maybe. Investigation: KaMeRa already has burst-aware dedup — `_collapse_near_duplicates` in [phase2_quality/burst_ranker.py](../phase2_quality/burst_ranker.py) (shipped 2026-05-17, cosine ≥0.97) — but it only runs inside `rank_burst()`, the LLM comparison triggered when a group is opened in GroupLoupe or picked up by the prerank worker. It was never wired into `/auto-cull`, which scores every photo independently via `_compute_auto_decision` with no cross-photo awareness at all. The existing `burst_keep_guarantee` post-pass in `run_auto_cull` only handles the opposite failure mode — rescuing a burst from *zero* keeps — nothing capped the other end.

Added the inverse post-pass, `_cap_burst_keeps()` ([backend/routers/decisions.py](../backend/routers/decisions.py)): reuses the same `/similarity-groups` clustering `_group_undecided_into_bursts` already builds (not the LLM-based dedup — needs to run over the whole undecided batch cheaply, no per-burst model call). For every burst with ≥2 members and ≥2 photos independently decided `keep`, keeps only the single best-by-priority member (`top_n_candidates` — face_sharpness → eyes_open → sharpness → IQA → aesthetic → overall_score, same priority as the group hero selector) and demotes every other keep to `maybe` (reason `burst_keep_cap`) so the alternates stay reviewable rather than silently vanishing to Reject. Runs in both `run_auto_cull` and `auto_cull/preview`, and *before* the existing `burst_keep_guarantee` floor pass — safe ordering, since capping a burst down to one keep can never trigger that pass's zero-keeps rescue condition.

New setting `auto_cull_cap_burst_keeps` (bool, default **on**), same registry pattern as the PR3 `auto_cull_uncertain_to_maybe` toggle — `DEFAULT_AUTO_CULL_CAP_BURST_KEEPS` in [phase1_technical/quality_analyzer.py](../phase1_technical/quality_analyzer.py), wired through `_NUMERIC_SETTINGS`/`_BOOL_SETTINGS` in [backend/routers/settings.py](../backend/routers/settings.py), toggle added to Settings → Model → Decision thresholds ([frontend/src/modals/SettingsModal.jsx](../frontend/src/modals/SettingsModal.jsx)). Both endpoints' response payloads gained a `demotions` field alongside the existing `promotions` (not yet surfaced in the UI — same as `promotions` today).

7 new tests in [tests/test_burst_keep_cap.py](../tests/test_burst_keep_cap.py) (unlike the existing `test_burst_keep_guarantee.py`, which replicates the policy inline, these call the actual production `_cap_burst_keeps` directly since it was extracted as an importable function). 359/359 passing. `npm run build` clean.

---

## Decision-reason logging + stronger AI-pick loupe interaction (2026-07-25)

Prompted by a design discussion on whether the in-group LLM burst ranking (`/rank-burst`) should feed the auto-cull decision path. Conclusion was no — the LLM (`qwen2.5vl:7b`, a generic vision model) shouldn't override the personal GBR model in the committed-decision path; see [docs/Learning-Journal.md](Learning-Journal.md) for the full reasoning. Two smaller, lower-risk improvements shipped instead:

**1. Decision-reason logging (backend).** The `decisions` table previously stored only `image_id | decision | decided_at` — no record of *why* a decision was made, and no history at all (auto-cull reasons like `burst_keep_guarantee` or `uncertain` were computed and returned in the API response but never persisted). Two migrations:
- **v49** adds `reason TEXT` to `decisions` — current-state only, overwritten on re-decision, cleared by Clear Analysis/tab-close (same lifecycle as the row itself).
- **v50** creates `auto_decision_log` (append-only, keyed on `sample_uuid`, never overwritten or deleted) — logs every decision, manual and auto, with an `is_auto` flag. Deliberately excluded from `clear_analysis()`'s delete list, same durability convention as `training_samples`/`shooting_log`, so it survives Clear Analysis and closing a tab. This is what finally makes "how often does each auto-cull path fire" and "how often do I override an auto-decision" answerable questions instead of guesses.

`_apply_decision()` ([backend/routers/decisions.py](../backend/routers/decisions.py)) grew a `reason` param threaded through from all four call sites (`POST /decision` → `"manual"`, `POST /bulk-decision` → `"manual"`, the auto-cull loop → the rule name from `_compute_auto_decision`, RAW/JPEG companion mirror → inherits the parent's reason). Also fixed a gap in the burst keep-guarantee post-pass: it was promoting a photo to Maybe without updating that photo's `reasons` dict entry, so the log would have recorded the photo's pre-promotion reject reason instead of `"burst_keep_guarantee"`.

**2. Stronger AI-pick interaction in GroupLoupe** ([frontend/src/views/GroupLoupe.jsx](../frontend/src/views/GroupLoupe.jsx)) — the LLM's judgment surfaced more usefully at the one place it already legitimately influences the UI (the loupe), not pushed into auto-cull:
- **Reason caption in Filmstrip/Loupe mode.** Survey mode already showed the AI pick's one-line reason as a small badge under the "Best" star; Filmstrip mode (the more commonly used view) never received `heroReason` at all. Added a caption over the big preview, shown only while the hero itself is focused.
- **`A` — "accept the AI pick."** New hotkey bound to the existing `runBatch('reject')` (already wired to the "Keep best · Reject rest" toolbar button) — keeps `effectiveHeroId`, rejects the rest of the burst, closes the loupe. One keystroke for ripping through a near-dupe burst instead of reaching for the mouse.
- **Rank ordinals.** `burstResult.rankings` carries a full best-to-worst order, but only rank-1 was ever surfaced (the star badge). Non-hero tiles in both Survey and Filmstrip mode now show a small numbered badge (`2`, `3`, …) for photos the LLM actually ranked, so a burst reads as "already triaged" instead of requiring the user to reopen the top-bar inspect popover to see the full order.
- The "AI ranked top N of M" filter-awareness chip (click-to-inspect which photos were LLM-evaluated) was already shipped from an earlier session — no changes needed there.

345/345 tests passing (migration verified idempotent + non-destructive against a copy of the live DB first). `npm run build` clean, dev-server smoke test clean.

---

## `_Keeps_reviewed` review gate — Pieces 1–3 shipped (2026-07-25)

Full plan in [docs/PLAN-keeps-reviewed.md](PLAN-keeps-reviewed.md). Provenance now watches `_Keeps/_Keeps_reviewed` instead of `_Keeps` (already shipped Provenance-side 2026-07-24) — a keeper only gets archived once a human has actually looked at it. This session shipped the KaMeRa-side counterpart, all three pieces:

- **Piece 1 — manual keep skips the gate.** `resolve_dest_folder()` ([backend/file_mover.py](../backend/file_mover.py)) grows a keyword-only `reviewed: bool` param — when `decision == "keep" and reviewed`, it nests `_Keeps_reviewed` under wherever "keep" resolves to (default subfolder *or* an active folder override, so custom-Keeps-folder users still get correct nesting). `_apply_decision()` ([backend/routers/decisions.py](../backend/routers/decisions.py)) passes `reviewed=(decision == "keep" and not is_auto)` — a manual K already *is* the review, so it routes straight past plain `_Keeps`. Auto-cull keeps (`is_auto=True`) are unchanged, still landing in plain `_Keeps`.
- **Piece 2 — `POST /mark-reviewed`.** New `_move_to_reviewed()` helper validates the photo is decided `keep` and currently sitting directly under `_Keeps` (400 otherwise — calling it on the wrong photo is a caller bug, not a race), then moves it into `_Keeps_reviewed`, mirroring onto a hidden RAW/JPEG companion the same way `_apply_decision` does. DetailView gained a "Mark reviewed" button (green, below K/M/R) shown only via a new `needsReview(image)` predicate in [frontend/src/ui/format.js](../frontend/src/ui/format.js) — true iff `decision === 'keep'` and `file_path` contains `_Keeps` but not `_Keeps_reviewed`. No new DB column; reviewed-state is fully derived from the path, matching how "kept" is already derived from the `decisions` table. ImageCard also grew a hover-only top-right check button for the same action (skipped in select mode).
- **Piece 3 — bulk "Mark all reviewed".** `POST /bulk-mark-reviewed` loops the Piece-2 helper over a list of ids; idempotent — a photo already in `_Keeps_reviewed` is silently skipped (not an error), so re-running over a mixed view is safe. New bulk-action bar in the `decisionFilter === 'keep'` grid view (App.jsx), scoped to only the not-yet-reviewed subset via a `needsReviewIds` memo, mirroring the existing Maybe/Reject bulk-bar confirm pattern.
- **Reviewed-state UX: badge, not disappear.** A marked-reviewed photo stays visible in the Keep-filtered view rather than dropping out — matches the "keep filtered views minimal, stay in context" pattern already used elsewhere.
- **Bug found + fixed mid-implementation:** `_apply_undo()`'s no-`previous_path`-supplied reconstruction (used for the RAW/JPEG companion's mirrored undo, which always passes `previous_path=None`) walked up exactly one directory level to escape the K/M/R subfolder. A reviewed keep sits *two* levels deep (`_Keeps/_Keeps_reviewed/photo`), so the old logic landed the companion back in plain `_Keeps` instead of its true source folder. Fixed by stripping the extra `_Keeps_reviewed` level first when present. Caught by two pre-existing companion-mirror tests failing after the Piece-1 routing change ([tests/test_raw_jpeg_companion.py](../tests/test_raw_jpeg_companion.py), [tests/test_analysis_mode_jpeg_priority.py](../tests/test_analysis_mode_jpeg_priority.py)) plus a new regression test.
- Verified live end-to-end against the running launcher instance (synthetic DB rows + scratch files, not real photos): manual keep → `_Keeps_reviewed` directly, auto-cull keep → plain `_Keeps`, mark-reviewed → moves + 400s correctly on wrong-state calls, undo → true source folder (not plain `_Keeps`), bulk endpoint moves + skips correctly.
- 10 new tests in [tests/test_keeps_reviewed.py](../tests/test_keeps_reviewed.py) plus 4 new `resolve_dest_folder(reviewed=...)` cases in [tests/test_file_mover.py](../tests/test_file_mover.py). 345/345 passing.

## Batch analysis now absorbs files that land mid-import (2026-07-24)

Philipp spotted the mismatch live: Provenance showed 313/1226 photos imported while the running batch's progress bar was still stuck at 6/137. Root cause — `analyze_folder` took a one-time `folder.iterdir()` snapshot at batch start (`to_analyze`/`total` frozen at that instant) and explicitly paused the `FolderWatcher` for the whole batch duration (resumed only in `_run_batch`'s `finally`). Any file Provenance dropped in after the snapshot was invisible to both the running batch (fixed list) and the watcher (paused) — it would sit undiscovered until the batch ended and either the watcher resumed or someone manually reran analyze-folder.

**Fix:** `_run_batch` in [backend/routers/analysis.py](../backend/routers/analysis.py) now rescans the source folder every `RESCAN_INTERVAL_SECONDS` (10s) via a new shared `_scan_candidates()` helper (extracted from the scan logic that used to live inline in `analyze_folder`), appends any not-yet-queued analyzable files to the live `to_analyze` list, and bumps `_progress["total"]` so the progress bar keeps climbing instead of finishing early. The watcher still stays paused for the whole batch (unchanged) — one continuous batch now absorbs the entire import instead of needing a manual follow-up run. 324/324 tests passing; no new tests added since the existing analyze/batch/progress suite already exercises `_run_batch`'s shape and this is an additive rescan inside the same loop — a full mid-batch-arrival integration test would need a live filesystem-timing harness, deferred as a `# TODO` unless a regression shows up.

---

**Open TODOs (backlog, added 2026-07-24):**
- ~~**Groupings are lost after reloading the app.**~~ Fixed 2026-07-25 — see the entry above. Root cause was a stale `manual_group_id` left on a group's remaining member after removing photos from it, not reload/caching.
- **Surface AI-burst-rank vs. auto-cull agreement in the UI.** The in-group LLM ranking (`/rank-burst`, [phase2_quality/burst_ranker.py](../phase2_quality/burst_ranker.py)) picks a `llmHero` per group independently from the auto-cull/personal-model decision on each photo. Right now there's no UI signal for whether the LLM's top pick is the *same* photo the auto-cull mechanics would keep, or whether they disagree. Would help catch cases where the vision-model rank and the technical/personal-model score are pulling in different directions. Needs: compare `llmHero.id` (GroupLoupe.jsx) against the group member with `decision == 'keep'` (or the top personal_score if undecided), then render an agree/disagree indicator — e.g. a chip or icon distinct from the existing "AI ranked top N of M" chip. Not started.
- ~~**Build the `_Keeps_reviewed` review gate.**~~ Shipped 2026-07-25 — see the entry above.
- **Allow keeping more than one photo per group/burst.** Added 2026-07-26 (Philipp). Today the burst-aware logic is single-winner: `_cap_burst_keeps()` ([backend/routers/decisions.py](../backend/routers/decisions.py), shipped 2026-07-25) demotes a burst down to exactly *one* keep (the top-by-priority member) and pushes every other keep to Maybe; the group hero selector and `/rank-burst` LLM pick likewise surface a single "best." But a burst often has ≥2 genuinely keep-worthy frames (different expression, different subject in frame, a deliberate bracket) that shouldn't collapse to one. Explore letting a group retain N>1 keeps — e.g. a configurable per-burst keep budget, a score-gap threshold (keep everything within X of the best rather than only rank-1), or surfacing the alternates as "co-keeps" instead of demoting them to Maybe. Touches `_cap_burst_keeps` (the cap is the thing to relax), the `top_n_candidates` priority selector, and the GroupLoupe hero/`A`-accept interaction (which currently assumes one winner). Not started — design question first (what's the right budget signal), then implementation.
- **Detail view / grid selection still loses focus mid-batch, despite two fix attempts.** Reported 2026-07-25: with DetailView open (or a photo selected in grid view) while an analysis is actively running, the viewed/selected photo still gets scrolled away or swapped as new photos land. Commit `499596c` (2026-07-23) made `selectedIdx` re-resolve by photo id instead of raw array index (since `/images` returns newest-first, insertion shifts every existing photo's position). That held up under static review, so a same-session follow-up patched a second bug in [frontend/src/App.jsx](../frontend/src/App.jsx) (~line 1148): the grid's `scrollIntoView`/focus-steal effect was keyed on `selectedGridIdx` (a *position*, not an id), so it re-fired and yanked scroll/focus on every mid-batch arrival even though `selectedIdx` was correctly still pointing at the same photo underneath. Fixed by gating the effect on the selected photo's id actually changing (`lastScrolledIdRef`) plus skipping entirely while `analyzing` is true. **Philipp confirmed this did NOT fix it** after a hard reload + resuming the in-progress batch — so there's at least one more path still causing the jump, not yet identified. Candidates not yet ruled out: the DetailView filmstrip's own re-render/reflow when `items` grows (visually shifts thumbnails even without an explicit scrollIntoView call); GroupLoupe's `trackFocusedScroll` path if a group's member list is mutating mid-batch; or the "hard reload mid-batch" resume path specifically re-triggering `userHasNavigatedRef`/effect ordering differently than a cold start into an idle tab. Needs live repro with a running batch (not just static review) to pin down before attempting a third fix.

## Launcher fixes: startup-hang, Dock icons, quit-teardown (2026-07-24)

App "wouldn't start" from Finder — showed **"Application Not Responding"** in the Dock, recurring over days. Root cause was NOT the backend (`/health` returned 200 in ~2ms, all endpoints answered): the **launcher applet** (`/Applications/KaMeRa.app`, an osacompile'd AppleScript) hung in `do shell script`. AppleScript's `do shell script` waits until the spawned shell's stdout/stderr pipe closes; `start.sh` runs `launcher.py` in the foreground and it never returns (blocks on the rumps menu-bar loop + uvicorn), so the pipe never closed → applet main thread wedged. `& `, `nohup … &`, all-fd redirects, `( … & )` subshell, and `disown` were all verified to still hang. **Fix:** detach the child into its own session via `/usr/bin/perl -e 'use POSIX; fork and exit; POSIX::setsid(); exec @ARGV' ./start.sh …` — `do shell script` returns in ~0.14s, backend reparents to launchd (PPID 1). See [docs/Learning-Journal.md](Learning-Journal.md) 2026-07-24 for the full narrative.

**Sibling fixes shipped same session:**
- **Python "rocket" removed from the Dock** — `launcher.py::_run_menubar_macos` sets `NSApplication.setActivationPolicy_(NSApplicationActivationPolicyAccessory)` before the rumps run loop → menu-bar-only, no Dock icon.
- **App window shows the KaMeRa icon, not Chrome's** — applet now `open`s the installed PWA shim `~/Applications/Chrome Apps.localized/KaMeRa.app` (own icon + Dock identity) instead of `open -a 'Google Chrome' --app=`.
- **Menu-bar Quit now closes the PWA window too** — previously it killed the backend but left the Chrome PWA window (and its lit Dock icon) behind. `launcher.py::_close_pwa_window()` runs on Quit. `osascript … to quit` and `NSRunningApplication.terminate()` both silently fail on a Chrome app-mode window; only reliable + safe handle is `pkill -f "Chrome Apps.localized/KaMeRa.app/Contents/MacOS/app_mode_loader"` (unique path — can't hit the user's regular Chrome).

**Applet source is now version-controlled** — [launcher/main.applescript](../launcher/main.applescript) is the source of truth, [launcher/build_app.sh](../launcher/build_app.sh) recompiles it into the bundle in place. Closes the "no buildable source in repo" risk that let the hang recur from an ad-hoc rebuild. `launcher.py` changes take effect on next launch automatically (runs from source, not a bundle); applet changes need `build_app.sh`. 323 tests green; a `window-controls-overlay` manifest experiment (remove the PWA title-bar text) was reverted — Philipp decided the standard bar is fine and it needed a PWA reinstall to take effect.

**Open TODOs (launcher 2026-07-24):**
- **Retire the launcher menu-bar item entirely.** "Open KaMeRa" is redundant (opens a duplicate Chrome window); "Quit" should be handled by quitting the KaMeRa **PWA from the Dock right-click menu**, which should trigger the full teardown (backend + PWA). Net: no menu-bar icon at all; Dock-quit does the work. Mechanism sketch: since the launcher owns the backend but not the PWA window, a Dock-quit of the PWA needs to *also* stop the backend — likely a small watcher (the launcher polls whether the PWA `app_mode_loader` is still alive and self-terminates the backend when it dies), or move backend lifecycle to a headless LaunchAgent.
- **Single Dock icon is architecturally blocked by the PWA.** A Chrome PWA can only load a URL — it cannot run a pre-flight script to start the backend, and it always claims its own Dock icon. So "one pinned PWA icon + click-just-works + backend not always running" are in tension (pick two). The only true one-icon path is a headless LaunchAgent keeping the backend up (background RAM cost) with the PWA pinned. Decision deferred; Philipp leaned on-demand. Revisit alongside the menu-bar-retirement TODO — they're the same architecture question.

**Resolved — menu-bar item retired in favor of a headless PWA watcher (2026-07-24, follow-up):** Philipp confirmed the on-demand direction (no LaunchAgent) and picked "retire the menu-bar entirely" over "keep a minimal Quit fallback." `launcher.py::_run_menubar_macos` (rumps menu-bar loop, "Open KaMeRa" + "Quit" items) replaced with `_watch_pwa_macos` — a headless loop that `pgrep -f`-polls `PWA_LOADER_PATH` every 2s (`PWA_POLL_SECONDS`) and terminates the tracked backend process the moment the PWA process disappears, i.e. whenever the user quits KaMeRa from the Dock (right-click → Quit or Cmd+Q on the PWA window). `_close_pwa_window()` (the old rumps-Quit-triggered PWA killer) is gone too — teardown now only ever runs *after* the PWA has already exited, so there was nothing left for it to call. `rumps` dropped from `requirements.txt`/`NOTICES.md`/`README.md` (no longer imported on the primary macOS path); `pystray` stays for Windows/Linux, which have no installed-PWA equivalent to watch and keep the tray icon's manual Open/Quit. No applet or `build_app.sh` changes needed — the applet already just health-checks the backend and `open`s the PWA shim; the watcher lives entirely inside `launcher.py`, which the applet already runs detached via the perl/setsid trick. 323/323 tests still passing (no test file covers `launcher.py` directly — it's a standalone script). Verified live end-to-end: launched fresh via the real `/Applications/KaMeRa.app` applet, confirmed uvicorn + the PWA's `app_mode_loader` + `launcher.py`'s watch loop all came up, then killed the PWA process to simulate a Dock-quit — the backend process and `launcher.py` both exited on their own within the same second, and `/health` stopped responding.

## RAW+JPEG display mode: filter grid tiles + mirror decisions to the hidden companion (2026-07-22)

User asked why `DSCF1041.JPG` scored Technical Quality 99 while `DSCF1041.RAF` scored 46 — same shot, RAW+JPEG capture. Answer: the JPEG is Fuji's in-camera-processed rendition (sharpened, denoised) while the RAF is the raw demosaic with zero sharpening applied (sharpness is deliberately non-recoverable, see Key Active Decisions). Not a bug — but it surfaced that RAW+JPEG pairs have always been two fully independent `images` rows (own id, own scores, own decision) with **zero linkage**, doubling grid density and letting the two sides of one shot get decided completely independently — confirmed live in `data/app.log`: `DSCF1041.JPG` moved to `_Keeps/` while `DSCF1041.RAF` moved to `_Trash/` in the same batch.

**New setting — `raw_jpeg_display_mode`** (`"raw" | "processed" | "both"`, default `"both"`, Settings → Display → "RAW+JPEG grid tiles"). First string/enum setting in the app; added a `_STRING_SETTINGS` registry in [backend/routers/settings.py](../backend/routers/settings.py) alongside the existing `_NUMERIC_SETTINGS`/`_BOOL_SETTINGS`, wired through `GET`/`POST /settings` and `tunable_setting_keys()` (so it's included in taste-model backup/export).

**Pairing is inferred at query time, not stored.** A RAW+JPEG pair = two rows in the same `source_folder` whose filenames share a stem (case-insensitive), one `format` in `RAW_FORMATS` and one not (`is_raw_format()` / `companion_stem_matches()` in [backend/constants.py](../backend/constants.py)). No schema migration. `GET /images` (`_filter_raw_jpeg_pairs` in [backend/routers/images.py](../backend/routers/images.py)) hides one side of a detected pair when mode isn't `both` — orphans (no same-stem sibling in the folder) always show regardless of mode.

**Decisions mirror to the hidden companion.** `_apply_decision` and `_apply_undo` in [backend/routers/decisions.py](../backend/routers/decisions.py) look up the companion via `_find_companion_image_id` and recursively apply the same decision (or undo) to it, so both files move together and the DB stays consistent even though the user only saw one tile. Guarded against re-entrancy (`_mirror=False` on the recursive call) and best-effort (a companion move failure never fails the visible photo's already-committed decision).

**The companion never trains the model — this is the part that needed care.** RAW and processed renditions of the same shot score very differently on sharpness (46 vs. 99 in the motivating example), so mirroring a "keep" onto both would teach the GBR delta model contradictory sharpness labels for what is compositionally one photo. Fixed by decoupling "was this a system decision" (`is_auto`, controls auto-cull semantics) from "should this write a training sample" (`_train`, new internal-only param) — the mirrored companion call always passes `_train=False` regardless of whether the trigger was manual or auto-cull. Only the photo the user actually looked at contributes its feature vector to `training_samples`.

12 new tests in `tests/test_raw_jpeg_companion.py` (mirroring, training exclusion incl. the manual-decision case, undo mirroring incl. real-filesystem move/undo round-trip, cross-folder non-pairing, orphan visibility) + 5 in `tests/test_settings_persistence.py` for the new string-setting registry. Caught and fixed a pre-existing test-fixture gap along the way: `get_setting()` always resolves against `DEFAULT_DB_PATH` unless a caller threads a different path through explicitly (same as every other single-setting reader in the codebase — `_reject_to_system_trash_enabled`, `_is_sidecar_preview_preferred` — this is fine in production since there's only ever one real DB, but `test_settings_persistence.py`'s `settings_db` fixture wasn't intercepting that explicit-argument call, so it silently read the real DB instead of the tmp one). 323/323 passing.

`npm run build` clean. **Not browser-tested this session** — the backend couldn't be reached from this environment's tool sandbox during the verification pass, so the live Settings → Display click-through and a real K/M/R + undo cycle on a RAW+JPEG folder are still outstanding. Worth doing before trusting this in a real culling session.

---

## Watch-folder bulk-drop meltdown → queued single-worker watcher + locked model loads (2026-07-19)

Copying a full card (263 RAF+JPG files) into the watched folder wedged the whole backend: "auto-analysing pictures added to the watch folder seems to not work." Live diagnosis on the running process: 334 threads, 16 GB RSS, 258% CPU, HTTP unresponsive for 12+ minutes, ~3 files analyzed in 16 minutes.

**Root cause 1 — thread-per-file watcher.** `_PhotoEventHandler._handle` spawned one daemon analyze thread per watchdog event. Fine for tethered trickle-in; a bulk card drop spawned ~260 concurrent rawpy decodes (each RAF demosaic allocates hundreds of MB), thrashing RAM + the GIL until nothing progressed. **Fix:** [backend/file_watcher.py](../backend/file_watcher.py) now pushes detected files onto a `queue.Queue`; ONE worker thread (`watcher-analyze`) drains it sequentially — consistent with the standing "no cross-photo parallelism" decision. `_pending` set dedupes repeat events per file; `in_flight` = queued + running, so `/watch-progress` semantics (and Provenance's settle gate) are unchanged. `SETTLE_SECONDS` is measured from the file's *event* time, so a backlog doesn't re-pay the 2s settle per file. `stop()` signals a per-session `Event` + sentinel: backlog is dropped, the worker exits after at most the file it's mid-analyzing.

**Root cause 2 — unlocked lazy model loads.** The stampede drove concurrent first-calls into `_get_siglip()`, which has no lock: torch 2.11 + transformers 4.57 init weights on the meta device via accelerate *global* state, so racing `from_pretrained()`/`.to(device)` calls fail with "Cannot copy out of meta tensor" — and the *loser* set `_load_failed = True`, permanently poisoning SigLIP for the process even though a winner's load succeeded (log showed 3× "SigLIP-2 model ready" followed by every later embed/text call refusing). This is the exact race `_warm_models` documented and serialized in 2026-05 — but only inside the warm-up thread; warm-up racing the first analyze was still exposed. **Fix:** double-checked locking (`_load_lock`) in [similarity_scorer.py](../phase2_quality/similarity_scorer.py), [iqa_scorer.py](../phase2_quality/iqa_scorer.py), [aesthetic_scorer.py](../phase2_quality/aesthetic_scorer.py) — lock-free fast path once loaded; one loader at a time during first load; a concurrent second caller now waits and gets the winner's model instead of racing and poisoning the flag.

Tests: [tests/test_watch_progress.py](../tests/test_watch_progress.py) rewritten for the queue design (counters, dedupe, decision-subfolder skip, sequential drain, stop-abandons-backlog). Full suite 297 passed. The wedged process was killed after the fix landed; changes take effect on next launch (launcher runs uvicorn without `--reload`).

## Auto-close a fully culled group + advance to next item (2026-05-19)

GroupLoupe already auto-closed and advanced the grid cursor when the user resolved the last undecided photo via K/M/R or a batch action that finished off the hero ([GroupLoupe.jsx:510](frontend/src/views/GroupLoupe.jsx#L510), [596](frontend/src/views/GroupLoupe.jsx#L596) → `onAllDecided` → `closeLoupeAndAdvance`). Two adjacent paths leaked: K/M/R inside DetailView opened *from* a group context dropped the user back into a now-fully-decided GroupLoupe, and the grid-filmstrip group-batch action ("Keep best · Reject rest") parked the filmstrip on the resolved group hero.

**Centralized via a post-decision ref:** new `maybeResolveActiveGroupRef` in [App.jsx:1151](frontend/src/App.jsx#L1151) is called from `sendDecision`, `sendGroupDecision`, and `bulkDecide` after they commit. It looks up the active culling group (loupe → detail-group-context → grid-filmstrip selectedGroupId, all resolved through the live `displayGridItems` memo, never a stale `detailGroupContext` snapshot) and checks `images.every(img => decidedSet.has(img.id) || !!img.decision)`. When true, defers one tick via `setTimeout(0)` so the caller's pending `setSelectedIdx` / `setSelectedGroupId` updates commit first, then calls the (now generalized) `closeLoupeAndAdvance(targetId)` which tears down both loupe + DetailView and advances grid focus past the group.

**Double-fire suppression:** GroupLoupe's existing sync `onAllDecided` path still runs (snappier than `setTimeout(0)`), so `closeLoupeAndAdvance` stamps a `lastResolvedGroupRef` and the deferred ref-callback re-checks it inside the timer; if a sync handler beat it to the punch, the deferred path is a clean no-op. The marker clears when `activeCullGroup` changes, so undo + re-enter is fine.

Build clean (`npm run build`); not browser-tested — Claude doesn't have a UI surface, so the user verifies the K/M/R-in-DetailView-from-loupe and DetailView-filmstrip-batch flows end-to-end.

---

## Resume-on-interruption banner + auto-prerank on manual group create (2026-05-17, same day, follow-up)

User asked two related questions in succession that exposed two real holes:

**1. "If I close the laptop mid-analysis, will it continue?"** No — `analyze_folder`'s loop state is in-memory only; on process death the remaining files are simply not analyzed. The analyzer *already* skips done files on re-run (it subtracts `analysis_status='done'` paths from the disk walk at [backend/routers/analysis.py:765-771](backend/routers/analysis.py#L765)), so resume == "re-POST analyze." The fix is detection + a one-click prompt, not new resume logic.

- New endpoint `GET /folders/unfinished?folder_path=...&include_subfolders=...` walks the folder and returns `{total_on_disk, done_count, unfinished}`. Skips `_Keeps`/`_Maybes`/`_Trash` to match analyze_folder's walk semantics — otherwise moved-and-analyzed photos would forever flag a folder as "unfinished."
- `useTabs.js` fetches it on every tab focus (whenever `status === 'ready'` and `folderPath` is set). Result lands on the tab as `unfinishedCount` + `totalOnDisk`.
- `App.jsx` renders a small amber banner above the contextual toolbar when `unfinishedCount > 0`: *"X photos in this folder are not yet analyzed — a prior run was interrupted before finishing."* with a `Resume analysis` button that calls the existing `runAnalysisForTab` (same code path as the initial Analyze button).
- 6 new tests in `tests/test_folders_unfinished.py` covering: all-done returns 0, partial-done returns delta, `_Keeps/_Maybes/_Trash` are excluded both flat and recursive, unsupported file extensions ignored, missing folder returns 404, empty folder returns 0 without DB query.

**2. "When I create a new manual group, will it be AI-analyzed automatically?"** No — `setManualGroup` in `App.jsx:534` called `loadGroups()` on success but not `loadGroupsAndPrerank()`, so the prerank worker never picked up the new membership. The user had to open the loupe to trigger /rank-burst lazily. Fixed: the main success path swaps to `loadGroupsAndPrerank()`. The undo-replay branch keeps `loadGroups()` because replays restore prior state that was likely already ranked. `enqueue_groups` dedupes by hash so re-posting unchanged groups is a no-op.

273/273 tests passing.

---

## Surface "near-duplicate burst" as a first-class outcome (2026-05-17, same day, follow-up)

In the post-optimization drain, 7/64 groups returned `status='too_few'` because the new SigLIP dedup collapsed every member to a single representative (cosine ≥ 0.97). The outcome was correct — there's nothing for a vision model to compare when the frames are visually identical — but to the UI those groups looked identical to "AI ranking hasn't run yet" (`pending`) or "AI rank failed" (`error`). User flagged the missing distinction.

**Fix:**
- New `burst_rankings.outcome TEXT NOT NULL DEFAULT 'ranked'` column (migration v47). Existing rows default to 'ranked' so semantics are preserved.
- `rank_burst` now persists a sentinel row with `outcome='near_duplicates'` (and `rankings_json='[]'`) keyed on the **pre-dedup** members_hash whenever dedup collapses a burst below `_MIN_MEMBERS`. New top-of-function cache lookup short-circuits subsequent calls with `status='near_duplicates'` — no second dedup run.
- `_annotate_prerank_state` learns the new state: groups with outcome='near_duplicates' surface as `prerank_state='near_duplicates'` (distinct from `ready`, `pending`, `in_progress`, `not_applicable`).
- **UI:** GroupTile chip shows a small neutral "≈" glyph in place of the rainbow "AI" / sparkle. Loupe header replaces the would-be "AI ranked top N of M" chip with `"≈ Near-duplicate frames — using score-based pick"` in neutral surface chrome (not amber, not coral — this is a deliberate AI determination, not an error).
- `onRankComplete` (which triggers a grid `/similarity-groups` refetch so the tile chip flips) now fires for `near_duplicates` outcomes too, not just `ranked`, so the badge updates without a full reload.
- 3 new tests: `test_rank_burst_persists_near_duplicates_when_dedup_collapses_all` (burst_ranker), `test_group_with_near_duplicates_outcome` and `test_mixed_outcomes_resolved_independently` (annotator). 267/267 passing.

Effect: those 7 groups now read "≈ Near-duplicate frames" on hover at the grid AND get a clear explanation in the loupe header instead of looking like a failure mode.

---

## Burst-rank speed pass: 640→512px, top-12→8, near-dup dedup (2026-05-17, same day)

Baselined the burst-rank wall time at ~110s/group (size-74 in 115.6s, size-64 in 109.8s, size-43 in 122.6s — first three groups of the post-fix drain). Vision-encoder pass on 12 attached previews dominates; LLM decode is secondary. Three independent levers, all shipped together:

1. **Preview long-edge 640 → 512px.** Each preview drops from ~600 to ~400 vision tokens. Detail loss is invisible at portrait-distance face crops (face still ≥ ~120px wide).
2. **`_MAX_MEMBERS` 12 → 8.** Above 8 attached frames the local 7-8B vision model's comparative reasoning degrades; smaller-N is also linearly faster. The unranked tail keeps its score-based standing as before.
3. **Intra-burst near-duplicate dedup.** New `_collapse_near_duplicates(image_ids, conn)` walks SigLIP embeddings (already in `images.embedding`), greedy-merges anything with cosine ≥ 0.97 into a single representative (highest-priority by `top_n_candidates`'s scoring). Cache key uses the collapsed set; final rankings expand back to all original ids so absorbed photos inherit their rep's rank with a `"near-duplicate of #X"` reason. Falls back to a no-op when embeddings are missing.

**Implementation notes:**
- Dedup runs **before** `_members_hash`, so the cache key reflects what the LLM actually saw. Two identical-dedup outcomes from different inputs still hit the same cache row.
- `merged_map` plumbed from `rank_burst` into `_leader_compute_rank` (new optional kwarg, default = identity map for back-compat with the inflight-coalescing path that mocks the leader).
- `evaluated_ids` in the result now contains rep + absorbed ids so the loupe's "AI ranked top N of M" chip credits every photo that got an effective rank.
- 4 new tests in `tests/test_burst_ranker.py` (collapse high-cosine, keep distinct pairs, no-op on missing embeddings, end-to-end expansion). 4 existing pre-filter tests switched from hardcoded `12` to `_MAX_MEMBERS` so future cap changes don't break them. Full suite 264/264.
- `burst_rankings` cache wiped (42 → 0) so the next drain re-ranks every group under the new policy and gives a clean wall-time comparison.

Expected ~40-50% wall-time reduction per group, plus skipping the encoder pass entirely on absorbed near-duplicates. Will be verified by re-triggering the full-library drain after restart.

---

## Mid-batch AI burst ranking + RAW preview fallback (2026-05-17)

Caught during an 878-photo batch (108 JPG + 274 RAF + 496 NEF) that finished in 90m56s. After completion the prerank worker fired and **18/20 burst rank calls failed** with `0/N previews readable`. Root cause: 496/496 NEFs and 88/274 RAFs had `preview_path = NULL` because Z6III HE\* NEFs take the analyzer's `extract_thumb` fast path (never writes a cached preview) and RAFs only cache when `/previews/<id>` is hit by the viewer. The burst worker's `_resolve_preview_bytes` only knew two paths: cached preview OR pass-through for already-viewable formats. RAW-without-cache fell off the world.

**Fix shipped:**
- `phase2_quality/burst_ranker.py::_resolve_preview_bytes` gained a third resolution path: when format is RAW and no `preview_path` is set, call `backend.routers.analysis._generate_preview`, write the result to `data/previews/<id>.jpg`, and `UPDATE images SET preview_path = ?`. Same code path as `/previews/<id>`, so the side effect also fixes the viewer cache for those rows.
- Per-image failure cache (`_raw_preview_failed`) so a single corrupt RAW doesn't make every burst it appears in pay the demosaic-then-fail cost ([feedback_lazy_load_failure_caching.md](memory/feedback_lazy_load_failure_caching.md)).
- SELECT in `_leader_compute_rank` widened to include `id` (needed for the cache filename).
- 2 new tests in `tests/test_burst_ranker.py`: `test_raw_without_cached_preview_uses_generate_fallback` pins the happy path; `test_raw_fallback_failure_is_cached_per_image` pins the failure-caching contract. Replaces the old `test_unknown_format_with_no_preview_path_skips` (premise inverted by the fix).

**Mid-batch prerank trigger (shipped same session per user request):**
- `frontend/src/hooks/usePolling.js` now fires `onBatchComplete` (which is `loadGroupsAndPrerank` in `App.jsx`) every `MID_BATCH_PRERANK_EVERY = 50` photos during the running batch, not only at the running:true→false transition.
- Backend's `enqueue_groups` already de-dupes against the burst_rankings cache, so re-posting growing supersets is a no-op for already-ranked bursts.
- Net effect: AI burst ranking starts warming the cache as soon as bursts become detectable. By the time the batch finishes most pre-existing bursts already have their amber best-of-burst ring computed.

**Also in this session:** grid now shows a leading shimmer skeleton tile (top-left, regardless of sort order) labeled with `progress.current_file` while a batch is running — makes the "still analyzing" state visible when sort doesn't put new photos at the bottom.

---

## Manual group composition + layered union gate (2026-05-14 → 15)

Two-day arc fixing the multi-scene cluster fusion problem.

**Day 1 (2026-05-14)** shipped the **layered union gate** on top of SigLIP cosine clustering: face-identity (FaceNet ≥ 0.50) → scene-tag blocker (frozenset of incompatible pairs) → color histogram (4×4×4 RGB, ≥ 0.90 cosine, computed on-demand from preview JPEGs). Default time-gap dropped 120s → 60s. Auto-clustering now refuses cross-scene unions even when SigLIP cosine bridges them. Same day shipped a slider experiment in the loupe bottom pill — and walked it back after the user pointed out that slider tuning is invisible at the photo level. The slider was the wrong abstraction; manual photo moves are the right one.

**Day 2 (2026-05-15)** replaced the slider with **manual group composition**:
- New `images.manual_group_id TEXT` column (migration v46). NULL = auto-cluster as today; non-NULL anchors photos together regardless of cosine. Auto-clusters and manual buckets never merge.
- `group_by_similarity` refactored to a two-phase reconciliation: manual buckets first, then the existing time-segment + cosine + layered-gate pipeline on the unanchored remainder. Legacy 2/3-tuple callers still work via tuple-length detection.
- New `POST /set-manual-group` endpoint, 4 modes: `new_group` (mint one uuid for all), `singletons` (one uuid each → all drop from /similarity-groups), `join_group` (inherit a target image's anchor, mint one if target was unanchored), `clear` (NULL → return to auto).
- `useMultiSelect` hook (Finder-style shift+click: anchor advances on click but not on shift+click, so repeated shift+clicks extend from the same pivot). Grid and loupe each instantiate their own copy.
- Grid: cmd+click toggles, shift+click extends across `displayGridItems`, contextual top action bar ("Move into group… / New group from selection / Cancel"), pick-target mode highlights every GroupTile.
- **Loupe redesign**: permanent left rail of compact GroupTiles, resizable + collapsible (DetailView side-panel pattern: `pca.loupeRailWidth`, `pca.loupeRailCollapsed`, 36px collapsed). Clicking a rail tile switches the open loupe to that group.
- **Auto-expand on drag-hover**: collapsed rail auto-expands when a multi-select drag enters it, snaps back on drop/cancel without mutating the saved preference. One-line derived state.
- Drag-and-drop (native HTML5, no library, mirroring the existing TabBar pattern): sources on selected ImageCards (any selected card carries the whole selection via JSON payload), drop targets on GroupTiles in the grid, RailGroupTiles in the loupe rail, and a "Make singletons" coral-dashed zone at the bottom of the rail. Click-to-pick-target stays as a keyboard-friendly fallback.
- Sliders relocated to Settings → Model → "Grouping" section with helper copy framing them as a power-user escape ("Most users won't need to touch this — manually moving photos in and out of groups is usually the right answer").
- 14 new tests in `tests/test_manual_group.py` (anchor wins over cosine, anchor forces grouping against cosine, anchor-singleton drops, anchor + auto-cluster coexist with no bridging, all 4 endpoint modes, 3 error paths). Full suite 256/256.

**Tone fix in the same arc**: in-loupe ranker `temperature` dropped 0.1 → 0.0 (greedy decoding; deterministic JSON; marginal speed-up). Tests don't assert on this.

---

## DetailView overhaul + shared Filmstrip primitive (2026-05-15)

Big day on DetailView. Removed the standalone "Filmstrip" grid layout (it duplicated DetailView); DetailView is now the only filmstrip surface, reached via double-click / Space from the grid. Side panel close (X) moved to a hover-visible button in the top-left of the picture pane; the panel header now shows just collapse + filename. Black letterbox no longer closes DetailView — only the X or Esc.

Filmstrip got a 40px collapsible toolbar with double-chevron (matching the side panel's `ChevronsLeft/Right`) and a drag-resize handle on its top edge. Drag below 80px collapses; drag back up during the same gesture re-expands at MIN (closure-state-trap fix); chevron-toggle restores the last expanded thumb size from `pca.stripThumbAtExpand`. On initial open the focused thumb centers in the strip (one-shot, doesn't re-fire on focus changes per user request). ResizeObserver anchors the focused thumb's viewport position while the user drags the resize handle.

Both DetailView and GroupLoupe were duplicate filmstrip implementations — consolidated into `frontend/src/ui/Filmstrip.jsx` (toolbar + collapse + resize + auto-scroll + per-thumb render-prop). Per-surface chrome (group cells with stacked-paper edges in DetailView; hero rings, multi-select checks, drag-and-drop, file-format badges in GroupLoupe) supplied via `renderThumb`.

UI polish in the same session: K/M/R decision buttons tint to their color on hover (`transition-colors`); per-meter info icons on AI Quality dropped — hovering the meter row itself opens the popover via `HoverPopover block`; Histogram (i) icon dropped too; Histogram/EXIF/Explanation now share the bordered `Section` card chrome. Pill controls (Sort/Filter/View/Tab-settings/Search) unified to the same hover style as the filmstrip chevron (subtle white bg + brighter text instead of `hover:opacity-70`). SortPill now takes `orientation="horizontal"` in DetailView so direction arrows read → / ← instead of ↓ / ↑.

"Advance cursor" setting moved from the tab-settings popover into global Settings (Display tab). F hotkey added: toggles "focus mode" — collapses both side panel and filmstrip, snapshot-restores prior state on next F. Fullscreen zoom cycle changed from `[1,2,3]` (looping) to `[1,1.5,3]` (exits to DetailView after 3×). Bottom hint text in fullscreen removed.

Bug fixes along the way:
- Drag-to-collapse used to dismiss DetailView (mouseup-synthesised click bubbled to outer onClose). Now both strip-resize handlers install a one-shot capture-phase click swallow on mouseup.
- Portrait photos in DetailView were rendered at intrinsic height and overflowed under the filmstrip — `display:grid` cell auto-sized to img. Fix: `gridTemplateRows: minmax(0,1fr)` + `gridTemplateColumns: minmax(0,1fr)` + `placeItems: center`.
- Resize handle's `title="Drag to resize"` native tooltip floated above popovers — dropped (cursor change is enough affordance).
- Resize handle is now `pointer-events-none` while any pill popover is open (`suppressPanelResize` flag) so the cyan hover doesn't bleed through the popover.

---

## Vision-model swap + top-N burst pre-filter (2026-05-13)

Burst-rank LLM swapped from `moondream` (1.6 GB) to `qwen2.5vl:7b` (6 GB), and large bursts (>12 photos) now get LLM ranking via a server-side pre-filter instead of being rejected.

- **Why the swap.** `data/app.log` showed moondream returning prose captions of single images for every burst-rank call instead of the comparative JSON array the parser at [phase2_quality/burst_ranker.py:107-150](../phase2_quality/burst_ranker.py#L107-L150) requires. `qwen2.5vl:7b` is native multi-image, has a 125K context window, and is post-trained for structured JSON output — it actually follows the prompt contract. Moondream removed entirely from `_VISION_PREFIXES` rather than kept as a fallback.
- **Pre-filter for bursts >12.** The previous `_MAX_MEMBERS=12` cap returned `status="too_many"` and dropped to a score-based pick for any larger group. Replaced with a server-side pre-filter that trims down to the top-12 candidates by the canonical priority (face_sharpness → eyes_open → sharpness → IQA → aesthetic → overall_score) before the LLM is called. The remaining photos keep their score-based standing — they just don't get an LLM-authored reason. See new [backend/group_scoring.py](../backend/group_scoring.py) (`compute_best_reason` / `score_candidate` / `top_n_candidates`) — the priority helper was extracted from `backend/routers/model.py` so both the similarity-group hero picker and the burst pre-filter share one source of truth.
- **Response shape extended.** `/rank-burst` returns two new fields on every status: `evaluated_ids` (≤12 sorted ids the LLM actually ranked) and `filtered_from` (original input count). Cache key (`burst_rankings.members_hash`) is the hash of the EVALUATED set, so two large bursts whose top-12 happen to overlap share a cache row — harmless because the LLM input is byte-identical.
- **GroupLoupe UI.** New "AI ranked top N of M" chip in the top bar appears only when the burst was pre-filtered. Click the chip to inspect which photos the model evaluated (popover lists the filenames). Non-evaluated tiles get a small muted dot top-right in both Survey and Filmstrip modes. InfoTooltip on the chip explains the pre-filter formula.
- **Frontend rename.** `PullMoondreamButton.jsx` → `PullVisionModelButton.jsx`, all three import sites (SettingsModal, GroupLoupe, DetailView) updated. Button copy switched to qwen2.5vl / ≈6 GB throughout. Default model in [usePullModel.js](../frontend/src/hooks/usePullModel.js) is now `qwen2.5vl:7b`.
- **Pull-size lookup.** Replaced the binary `name.startswith("moondream") else 4000` branch in [llm_explainer.py:230](../phase2_quality/llm_explainer.py#L230) with a small `_PULL_SIZE_MB` dict + `_estimate_size_mb()` helper so the download progress copy stays accurate per model.
- **Tests.** New [tests/test_group_scoring.py](../tests/test_group_scoring.py) and [tests/test_burst_ranker.py](../tests/test_burst_ranker.py) — pre-filter ordering, missing-score handling, cache key on the filtered set, picker behaviour.
- **No DB migration.** New response fields are API-shape only. Pre-existing `burst_rankings` cache rows from moondream remain valid until membership changes; users who want to invalidate can `DELETE FROM burst_rankings WHERE model = 'moondream'`.

---

## Analyze-progress observability + ImageCard shimmer + filename normalization (2026-05-13)

Outcome of a live monitoring session on a 152-file batch. Four code fixes + a 215-file rename on disk + DB.

- **`elapsed_seconds` reset + live exposure** ([backend/routers/analysis.py](../backend/routers/analysis.py)). The field used to be set once in the post-batch `finally` block and returned only after completion; if a new batch started before reading the endpoint, the stale value from the previous run leaked into the new run. Now: cleared in the `analyze-folder` start-update so every batch begins at None, and the endpoint returns live elapsed seconds while `running` is true (frozen final total once `running` flips to false).
- **Per-photo INFO timing log** ([backend/routers/analysis.py:638-645](../backend/routers/analysis.py#L638-L645)). One line per successful photo: `analyze — [42/152] DSCF0131.RAF done in 87.3s`. Restores observability for diagnosing slow batches from `data/app.log` alone — the previous batch ran 22 minutes with zero log output between model load and completion.
- **TOPIQ-IAA WARNING → DEBUG** ([phase2_quality/aesthetic_scorer.py:163-172](../phase2_quality/aesthetic_scorer.py#L163-L172)). The "raw=X exceeds [0,1] range" line was firing on every cold start because AVA-scale output is the *accepted* path for pyiqa's IAA wrapper, not an anomaly. Demoted to DEBUG and rewored.
- **`ImageCard` per-card shimmer underlay** ([frontend/src/views/ImageCard.jsx:63-72](../frontend/src/views/ImageCard.jsx#L63-L72)). RAW previews are demosaiced lazily on first `/previews/<id>` GET (1–3s for a RAF), so freshly-analyzed cards previously showed a dark empty tile during the handoff between the trailing-skeleton slot collapsing and the `<img>` firing `onLoad`. Now a shimmer covers the gap; the `<img>` fades in over it.
- **`/analyze-progress` docstring updated** to match the new contract — `running` is the done-detection sentinel, not `elapsed_seconds`.
- **Filename normalization (215 files + 135 DB rows).** Files in `Photo culling playground pics/lvl 1/` and `lvl 2/` had their timestamp prefix duplicated 5× (`"2026-03-29 14.42.30 - 2026-03-29 14.42.30 - … - DSCF0127.RAF"`). Renamed in lockstep with the SQLite `images.file_path` and `images.filename` columns; collision check + filesystem rollback on DB failure baked in. Zero errors.

**Open TODOs (this session):**

- Per-photo perf regression — the monitored batch averaged ~95s/photo vs. the documented baseline of 17s/RAF and 1.7s/JPG. AC plug-in helped some but didn't restore baseline. The new INFO timing log is the prerequisite for attributing the slowness to specific scorers; next batch should produce diagnosable data.

---

## SOTA-gap PR3 — Bootstrap CIs + Uncertain → Maybe routing (2026-05-12)

Auto-cull now consults a per-photo uncertainty estimate before committing to a hard Keep/Reject decision near the cutoffs. Photos where the personal model's prediction is high-variance AND lands within ±std of the keep_threshold or maybe_threshold are routed to Maybe with reason `"uncertain"` instead of being silently miscategorised. Instant-reject rules (closed eyes, blurry frame, etc.) fire first and are unaffected.

- **Uncertainty ensemble.** `PersonalModel.train()` and `train_from_samples()` now fit 20 sub-sampled GBR pipelines (`subsample=0.7`, `n_estimators=50`, different `random_state` per member) alongside the main pipeline. Total train cost ~5× the main pipeline — auto-trainer daemon absorbs the wait. The ensemble lives on `self._uncertainty_ensemble` and is persisted in the pickle under a new top-level key `"uncertainty_ensemble"`.
- **New prediction API.** `predict_with_uncertainty(row) → (score, std_dev)` and `predict_batch_with_uncertainty(rows) → list[(score, std_dev) | None]` map the 20 ensemble predictions through the same `overall_score + delta×25` formula as the main pipeline, then compute `np.std()` on the personal-score values so the threshold setting is in user-facing 0–100 units.
- **Backward compat.** `PersonalModel.load()` tolerates pickles without the new key — `_uncertainty_ensemble` falls back to `[]` and `predict_with_uncertainty` returns `(score, 0.0)`. The boundary router treats 0.0 std as "no signal" and falls through to the hard decision, so old pickles keep working without an immediate retrain.
- **Boundary routing in `_compute_auto_decision`.** When `auto_cull_uncertain_to_maybe` is enabled AND `model_status == "ready"` AND `std_dev ≥ uncertainty_threshold` AND the score is within ±std of either decision boundary → returns `("maybe", "uncertain")`. The `predict_with_uncertainty` path is taken whenever the toggle is on, regardless of whether the row carries a pre-computed `personal_score`, so the std is always live.
- **Two new settings** in `phase1_technical/quality_analyzer.py::get_decision_thresholds()`:
  - `auto_cull_uncertain_to_maybe` (bool, default `true`)
  - `uncertainty_threshold` (float 0.0–50.0, default `8.0` — roughly one tier of disagreement on the 0–100 personal_score scale)
  Exposed in Settings → Model under "Personal-model cutoffs". The numeric slider is hidden when the toggle is off.
- **AutoCullModal UI.** New "Uncertain — routed to Maybe" row appears between the K/M/X bars and the existing "Why rejected?" breakdown when any photos were rerouted. Amber color matches the Maybe decision; only renders when `rule_breakdown.uncertain > 0`.
- **`rule_breakdown.uncertain`** added to the `/auto-cull/preview` response. Counted alongside (not inside) the reject buckets since uncertain photos land in Maybe.
- **No re-analysis required.** Affects only the model fit and a few UI surfaces. Next `train_from_samples` cycle — triggered by any decision past the auto-trainer's 10-decision throttle — refits the ensemble. Until then, the existing personal model continues to drive auto-cull without the routing.
- **Tests.** +6 in `tests/test_personal_model.py` (ensemble train, single+batch prediction shape, save/load round-trip, pre-PR3 pickle tolerance), +6 in `tests/test_auto_decision.py` (boundary routing on/off, threshold gate, both keep- and maybe-boundary, instant-reject precedence, far-from-boundary passthrough). 183 tests pass (was 171).

Background: identified as Upgrade #3 in the 2026-05-12 SOTA assessment.

---

## SOTA-gap PR1 — Aesthetic scorer swap (2026-05-12)

Swapped the aesthetic scorer from the CLIP ViT-L/14 + camenduru `sac+logos+ava1` head to pyiqa's **TOPIQ-IAA** (AVA-trained Image Aesthetic Assessment, CFA-Net + Swin backbone). Same `aesthetic_score` column shape (0–100) but the distribution is much narrower (n=40 survey: min=31.6, max=49.5, mean=41.8, stdev=4.7). Band cutoffs recalibrated **46/42/36** (down from the previous 70/50/30) to partition the new distribution into top 15% / 50% / 85%.

- `FEATURE_SCHEMA_VERSION` bumped 4 → 5. `PersonalModel.load()` now refuses pickles whose `meta.feature_schema_version` doesn't match — forces an automatic retrain once enough decisions accumulate.
- Schema migration v42 NULLs the `aesthetic_score` key in every `training_samples.features_json` row so old-scale aesthetic values don't mix with new-scale ones at retrain. All other features in those rows survive; SimpleImputer fills the NULL with the training-set mean.
- User-facing copy updated in DetailView, mobile SignalStrip/DetailSheet, SettingsModal, and FAQ. LAION references in code comments rewritten or removed.
- **Re-analysis required:** Clear Analysis + re-run to refill the `aesthetic_score` column on a new-scale basis. Until you do, the aesthetic chip and bar appear empty on every photo (PR1.5 migration v43 nulled the column so mixed-scale data doesn't leak into the personal model).
- If `personal_score` predictions look meaningfully off after the auto-retrain, re-tune Settings → Model decision thresholds. The pipeline's `StandardScaler` normalizes feature scale, so this is observation-only — don't pre-emptively change defaults.

Background: identified as Upgrade #1 in the 2026-05-12 SOTA assessment.

---

## SOTA-gap PR2 — SigLIP-1 → SigLIP-2 (2026-05-12)

Swapped the SigLIP vision/text encoder from `google/siglip-base-patch16-224` to `google/siglip2-base-patch16-224` ([arXiv:2502.14786](https://arxiv.org/abs/2502.14786)). Same 86M-param scale, same 768-dim embedding output (no schema change to the `embedding` column shape), reported +~2.4 pts on zero-shot accuracy benchmarks. Affects burst grouping, semantic search, the four content-axis chips (subject prominence / background distraction / eye contact / decisive moment), and zero-shot scene classification — all of which run through SigLIP embeddings.

- Class swap in `similarity_scorer.py`: `SiglipVisionModel` / `SiglipTextModel` → `Siglip2VisionModel` / `Siglip2TextModel` (transformers ≥ 4.49). `AutoProcessor` / `AutoTokenizer` unchanged.
- `FEATURE_SCHEMA_VERSION` bumped 5 → 6. `PersonalModel.load()` refuses the existing pickle until retrain.
- Schema migration v44 NULLs the four SigLIP content-axis keys in every `training_samples.features_json` row. Scene one-hots are intentionally preserved — zero-shot scene labels are stable enough across SigLIP versions.
- Schema migration v45 NULLs the SigLIP-derived columns in the live `images` table: `embedding`, the four content axes, `scene`, and `scene_confidence`. Existing UI gates (`!= null`) hide affected chips/bars until re-analysis refills the columns.
- **Re-analysis required:** Clear Analysis + re-run. Until you do, burst grouping returns no clusters (embeddings are NULL), semantic search is offline, and the Content Signals section in DetailView is hidden.
- **`background_distraction_ceiling` recalibrated 0.85 → 0.65.** SigLIP-2's content-axis sigmoid distribution is much narrower than SigLIP-1's (60-photo survey: min=0.45, p50=0.55, p90=0.59, max=0.61). The 0.85 default never fired on the new distribution. 0.65 sits just above the observed p99, catching the busiest-backdrop tail. Users who set a custom value are unaffected (settings.background_distraction_ceiling overrides the default).
- **Known UX regression (not fixed in this PR):** the four content-axis chips in DetailView's "Content Signals" section use tier cutoffs 0.75/0.55/0.35 calibrated for SigLIP-1's wider spread. On SigLIP-2, every photo will read "Weak" or "Cluttered" until those cutoffs are re-tuned. **Fixed in PR2.5 below.**

Background: identified as Upgrade #4 in the 2026-05-12 SOTA assessment.

---

## SOTA-gap PR2.5 — Content Signals bands + test gaps + background audit (2026-05-12)

Three small follow-ups from the PR2 code review, plus the deferred Content Signals band fix:

- **Content Signals tier cutoffs recalibrated for SigLIP-2's narrower distribution.** A fresh 60-photo audit (20 NEF / 20 RAF / 20 JPG, mixed indoor + street + portrait) showed the four content axes have **meaningfully different** distributions, not a single shared scale:
  - `subject_prominence`     range 0.21–0.64, p25=0.35 / p50=0.42 / p75=0.50
  - `background_distraction` range 0.16–0.75, p25=0.32 / p50=0.38 / p75=0.45
  - `eye_contact`            range 0.41–0.57, p25=0.47 / p50=0.48 / p75=0.51 (33 faces)
  - `decisive_moment`        range 0.29–0.65, p25=0.44 / p50=0.48 / p75=0.52

  A single shared ladder would flatten `eye_contact` (squeezed into a 0.16-wide band) while spreading the other axes correctly. `contentBand`/`contentTint` now use a per-axis `CONTENT_AXIS_CUTOFFS` map keyed by axis name. Each axis divides into rough quartiles: tier 4 (best) ≥ p75, tier 3 ≥ p50, tier 2 ≥ p25, else tier 1 (worst). Verified by running the tier function back over the same 60-photo dataset — each axis spreads ~25/25/25/25%.

- **Background-distraction audit verdict: keep 0.65 default.** Same 60-photo audit, count of photos with `background_distraction_score ≥ 0.65` = **1/60 (1.7%)**. Below the "too lax" threshold (<5%) but the one photo that trips it has a visibly busy background — the rule still does meaningful work on the long tail without false-positiving keepers. The earlier PR2 60-photo survey (max=0.61) underestimated the upper tail; this fresh audit captures it (max=0.75). Default stays at 0.65 with the audit documented here so the rationale is preserved for a future re-tune.

- **Test gaps from PR2 review closed:**
  - `tests/test_auto_decision.py::test_background_distraction_ceiling_default_is_0_65` pins the default value so a future "round to 0.7" by-eye change won't slip through.
  - `tests/test_similarity_scorer.py::test_siglip2_loads_with_siglip1_vision_class` asserts the loaded model is `SiglipVisionModel` (SigLIP-1 architecture, SigLIP-2 weights) — catches a regression if someone "modernizes" the import to `Siglip2VisionModel`, which fails with a conv-stem shape mismatch on the base checkpoint.

- **DEFAULT_T fixture in `test_auto_decision.py`** updated 0.85 → 0.65 to match the current production default (was stale since PR2). Two scenario tests that used 0.85 as a custom override updated to 0.65 with a comment explaining the picked score.

**No re-analysis required.** The bands are read live from existing `images.*_score` columns; the audit + test changes are pure code. 171 tests pass (was 169).

---

## SOTA-gap PR1.5 — Post-PR1 hardening (2026-05-12)

Three small follow-ups from the PR1 code review:

- **Fix pyiqa cache-detection path** in `iqa_scorer.py` and `aesthetic_scorer.py`. The check was looking at `~/.cache/pyiqa/` but pyiqa actually caches at `~/.cache/torch/hub/pyiqa/`. The wrong path made the "downloading 100 MB" toast fire on every cold start even when weights were present. Now the toast only appears on a genuinely cold cache.
- **Schema migration v43** NULLs `aesthetic_score` in the live `images` table. Without this, photos analyzed before PR1 still carried old-scale aesthetic values that would silently mix with new-scale TOPIQ-IAA predictions when the personal model retrains. Existing UI gates (`image.aesthetic_score != null`) hide the bar/chip until re-analysis refills the column.
- **CHANGELOG reframed** the threshold-recalibration concern as observation-only — `StandardScaler` in the model pipeline neutralizes feature-scale shifts, so pre-emptively changing decision thresholds is wrong.

---

## Phase status archive (formerly in CLAUDE.md)

## Current Phase: Phase 2 — In Progress 🚧

### Phase 1: Complete ✅
- [x] requirements.txt · start.sh · README.md · RELEASE_NOTES.md (v0.1)
- [x] XMP sidecar writes (rating + color label on decision via pyexiftool, 2026-05-05). UUID-on-ingest is documented as future work — UUIDs currently live in SQLite only.
- [x] UUID tracking (SQLite). XMP-sidecar UUID write is deferred — see backlog.
- [x] fastdup → permanently dropped (Python 3.13 incompatible)

### Phase 2: Module 1 — Face Detection ✅
- [x] `phase2_quality/face_analyzer.py` — FaceLandmarker + BlazeFace fallback
- [x] Face signals wired into `quality_analyzer.py` (runs on every photo)
- [x] Face columns in SQLite (8 new columns via migration)
- [x] `/analyze` + `/analyze-folder` endpoints write face data
- [x] Detail view UI shows face section (count, sharpness, eyes, size ratio)

### Phase 2: Module 2 — TOPIQ IQA ✅
- [x] `phase2_quality/iqa_scorer.py` — TOPIQ no-reference IQA, lazy-loaded, 1024px downscale
- [x] `iqa_score` column in SQLite via migration
- [x] Both `/analyze` + `/analyze-folder` write iqa_score; `GET /images` returns it
- [x] DetailView shows "Perceptual quality" bar (hidden when null)

### Phase 2: Module 3 — LAION Aesthetic Predictor ✅
- [x] `phase2_quality/aesthetic_scorer.py` — LAION V2 Linear, lazy-loaded, 512px downscale
- [x] `aesthetic_score` column in SQLite via migration
- [x] Both `/analyze` + `/analyze-folder` write aesthetic_score; `GET /images` returns it
- [x] DetailView shows "Aesthetic appeal" bar (hidden when null)

### Phase 2: Model Download Toast ✅
- [x] `phase2_quality/model_status.py` — thread-safe begin/end/snapshot registry
- [x] iqa_scorer + aesthetic_scorer call begin()/end() around model load; detect download vs. cache-hit
- [x] `GET /model-status` endpoint in main.py
- [x] DownloadToast component in App.jsx — spinner, model name, size, elapsed time; polls every 1s

### Phase 2: Module 4 — SigLIP Semantic Similarity ✅
- [x] `phase2_quality/similarity_scorer.py` — SigLIP base (~300 MB), 768-dim L2-normalised embeddings
- [x] `embed_image()` + `group_by_similarity()` (Union-Find cosine clustering) + JSON helpers
- [x] `embedding` TEXT column in SQLite via migration
- [x] Both `/analyze` + `/analyze-folder` write embedding; `GET /similarity-groups?threshold=0.90` returns clusters
- [x] `transformers>=4.40.0` added to requirements.txt

### Phase 2: UX Improvements ✅
- [x] Stop analysis: mid-photo cancellation via shared `threading.Event` + `StopRequested` raised between scorer boundaries (2026-05-04). UI shows "Stopping…" while the in-flight RAW decode finishes.
- [x] Analyze button: hover-to-Stop (CSS `group`/`group-hover` label swap, no JS state needed)
- [x] Clear Analysis does NOT delete model caches — only SQLite rows + preview JPEGs

### DetailView panel restructure (2026-05-03) ✅
- [x] Sticky header (filename + ESC + Decision buttons, Surface 200 `#161718`) over scrollable content (`flex-1 min-h-0 overflow-y-auto`); panel itself is `overflow-hidden`. Body scroll locked while DetailView is mounted.
- [x] Three section types: `Section` (score sections w/ headline chip + bordered chrome on expand, default closed), `AiQualityRow` (non-expandable, color-tinted chip via `iqaTint()` mapping TOPIQ band → sage/cyan/amber/coral), `CollapsibleSection` (EXIF, Explanation; chevron right-aligned, default open).
- [x] `ScoreChip` supports `tint` (color-coded border/bg) and `hoverInfo` (wraps chip in `HoverPopover`).
- [x] `LearnedSignals` stacked-bar viz of personal-model top-3 features + `other N signals` segment in Muted Bronze `#8C7A5E`.
- [x] InfoTooltip + HoverPopover use `createPortal(content, document.body)` to escape ancestor `hover:opacity-*` cascades. Inline `style={{ backgroundColor }}` cannot override inherited opacity.
- [x] `DELETE /explanation/{image_id}` endpoint clears stored narrative; frontend has matching "Clear explanation" button.
- [x] Sharpness/Exposure use the standard `ScoreBar` in Technical Quality (matches AI Quality block; earlier `Donut` variant removed 2026-05-03).
- [x] Floating bottom toolbar (Sort/Filter/View/Tab settings) hides when DetailView is open (except in sticky-filmstrip mode — see below). Layout: Sort · Filter · View (icon + size label) · Tab settings (right, sliders glyph). All close on outside click via a document-level `mousedown` listener (each wrapper marked `data-dropdown="true"`) — `fixed inset-0` backdrops don't work because the pill bar's transform creates a stacking context. **View pill** (2026-05-11): replaces the old Size pill. Two-section overlay: **Layout** (Grid · Filmstrip) + contextual **Size** (Tile size for Grid → cols 8/6/4/2; Thumbnail size for Filmstrip → 80/120/180/260 px). Filmstrip layout is implemented as a "sticky" DetailView — when `gridLayout === 'filmstrip'`, an IIFE in App.jsx mounts DetailView with the existing `gridFilmstrip` prop and flips `onClose` to set `gridLayout='grid'`. Pill is `z-[60]` (above DetailView's z-50) and its `bottom` offset is computed by `pillBottomAboveStrip(thumbWidth, chrome)` from `ui/filmstripMetrics.js` so the pill never collides with the strip. Preview pane uses tight padding (`px-2 pt-2`, dynamic bottom) so portraits fill the viewport. ViewPill component lives at `frontend/src/ui/ViewPill.jsx`; the same component renders the GroupLoupe bottom pill. Loupe mode (`pca.loupeMode`) persists across loupe re-opens.

### Histogram + clipping overlay (2026-05-04) ✅
- [x] `phase1_technical/exposure.py::compute_histogram(rgb)` returns 256-bin counts for R/G/B/luminance plus per-channel and any-channel clipping percentages (np.bincount on uint8 channels — sub-ms on a 1600px preview).
- [x] `GET /histogram/{image_id}` — computes on-demand from the cached preview JPEG (same pixels DetailView shows). No DB column; storing 5 KB/row × thousands of photos isn't worth it for an open-once-per-image panel section.
- [x] `GET /clipping-mask/{image_id}?mode=highlights|shadows` — returns a transparent PNG that tints clipped pixels (amber for highlights, cyan for shadows). Fetched only when a toggle is on; browser caches across re-toggles.
- [x] `HistogramSection` in DetailView: collapsible Section (defaults closed, persisted to localStorage), SVG with R/G/B layered curves at 55% alpha for the additive Lightroom look, headline chip = any-channel highlight clip %.
- [x] Clipping toggle state lives in DetailView (not HistogramSection) so the same toggles drive both the histogram band overlay AND a Lightroom-style PNG overlay on the preview image. Overlay uses `mix-blend-mode: screen` + `pointer-events: none` so click-to-zoom still works through it.
- [x] Per-image clipping persisted to SQLite as `highlight_clip_pct` / `shadow_clip_pct` (gray-channel from `analyze_exposure`). Wired into both `/analyze` and `/analyze-folder` INSERTs and into `GET /images`.
- [x] Phase 3 features bumped 15 → 17 (highlight_clip_pct, shadow_clip_pct). `PersonalModel.load()` checks `n_features_in_` against the live `_COLUMNS` length and refuses stale pickles — forces a clean retrain when feature schema bumps. Frontend `TOTAL_FEATURES` constant updated to match.

### Design system updates (2026-05-03) ✅
- [x] `.label` class redefined: 14px Inter / 500 / `#cecece` / title-case (was 11px caps `#6a6b6c`). Used app-wide for section/panel headers. Description in `docs/DESIGN.md` §2.
- [x] Muted Bronze `#8C7A5E` added to design system as the "aggregate / other" segment color for stacked charts.
- [x] Helper-text contrast pass: `#4a4a4a` → `#9c9c9d` for ~25 descriptive paragraphs across Settings, App, GroupStrip. `#4a4a4a` reserved for placeholders, separators, hover-from base colors.

### Phase 2: Multi-Tab Folder Analysis ✅
- [x] Backend: `GET /images?source_folder=`, `GET /folders`, `POST /clear-folder`, `source_folder` field on `_progress` + `/analyze-progress`
- [x] Per-tab state in `App.jsx`: `tabs[]` array with `{id, folderPath, status, images, progress, analyzeResult, selectedIdx, loaded}`. Legacy `images` / `selectedIdx` / `folderPath` / `analyzing` / `progress` / `analyzeResult` / `resultDismissed` are now derived from the active tab so existing call-sites are unchanged.
- [x] `TabBar` component with HTML5 native drag-reorder, `+ New analysis` pinned right (secondary CTA outside the scroll area), close × confirmation modal, image count chip
- [x] Skeleton placeholders during analysis; live grid refresh as `done` advances on `/analyze-progress`
- [x] Tab restoration on launch: `GET /folders` → one tab per analyzed folder + trailing empty tab. Active tab keyed by `localStorage.pca.activeFolderPath` (uuids regenerate each launch).
- [x] Sequential analysis only (single-track). UI surfaces "another analysis in progress" modal on conflict.
- [x] Filename search bar (right side of filter bar) with case-insensitive match highlighting via `<HighlightedText>`
- [x] One-step new analysis (2026-05-03): clicking "+ New analysis" tab opens Finder picker directly (`prompt: "Start analysis"` — macOS doesn't allow renaming the OK button itself); confirming with a folder runs busy/overwrite guards then `runAnalysisForTab(tabId, path)`. The empty-tab toolbar (folder field + Analyze + Watch live buttons) is gone.
- [x] Watch live as a per-tab flag (2026-05-03): `tab.watchLive: boolean` replaces the dedicated `kind: 'live'` / `makeLiveTab` model. Toggling on tab X turns it off everywhere else (single-watcher backend). Lives in the Tab settings pill. Tab strip shows a flashing cyan dot on the watching tab; hover tooltip on every other tab tells the user where Watch live is active. Closing a tab with `watchLive: true` stops the watcher first, then clears the folder.

### Phase 2: Similarity Group UI — redesigned 2026-05-05 ✅
- [x] `GroupTile` — single grid cell per group (stacked-paper edge + count chip + K/M/R progress chips). Replaces old `GroupStrip` (col-span-full row). Clicking opens GroupLoupe.
- [x] `GroupLoupe` — fullscreen overlay (z-50). Two sub-modes: Survey (n-up equal-size grid, default) and Loupe (large preview + horizontal filmstrip). Toggle with S.
- [x] Synchronized zoom (Z key): CSS transform-origin shared across all Survey tiles so sharpness/expression compares at the same pixel region across frames.
- [x] AI pick: Warm Amber `#E8B84A` ring + corner star badge on the hero tile in both Survey and Loupe filmstrip. Cyan ring stays on focused tile; amber badge visible in both states.
- [x] Batch actions: "Keep best · Maybe rest" + "Keep best · Reject rest" — both behind `ConfirmModal` + 5s undo toast. Use existing `POST /bulk-decision`. Replaced the 3-second click-to-confirm reject button.
- [x] GroupLoupe hotkeys: ←/→ navigate, K/M/R decide focused, Enter→DetailView in group context, S toggle mode, Z sync zoom, Esc back.
- [x] DetailView `groupContext` prop: when opened from GroupLoupe, prev/next cycle within the group; a filmstrip strips pins at the bottom of the preview pane (`pb-[112px]` + absolute positioned bar). "↩ Back to group" closes detail but returns to loupe.
- [x] `useKeyboard` simplified: no group-specific branches; all gated off `loupeOpen` flag so the loupe owns its hotkeys without conflict.
- [x] `GroupStrip.jsx` deleted. `focusedPhotoId`, `focusedGroupId`, `compareIds`, `expandedGroupId`, `hideGroupDupes`, `setGroupHero`, `handleCompare` all removed.

### Phase 3: Personal Scoring Model 🟡 — wired, awaiting real decisions
- [x] `phase3_learning/feature_extractor.py` — 31-dim feature vector from DB row (schema v4)
- [x] `phase3_learning/personal_model.py` — `PersonalModel` (Imputer → Scaler → GBR)
  - Labels: keep=+1, maybe=0, reject=-1; delta scaled ×25 → personal_score = base + delta
  - `predict_batch()` — O(1) pipeline.predict() call for all N images
  - Saves to `data/models/personal_model.pkl` via pickle
  - `MIN_DECISIONS = 20`
- [x] `GET /model-info` — status, decided_count, top_features
- [x] `POST /train-model` — train + save; GET /images includes personal_score in-flight
- [x] `PersonalModelPanel` in App.jsx — progress bar → 20, Train/Retrain button
- [x] `personal_score` indigo bar in DetailView
- [x] In-memory `personal_score` cache (read-through `dict[image_id → score]`) — invalidated on retrain + per-id eviction on `/analyze`, `/analyze-folder`, `/clear-folder`, `/clear`
- [ ] Future: sample_weight decay (recency bias) · background warm-up of cache after train

### Phase 3: Training Mode ✅
- [x] `buildTrainingQueue()` — shuffled undecided photos + re-shows injected at random [5,25] intervals
- [x] `RevealOverlay` — 1.5s post-decision flash: AI score + agree/overrule signal
- [x] `ConflictModal` — conflict resolution when re-show gets a different decision (keep latest / keep original / maybe)
- [x] `TrainingModeView` — fullscreen score-blind culling: EXIF only, no score bars, K/X/M + buttons
- [x] Train tab in header (Grid | Groups | **Train · N**), disabled when no undecided photos
- [ ] Future: A/B pairwise comparison mode (Elo/Bradley-Terry, different data model)
- [ ] Future: taste-evolution context in ConflictModal ("you decided this 3 weeks ago")

### Post-Decision File Moves ✅
- [x] `backend/file_mover.py` — `move_photo()` + `resolve_dest_folder()` + `_safe_path()` (stdlib only)
- [x] `backend/database.py` — `source_folder` column migration + backfill + `settings` table + `get_setting`/`set_setting`
- [x] `POST /decision` — moves RAW + XMP sidecar immediately, updates `images.file_path`, returns `new_path`
- [x] `GET /settings` + `POST /settings` — folder path configuration endpoints
- [x] Frontend `SettingsModal` — FolderInput for Keep/Maybe/Reject folders, Reset per field, `⚙ Folders` button in header
- [x] `sendDecision` — error toast on move failure (409/500), updates local `file_path` from `new_path`

### Analysis Debugging ✅
- [x] `logging.basicConfig()` + `RotatingFileHandler` in `backend/main.py` — all logger.* calls now write to `data/app.log` (1 MB × 4 files, persists across restarts)
- [x] `logger.exception()` on single-file + batch-loop error catches — full traceback in log, not just `str(e)`
- [x] `_current_step` tracking in batch loop — errors report which step failed (analysis / exif / database)
- [x] `_get(fut, step_name)` helper in `quality_analyzer.py` — prefixes exception message with `[sharpness]`, `[face-detection]`, etc.
- [x] Phase 2 scorers upgraded `logger.warning` → `logger.exception` — full traceback on IQA/aesthetic/embedding failures
- [x] `GET /debug/last-errors` — last 50 per-file errors as JSON with timestamps
- [x] `GET /debug/log-path` — returns absolute path to `data/app.log`
- [x] Error list UI shows amber `[step]` badge per error + "open full log →" link that opens `data/app.log`

---

## Sprint log archive (formerly in CLAUDE.md)

**Last Updated:** 2026-05-11 · Version 0.12

**Group lifecycle + filter fix (2026-05-11):** Two related defects fixed. (1) Decision filter (Keep/Maybe/Reject/Undecided) used to collapse each group to its hero photo and match only the hero's decision — so marking a non-hero member as Maybe/Reject hid the whole group from those filter views. Replaced with a "keep the group, attach filterContext" pattern: when ≥1 member matches, the group tile stays visible, gets `opacity: 0.55` partial-match dim, the footer flips to e.g. "2 of 5 kept" (via `DecisionWord`), and a mini decision-letter badge appears bottom-right. Decision + composition filters now combine into one `passesAll` predicate so "Keep + Portraits" = kept portraits ([App.jsx:431–471](frontend/src/App.jsx#L431-L471)). (2) `GroupTile`'s "best outcome wins" logic was misleading — a 1K+4R group rendered as green Keep. Replaced with strict semantics: `resolvedSingle` colored ring + K/M/R badge only when EVERY member shares the decision; mixed-resolved gets a neutral ring + small ✓ corner badge while the existing progress chip carries the breakdown ([GroupTile.jsx](frontend/src/views/GroupTile.jsx)). Hero-swap in filter views was deferred (AI hero stays as displayed thumbnail; dim + footer disambiguate). GroupLoupe doesn't yet read `filterContext` — opening a group from a filter view shows all members.

**DetailView filmstrip + collapsible panel (2026-05-11):** DetailView now runs in three modes: solo (per-photo K/M/R), group-loupe context (filmstrip = group members, K closes back to loupe), and grid-filmstrip with group focused (panel shows AI hero + banner + "Keep best · Maybe/Reject rest" batch buttons mirroring GroupLoupe's top-bar pattern). Side panel collapses to a 36px rail via `pca.detailPanelCollapsed`. Filmstrip uses solid `bg-[#101111]` (was semi-transparent), instant `scrollLeft` math (not `scrollIntoView`) so the strip never animates or scrolls ancestors. K/M/R in solo grid-filmstrip mode stays in DetailView and lets App.sendDecision auto-advance the cursor — don't also call onNext or you double-step. ESC keycap in the panel header replaced by Lucide `X` icon. Global Esc in useKeyboard.js prioritizes `detailOpen` over `loupeOpen` so closing DetailView from GroupLoupe context works via key. GroupLoupe top-bar moved above body (was rendered after, pushing it to the bottom). GroupLoupe Survey tiles got `onDoubleClick` → opens DetailView with groupContext.

**Per-photo undo (2026-05-11):** Replaced the time-ordered undo stack with a selection-scoped model. `U` / `Cmd+Z` now undoes the decision of whichever photo is currently selected — in the grid, GroupLoupe (focused tile), CompareView (focused panel), or DetailView (open image). Previously only the most-recent K/M/R could be undone, which broke for groups where the user wanted to walk back any one of several decisions. Backend `POST /undo-decision` now accepts an optional `previous_path`; when omitted, it reconstructs from `images.source_folder / images.filename` so the frontend doesn't need to remember per-photo provenance ([backend/routers/decisions.py:46](backend/routers/decisions.py#L46)). Frontend: removed `lastUndoRef` plumbing from App.jsx + useKeyboard.js + GroupLoupe.jsx + CompareView.jsx + DetailView.jsx; new `undoImage(imageId)` helper in App.jsx fans out to all surfaces. Toasts after K/M/R lost their `Undo` action — they're now passive notifications (3s). The bulk "Keep best · Reject/Maybe rest" toast no longer offers a single-click undo for the whole batch; users undo each photo individually inside the loupe. Silent no-op when U is pressed on a photo with no decision (matches Cmd+Z conventions).

**Grid keyboard navigation (2026-05-10):** Arrow keys now walk every grid cell — including group tiles — and the grid is a proper ARIA composite widget. Per-tab state added `selectedGroupId` ([tabs.js](frontend/src/tabs.js)) so the cyan ring can sit on either a photo or a group. Cells use **roving tabIndex** in the App.jsx grid render: the highlighted cell carries `tabIndex={0}`, all others `tabIndex={-1}`. Tab into the grid lands on the highlighted cell; Tab out moves to the next region. The grid container has `role="grid"`, each wrapper `role="gridcell"`. Hotkeys (arrows, K/M/R, Space, Enter) are gated in [useKeyboard.js](frontend/src/hooks/useKeyboard.js) on `gridRef.contains(document.activeElement)` — typing K in the search bar produces "k" not a cull, and arrows only work when the grid has focus. Tabbing out hands keyboard control off; clicking a photo/group re-engages grid mode. The arrow-movement scroll-into-view effect also calls `el.focus({preventScroll: true})` (only when focus is already inside the grid) so DOM focus and the cyan-ring cursor stay in lockstep. **Secondary affordances inside cells must be `tabIndex={-1}`** — the "Add to compare" badge was creating one Tab stop per photo and drowning Tab nav in noise; same applies to GroupTile's inner button (the wrapper div owns the cell's Tab focus). **Single-click vs double-click reverted:** cards and group tiles are now single-click=select, double-click=open (Space/Enter as keyboard "open"). The 2026-05-06 "single-click opens DetailView" decision didn't survive — once arrow keys could land on groups, mouse symmetry required a select verb too. After K/M/R on a single photo, the cursor advances along the *visible* list (`displayGridItems`); if the next cell is a group, the group is highlighted (not auto-opened). When the last undecided photo in a group gets decided inside the loupe (single decision OR "Keep best · Reject rest" batch), `GroupLoupe.onAllDecided` fires → the loupe auto-closes and grid focus advances past the resolved group. ShortcutsModal updated to remove stale `Tab/Shift+Tab/H` entries (those handlers were dropped in the 2026-05-05 redesign and never replaced).

**Sort dropdown overhaul (2026-05-08):** Bottom-pill Sort got a 2-level menu and persistence. L1 = Date / Name / Score ▸; L2 expands to all sortable metrics grouped by Technical / AI Quality / Personal scoring / EXIF (lean defaults preselected, the rest toggleable in Settings → Display → Advanced — Sort options). Direction toggle moved to a leading clickable arrow icon (1px hover stroke for affordance) — flipping direction is independent of which field is selected. Default = `desc`; direction persists across field changes. State lives in `localStorage.pca.sort` + `localStorage.pca.sortMetricsVisible`, shared across tabs and applied to photos inside every GroupLoupe (passed as `sortField`/`sortDir` props). Per-metric semantic phrasing (`desc`/`asc` strings on each metric row in `sortMetrics.js` — "Biggest smiles" / "Smallest smiles", "Most open eyes" / "Most closed eyes", etc.) drives the leading-arrow tooltip and a faint subtitle next to every L1+L2 row so the user sees what each pick will produce before clicking. New files: `frontend/src/sortMetrics.js`, `frontend/src/hooks/useSort.js`, `frontend/src/ui/SortPill.jsx`. **Gotcha caught:** a local `useState` named the same as a top-level import (App had `compareImages` in both) renders fine in dev but the production bundle lets the local state shadow the import — diagnosed by grepping the prod bundle when the screen blanked. Always alias imports if a local with the same name exists.

**Grouping + zoom UX sprint (2026-05-06 follow-up):** Time-gap primary split shipped in `phase2_quality/similarity_scorer.py::group_by_similarity` — sorts by `shot_at`, segments wherever consecutive gap > `time_gap_seconds` (default 120 s), then runs cosine union-find inside each segment. `/similarity-groups` and `/face-groups` both accept the param; latter defaults to `None` (people clustering tracks identity across moments). Cluster sliders moved into a "Cluster" pill inside `GroupLoupe`; `setLoupeAnchorId` re-anchors the loupe on the focused photo's new (smaller) group after re-cluster. `/face-groups` now uses dedicated **FaceNet identity embeddings** (`phase2_quality/face_identity.py`, InceptionResnetV1 vggface2, schema v38, threshold default 0.50, range 0.30–0.70) instead of full-photo SigLIP — same person clusters across scenes/lighting now. `pip install --no-deps facenet-pytorch` (its setup.py pins an old Pillow that breaks Python 3.13's pip resolver — `start.sh` does the no-deps install after `requirements.txt`). GroupLoupe Survey added an `Auto` tile-size mode (default) that probes every photo's natural aspect via `Image()` preloads and packs into a CSS Grid with per-row heights (each row sized to its tallest tile) — handles any count, any aspect, mixed orientations. **Zoom interaction unified app-wide:** `ZOOM_SCALES = [1, 2, 3]`, click cycles, drag pans, two-finger scroll pans, Z key cycles. Identical pattern in GroupLoupe (Survey + Loupe), CompareView, and DetailView fullscreen. Click after drag suppressed via `lastWasDragRef` microtask trick. Dead `zoomed` state in DetailView removed.

**UX overhaul (2026-05-06):** 18-item findings batch shipped. Highlights: GroupLoupe Space-key parity + S/M/L tile range widened (130/280/540). DetailView click → true fullscreen (z-100, no chrome), info panel resizable via drag handle (280–600px, localStorage persisted), histogram repositioned right before EXIF as chrome-less collapsible, top-influencer popover gets feature descriptions, AI Quality rows get InfoTooltips + subtitles, filename copy-on-hover. Grid: single-click opens DetailView (no more double-click) ⟶ **reverted 2026-05-10 to single-click=select, double-click=open** (see Grid keyboard navigation below); ImageCard switched to `aspect-[4/3]` so "Largest" actually fills cells, decision-tinted ring on the whole card. Folders pill deleted — `TabFoldersForm` renders inline inside Tab settings. Watch live requires explicit Switch confirm when another tab has it. New setting `reject_closed_eyes_all_faces` (skips group photos). RAF/JPG/HIF format badges on GroupLoupe tiles + "Mixed formats" warning on GroupTile when a group spans both RAW and rendered. **Backend:** `reject_to_system_trash` setting + `trash_photo()` in file_mover.py (send2trash) — opt-in alternative to _Trash/ folder. Subfolder analysis: `POST /analyze-folder` accepts `include_subfolders`, `GET /has-subfolders` for the picker; `_apply_decision` now bases dest folder on the photo's own parent dir so subfolder structure is mirrored. **LM Studio → Ollama** migration: same OpenAI-compatible API at `localhost:11434/v1`, rich `/lm-status` returning `not_installed | not_running | no_models | ready`, vision-preferred model picker (moondream/llava). All 127 tests pass.
**PersonalModelBanner growth tiers (2026-05-05):** Banner no longer hides when status=ready. Post-50 phase adds four growth tiers: calibrating (50–100, amber→sage bar), knows-your-eye (100–200, sage→cyan), your-curator (200–500, cyan), deeply-attuned (500+, full glowing bar). Dismiss is tier-keyed (not boolean): `bannerDismissedAtTier` in localStorage means the banner auto-resurfaces when the model advances to the next tier. When dismissed from the grid, the banner moves to Settings → Model (rendered there only when `bannerDismissed` is true). `BannerStates.jsx` is a temporary dev-only preview (Shift+B); remove when no longer needed.
**Perf baseline:** 741 photos in 53m 1s; full Tier 1 perf wins shipped (DB WAL, indexes, ML device cache, mps throttle, BlazeFace single-inference, ImageCard memo, search debounce, polling backoff). See `docs/perf-analysis-2026-05-05-tier1.md` for the deferred backlog.
**Phase 3 update (2026-05-05):** Personal model now auto-trains in the background. New `training_samples` table (schema v21, durable, no FK to images) freezes feature vectors at decision time and survives Clear Analysis / folder moves. `phase3_learning/auto_trainer.py` fires `maybe_train_async()` from `_apply_decision`; gates on MIN_DECISIONS=30 floor + RETRAIN_DELTA=10 new samples + single-flight lock. Banner above the grid is informational only (no Train button); manual "Retrain now" lives in Settings → Model. Atomic pickle save (write-tmp + fsync + os.replace). Auto-cull only delegates to the model when `model_status == "ready"` (≥50 samples AND beats baseline).
**Workflow integration (2026-05-05):** XMP rating + color-label writes shipped (Tier 1 #1). Every K/M/X decision now writes `XMP:Rating` + `XMP:Label` to a `.xmp` sidecar via `backend/xmp_writer.py`, so Lightroom Classic / Capture One / Bridge / Luminar Neo see the decision on import. Toggle in Settings → Display ("Write decisions to XMP sidecars", default ON). Best-effort writes: failures log but never abort the decision.
**Histogram persistence (2026-05-05):** `images.histogram_json` column (schema v30) stores compute_histogram() output at analyze time. `GET /histogram/{id}` reads from DB (2 ms) and backfills on first open for pre-v30 rows. Fixes 5+ s perceived lag caused by on-demand preview JPEG decode competing with LM Studio explanation requests. Also fixed a luminance truncation bug: `.astype(np.uint8)` was flooring 254.97 → bin 254, making one spike dominate maxVal and flattening all curves. Fixed with `np.round()` + `np.clip()` before cast. All 741 existing histograms were cleared and recomputed.
**Tier 1 + Tier 2 industry-comparison sprint (2026-05-05 → 2026-05-06):** Tier 1: XMP K/M/X ratings, free-text semantic search (SigLIP text encoder), expression scoring (smile + jaw-open from blendshapes), recency-weighted training (180-day half-life), EXIF auto-cull rules (reciprocal rule, ISO ceiling), Conservative/Balanced/Aggressive presets, "best in cluster because…" reason display, zero-shot scene tagging. Tier 2: Compare 2-up/4-up sync-zoom view, per-person face clustering (Bursts/People mode toggle), active learning queue (training sorts by uncertainty), A/B pairwise training (`pairwise_comparisons` table, fed into GBR as 0.4×-weighted synthetic samples). Personal model grew 17 → 19 → 27 dims (smile/mouth-open + 8 binary scene one-hots); `FEATURE_SCHEMA_VERSION` bumped to 3 — retrains on next decision. DB at schema v36.
**Open TODOs (Tier 1+2 sprint follow-ups):**
- Backfill `smile_score` / `mouth_open_score` / `scene` / `scene_confidence` for the existing 741 analyzed photos (currently NULL until each folder is re-analyzed; no migration script written).
- UUID-on-ingest XMP sidecar write — still DB-only. Future work for survival of photo identity through external moves.
- Tier 3 items deferred: Lightroom Classic catalog write-back (#13), Capture One Sessions integration (#14). Tier 1 #1 (XMP ratings) covers ~90% of what users need from both.

**Open TODOs (UX overhaul 2026-05-06 follow-ups):**
- Ollama auto-start daemon — ✅ shipped (2026-05-06): `ensure_daemon_running()` + `shutdown_daemon()` in `llm_explainer.py`, called from lifespan in `main.py`. Spawns `ollama serve` on startup if installed but not running; terminates on app shutdown only if we own the process.
- Subfolder analysis + folder overrides interaction — when a folder override is set on an analysis root, it applies to ALL photos including subfolder ones (overrides take precedence over photo-parent default). May not match "mirror subfolder structure" intent if user explicitly sets an override.
- Backfill `faces_eyes_open_json` for existing analyzed photos (NULL on rows analysed before schema v37). Until re-analysed, group shots fall back to the conservative "never reject" path with `reject_closed_eyes_all_faces` enabled.

**Open TODOs (grouping + zoom sprint follow-ups):**
- Backfill `face_embedding` for the existing 741 analyzed photos (NULL on rows analysed before schema v38; People mode silently filters them, Tab settings shows an amber re-analyse nudge with the count).
- GroupLoupe.jsx top-of-file docstring still says "Pressing Z toggles a synchronized zoom" — copy-paste tweak to "cycles 1× → 2× → 3× → off".
- DetailView in-panel preview still single-click → fullscreen (no in-place zoom). Fullscreen is where panning lives. Could harmonise if users expect cycle-zoom in the panel too.
- Mixed-orientation groups in GroupLoupe Auto layout: minority-orientation tiles get vertical breathing room inside their cell (row height = tallest tile in the row). Per-tile masonry would eliminate this but adds complexity — defer until users hit it.

**Open TODOs (launcher / distribution 2026-05-06):**
- macOS menu-bar tray icon blocked by macOS 26 (Tahoe beta) — unsigned processes can't add status items. Re-test on stable macOS release. Quit button in UI covers the gap for now.
- `frontend/dist` rebuild is not automatic when source changes — user must re-run `./start.sh` (which skips the build if dist/ already exists). Add a hash-based staleness check to launcher.py.
- Windows: `start.cmd` tested on macOS only — needs a real Windows smoke test.
- App icon only installed manually — no automated step to keep `/Applications/KaMeRa.app` in sync after `launcher/icon.icns` changes.

**Open TODOs (DetailView filmstrip 2026-05-11):**
- Group banner in grid-filmstrip mode shows hero filename only — could surface the AI burst-rank "best because…" reason (already loaded by GroupLoupe via `/rank-burst`) so the user knows *why* the AI picked this one before bulk-deciding.
- ShortcutsModal not updated for "double-click / Enter / Space opens focused group from filmstrip" — user explicitly said skip, but if a Help refresh ships later, it should land there.

**Open TODOs (Group lifecycle + filter fix 2026-05-11):**
- Hero swap in filter views deferred — AI hero stays as displayed thumbnail even when it's not the matching photo. Open question whether a Keep-filter group should show one of the kept members as thumbnail; the partial-dim + "X of Y kept" footer already disambiguates, so wait until it feels wrong.
- GroupLoupe doesn't read `filterContext` — opening a group from a filter view shows all members, not just matching ones. Deliberate for now (full context matters in the loupe). Prop is ready to thread through if it ever feels wrong.

**Mobile companion view:** see [docs/MOBILE_STATUS.md](docs/MOBILE_STATUS.md) for current state, architecture, and the parity ledger. Run `/sync-mobile` to port a desktop change to the mobile bundle (diff-driven menu; auto-updates the ledger and Learning-Journal). Desktop remains the active surface — mobile is sync-on-demand.

**Full decision log, open questions, case study plan:** [docs/PROJECT_WIKI.md](docs/PROJECT_WIKI.md)
