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

## 2. Auto-cull and a Photos model (Phase B)

**Where it stands.** The corpus split shipped; the second *model* did not.
`training_samples.corpus` separates `folder` (2971) from `photos` (7).
`auto_trainer` filters to `folder`, Photos rows get `personal_score = null`, and
auto-cull is off on Photos tabs. Nothing about that is a stub — it is the honest
state of a model that has never seen an iPhone photo.

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

**If it is worth it**, Phase B is designed as a strict superset — nothing shipped
gets undone:

- `personal_model.py::_MODEL_PATH` becomes a constructor arg: `PersonalModel(corpus="folder")` keeps today's path exactly, `corpus="photos"` gets `data/models/personal_model_photos.pkl`.
- `backend/state.py` gains `_personal_models: dict[str, PersonalModel]`, keeping `_personal_model` as an alias so all ~15 call sites still compile.
- `auto_trainer` functions take `corpus` (default `"folder"`).
- `GET /images` groups rows by `source_type` and calls the matching model per group (it currently short-circuits Photos rows to `None`).
- `/model-info?corpus=photos`; banner and Settings gain a corpus toggle.
- Auto-cull's `supports_auto_cull` flag flips, and the `AND i.source_type = 'folder'` filters in `run_auto_cull` / `auto_cull_preview` become corpus-aware.

**Feature-vector caveat.** 31 features assume a camera: `focal_length_mm`,
`aperture`, `iso`, `shutter_speed`. An iPhone reports a handful of discrete focal
lengths and a fixed aperture, so those columns are near-constant and the GBR will
ignore them. Not a bug, but worth measuring before concluding the model "does not
work" — check feature importances on the Photos corpus first.

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

**Resume banner** is off for Photos tabs (`/folders/unfinished` 400s on `photos://`).
Re-running the analysis on the same tab already skips finished photos, so this is
cosmetic. Relevant because a 1248-photo album will not be culled in one sitting.

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
