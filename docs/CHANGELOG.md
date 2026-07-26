# KaMeRa — Changelog

Historical sprint log moved out of CLAUDE.md on 2026-05-12 to keep the project's auto-loaded context lean. Git log is the canonical source — this file is a curated narrative for skim-reading.

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
