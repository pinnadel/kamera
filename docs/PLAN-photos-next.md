# Photos source — what's left

Starting point for the next session. Written to be picked up cold: every item
says what it is, why it matters, what evidence exists, and what it would cost.

Shipped so far: commits `4e6162a` (Photos source type) and `787d13f` (LaunchAgent
backend). Design and test-library setup: [PLAN-photos-source.md](PLAN-photos-source.md).

**Verified live, don't re-check:** analysis of a real album, K/M/R written and read
back as exactly one keyword each, undo clearing it without touching the user's own
keywords, every original unmoved in `originals/`, no decision folders created inside
the package, and `training_samples` staying split (`folder` 2971 / `photos` 7).

---

## 0. Verify TCC under the LaunchAgent — ✅ done 2026-09-19

`787d13f` moved the backend out of an applet-spawned process and into a user
LaunchAgent (`gui/<uid>`). **Every TCC finding in this feature was measured against
the old process**, whose chain was `uvicorn → python → bash → launchd`. A launchd
job is a different responsible process, so Photos access may behave differently —
better or worse.

Cheap to check, and everything else depends on it:

```
launcher/install_agent.sh          # install the agent
# open KaMeRa normally, then:
curl -s localhost:8000/photos-libraries | head -c 300
```

- Libraries listed → fine, nothing to do.
- `403` with the Full Disk Access message → the agent needs its own grant.
- Empty list → the directory scan is blocked; the typed-path route still works.

**Answer: libraries listed, nothing to do.** `/photos-libraries` returned both
libraries with `readable: true`, `/photos-albums` opened the selected one and listed
the 1248-photo album. `detected` is still `null` (`get_last_library_path()` stays
blocked), which the explicit-choice design already handles. Recorded in
[PLAN-photos-source.md](PLAN-photos-source.md)'s TCC section, which now describes the
LaunchAgent process.

---

## 1. Reconcile KaMeRa ↔ Photos — ✅ shipped 2026-09-19

**The gap.** Decisions flow one way. Philipp deleted three `KaMeRa:*` keywords by
hand in Photos on 2026-09-18 and KaMeRa never noticed — two photos kept a `keep`
decision with no keyword, which is also what sent an earlier debugging session
chasing a bug that did not exist.

Drift is normal, not exotic: keywords edited in Photos, an asset removed from the
album, a photo deleted, a write lost.

**Shape.** A `GET /photos-album/diff?source_folder=…` that walks the tab's rows and
reports per photo: decision, actual keyword, and the mismatch class —

| Class | Meaning | Sensible default |
|---|---|---|
| `keyword_missing` | decided in KaMeRa, no marker in Photos | re-apply |
| `keyword_foreign` | marker disagrees with the decision | ask — either side may be right |
| `decision_missing` | marker in Photos, nothing in KaMeRa | import as a decision |
| `asset_gone` | UUID no longer resolves | drop the row |
| `not_in_album` | asset left the album | drop the row |

Then a `POST /photos-album/reconcile` applying a chosen direction per class.

**Decided as recommended:** neither side is authoritative. `GET /photos-album/diff`
shows the drift, the user picks a direction per class, and only `keyword_missing`
defaults to acting — there the two sides do not actually contradict each other, one
simply never arrived. `POST /photos-album/reconcile` applies it.

Shipped shape:

| Class | Actions (first is the default) |
|---|---|
| `keyword_missing` | **apply** · clear (drop our decision) · skip |
| `keyword_foreign` | **skip** · kamera (overwrite keyword) · photos (adopt keyword) |
| `decision_missing` | **skip** · import · clear (strip the marker) |
| `not_in_album` | **skip** · drop the row |
| `asset_gone` | **skip** · drop the row |

Classification is a pure function in `backend/photos_reconcile.py` so the dangerous
half — which rows get deleted — is testable without a Photos library
(`tests/test_photos_reconcile.py`, `tests/test_photos_reconcile_apply.py`). Every
write reuses an existing path: the sink for keywords, `_apply_decision` for
decisions, `_delete_image_rows` for removals. Reconcile re-reads the diff rather
than trusting the client, so an edit made in Photos while the modal sat open is not
overwritten by the picture the user was shown.

UI: **Compare with Photos** in a Photos tab's settings popover
(`frontend/src/modals/PhotosReconcileModal.jsx`).

Two refusals worth keeping: an asset carrying **two** KaMeRa keywords is reported,
never resolved by a coin flip, and a photo that merely left the album is never
dropped by the `asset_gone` action.

**Not yet observed live:** no photo in the available library currently carries a
`KaMeRa:*` keyword, so a non-empty keyword read has not been seen end to end. Add
one by hand in Photos and run Compare to close that gap.

**Note:** this also subsumes the deferred album-membership reaper. `sync-folder`'s
`Path.exists()` test is disabled for Photos tabs (`supports_path_reaper = False`)
because it is wrong in both directions there; `not_in_album` is the right predicate.

---

## 2. Auto-cull and a Photos model (Phase B) — SHIPPED 2026-09-19

**Where it stands.** Built as the strict superset described below, with two
changes the measurements forced (see *What measuring changed* at the end of this
section). The corpus still has to fill up — 7 samples against a floor of 150 —
so the model is wired but silent. That is the intended state, not a stub.

**What shipped:**

- `PersonalModel(corpus)` with per-corpus pickle, training floor and scoring mode.
- `state._personal_models` + `model_for(source_type)`; `_personal_model` kept as
  the folder alias so no existing call site changed meaning.
- `auto_trainer` is corpus-aware throughout, including per-corpus in-flight flags
  (a shared `_running` bool would have let a folder train swallow a photos trigger)
  and a corpus filter on the pairwise synthetics.
- `GET /images` groups rows by `source_type` and scores each group with its own
  model instead of short-circuiting Photos to `None`.
- `/model-info?corpus=`, `/train-model?corpus=`, `/reset-personal-model?corpus=`.
- Corpus-scoped decision thresholds via `<corpus>:<key>` with fallback.
- Settings → Model shows the Photos model's progress toward its floor.
- `tests/test_photos_model_corpus.py` — 19 tests over the seam.

**What measuring changed.** Two of the assumptions in the original sketch did not
survive contact with the data:

1. *The feature-vector caveat was right but incomplete.* `focal_length_mm` holds
   5% of the camera corpus's spread (an iPhone has ~4 lens positions) and
   `aperture` 15% — and the two are perfectly collinear, since each iPhone lens
   has a fixed aperture. `scene_is_night` has zero variance (Night Mode). But the
   bigger problem was not the features, it was the *base score*.
2. *`overall_score` cannot rank iPhone photos at all.* Median 93.1, p75 98.6,
   half the corpus inside a 6-point band. So Photos scores in "absolute" mode and
   ignores the base entirely, rather than offsetting it. Chasing this turned up a
   finding about the shipped camera model too — see the *Personal score
   construction* row in CLAUDE.md.

**The folder model was flipped too**, same session, once the sweep showed it
measured better (0.741 vs 0.713). That required `folder:keep_threshold` /
`folder:maybe_threshold` to be seeded from the fit before the next restart —
absolute scores against the old 70/45 cutoffs would have been badly wrong. Live
values: 39.65 / 35.56, a narrow band because the corpus is 72% reject and only
2.9% maybe. Chasing the flip surfaced a fourth copy of the scoring formula
hardcoded inside `_bootstrap_validate`, which stands behind the readiness gate —
see the *Validator must use the live formula* row in CLAUDE.md.

**Still open:**

- Re-derive the Photos cutoffs from real decisions once the corpus fills, rather
  than from whatever the first qualifying fit produces.
- `uncertainty_threshold` is effectively inert: at 8.0 it fires on 0.1% of the
  folder corpus (ensemble std is median 2.44, p90 4.55). It was inert before the
  flip too — absolute mode doubled the std scale, so this is not a regression —
  but the "Uncertain → Maybe" routing has never really run. Retuning it would
  start producing Maybes, which may not be wanted at all.
- The validator's baseline/model cutoff asymmetry (see CLAUDE.md).

**Why the thresholds mislead.** `MIN_DECISIONS = 30` is the floor for *training at
all*, and the readiness gate is 50. Neither is the point where a model becomes
*useful*. The camera model rests on 2971 samples. Treat a few hundred Photos
decisions as the real starting line and resist training at 30 just because it
becomes possible.

**Ask before building:** does auto-cull on iPhone photos actually add anything?
The measured scorers (sharpness, IQA, aesthetic, face, scene) all work there today.
An iPhone's computational pipeline compresses exactly the variance the camera model
learned from — near-everything is sharp and well exposed — so the useful signal may
be composition and expression, which sort filters already surface. **Cull a few real
sessions and see whether you miss it** before committing to Phase B.

**The original design sketch** (kept for the record — this is what was built):

- `personal_model.py::_MODEL_PATH` becomes a constructor arg: `PersonalModel(corpus="folder")` keeps today's path exactly, `corpus="photos"` gets `data/models/personal_model_photos.pkl`.
- `backend/state.py` gains `_personal_models: dict[str, PersonalModel]`, keeping `_personal_model` as an alias so all ~15 call sites still compile.
- `auto_trainer` functions take `corpus` (default `"folder"`).
- `GET /images` groups rows by `source_type` and calls the matching model per group (it currently short-circuits Photos rows to `None`).
- `/model-info?corpus=photos`; banner and Settings gain a corpus toggle.
- Auto-cull's `supports_auto_cull` flag flips, and the `AND i.source_type = 'folder'` filters in `run_auto_cull` / `auto_cull_preview` become corpus-aware.

**Feature-vector caveat** (measured 2026-09-19 — Photos rows vs. the 2971 frozen
folder vectors, standard deviation as % of the camera corpus's):

| feature | photos sd vs camera | note |
|---|---|---|
| `focal_length_mm` | 5% | ~4 discrete lens positions (2.7 / 6.8 / 9.0 / 16.9mm) |
| `aperture` | 15% | fixed per lens → perfectly collinear with focal |
| `face_sharpness_score` | 26% | compressed |
| `scene_is_night` | 0% | Night Mode erases the category |
| `smile_score` | **127%** | *more* spread than the camera corpus |
| SigLIP content axes | ~100% | transfer cleanly |

The Photos corpus is a **people** corpus: `face_present` 0.54 → 0.68,
`scene_is_portrait` 0.35 → 0.65. Expect importance mass to move off camera
physics and onto expression and content. Unrelated but found on the way:
`shake_detected` is constant 0 across all 507 images *and* all 2971 training
samples — a dead feature everywhere, not just here.

---

## 3. Mobile bundle does not know Photos tabs

`frontend/src/mobile/` has its own folder list and pick-folder flow. It will treat a
`photos://…` source as a path. Untested — expect breakage rather than graceful
absence.

Minimum: hide Photos sources on mobile. Better: read-only browsing of a Photos tab,
since decisions from mobile would need the same AppleScript path that only exists on
the Mac. See [MOBILE_STATUS.md](MOBILE_STATUS.md) and the `sync-mobile` skill.

---

## 4. Smaller items from this work

**~~`trash-rejects` is not tab-scoped.~~ Fixed 2026-09-19.** `source_folder` is now a
required body field — omitting it is a 422, not a wider sweep — and the frontend posts
the active tab's folder. The confirm line names the tab. Worth recording *why* it was
worth doing: the bar the button sits in was already labelled with the active tab's
reject count, so the number shown never described what the server did. Guarded by
`tests/test_trash_rejects_scope.py`.

**Found while fixing the above: `reject_to_system_trash` never worked.** Written as
`"1"` by `POST /settings`, read with a literal `== "true"` test in `sources/folder.py`,
so it always evaluated False. Fixed via `database.get_bool_setting()`, which the three
other (already-correct) boolean readers now share. Relevant here because it is the
setting that decides whether a folder-tab reject is reversible at all.

**The undo stack does not survive a reload.** `useUndoStack` is in-memory. After a
reload, `U` falls through to per-photo undo, which acts on the *selected* photo — and
after K/M/R the cursor has already advanced to an undecided one. That now says so
instead of doing nothing silently, but persisting the stack (sessionStorage, capped)
would remove the dead end entirely. Only worth it if it actually bites in practice.

**Same asset in two open album tabs** is two `images` rows sharing one `photos_uuid`.
Both write the same keyword (idempotent), but an undo on one clears the marker the
other still claims. A refcount table is the fix; rare enough to leave documented.

**Preview source.** Photos derivatives are 768×1024 against a 1600² preview target,
so the original is decoded instead — correct, and worth re-checking only if HEIC
decode ever shows up in a profile.

**Resume banner** — shipped 2026-09-19, and it was not cosmetic after all. The
note below assumed "re-running the analysis on the same tab already skips
finished photos", which was true of the *endpoint* and false of the batch:
`_run_batch` built its `queued` set as `folder / name` over `skipped`, and
`folder` is None for an album. A fresh run has an empty `skipped`, and an empty
comprehension never evaluates its body — so the TypeError only appeared on a
re-run, i.e. only ever on a resume. It raised before the try block, so the
`finally` never cleared `_progress`, and the UI sat on a batch reporting 0 of N
with nothing to click. Three parts to the fix:

- the guard on `queued` (`backend/routers/analysis.py`);
- `/folders/unfinished` answers for albums, using the `asset_count` (schema v53)
  that the last analyze run recorded — an album has no directory to walk, and
  re-enumerating per tab focus would mean an osxphotos read, or AppleScript
  against a running Photos.app for a Smart Album;
- `POST /analyze-photos-album` accepts `source_folder` alone, because
  `album_uuid` was never stored on a tab and is therefore absent from every tab
  restored out of localStorage — which is the only kind of tab a resume
  happens on.

Covered by `tests/test_photos_resume.py`.

---

## 5. Further out: a real Photos integration

Established early and still true: macOS has no "browse and triage" extension point.
A **Photo Editing Extension** is single-asset and must return an edited image; a
**Project Extension** is for print products and cannot write decisions back. The only
fitting type is a **Share Extension** ("select photos → Share → KaMeRa"), and any
extension must live in a signed `.app` built in Xcode — which the AppleScript applet
is not. It would also be a thin client: the sandbox will not host the pyiqa /
SigLIP / FaceNet pipeline, so the work stays in the FastAPI process either way.

The cheap step in between is **Shortcuts**: a Quick Action that hands the selection
to `localhost:8000`. No Xcode, no signing, and close to the same feel.

A signed bundle would also unlock `NSPhotoLibraryUsageDescription`, i.e. PhotoKit —
which would make real album removal and asset deletion possible, both of which are
impossible today.

---

## Suggested order

1. ~~**§0** — verify TCC under the LaunchAgent.~~ Done 2026-09-19: no new grant needed.
2. **Cull for a few real sessions.** Several items above ask a question that only
   use answers: is auto-cull missed, does drift bite, is the undo dead end real.
3. ~~**§1 reconcile**~~ — shipped 2026-09-19.
4. **§3 mobile** — small, and currently an outright breakage.
5. **§2 Phase B** — only once the Photos corpus is in the hundreds *and* the
   session in step 2 says auto-cull is missed.
