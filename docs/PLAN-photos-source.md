# Apple Photos as a source type

> Open follow-ups live in [PLAN-photos-next.md](PLAN-photos-next.md) — start there.

How KaMeRa culls an Apple Photos album without exporting anything, why it is
built the way it is, and how to test it safely.

---

## The one rule

**Never move, rename or trash a file inside a `.photoslibrary` package.** It is
a database, not a folder — Photos tracks every asset by path in its own sqlite
catalogue, and relocating a file corrupts that catalogue irrecoverably.

This is enforced structurally, not by convention. `backend/file_mover.py` is
the only module in the repo that calls `shutil.move` or `send2trash` on a
photo, and `_assert_outside_photos_library()` guards `move_photo`,
`trash_photo` and `resolve_dest_folder`. Any caller, present or future, is
refused. `tests/test_photos_library_guard.py` holds it in place, including the
symlink-laundering route.

---

## How it works

| | Folder tab | Photos tab |
|---|---|---|
| Discovery | directory walk | album membership via `osxphotos` |
| `images.file_path` | the real path | the real path inside `originals/` |
| `images.source_folder` | the directory | `photos://<lib-hash>/<album-uuid>` |
| `images.source_type` | `folder` | `photos` |
| Keep / Maybe / Reject | move into `_Keeps` / `_Maybes` / `_Rejects` | `KaMeRa:Keep` / `:Maybe` / `:Reject` keyword |
| Undo | move back | clear the keyword |
| Delete | `send2trash` | **impossible** — mark only |

Assets are analysed **in place**. Nothing is copied or exported: `rawpy` and
`pillow-heif` decode straight out of `originals/`.

### Why keywords, not albums

An album per decision is the obvious design and is measurably wrong.
AppleScript cannot remove a photo from an album at all, so `photoscript`
emulates removal by deleting the album and rebuilding it. Measured on a
6-photo album: add 6 → 0.23 s, remove **one** → 1.27 s, i.e. ~0.21 s per photo
already in the album. One undo on a 500-photo Keeps album would take ~1.5
minutes.

Keywords are O(1) both ways (set 0.03 s, clear 0.19 s) and Photos builds Smart
Albums from them — so the album view still exists, maintained by Photos.

### Why not PhotoKit

`PHPhotoLibrary.requestAuthorizationForAccessLevel(readWrite)` returns
`denied` **instantly, with no prompt**, because a bare Python binary carries no
`NSPhotoLibraryUsageDescription`. That needs a signed `.app` bundle. Do not
re-attempt `PHAssetChangeRequest` without one.

### The wrong-library guard

Asset UUIDs are per-library, and AppleScript writes always land in whatever
library Photos.app currently has open. Three checkpoints:

1. `/analyze-photos-album` 409s if the requested library is not the open one.
2. `PhotosSink.preflight()` compares `photos_albums.library_path` to
   `osxphotos.utils.get_last_library_path()` — advisory, names both libraries.
3. **The binding one:** looking the asset up by UUID in the wrong library
   returns nothing, so a mis-targeted write cannot happen. The keyword is then
   read back and verified before the `decisions` row is written.

`photoscript.PhotosLibrary().name` returns `"Photos"` (the app), not the
library — `get_last_library_path()` is the only handle on library identity.

---

## Not available on a Photos tab

Auto-cull · trash-rejects · mark reviewed / `_Keeps_reviewed` · RAW+JPEG
companion mirroring · the file watcher · per-tab destination folders ·
`sync-folder`'s existence reaper · the Resume banner.

Each is declared by a `supports_*` flag on the sink and honoured by the
routers, so a hand-rolled request cannot bypass them either. Reveal in Finder
is deliberately kept — `open -R` is read-only and useful.

`sync-folder` deserves a note: `Path(file_path).exists()` is wrong in **both**
directions here. Removing an asset from the album leaves the original on disk
(stale row survives), and an iCloud offload deletes the file for a photo that
is perfectly fine (row and decision destroyed). Album membership is the right
predicate; that endpoint is deferred.

---

## "Optimize Mac Storage" is the prerequisite

With it on, originals are not on disk and cannot be scored. The evicted assets
keep only ~0.09 MB thumbnails — far too small to score, and deliberately *not*
substituted: a sharpness number computed on a 768×1024 thumbnail would enter
`training_samples` as if it were real.

Measured on the author's library: 1341 of 5477 missing, **age-ranked** —
2019–21 100 %, 2024 48 %, 2025 8 %, 2026 27 %. So it barely touches recent
imports and makes archive culling impossible.

**Fix:** Photos → Settings → iCloud → *Download Originals to this Mac*
(~7.4 GB on that library). Missing assets are skipped and counted in the
analyse response (`missing_originals`), never analysed.

---

## TCC: which library, and why you pick it yourself

Two different permissions are involved, and only one of them is grantable.

**Full Disk Access** covers the `.photoslibrary` package — reading originals,
the catalogue, everything KaMeRa actually needs.

**Files & Folders → \<process\> → Photos** covers Photos' *own container*, which
holds `com.apple.Photos.plist` — the preference naming the last-opened library.
This is a different thing, and on macOS 26+ the toggle for a bare binary
**cannot reliably be switched on**. Observed 2026-09-18: the `python3.14` entry
appears, and the Photos toggle refuses to enable.

That matters because the backend is its own TCC identity. The launcher detaches
uvicorn via `perl POSIX::setsid`, so its process chain is
`uvicorn → Python → /bin/bash → launchd` — no app ancestor, so Full Disk Access
granted to KaMeRa.app does not reach it, and neither does anything inherited
from a terminal.

### The consequence: auto-detection is a hint, not a dependency

`osxphotos.utils.get_last_library_path()` reads that protected preference, and
under a denial the file reads as simply *absent* — indistinguishable from
"Photos has never been opened". Building on it would make the feature depend on
a permission the user may be unable to give.

So the library is **chosen explicitly and remembered** (`settings` key
`photos_library_path`):

- `GET /photos-libraries` enumerates libraries by **scanning** `~`,
  `~/Pictures`, `~/Downloads`, `~/Documents`, `~/Desktop`, `~/Movies` and
  `/Volumes/*` one level deep. Internal ones (`~/Library/Photos/Libraries/…`
  for Generative Playground / Visual Intelligence / Syndication), container
  temp paths and scratch dirs are filtered out.
- **Spotlight (`mdfind`) is a bonus, not the basis.** Its results are
  TCC-filtered per *calling* process: the detached backend gets an empty list
  back even for libraries it can open and read perfectly. That is what made the
  picker report "No Photos library found on this Mac" while
  `GET /photos-albums?library_path=…` returned the album fine.
- The picker also takes a **typed path** (with `~` expanded). Reading a library
  package needs only the folder permission the backend demonstrably has, so
  this route works even when every form of discovery is blocked.
- `POST /photos-library` stores the choice. `current_library_path()` returns it
  first and only falls back to auto-detection.
- The picker shows a library step whenever the answer is not obvious, and marks
  the auto-detected one "open" when that information is available.

`get_system_library_path()` reads the same protected container and is likewise
blocked. **KaMeRa never calls it.**

### What this costs

`PhotosSink.preflight()`'s "is the right library open?" comparison degrades to
best-effort when detection is blocked. That is acceptable because it was never
the real guarantee: asset UUIDs are per-library, so looking one up in the wrong
library returns nothing and the write becomes a clean 409. The keyword is read
back and verified before any `decisions` row is written.

## Setting up a test library

Do not develop against the real library. The live tests refuse to run unless
the open library is named `KaMeRa-Test`.

1. Quit Photos. Hold **Option** and launch it → *Create New…* →
   `~/Pictures/KaMeRa-Test.photoslibrary`.
2. Import ~50 assets covering every branch: a `.RAF`, a `.NEF`, a `.HEIC`, a
   `.JPEG`, a RAW+JPEG pair sharing a stem, a `.MOV`, and one whose original
   you then remove.
3. Create an album `KaMeRa Test` with ~20 of them. Create a second album
   sharing 3 assets with the first — that exercises the same asset appearing
   in two tabs (see Known limitations).
4. Quit Photos and relaunch it **on the test library**, so it becomes the
   "last opened" one. Verify:
   ```
   venv/bin/python -c "import osxphotos; print(osxphotos.utils.get_last_library_path())"
   ```
5. Run the gated tests: `venv/bin/pytest -m photos_live`

### Manual acceptance, in order

Analyse the album → grid shows `IMG_1234.HEIC`-style names, not UUIDs →
previews render for RAF **and** HEIC → press `K` → in Photos, confirm
`KaMeRa:Keep` appeared **and no existing keyword was lost** → `U` → keyword
gone, the user's own keywords intact → `K` then `R` → exactly one marker →
`stat` the `originals/` path before and after: **unchanged** → switch Photos to
another library → `K` → 409 naming the library → switch back → `K` works →
close the tab → rows gone, keywords still in Photos.

---

## Known limitations (v1)

- **The same asset in two open album tabs** is two `images` rows sharing one
  `photos_uuid`. Both write the same keyword (idempotent, fine), but an undo on
  one clears the keyword the other still claims. A refcount table is not worth
  it yet.
- **No auto-cull.** It runs on the personal model, which trains on the folder
  corpus. Photos decisions are collected under `training_samples.corpus =
  'photos'` and are not trained on until that corpus is worth a model of its
  own (`MIN_DECISIONS = 30`). `personal_score` is `null` on Photos tabs rather
  than a confident number from a model that has never seen iPhone photos.
- **Reject only marks.** Empty the rejects yourself via a Smart Album on
  `KaMeRa:Reject`.
- **Previews decode the original**, not the Photos derivative — those are
  768×1024 against a 1600² preview target, so they would be a visible
  downgrade for a 0.2 s saving that is cached away after the first view.
- **macOS only.** `osxphotos` / `photoscript` are the one documented exception
  to compatibility rule 4; every entry point is gated on
  `photos_library.is_available()` and 501s elsewhere.
