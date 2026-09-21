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

### Why did auto-cull keep several near-identical photos from one burst — or send some to Reject?

Auto-cull scores every photo **independently**, so a run of near-identical
frames (same pose, seconds apart) can each clear the Keep cutoff on their own.
The **Deduplicate Keeps within a burst** pass (Settings → Model → Decision
thresholds, on by default) reconciles that against the burst grouping you see
in the UI. For each burst it:

- Always keeps the single best photo — by default the **vision model's** pick
  for that group, the same judgement behind the amber-star hero (see the next
  entry); the score priority only decides it when no AI rank is available.
- Lets a few **extra** frames also Keep — but only genuinely strong ones: each
  must clear the Keep cutoff by a margin *and* score close to the burst's best
  frame, up to a maximum number of Keeps per burst.
- **Rejects near-pixel-identical duplicates** outright: if two frames are
  visually near-identical (cosine ≥ 0.97, the same test the AI burst ranker
  uses), the lower-scoring one is redundant regardless of its score and goes
  straight to Reject.
- Demotes the rest, **graded**: a close alternate goes to **Maybe**; a clearly
  weaker frame (well below the best, or under the Maybe cutoff) goes to
  **Reject**. And once the burst has already kept a few Maybe alternates, every
  *further* redundant frame is Rejected too — this is what stops a 30-frame
  burst from dumping 29 Maybes on you. A big tight burst ends up roughly *1
  Keep + 2 Maybes + the rest Reject* instead of *1 Keep + 29 Maybes*.

**Distinct scenes within one burst keep their own budget.** The time-gap
grouping can lump *visually different* shots into one burst — you shoot one
angle, pan, and shoot a completely different angle a few seconds later, still
inside the burst's time window. The dedup pass detects those distinct sub-scenes
(cosine union-find at 0.94, stricter than the 0.90 that forms the burst) and
gives **each scene its own Keep/Maybe budget**. So a burst that's really two
different framings can auto-Keep two photos — one per scene — instead of
rejecting the second as a "redundant" copy of the first. A burst that's genuinely
one moment still collapses to a single Keep. A frame the model can't place
visually (missing embedding) gets its own scene and is kept, never auto-rejected.

Five knobs sit under the toggle. The score-based ones read `personal_score`
points once the model is Ready, `overall_score` points before that:

- **Max Keeps per burst** (default `2`) — how many photos from one burst may
  auto-Keep, counting the best. **Set to `1`** to keep only the single best
  photo per burst (the original behaviour).
- **Max Maybe alternates per burst** (default `2`) — after the Keeps, how many
  more frames may stay as Maybe for review before the rest auto-Reject. **Set
  to `0`** to reject every non-kept frame; raise it for a wider safety net.
- **Extra-keep margin above cutoff** (default `8`) — how far over the Keep
  cutoff an extra frame must score to survive. Higher = pickier.
- **Extra-keep closeness to best** (default `6`) — how close to the best frame
  an extra frame must be. Lower = only near-identical-quality frames double-keep.
- **Reject when this far below best** (default `20`) — a non-surviving frame
  more than this far below the best (or below the Maybe cutoff) is Rejected
  instead of routed to Maybe. Raise it to make this specific Reject rarer.

The pass uses whichever score drove each photo's Keep verdict —
`personal_score` once the model is Ready, `overall_score` in threshold-fallback
mode. It runs in both the live preview and the committed run, so the counts you
see before committing match what happens. See
[`_cap_burst_keeps`](backend/routers/decisions.py) and the burst keep-guarantee
floor pass, which still rescues a burst that would otherwise end up with zero
Keeps.

### Who decides which photo of a burst auto-cull keeps?

**The local vision model, by default** (Settings → Model → Decision thresholds →
*Let the AI pick each burst's best photo*). It is the same comparison you get
when you open a group in GroupLoupe: all the frames go to the model in one
context and it ranks them against each other — peak smile vs. 80 ms before, eyes
fully open vs. mid-blink. Turn it off and the winner is chosen by the score
order instead (sharpest face → eyes open → sharpest frame → IQA → aesthetic →
overall), which can't see any of that. On this library the two disagree in
**29 of 41** groups, so it is not a tiebreaker — it changes the outcome.

**Only contested groups are ranked**, because a fresh rank costs about a minute:

- The group needs **3+ photos** (the same floor the background pre-ranker uses).
- And either **2+ Keeps** — the dedup pass has to choose which survives — or
  **zero Keeps**, where the keep-guarantee pass decides which photo you get to
  rescue. A group with exactly one Keep is already settled, so ranking it would
  be paid for and ignored.

Groups you already opened in GroupLoupe, or that the background pre-ranker
drained, are **free** — it is one shared cache. The Auto-cull modal tells you how
many groups still need ranking and roughly how long before you commit, then
shows the progress. **Stop is safe at any point during ranking**: nothing moves
until ranking has finished, so stopping leaves the folder untouched, and the
ranks already computed are kept for next time.

The AI's pick decides **one thing only**: which photo is the group's winner.
The Keep/Maybe budgets, which near-duplicate survives a pair, and the order
things demote in all still run on the scores. And in the keep-guarantee case it
will not promote a frame that was rejected as physically unrecoverable (blurry,
over the ISO ceiling, breaks the reciprocal rule, low IQA) when a merely
subjectively-rejected alternative exists — ranking frames against each other
answers "which of these is best", not "is this one good enough".

**No vision model installed, or Ollama not responding?** Every group falls back
to the score pick and the run completes — but it says so, both in the response
and in the completion toast ("N groups used scores"). A silent fallback would be
indistinguishable from the feature working.

### Does the grouping Time-gap slider affect auto-cull?

**No — auto-cull dedup ignores the Time-gap entirely.** That slider (Settings →
Grouping) only shapes the groups you *browse* in Bursts / GroupLoupe. Auto-cull's
burst dedup groups by **visual similarity only**.

This is deliberate. The Time-gap can only ever split bursts *coarser* than the
camera's inter-frame spacing — and continuous shooting fires several frames
within one EXIF second (timestamps are second-resolution), so those frames share
a timestamp and **no gap can separate them**. On a library of fast bursts the
slider was effectively inert for auto-cull. Cosine similarity, by contrast, *can*
tell those frames apart. So auto-cull forms its groups with the **Similarity**
threshold and decides how many survive within each group via the **Max Keeps /
Max Maybe alternates per burst** budgets. Those are the real levers.

("But won't similarity-only grouping merge two separate moments minutes apart
into one giant burst?" — no: the dedup pass then splits each group into distinct
visual sub-scenes at cosine 0.94 and budgets each scene separately, so distinct
moments each keep their own photos. See the burst-dedup FAQ above.)

### Why does the auto-cull Keep/Maybe/Reject count barely move when I change grouping?

Because burst dedup can only re-decide photos that were **already Keeps**. The
Auto-cull preview's Rejects come from two independent sources:

1. **Per-photo quality rejects** (the personal model's own call, or the quality
   thresholds below readiness) — usually the large majority. These are decided
   photo-by-photo and **no grouping change can touch them**. If the model
   already rejected a soft or badly-exposed frame, it stays rejected.
2. **Redundant burst-frame rejects** — the only ones grouping affects. Raise the
   **Similarity** threshold or lower the **Max Keeps / Max Maybe** budgets → more
   redundant frames demoted; loosen them → fewer.

The Auto-cull modal shows this split explicitly (a line like *"12 rejected as
redundant burst frames · 281 from per-photo quality"*), plus a "Redundant burst
frame" row in **Why rejected?**. If the counts barely move, it's telling you your
model is doing most of the culling on its own — the burst dedup is a small
top-up, and the lever to make it more aggressive is Similarity + the budgets.

### Does auto-cull teach the personal model?

No. The personal model learns **only** from your manual K/M/X decisions —
never from auto-cull's own output. If auto-cull fed itself, it would lock in
its own mistakes (a self-reinforcement loop) and your taste would stop
steering it.

Mechanically, the training-corpus write is gated in
[`_apply_decision(..., is_auto=...)`](backend/routers/decisions.py#L220):
`POST /auto-cull` passes `is_auto=True` and writes the `decisions` row but
skips `training_samples`; manual `POST /decision` and `POST /bulk-decision`
use the default `is_auto=False` and write the sample.

The payoff is the override case: when you **disagree** with an auto-cull
call and change it by hand — e.g. auto-cull rejected a frame you then Keep —
that manual decision *is* recorded as a training sample, so the model learns
from exactly the moments it got you wrong. A photo auto-culled and never
touched contributes nothing to training.

### If I cull my Apple Photos library by hand, does that train anything?

**Short answer:** Yes, but into a *second* model, not the camera one — and that
model stays silent until its corpus is much larger than the camera model's was.

There is one model per corpus — the **camera model** and the **iPhone model**
in the UI, `folder` and `photos` in the database (the internal keys predate the
names and are not worth a migration). `training_samples.corpus` tags each sample,
taken straight from
`images.source_type` ([decisions.py:409](backend/routers/decisions.py#L409)), and
[`state.model_for()`](backend/state.py) hands each row to the model trained on
its own distribution. They never mix: an iPhone HEIC and a RAF score on
incompatible sharpness / aperture / ISO distributions, so one GBR over both
would learn contradictory labels — the same reasoning as the RAW+JPEG
companion's `_train=False`.

Two things differ for the iPhone model:

- **Its training floor is 150, not 30** ([`min_decisions_for()`](phase3_learning/personal_model.py)).
  An iPhone's computational pipeline flattens exactly the variance the model
  learns from, so a fit at 30 samples would be noise wearing a model's clothes.
- **Both models now predict an *absolute* score** rather than adjusting
  `overall_score`. For Photos that base is unusable: measured on 449 rows, `overall_score` has a median of 93.1
  and half the corpus sits inside a six-point band, because 50% of iPhone photos
  score ≥95 sharpness. The camera model switched for a different reason — a
  bootstrap sweep showed pure GBR beats the old offset formula there too (0.741
  vs 0.713 ordinal accuracy). A side effect worth knowing: `personal_score` is
  no longer a quality percentage in either model, which is why the decision
  cutoffs are derived from the fit instead of typed in.

Until the iPhone model reaches the ready tier, Photos rows show no
`personal_score` and auto-cull on a Photos tab declines with a count of how many
decisions are still needed ([`_require_auto_cull_model`](backend/routers/decisions.py)).
That refusal is deliberate: falling back to the quality thresholds there would
mark roughly 70% of an iPhone library as Keep. Settings → Model shows the
corpus filling up.

---

## Files & workflow

### Which file formats can KaMeRa analyze?

**Short answer:** RAF · NEF · 3FR · DNG · ARW · CR2 · CR3 · JPG/JPEG · PNG ·
TIFF · HIF. `.3FR` (Hasselblad X2D II) and `.DNG` (DJI drones, Adobe DNG
Converter output, natively-DNG cameras) are both fully supported.

The single source of truth is
[constants.py::RAW_FORMATS](backend/constants.py#L30). Two other constants are
*derived* from it and should never be edited by hand:

- `RAW_DECODE_SUFFIXES` — lowercase, dot-prefixed mirror, for matching
  `path.suffix.lower()`. Every scorer's RAW decode branch imports this.
- `SUPPORTED_EXTENSIONS` — the union of `RAW_DECODE_SUFFIXES` and the
  processed formats. This is the gate the analyzer, the file watcher, and
  `GET /images`' folder scan walk with.

**Why the derivation matters:** the two constants used to be maintained
separately, and a format could be listed as decodable in `RAW_FORMATS` while
being missing from `SUPPORTED_EXTENSIONS` — in which case the files were never
*discovered*, which on screen is indistinguishable from an empty folder. That's
exactly what happened to `.dng`/`.arw`/`.cr2`/`.cr3` until 2026-08-28.
`tests/test_raw_format_coverage.py` now fails if they drift apart, or if any
scorer reintroduces a hardcoded `('.RAF', '.NEF', '.3FR')` tuple.

**Adding a new RAW format** is a one-line change to `RAW_FORMATS` — provided
rawpy/LibRaw can decode it. Verify with `analyze_photo_quality()` on a real
file, not `rawpy.imread()`: the former is the actual call site and exercises
all seven scorers.

Two formats that look supported but aren't analyzed standalone: `.hif` *is*
analyzed on its own (X100VI / X Half can shoot HIF-only), but becomes
move-only when a same-stem RAW sits beside it — see
[is_standalone_hif](backend/constants.py#L77). `.phos` is a Hasselblad
edit-file and is never analyzed.

Note: ARW / CR2 / CR3 are wired through the same derivation but have never been
run against real Sony or Canon files.

### What gets written to disk on every K/M/X decision?

1. **The RAW + sidecar move** to `_Keeps/`, `_Maybes/`, or `_Rejects/` (or
   per-folder overrides). The Fuji `.HIF` companion moves with the RAW when
   present.
2. **`XMP:Rating` (5/3/1) and `XMP:Label` (Green/Yellow/Red)** to a `.xmp`
   sidecar next to the photo, so Lightroom Classic / Capture One / Bridge /
   Luminar Neo see the decision on import. Toggle in Settings → Display
   ("Write decisions to XMP sidecars", default ON). Best-effort — XMP failures
   log but never abort the decision.
3. **A row in `decisions`**. For *manual* K/M/X, also a frozen feature
   snapshot in `training_samples` (durable, no FK to `images`, survives Clear
   Analysis). Auto-cull decisions write the `decisions` row but **not** the
   `training_samples` row — see "Does auto-cull teach the personal model?"
   below.

If `reject_to_system_trash` is enabled, the reject branch routes through
`send2trash` instead of `_Rejects/` and skips the XMP write (the file is no
longer addressable). *This setting did nothing until 2026-09-19* — it was
written as `"1"` and read with a `== "true"` test, so it always evaluated
False and rejects kept going to `_Rejects/`. Fixed in
[sources/folder.py](../backend/sources/folder.py) via
`database.get_bool_setting()`; if you had the toggle on before that date,
it is live now.

### Does auto-cull respect "send rejects to system Trash"?

**Short answer:** yes — and that is the one setting that makes auto-cull
irreversible, so the modal now says so before you click.

Auto-cull does not have its own file-moving code. It commits through the same
sink as a manual **R** press
([sources/folder.py:65](../backend/sources/folder.py#L65)), which means
`reject_to_system_trash` applies to it in full. With the toggle on, pressing
**Cull** sends every rejected file to the OS Trash in one action.

That changes what you can take back:

| Route | Where rejects go | Can `U` undo it? |
|---|---|---|
| Default | `_Rejects/` inside the source folder | **Yes** — the file never left |
| `reject_to_system_trash` on | The system Trash | **No** — recoverable from the Trash, but KaMeRa can no longer address the file |

`GET /auto-cull/preview` resolves the actual destination server-side and
returns it as `reject_destination` (`mode`, `label`, `undoable`), honouring any
per-folder `rejects_folder` override. The modal renders that instead of a fixed
sentence, switches the confirm button to the coral destructive variant in Trash
mode, and leads with the count that leaves the shoot rather than the total.

### Can I cull a Smart Album?

**Yes, since 2026-09-19.** They appear in the album picker with a cyan sparkle
icon and the note *"contents update themselves"*.

Two differences from a regular album, both worth knowing:

**Photos has to be running** with that library open. A Smart Album is a stored
query, not a stored list — Photos keeps no membership rows for one and
evaluates the predicate live, so its contents can only come from Photos itself
(over AppleScript, ~0.3 s for 2000 photos). Regular albums are read straight
from the library catalogue and need none of that. If Photos is closed you get a
503 that says so; if it is closed while the *picker* is open, the Smart Album
simply shows `—` instead of a count.

**Membership moves under you.** That is the point of a Smart Album, but it
interacts with culling: a Smart Album built on `KaMeRa:Keep` (the pattern this
app suggests elsewhere) gains and loses photos *as you decide them*. Analysis
takes a snapshot — the `images` rows are created once — so your grid stays
stable, but **Compare with Photos** will then report those photos under *"No
longer in this album"*. That class defaults to leaving rows alone, so it is
noise rather than risk; just do not bulk-drop it on a keyword-based Smart Album.

Apple's own smart albums (Favourites, Recents, Screenshots, Selfies…) are not
offered. They are not addressable the same way and their names exist only as
runtime localisations, so KaMeRa offers the ones you made yourself.

### New photos are in my Smart Album but I cannot see them in KaMeRa

**Short answer:** Tab settings → **Re-analyse album**. It re-reads the album
and analyses anything new; everything already analysed is skipped. Then read
the result banner — it now says what it could *not* take.

A Smart Album is a stored query, so it gains members on its own. Nothing polls
for that: the Resume banner measures against the asset count the last run
recorded, so an album that grew still reports nothing missing — the counter is
written by the very run it is supposed to prompt.

**If almost nothing appears, the photos are probably still in iCloud.** This is
the common case for photos shot in the last day or two: the asset is in the
album, but its original file has not come down to this Mac yet. Analysis reads
the original — the local 768×1024 derivative would produce a sharpness score
that means nothing and would then enter the training corpus as if it were real
([photos_library.py](../backend/photos_library.py), `album_assets`). Measured on
one real album: of 18 new members, 16 were iCloud-only.

The result banner names them in amber. To fix it, select those photos in
Photos, choose **Download Original**, wait for the download, then re-analyse.
Videos are listed separately and greyed out — they are never analysed.

**Compare with Photos** shows the same photos under *"In the album, never
analysed"*, with the iCloud ones marked *not downloaded*. It is the better view
when you want the list before acting, but it re-parses the whole library
catalogue (~11s on a 2000-photo album), so the button is the faster route when
you just want them in.

### I changed a KaMeRa keyword in Photos — does KaMeRa notice?

**Not on its own.** Decisions flow one way: KaMeRa writes the keyword and never
reads it back. Open the Photos tab's settings and press **Compare with Photos**
to see the drift and decide what wins.

The comparison sorts every disagreement into six kinds, because they do not all
have the same answer:

| What it found | What it usually means | Default |
|---|---|---|
| Decided here, unmarked in Photos | The write never landed, or you deleted the keyword | **Re-apply the keyword** |
| The two disagree | Both sides carry a decision, and they differ | Leave alone |
| Decided in Photos only | You set the keyword directly in Photos | Leave alone |
| No longer in this album | The photo left the album | Leave alone |
| Gone from the library | The photo was deleted | Leave alone |
| In the album, never analysed | Photos joined the album after the last run | Leave alone |

**Why only the first one is preselected:** Photos and KaMeRa disagree for good
reasons in both directions — a keyword you set by hand is a real decision, and so
is one you made here. Picking a winner automatically would be wrong about half the
time and you would never see it happen. The first row is different: there is no
contradiction there, one side simply never got the message.

**The last row is the only one that is not a disagreement.** A Smart Album is a
stored query, so it picks up new photos on its own — those sit in the album with
no scores and cannot be culled. Choosing *Analyse them* does not write anything
from the comparison; it runs the album's ordinary analysis afterwards, which
skips everything already done. Videos and formats KaMeRa cannot decode are not
listed: they are permanent non-members of the analysable set, and an album's few
hundred videos would bury the photos that matter.

Photos whose **original is still in iCloud** are listed but marked *not
downloaded*. Analysis reads the original file; the local 768×1024 derivative
would produce a sharpness score that means nothing and then enter the training
corpus as if it were real. Select them in Photos, choose **Download Original**,
then run the comparison again.

Two things it refuses rather than guesses:

- A photo carrying **both** `KaMeRa:Keep` and `KaMeRa:Reject` has no single "what
  Photos says". Adopting it is refused and reported, so you resolve it in Photos.
- **Removing a row** for a photo that left the album is a separate choice from
  removing one whose photo was deleted. Dropping the second never drops the first.

Removing a row only removes KaMeRa's record — the photo in your library is never
touched. Photos exposes no delete to KaMeRa at all
([sources/photos.py](../backend/sources/photos.py)).

The comparison re-reads the whole library catalogue on purpose, which takes a
moment on a large library: a cached read would report drift that is already gone
and miss the drift you are looking for.

### Does "Move to system trash" affect other tabs?

**No — not since 2026-09-19.** It used to. `POST /trash-rejects` swept *every*
reject in the database, while the bar it lives in is labelled with the active
tab's reject count — so pressing it in one folder tab emptied another's rejects
under a number that described neither.

`source_folder` is now a required body field
([decisions.py](../backend/routers/decisions.py)): a call that omits it gets a
422 rather than falling back to a wider sweep, and the confirm line names the
tab it will act on. Photos albums were never affected — `supports_bulk_trash`
is `False` there, because Photos exposes no delete to AppleScript at all.

### In what order should I work through the buckets after an auto-cull?

**Short answer:** turn on **Only unconfirmed** and the order stops mattering.
Without it, the one order that never shows you the same photo twice is
**Rejects → Maybes → Keeps**.

The naive instinct is to rank the buckets by importance. The useful question is
where a photo *goes* when you touch it. Every bucket feeds the other two —
Rejects promote to Keep, Maybes split both ways, Keeps demote — so on paper no
linear order is safe: whatever you visit last receives photos from everything
before it.

What breaks the tie is that a human verdict is recorded per photo, not per
bucket. `decisions.reason` is `'manual'` after any K/M/R you press and after
Mark reviewed, and names the auto-cull rule (`personal_keep`, `closed_eyes`,
`burst_duplicate`, `uncertain`, …) otherwise
([decisions.py:600](backend/routers/decisions.py#L600)). The **Only unconfirmed**
pill in the filter bar filters on exactly that, in every bucket at once
([format.js::isUnconfirmed](frontend/src/ui/format.js)). With it on, a photo
you decide leaves the view immediately and never comes back, wherever it
landed — so you can work the buckets in any order, and the All view plus the
filter is your whole remaining workload in one list.

**If you'd rather not use the filter,** Rejects → Maybes → Keeps still works,
for a specific reason:

1. **Rejects** is untouched, so it holds only machine verdicts. A rescue (R→K)
   goes straight to `_Keeps/_Keeps_reviewed` because manual keeps skip the
   review gate ([decisions.py:387](backend/routers/decisions.py#L387)) — so it
   won't show up as awaiting-review work in step 3. Sort by `personal_score`
   descending and the model's most likely mistakes come first. The one rule:
   don't press M here, or you'll refill the bucket you visit next.
2. **Maybes** still holds only auto-Maybes; step 1 pushed nothing into it. K
   exits to the reviewed folder, R exits into a bucket you're already done with.
3. **Keeps** last, because both earlier passes feed it. Everything you promoted
   by hand is already marked reviewed, so the "N auto-culled photos awaiting
   review" bar scopes itself to the genuine leftovers. Demotions from here fall
   into finished buckets.

**Pressing the same key again is a confirmation, and it trains.** K on a photo
auto-culled to Keep writes a `training_samples` row — a human label at the
model's decision boundary, which is the most valuable kind. **Mark reviewed**
(the ✓ on hover) is the other half of the pair: it marks the photo as seen
*without* writing a training sample, so a bulk review can never feed the model
its own output. Both set `reason = 'manual'`, so both remove the photo from the
unconfirmed filter.

There is deliberately no "confirm all Maybes/Rejects" button. That would be
rubber-stamping at scale and would defeat the guard described in "Does
auto-cull teach the personal model?" above.

### How do I re-test auto-cull on an already-analysed group without re-analysing?

**Short answer:** Multi-select the group's photos, press **U** (or **Cmd+Z**)
to send them all back to undecided in one keystroke, then run **Auto cull**
again. Undo only reverses the *decision* — it never touches the analysis, so
no photos get re-decoded.

Why it's safe to loop: undo (`/undo-decision` →
[`_apply_undo`](backend/routers/decisions.py#L622)) moves the file back home
out of `_Keeps`/`_Maybes`/`_Rejects`, deletes the `decisions` row, and drops the
`training_samples` row for that photo — but it leaves the `images` row
completely intact. Every score, feature, and embedding stays frozen from the
original analysis, so the next auto-cull run re-decides from the *same*
numbers. That's exactly what you want when tuning thresholds or testing a new
auto-cull rule: change the setting, revert the group, re-cull, compare.

**Bulk undo acts on the whole selection, in every view.** This mirrors K/M/R:
once a multi-selection is active, one keystroke acts on all selected photos.
In the grid the multi-select branch runs *before* the undo stack
([useKeyboard.js:230](frontend/src/hooks/useKeyboard.js#L230) →
[`bulkSendUndo`](frontend/src/App.jsx#L1664)); the GroupLoupe binds the same
selection-aware undo. Already-undecided members in the selection are skipped
(nothing to reverse).

One quirk to expect: a **group tile only ever shows *undecided* members**, so
once auto-cull has decided a group the photos split out into individual cards
in the Keep / Maybe / Reject (and All) views. You select *those cards* to
revert — and because group membership is merged from live state
([`enrichedGroups`](frontend/src/hooks/useGroups.js#L154)), the reverted photos
rejoin their group tile immediately, with no folder reload.

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

### Why would TOPIQ and Aesthetic be blank for every photo in a batch?

**Short answer:** A model that failed to load stays failed for the whole
process, by design. Before 2026-08-28 a thread race could cause that on cold
start; it's fixed now. If you still see it, restart the app and check
`data/app.log` for the *first* load error.

`iqa_scorer` and `aesthetic_scorer` each latch a `_load_failed` flag on a fatal
load error, so subsequent photos short-circuit instead of re-attempting the
load 700 times ([iqa_scorer.py:79](../phase2_quality/iqa_scorer.py#L79)). That
guard is deliberate — but it means **one** bad load blanks the column for the
entire run, and the UI just shows empty values rather than an error.

The race that used to trigger it: `accelerate` initialises weights on the meta
device via a **global** monkey-patch of `torch.nn.Module.register_parameter`.
With the seven scorers fanned across a six-worker pool, SigLIP loading in one
thread made pyiqa build its net on the meta device in another, and that
thread's `.to(device)` failed with `Cannot copy out of meta tensor`. Every
torch loader now holds one process-wide lock across construction
([model_loading.py](../phase2_quality/model_loading.py)); the per-module locks
that preceded it couldn't help, because the collision was *between* modules.

Recovery if a load fails for some other reason (bad torch upgrade, corrupted
weight cache): **Settings → Model → Reload models** clears `_load_failed` via
`reset_load_failed()` and retries, without restarting the app.

### What happens when I drop a whole card into the watched folder at once?

The files queue up and get analyzed **one at a time** by a single worker
thread — the "analyzing… (N left)" pill counts the backlog down honestly.
Detected files go onto a `queue.Queue` in
[backend/file_watcher.py](../backend/file_watcher.py) (`FolderWatcher._enqueue`
→ `_worker_loop`); repeat events for the same file are deduped via a pending
set, and the 2s write-settle wait is measured from each file's arrival, so a
long backlog doesn't re-pay it per file. Expect a few seconds per RAF, ~1s
per JPEG — a 260-file card takes a while, but the UI stays responsive the
whole time and progress is visible.

Before 2026-07-19 this spawned one analysis thread **per file**, so a bulk
card drop launched hundreds of simultaneous RAW decodes and wedged the whole
backend (334 threads / 16 GB RAM / frozen UI). If the watcher ever looks
stuck again, check thread count and `data/app.log` before assuming it's dead.

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

### How do I move a whole group of Maybes (or Rejects) at once?

Two places, both acting on the group members *you can currently see* — a
survivor culled into a different bucket is left alone:

- **In the grid** (Maybes or Rejects view): hover the amber/coral-bordered
  run and a small action bar appears at its top-left — "Keep all" and
  "Reject all" on the Maybes view, "Keep all" / "Maybe all" on the Rejects
  view (it only offers the two decisions that aren't the one you're
  looking at). One click moves every visible member of that run.
- **In the "Group members" overlay** (opened by clicking the survivor chip,
  e.g. "1 Keep"): each section header (Keeps / Maybes / Rejects) with two or
  more photos gets the same "…all" buttons, so you can move a whole bucket
  without touching each row.

Both route through the same undoable `bulkDecide` batch path (one round-trip,
one Undo entry), so `U` / Cmd+Z reverses the whole move. The grid bar hangs on
the run's first tile via `runImageIds`; controls live in
[frontend/src/ui/TileChips.jsx](../frontend/src/ui/TileChips.jsx) `GroupBulkBar`
and [frontend/src/modals/GroupPartnersOverlay.jsx](../frontend/src/modals/GroupPartnersOverlay.jsx)
`SectionBulkBar`.

### Why do burst frames look ungrouped in the Keeps view?

They aren't — the **Keeps view deliberately doesn't draw groups**. Three gates
in [frontend/src/App.jsx](../frontend/src/App.jsx) stack up:

1. `gridItems` ([App.jsx:896](../frontend/src/App.jsx#L896)) emits a `group`
   tile only from *undecided* members. Any decided photo becomes an
   individual `{type:'image'}` card, so a fully-culled burst produces zero
   group tiles.
2. `displayGridItems` ([App.jsx:1009](../frontend/src/App.jsx#L1009)) drops
   surviving group tiles under any decision filter other than Undecided.
3. The re-clustering pass that rebuilds a visual enclosure for *decided*
   photos (`DecidedGroupTile` + survivor chip) is gated to
   `reject`/`maybe` only ([App.jsx:1064](../frontend/src/App.jsx#L1064)).

The rationale: on Rejects/Maybes you want to confirm "did a groupmate
survive?", which is what the chip answers. On Keeps every member already
survived, so there was nothing to reassure you about. Consequence: the Keeps
view is the one place where burst membership is invisible.

To see the real grouping, switch the filter to **All** or **Undecided**, or
open the group in GroupLoupe. To verify from the outside:

```bash
curl -s "localhost:8000/similarity-groups?threshold=0.90&time_gap_seconds=60" \
  | python3 -c "import json,sys; [print(g['size'], sorted(i['filename'] for i in g['images'])) for g in json.load(sys.stdin)['groups']]"
```

### Why did two frames seconds apart still land in different groups?

Three independent gates must all pass, and any one can split a burst. In
order:

1. **Time gap** — the primary split
   ([similarity_scorer.py:625](../phase2_quality/similarity_scorer.py#L625)).
   Default 60s ([useGroups.js:62](../frontend/src/hooks/useGroups.js#L62)).
   A 61-second gap splits; there is no tolerance band.
2. **SigLIP cosine ≥ threshold** (default 0.90). Note union-find is
   *transitive*: two frames at cosine 0.81 still share a group if a chain of
   intermediate frames links them. So a low pairwise score proves nothing on
   its own.
3. **The layered union gate**
   ([`_pairwise_can_union`](../phase2_quality/similarity_scorer.py#L414)),
   which runs on every edge that already cleared the cosine threshold:
   - both frames have a FaceNet embedding → require face cosine ≥ 0.50
     ([`_FACE_IDENTITY_COS`](../phase2_quality/similarity_scorer.py#L312));
   - otherwise → scene-tag blocker, then a strict RGB-histogram cosine ≥ 0.90.

Gate 3 is the common surprise. A frame where the subject turns away or gets
small can score face-cosine ~0.4 against its own neighbour and be denied
despite a 0.96 SigLIP match — the identity gate reads it as a different
person. A frame with **no face detected at all** falls to the histogram path
instead, where a background or exposure change (hist cosine ~0.77) splits it
off.

### The grouping defaults look wrong on my library. Can I tune them?

Yes — Settings → Workflow → Grouping section has the Similarity threshold
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
Amber ring + corner star in both Survey and Loupe filmstrip mark the pick, and
both modes now also show the LLM's one-line reason ("AI pick — sharpest eyes,
subject leaning into frame") — as a small badge under the star in Survey, and
as a caption over the big preview in Filmstrip mode while the hero photo is
focused. Non-hero photos the LLM ranked also carry a small numbered badge
(`2`, `3`, …) so a burst reads as fully triaged, not just "one winner picked."

Press **A** to accept the AI's pick in one keystroke: keeps the hero, rejects
the rest of the burst, closes the loupe. Same underlying call as the "Keep
best · Reject rest" toolbar button — the hotkey just skips the mouse. See
[frontend/src/views/GroupLoupe.jsx](../frontend/src/views/GroupLoupe.jsx)
`runBatch`, `rankById`.

### Can I see the AI's pick without opening GroupLoupe?

Yes, since 2026-09-19 — the LLM rank-1 isn't a loupe-only fact any more. Once
the background prerank worker actually ranks a burst (`prerank_state ==
"ready"`), `backend/routers/model.py`'s `_annotate_prerank_state` promotes
that rank-1 photo's id and reason onto the group's `best_image_id`/
`best_reason` server-side — the same fields every surface in the app already
reads to answer "which one's the best of this group." A manually-pinned hero
(the settings-table override) still wins over the AI, and a burst that hasn't
been ranked yet keeps showing its score-based guess exactly as before, just
without any of the markers below (`best_is_ai_pick` stays `false`).

Where it shows up:

- **Grid tile** — the group tile's "Best: <filename>" footer already read
  `best_image_id`, so it now names the AI's pick, not just the score winner,
  the moment ranking finishes — no extra chrome needed.
- **Group-scrub peek panel** (click a group tile) — the position bar's
  per-frame segment for the AI's pick gets the app's rainbow `.ai-border-sm`
  stroke, the same cue GroupLoupe's "ranking in progress" chip uses. No
  stroke on any segment means the burst hasn't been AI-ranked yet.
- **DetailView** — opened via a group tile (grid-filmstrip mode), the "Best:
  <filename>" line grows a small rainbow "AI pick" chip; opened from inside
  GroupLoupe/the peek panel, the group filmstrip's hero thumbnail gets the
  rainbow ring instead of the plain white outline. GroupLoupe additionally
  hands DetailView its own live `effectiveHeroId` rather than the group's
  possibly one-poll-stale `best_image_id`, so a rank that resolved seconds
  ago (before the next `/similarity-groups` poll) still shows correctly.

See `_promote_ai_pick` in
[backend/routers/model.py](../backend/routers/model.py),
[frontend/src/ui/GroupScrubPreview.jsx](../frontend/src/ui/GroupScrubPreview.jsx),
[frontend/src/views/DetailView.jsx](../frontend/src/views/DetailView.jsx).

### How does AI burst ranking work?

When you open a similarity group, the app sends up to 12 preview JPEGs in one
chat call to a local vision model (qwen3-vl:30b-a3b-instruct by default, via Ollama) and
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

### Does the AI burst ranking ever influence auto-cull decisions?

No, and this is deliberate. `/rank-burst` only ever affects the loupe UI (which
tile gets the amber "AI pick" star, and the hero used by the "Keep best ·
Reject rest" batch action) — it never writes a decision on its own, and
`_compute_auto_decision`/`_apply_decision`
([backend/routers/decisions.py](../backend/routers/decisions.py)) contain no
reference to it. The personal GBR model (trained only on your own manual
decisions) is the only thing that drives auto-cull.

The reasoning: `qwen3-vl` is a generic vision model with generic "good photo"
priors, not your taste specifically. Letting it influence a *committed*
decision — even just as a tiebreaker in the burst-keep-guarantee post-pass,
which only ever promotes to Maybe — would mean a generic model's judgment
quietly shapes which photos later become training samples (a promoted Maybe
you confirm or override still trains the model). That's a slow-taste-dilution
risk, not a performance one, so the LLM stays confined to the one place you're
actually looking at the photo and can accept or reject its suggestion
yourself — see the "AI pick" hotkey (**A**) above.

### How do I see why a photo got a particular auto-cull decision?

Two places, with different lifetimes:

- **`decisions.reason`** — the rule that produced the *current* decision
  (`manual`, `personal_keep`, `closed_eyes`, `burst_keep_guarantee`,
  `uncertain`, …). Overwritten on re-decision; cleared by Clear Analysis or
  closing a tab, same as the decision itself.
- **`auto_decision_log`** — an append-only history of every decision ever
  made, manual and auto alike (with an `is_auto` flag), keyed on the photo's
  `uuid` so it survives re-imports. Unlike `decisions`, this table is *not*
  wiped by Clear Analysis or tab-close — same durability convention as
  `training_samples`/`shooting_log`. This is what makes questions like "how
  often does the keep-guarantee actually fire" or "how often do I override
  auto-cull" answerable from the database instead of guessed at. No UI reads
  it yet as of 2026-07-25 — it's a foundation for future dashboard/analytics
  work.

See migrations v49/v50 in [backend/database.py](../backend/database.py) and
`_apply_decision()` in
[backend/routers/decisions.py](../backend/routers/decisions.py).

### When does AI burst ranking start running for a freshly analyzed folder?

Two triggers:

1. **Mid-batch**, every 50 photos. The polling tick that drives the progress bar also re-fetches `/similarity-groups` and POSTs the new memberships to `/prerank-groups`. So as soon as enough photos are scored to form recognisable bursts, the AI worker starts ranking them in the background — you don't have to wait for the batch to finish. See [frontend/src/hooks/usePolling.js](../frontend/src/hooks/usePolling.js) `MID_BATCH_PRERANK_EVERY`.
2. **At batch completion**, one final pass to catch bursts that only stabilised in the last 50 photos.

Re-posting growing supersets is safe — the backend's `enqueue_groups` skips any membership that already has a `burst_rankings` cache row, so the worker never re-ranks the same group.

If a burst hits an analyzed RAF or NEF whose preview hasn't been generated yet (Z6III HE\* NEFs always; RAFs whose viewer cache wasn't warmed), the ranker now generates one on the fly using the same `_generate_preview` path as `/previews/<id>` and writes it back to the cache — so the viewer benefits too. See [phase2_quality/burst_ranker.py](../phase2_quality/burst_ranker.py) `_resolve_preview_bytes`.

### How do I install the qwen3-vl vision model?

Two paths, both end at the same result:

1. **In-app (recommended).** When the app detects no vision model is installed,
   the GroupLoupe top bar and the Settings → AI explainer panel both show a
   "Pull qwen3-vl now (≈20 GB)" button. Click it, the download runs in a
   background thread and reports progress through the same model-status banner
   that shows SigLIP/TOPIQ-NR/TOPIQ-IAA/FaceNet downloads. The model is reused
   forever after.
2. **Terminal.** `ollama pull qwen3-vl:30b-a3b-instruct`. Same result, no app interaction.

Upgrading from `qwen2.5vl:7b`? Both can stay installed — the app prefers `qwen3-vl` whenever it is present (order of `_VISION_PREFIXES` in [phase2_quality/llm_explainer.py](../phase2_quality/llm_explainer.py)). Free the 6 GB with `ollama rm qwen2.5vl:7b` once you are happy with it.

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

*Last updated: 2026-07-27*

---

### Why did a blurry photo get Sharpness 100?

Only on the old metric, and only when the frame was grainy. It measured how much
*contrast energy* the pixels carry, and sensor noise carries plenty — at ISO 1000
on an iPhone the grain alone reads as "sharp". Photos now use edge width instead
([edge_sharpness.py](../phase1_technical/edge_sharpness.py)): how many pixels an
edge takes to go from dark to light. Blur widens it; grain and contrast don't
change it. Folder photos still use the old metric on purpose — its thresholds are
tuned on that scale.

### Why does a photo with a sharp background but a soft face still say "soft face"?

Because that is its own reject reason, and it has to be. On iPhone photos the
sharpness number you see is the *lower* of frame and face — a soft face drags
the whole score down, which is the point. But the soft-face rule needs the
frame's own sharpness to ask its question ("the camera focused on something,
just not the face"), so it reads a separate stored measurement
([edge_width_px](../backend/routers/decisions.py)) rather than the score on screen.

It matters more than it looks: once the model is ready, auto-cull decides on the
model's verdict and never reaches the "blurry frame" fallback, so soft-face is
the only sharpness guard left standing.

### What is the difference between the camera model and the iPhone model?

They are two taste models over two separate sets of your decisions, because an
iPhone HEIC and a camera RAW score on incompatible distributions. Camera photos
(folder tabs) train the camera model; Apple Photos albums train the iPhone one.
They need different amounts of evidence — 30 decisions against 150 — and in the
database they are still called `folder` and `photos`.

## Dashboard

### Why doesn't the timeframe filter change the taste-model card?

**Short answer:** Because there is no 30-day model. The chips window
*decisions*; the model card reports a single pickle that was fit on the whole
corpus, so it reports how many decisions landed inside the window instead of
pretending its own numbers moved.

`training_size`, `trained_at` and the accuracy-vs-baseline pair all describe
one artifact on disk (`data/models/personal_model.pkl`). Nothing about them is
time-sliceable: narrowing to 30 days doesn't produce a model trained on 30
days, it just changes which rows a *query* sees.

The old dashboard passed `since` to five endpoints and silently dropped it on
the sixth. The chips sat above all four cards, so selecting "30d" changed three
and left one alone with no explanation — "Trained on 2 971 decisions" read as a
30-day figure.

[dashboard.py](backend/routers/dashboard.py) now returns two extra fields from
`/dashboard/model-card`:

- `window_sample_count` — decisions in this corpus inside the window
- `window_since` — the window's start, echoed so the frontend can label the
  card without recomputing the date

and [ModelCard.jsx](frontend/src/views/dashboard/ModelCard.jsx) renders the
footnote *"Model facts are all-time — a 30d window can't retrain the model. 823
decisions landed inside it."*

If you actually want a model over a narrower slice, that's a retrain, not a
filter — Settings → Model → Retrain now, after the corpus itself has changed.

### Why does the dashboard have a Folder / Photos switch?

**Short answer:** Since 2026-09-19 each corpus trains its own model on its own
slice of `training_samples`, and pooling their statistics produces medians that
describe neither.

The split exists because iPhone HEIC and camera RAW have incompatible
distributions — the Photos corpus has a median `overall_score` of 93.1 with
half the rows inside a six-point band, where the folder corpus spreads across
the full range. A GBR fit across both learns the difference between *cameras*,
not between your keeps and rejects. Same reasoning as the RAW+JPEG companion's
`_train=False` (see CLAUDE.md).

Three endpoints take `corpus`:

- `/dashboard/model-card` — which pickle, and which floor (folder 30,
  photos 150, from `min_decisions_for()`)
- `/dashboard/decisions/timeline` — K/M/R per week
- `/dashboard/decisions/feature-deltas` — kept vs rejected medians

An unrecognised corpus is a **400**, not a fallback to folder
([dashboard.py](backend/routers/dashboard.py) `_parse_corpus`): a dashboard
that answers the wrong corpus with a straight face is worse than one that
errors.

**Shooting behavior is not filtered by it.** `shooting_log` has no
`source_type` column, and the question it answers ("what do I shoot with?")
doesn't split along the model's boundary — a phone frame is still a frame you
shot. The card header says "all sources" so the switch never appears to filter
something it doesn't.

### What does the ✗ in "What you keep vs reject" mean?

**Short answer:** It means your behaviour disagrees with what the feature
itself considers better — and that's the interesting row, not an error.

Each feature carries a `good` direction in
[KeepVsReject.jsx](frontend/src/views/dashboard/KeepVsReject.jsx) —
`'higher'` (sharpness), `'lower'` (ISO, clipping), or `'context'` (focal
length: a 35mm frame isn't objectively better than a 50mm one). The ✓/✗
compares the sign of `kept_median − rejected_median` against that direction.

A ✗ on `sharpness_score` says you are keeping the *softer* photos. That isn't
a bug in the scorer; it's the part of your taste the technical score can't
express, and it's exactly what the GBR delta model exists to learn. `context`
features get no glyph, because there's nothing to agree or disagree with.

The medians are computed over `features_json` blobs frozen at decision time,
so they reflect what the photo scored *when you judged it*, not what a rescore
would say today.
