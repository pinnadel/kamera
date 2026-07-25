# Plan: `_Keeps_reviewed` — a review gate before Provenance archives

> **Status:** Planning only — no code written yet. Written 2026-07-24,
> paired with a Provenance-side change already implemented (see
> `provenance/docs/DECISIONS.md`, entry dated 2026-07-24, and
> `provenance/CLAUDE.md`'s "Key Active Decisions" table).
>
> **Origin:** Provenance's `_Keeps` watcher used to archive a keeper into the
> Luminar library the instant it landed in `_Keeps` — often within seconds,
> since "Analyze & auto-cull" overlaps KaMeRa's live `/watch` with the import
> copy. That left no window to eyeball the auto-cull result in KaMeRa's grid
> before the file was renamed and moved out from under it. Investigated
> whether the Luminar catalog / ledger could substitute for that review —
> confirmed it can't: KaMeRa keys every query off the DB row's `file_path`,
> never updates it for a move it didn't perform, so the next preview fetch
> 404s and `sync-folder` eventually deletes the row outright. Once archived,
> a photo is gone from KaMeRa, full stop. Review has to happen before the
> move, not after.

---

## TL;DR

Provenance now watches `_Keeps/_Keeps_reviewed` instead of `_Keeps` — that
part is **already done** on the Provenance side. This doc covers the
matching **KaMeRa-side** work: give the user a way to move a photo from
`_Keeps` into `_Keeps/_Keeps_reviewed`, and make manual K decisions skip the
review step entirely (a manual keep decision already *is* the review).

| Piece | What | Effort |
|---|---|---|
| 1 | Manual K decision → straight to `_Keeps_reviewed` (skip `_Keeps`) | Small — one branch in `_apply_decision` |
| 2 | Single-photo "Mark reviewed" action (moves `_Keeps` → `_Keeps_reviewed`) | Small–Medium — new endpoint + one UI affordance |
| 3 | Bulk "Mark all reviewed" for a `_Keeps` view/session | Medium — reuses piece 2's endpoint per-id, plus a view to review from |

Do 1 and 2 together first — 2 is not useful without 1 clarifying which photos
even need it. 3 is a fast-follow once 1+2 are validated in daily use.

---

## Why the folder is nested (`_Keeps/_Keeps_reviewed`, not a sibling)

Confirmed safe by code read (2026-07-24): KaMeRa treats `_Keeps` as an
**opaque leaf** everywhere a folder gets scanned, watched, or written to.
Nesting a nother folder inside it is invisible to KaMeRa's own bookkeeping:

- `file_watcher.py`'s `Observer.schedule(..., recursive=False)` — the watcher
  never descends into `_Keeps` at all.
- `_PhotoEventHandler._handle()` skips any path where
  `any(part in DECISION_SUBFOLDERS for part in path.parts)` — matches
  `_Keeps` anywhere in the path, so a file under `_Keeps/_Keeps_reviewed/`
  is *also* skipped by the same check, at any depth.
- `analyze_folder` / `folder_unfinished`, even with `include_subfolders=True`
  using `rglob("*")`, filter out any path where a component is in
  `skip_dirs = DECISION_SUBFOLDERS` — the entire subtree under `_Keeps` is
  pruned regardless of nesting depth.
- `move_photo()` writes are always flat: `resolve_dest_folder()` computes
  `Path(source_folder) / _DECISION_FOLDERS[decision]` where `source_folder`
  is the durable `images.source_folder` column (the original ingest parent,
  captured once at analysis time) — **never** `Path(current_path).parent`.
  So KaMeRa's own re-decision moves (K→M, etc.) can never land inside a
  nested `_Keeps_reviewed` by accident; they always resolve relative to the
  original source folder, not wherever the file currently sits.

**Practical consequence:** you can create, move into, and move out of
`_Keeps/_Keeps_reviewed` freely — KaMeRa will never scan it, show its
contents in the grid, or write into it via any existing decision/move path.
It is 100% inert from KaMeRa's perspective unless the new code below
explicitly targets it.

---

## Current code map (read 2026-07-24 — verify line numbers before editing, this file drifts)

**`backend/constants.py:105`**
```python
DECISION_SUBFOLDERS: frozenset[str] = frozenset({"_Keeps", "_Maybes", "_Trash"})
```
This is the *set* used for scan/watch pruning. It is **not** the
decision→foldername mapping (see next).

**`backend/file_mover.py`**
```python
_DECISION_FOLDERS: dict[str, str] = {          # line ~20-24
    "keep": "_Keeps", "maybe": "_Maybes", "reject": "_Trash",
}
_DECISION_SETTING_KEY = { ... }                # line ~26-30, per-decision folder override key

def resolve_dest_folder(                       # line ~33-48
    decision: str, source_folder: str | Path, overrides: dict[str, str] | None = None,
) -> Path:
    """overrides[key] if a folder override is configured, else
    Path(source_folder) / _DECISION_FOLDERS[decision]."""

def move_photo(current_path: str | Path, dest_folder: str | Path) -> str:  # line ~54-104
    """shutil.move (+ .hif sidecar), refuses symlinks, no-ops if already at
    dest, collision-safe via _claim_path (line ~151-183)."""
```

**`backend/routers/decisions.py`**
```python
def _apply_decision(                            # line ~252-422
    image_id: int, decision: str,
    is_auto: bool = False, _mirror: bool = True, _train: bool = True,
) -> dict:
    ...
    photo_parent = source_folder or str(Path(current_path).parent)  # line ~323
    dest_folder = resolve_dest_folder(decision, photo_parent, overrides)  # line ~325
    new_path = move_photo(current_path, dest_folder)                # line ~328
    ...
    # updates images.file_path only (line ~335-338);
    # decision recorded via INSERT OR REPLACE INTO decisions

@router.post("/decision")                       # line ~430-440
def save_decision(request: DecisionRequest): ...  # body {image_id, decision}
    # manual single-photo path — calls _apply_decision(..., is_auto=False)

@router.post("/bulk-decision")                   # line ~583-599
def bulk_decision(...): ...
    # loops _apply_decision per id, still is_auto=False
```
`is_auto` is **already threaded through** `_apply_decision` as the exact
signal that distinguishes a manual K from an auto-cull keep — it's currently
unused for path selection (both flows call the identical
`resolve_dest_folder`/`move_photo` pair). This is the hook piece 1 uses.

**`backend/routers/analysis.py`**
```python
@router.post("/auto-cull")                       # line ~875-999
def run_auto_cull(source_folder: str | None = None): ...
    # Phase 3 (line ~981-987):
    _apply_decision(row["id"], decision, is_auto=True, _mirror=False)
```

**`backend/database.py`** — `images` table (line ~729-758): `id` (PK),
`file_path TEXT NOT NULL UNIQUE`, `filename`, `uuid TEXT UNIQUE`, `camera`,
`format`, `shot_at`, score columns, `analysis_status`, `imported_at`.
`source_folder TEXT` added via migration v13 (line ~300-301), backfilled by
`_step_v17_backfill_source_folder` (line ~100-106). Separate `decisions`
table (line ~795-800): `image_id` (FK, unique), `decision` (CHECK IN
keep/reject/maybe), `decided_at`. **No column currently records "reviewed"
state** — see Piece 2 below for how to represent it.

**Frontend** — manual decision trigger: `App.jsx:1188` `sendDecision(decision)`
POSTs directly to `` `${API}/decision` `` (`API` = `http://localhost:8000`,
`frontend/src/api.js:1`); passed as `onDecide` prop into `DetailView.jsx`
(bound at `App.jsx:2343`, invoked at `DetailView.jsx:269`). A second manual
path for grouped photos: `useGroups.js` `sendGroupDecision(imageId, decision)`
(line ~171-187), used by GroupLoupe, also POSTs to `/decision`. Bulk UI
paths: `bulkDecide` (`App.jsx:1326`) and `bulkSendDecision` (`App.jsx:1538`),
both hitting `/decision` per-id. `ImageCard.jsx` is display-only (decision
styling), no trigger logic — a "Mark reviewed" affordance most naturally
joins `DetailView.jsx` (single-photo) and wherever the `_Keeps` grid view is
rendered (for bulk).

---

## Piece 1 — Manual K goes straight to `_Keeps_reviewed`

**Why:** a human pressing K while looking at the photo already performed the
review Provenance's gate exists to guarantee. Routing it through `_Keeps`
first and requiring a second "mark reviewed" click would be pure friction
with no safety benefit — the auto-cull gate exists specifically because
*nobody looked at the photo yet*.

**Scope:** `is_auto=False` "keep" decisions resolve to
`_Keeps/_Keeps_reviewed` instead of `_Keeps`. `is_auto=True` (auto-cull)
"keep" decisions keep resolving to plain `_Keeps`, unchanged.

**Implementation sketch** (`file_mover.py` + `decisions.py`):

```python
# file_mover.py — new constant, and resolve_dest_folder grows a param
_KEEPS_REVIEWED_SUBFOLDER = "_Keeps_reviewed"

def resolve_dest_folder(
    decision: str, source_folder: str | Path,
    overrides: dict[str, str] | None = None,
    *, reviewed: bool = False,
) -> Path:
    if decision == "keep" and reviewed:
        base = Path(source_folder) / _DECISION_FOLDERS["keep"]
        return base / _KEEPS_REVIEWED_SUBFOLDER
    ...  # existing logic unchanged otherwise
```

```python
# decisions.py, inside _apply_decision, where dest_folder is computed:
dest_folder = resolve_dest_folder(
    decision, photo_parent, overrides,
    reviewed=(decision == "keep" and not is_auto),
)
```

**Things to check while implementing:**
- Folder overrides (`_DECISION_SETTING_KEY` / per-decision folder settings)
  — if a user has customized where "keep" lands, does `_Keeps_reviewed`
  still nest correctly under the *overridden* keep folder, or does it need
  its own override key? Decide before shipping; simplest correct behavior is
  probably "always nest under whatever the keep folder currently resolves
  to," so `_Keeps_reviewed` moves with the override.
- `resolve_dest_folder` currently creates the dest via `move_photo`'s own
  `mkdir` (verify — check `_claim_path`/`move_photo` for
  `mkdir(parents=True, exist_ok=True)` or equivalent); confirm the nested
  folder is created on first use, not assumed to pre-exist.
- Add `_Keeps_reviewed` to `DECISION_SUBFOLDERS`-style pruning **only if**
  future scan code might otherwise treat it as a live source folder — per
  the "opaque leaf" analysis above, everything that matters already prunes
  on `_Keeps` alone, so this is likely a no-op, but re-verify against
  current code before assuming.

**Tests to add** (mirroring existing decision tests):
- Manual keep (`is_auto=False`) on a fresh photo lands in
  `<source_folder>/_Keeps/_Keeps_reviewed/<file>`.
- Auto-cull keep (`is_auto=True`) still lands in
  `<source_folder>/_Keeps/<file>` (regression guard — this must NOT change).
- A manual keep with a folder override configured still nests correctly.

---

## Piece 2 — "Mark reviewed" action (single photo)

**Why:** the human counterpart to Piece 1 — for photos that arrived in
`_Keeps` via auto-cull, this is the action that says "I looked, this is
correct" and releases the photo to Provenance.

**New backend endpoint**, same shape as `/decision`:
```python
@router.post("/mark-reviewed")
def mark_reviewed(request: MarkReviewedRequest) -> dict:
    # body: {image_id: int}
    # 1. look up images row by id — confirm current file_path is under a
    #    `_Keeps` folder (reject/400 otherwise — this action only makes
    #    sense for already-kept photos)
    # 2. dest = Path(current _Keeps folder) / "_Keeps_reviewed"
    # 3. new_path = move_photo(current_path, dest)   # reuses existing mover
    # 4. UPDATE images SET file_path = ? WHERE id = ?
    # 5. return {new_path: ...}
```
This can very plausibly just be a thin wrapper that calls the *same*
`resolve_dest_folder(..., reviewed=True)` / `move_photo` pair Piece 1 adds,
parameterized off the photo's *current* location rather than a fresh
decision — check whether it's cleaner to extract a shared
`_move_to_reviewed(image_id)` helper used by both the auto-path-selection in
`_apply_decision` and this new endpoint, vs. duplicating the two-line move.
Prefer the shared helper — avoids the two call sites drifting.

**Data model note:** no new column is strictly required — "reviewed" is
fully derivable from `file_path` containing `_Keeps_reviewed` as a path
component, the same way KaMeRa already derives "kept" from the `decisions`
table row. Don't add a `reviewed BOOLEAN` column unless a query pattern
actually needs to filter/sort by it independent of path (if so, revisit).

**Frontend:** a "Mark reviewed" button/icon in `DetailView.jsx`, shown only
when the photo currently being viewed is decided `keep` and its `file_path`
is under `_Keeps` but not yet under `_Keeps_reviewed`. Wire it the same way
`onDecide` is wired today (`App.jsx:2343` → `DetailView.jsx:269` pattern) —
a new `onMarkReviewed` prop calling a new `markReviewed(imageId)` function
alongside `sendDecision` in `App.jsx`. After success, the photo should
either disappear from whatever `_Keeps`-scoped view it was in (if the grid
filters by current path) or show a "reviewed" badge — decide based on
whether you want `_Keeps` review to feel like "clear the pile" (disappear)
or "tag and keep browsing" (badge). Given the goal (fast visual QA pass over
an auto-cull batch), disappearing-on-mark to shrink the pile is probably the
better feel — validate with Philipp before building.

---

## Piece 3 — Bulk "mark all reviewed" (fast-follow)

**Why:** after Piece 1+2 are in daily use, the common case for an auto-cull
batch that all looks correct will be "yep, all good" rather than
photo-by-photo confirmation. A bulk action avoids N clicks for N correct
auto-cull decisions.

**Scope (defer detail design until 1+2 are validated):**
- A `_Keeps`-scoped grid/review view (may already effectively exist as
  "the grid filtered to decision === 'keep'" — check whether such a view is
  reachable today, similar to how the Maybe view works per
  `PLAN-next-sprints.md`'s Sprint A).
  a`
- `POST /bulk-mark-reviewed` taking a list of `image_id`s, looping the same
  shared helper from Piece 2 (mirrors `bulk_decision`'s existing shape at
  `decisions.py:583-599`).
- UI affordance: likely a toolbar button in the `_Keeps` view — "Mark all
  reviewed" (all currently-filtered photos) plus per-card mark-reviewed for
  picking out exceptions before bulk-confirming the rest.

Not spec'd further here — build after Piece 1+2 prove out the interaction,
so bulk design is informed by actually living with the single-photo flow
first rather than guessing.

---

## Out of scope / explicitly not doing

- **No grace-period delay, no polling, no "undo archive"** — the review gate
  IS the mechanism; nothing time-based is needed once the review step is
  synchronous and pre-move.
- **No changes to `_Maybes`/`_Trash` handling** — this only touches the
  "keep" decision path.
- **Provenance-side code is already done** — do not modify anything in
  `/Users/philippklein/dev/provenance`; it already watches
  `_Keeps/_Keeps_reviewed` and ignores plain `_Keeps` (see
  `provenance/src/provenance/config.py`'s `keeps_reviewed_dir`,
  `provenance/src/provenance/watcher.py`, `provenance/src/provenance/archive.py`).
  Confirm against that repo's current state if anything here seems to
  assume a Provenance behavior — Provenance is the consumer, KaMeRa is the
  producer; the contract is the filesystem path
  `<temp_import>/_Keeps/_Keeps_reviewed/`.

---

## Verification checklist before calling this done

> **Status update (2026-07-25):** Pieces 1–3 implemented. See
> [docs/CHANGELOG.md](CHANGELOG.md)'s "`_Keeps_reviewed` review gate" entry
> for the full account, including a real bug found and fixed mid-implementation
> (undo's home-reconstruction didn't account for the extra `_Keeps_reviewed`
> nesting level for the RAW/JPEG companion mirror path).

- [x] Manual K in a live cull → file appears in `_Keeps/_Keeps_reviewed/`
      immediately, NOT in `_Keeps/` even transiently. Verified via
      `tests/test_keeps_reviewed.py` and live curl against the running
      launcher instance (synthetic photo, not real data).
- [x] Auto-cull batch → keepers land in `_Keeps/` only; none jump straight
      to `_Keeps_reviewed`. Regression-guarded by
      `test_auto_cull_keep_lands_in_plain_keeps`.
- [x] Mark-reviewed on an auto-cull keeper moves it into
      `_Keeps/_Keeps_reviewed/`. Verified live via `POST /mark-reviewed`.
      Provenance pickup itself not verified this session (Provenance wasn't
      running) — the filesystem contract (destination path) is confirmed;
      Provenance-side debounce behavior is unchanged and out of scope here.
- [ ] A photo sitting in plain `_Keeps` is confirmed to NOT be touched by
      Provenance even after several minutes — not verified this session
      (would need a live Provenance instance watching a real folder).
- [x] Existing `_Keeps`-referencing tests still pass, updated where Piece 1
      intentionally changes manual-keep routing: `test_raw_jpeg_companion.py`
      and `test_analysis_mode_jpeg_priority.py` both had one assertion each
      updated from plain `_Keeps` to `_Keeps/_Keeps_reviewed` (manual-keep
      companion mirror tests). 345/345 passing.
- [x] Folder-override settings still produce a sane, tested `_Keeps_reviewed`
      location — nests under the override, not the default subfolder. See
      `test_resolve_dest_folder_reviewed_with_override` and
      `test_manual_keep_with_folder_override_nests_correctly`.
