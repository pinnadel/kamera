# Plan: the UI half — one surface from card to archive

> **Status: BUILT 2026-09-21**, same day it was written. See
> `docs/CHANGELOG.md` and `provenance/docs/CHANGELOG.md` for what shipped.
>
> **Deviations, all in the direction of reusing what existed:**
> - No separate cull-offer banner. The undecided bar with its "Auto cull"
>   button already *was* the offer; it only needed to say the import had
>   finished. Building a second one beside it would have been paint, not a rule.
> - No separate placeholder block. The trailing-skeleton block for running
>   batches was extended to cover an import run instead.
> - Provenance's **first** dialog is kept (Analyze / Import / Skip). The plan
>   said collapse it to Import / Skip, but "do not start a heavy ML app just
>   to dump a card" is a legitimate choice, and it is not a question about
>   content. Only the second dialog — the one that was — is gone.
>
> **The backend half shipped the same day** and this plan depends on all of
> it: `backend/provenance_link.py` (read-only seam), `GET /import-status`,
> `images.provenance_key` (migration v57), and Provenance's
> `status.py` → `~/Pictures/.kamera/import_status.json`. See
> `docs/CHANGELOG.md` (2026-09-21) and `provenance/docs/CHANGELOG.md`.
>
> **Origin:** a design critique of the two-surface workflow. Provenance asks
> two modal questions about *content* before a single photo is visible, then
> announces the result through a dialog that holds the screen for up to 30
> minutes — not because anyone wanted it loud, but because there was no place
> where a result could survive. Now there is.

---

## TL;DR

**Provenance is the sensor and the hands. KaMeRa is the only screen.**

| Piece | What | Effort |
|---|---|---|
| 1 | Poll `/import-status`, derive an import run onto its batch tab | Small |
| 2 | Placeholder tiles + live arrival during `copying` / `analyzing` | Medium |
| 3 | Cull offer as an in-tab banner; delete Provenance's second dialog | Medium (spans repos) |
| 4 | `lifecycle` on the photo; stop deleting archived rows | Medium |
| 5 | Release to archive: verb rename + honest consequence (no hotkey — see §6) | Small |
| 6 | Retire `notify.done()` for the normal path | Small (Provenance) |

Pieces 1–2 are self-contained. **3 and 6 change Provenance too.** 4 is the
one with a schema-shaped blast radius. 5 is mostly copy.

---

## The rule this all follows

Two rules, stated so later decisions don't need re-litigating:

1. **Provenance never asks a question and never reports a result.** It
   detects, copies, moves, archives, erases. Everything a human must read or
   answer renders in KaMeRa.
2. **Feedback volume follows reversibility, not importance.** Ambient icon →
   notification → tab chip → banner → modal. **Modal is reserved for the
   irreversible** (the card wipe). That single rule is what deletes
   `notify.done()` from the normal path.

The menu bar does *not* go away. A card is inserted when KaMeRa may not be
running; that is a system event and needs a system sensor.

---

## 1. States

One entity: the **import run** (`run_id`), plus a per-photo lifecycle.

### Run states (from `GET /import-status`)

```
idle ──card──► reading ──consent──► copying ──┬──► analyzing ──► triage
                  │                            │                   │
                  └──nothing new──► idle       └──mode=import──► done
                                                                    │
                     interrupted ◄── settle timeout / error ────────┘
```

`interrupted` is **deliberately distinct from `done`** — it is the only thing
that lets a resume affordance be honest rather than a guess. The backend
already emits it.

### Photo lifecycle (new, keep-only)

```
undecided ──K──► staged ──release──► archived ──tmutil──► backed_up
```

`staged` is the state the old completion dialog lied about: the keeper sits
in `_Keeps` and **nothing has reached the library yet**. Giving it a name in
the UI is most of the fix.

Rejects and maybes have no lifecycle — they never leave for the archive.

---

## 2. Piece 1 — the run reaches the tab

### No new tab kind

`temp_import` is a directory, so a **normal `kind: 'batch'` tab already
models it**. Adding `kind: 'import'` would fork every `isPhotosTab`-shaped
check in `App.jsx` for no gain.

Instead: derive. In the existing poll cycle, when
`run.temp_import === tab.folderPath`, attach the run to that tab:

```js
// derived per poll, never persisted — localStorage must not resurrect a
// finished run as a live one
{ importRun: { phase, photos_done, photos_total, volume, run_id } }
```

**Read tabs via `tabsRef.current`, not `setTabs(prev => …)`.** The
`/analyze-progress` poll learned this the hard way — the updater pattern
silently saw a stale empty array under StrictMode plus the initial-load race.

### If the tab doesn't exist yet

`ensureTabForFolder(run.temp_import)` (already in `App.jsx:782`). The tab
appears on its own when a run starts. **It does not steal focus** — if you
are culling another tab, a new tab quietly appears with a chip. Provenance's
`open_window()` should stop firing on its own (piece 3).

If KaMeRa was closed for the whole import, the status file is still there on
next launch and the tab materialises then. Nothing is lost.

---

## 3. Piece 2 — the grid fills

### Placeholder tiles

The single highest-value change, and the cheapest.

From `copying` onward the denominator is known (`photos_total`). Render
`photos_total - images.length` placeholder tiles at the end of the grid:
`#161718` surface (the existing pre-load tile colour, DESIGN.md §4.2), 3:2,
6px radius, the **existing** `shimmer` keyframe (`index.css:147`).

> The difference between "it's running" and "it'll take this long" is the
> denominator. Everything else in the loading treatment is decoration.

### Arrival

A real tile replaces a placeholder: `opacity 0→1` over **120ms** (the
system's existing opacity duration). No scale, no slide — at 800 tiles that
reads as confetti. No layout shift, because the placeholder already held the
slot.

### The chip

Tab chip while `phase ∈ {reading, copying, analyzing}`:
`↓ 23/47` during copying, `analysiert 23/47` during analyzing. Same
`liveAnalyzing` chip slot that exists today.

**Counts are per photo, not per file.** Provenance already reports that way
(a RAF+JPG+HIF sharing a stem is one photo); the denominator must not jump
when a RAW+JPEG pair lands.

---

## 4. Piece 3 — the cull question moves into the app

### Delete Provenance's second dialog

`autopilot._handle_card` currently asks *"Analyze & auto-cull / Just analyze
/ Cancel"* before a single photo is visible. Remove it. Dialog 1 collapses to
**Import / Skip** and always sets `/watch` (analysis during copy is free and
harmless).

### Ask in the tab instead

When `phase === 'analyzing'` ends and the folder has undecided analysed
photos, show a banner in the tab:

```
47 Fotos analysiert.        [Auto-cull vorschlagen]  [Selbst cullen]
```

You can see thumbnails while answering. That is the entire point.

Auto-cull arming already exists (`frontend/src/autoCullArming.js`,
`tab.autoCullAfterAnalysis`) — this reuses it rather than inventing a path.

### Consequence

Provenance's `mode` shrinks to `None | "analyze"`. `"cull"` disappears from
the autopilot, and with it `wait_for_settle` → `auto_cull` → result-modal.
KaMeRa fires its own auto-cull, as it already can, and shows the result
where the photos are.

---

## 5. Piece 4 — an archived photo stops vanishing

This is the piece with real blast radius. It has three parts.

### 4a. Backend: `lifecycle` on `GET /images`

Derived, not stored — the ledger is the source of truth and a stored copy
would drift:

| Value | Condition |
|---|---|
| `null` | no `provenance_key`, or not a keep |
| `staged` | key present, ledger `status != 'archived'` |
| `archived` | ledger `status == 'archived'`, `backed_up == 0` |
| `backed_up` | ledger `status == 'archived'`, `backed_up == 1` |

**Needs `provenance_link.archive_info_many(keys)`** — a batched read. The
per-key `archive_info()` that exists today opens a connection per call; N
photos would mean N connections. The `_staged_keys()` cache pattern
(invalidated on the ledger's mtime+size) is the model to copy.

### 4b. Backend: stop reaping archived rows

`sync_folder` ([images.py:417](../backend/routers/images.py#L417)) drops
every row whose `file_path` is missing. That is exactly what happens when
Provenance archives a keeper — **this line is why a photo disappears.**

```python
missing_ids = [r["id"] for r in rows if not Path(r["file_path"]).exists()]
```

becomes: missing **and not archived**. An archived photo's file is gone from
the working folder but it is not gone.

> Deliberate consequence: `data/previews/{id}.jpg` must survive too, or the
> tile has nothing to show. `_delete_image_rows()` currently removes it.
> The preview is the only thing that can render an archived photo — the
> original is in the Luminar library under a different path.

### 4c. Frontend: how an archived photo looks

**No new chip.** DESIGN.md §4.2 is explicit that ImageCard carries no chips
and the vocabulary is deliberately tiny. Lifecycle is not a decision, so it
must not borrow decision chrome.

| State | Treatment |
|---|---|
| `staged` | normal keep tile (sage ring, keep tint) — it is still working material |
| `archived` | `filter: saturate(0.35)`, footer strip shows the year folder (`2026 Bilder`) instead of the filename |
| `backed_up` | same, plus a filled dot before the folder name |

Desaturation is safe next to the reject treatment (`opacity 0.45`) because
**archived is keep-only** — an archived tile always carries the sage ring, a
rejected one always carries coral.

**Text, never colour alone.** The system already established this rule for
the decision glyph ("keep-green and reject-coral are a red/green confusable
pair"). The year folder in the footer *is* the accessible label; the
desaturation is the redundant, glanceable channel.

### 4d. The filter

No new IA. An archived photo keeps `decision = 'keep'` and appears in the
Keep view, which is precisely "what I kept". One opt-in `FilterPill` —
**"Archivierte ausblenden"** — for when the Keep view is a working surface
rather than a record.

**Audit every optimistic `setImages` patch** when adding the field — there
are 11 call sites in `App.jsx` today. A correct backend and green tests still
ship a visibly broken filter if one of them forgets to mirror the new field;
this has happened before with `reason` and the "only unconfirmed" pill.

---

## 6. Piece 5 — releasing to the archive

### Rename the verb

"Mark all reviewed" describes the bookkeeping, not the consequence. The
photo **leaves KaMeRa's working set and enters the library**, and that is
what the button should say.

| | Now | Proposed |
|---|---|---|
| Banner | `12 auto-culled photos awaiting review` | `12 Keeper bereit fürs Archiv` |
| Button | `Mark all reviewed` | `Ins Archiv freigeben` |
| Sub | — | `Sie verlassen KaMeRas Arbeitsansicht` |

The endpoints (`/mark-reviewed`, `/bulk-mark-reviewed`) keep their names —
this is a copy change, not an API change.

> **Coupling:** Provenance's completion modal currently instructs the user to
> click **"Mark all reviewed"** by name. Renaming the button without updating
> that string re-creates the exact defect fixed on 2026-09-21 — a message
> pointing at a control that no longer exists. Both change in one commit, or
> piece 6 lands first and deletes the string entirely.

### No hotkey — deliberately

The first draft of this plan gave release the `A` key. Checking the bindings
killed it twice over, and the second reason is the real one.

**`a` is already taken:** `GroupLoupe.jsx:694` binds it to
`runBatch('reject')` — reject every non-hero in the group. A key that means
"reject all" in one view and "release to the archive, irreversibly" in
another is a hazard, not a shortcut, and the two views are one keystroke
apart. (The surviving keys are narrow anyway: `k m r u c o s b z f - = space
enter escape arrows meta+z` are bound.)

**And a hotkey contradicts the ladder.** This document's own rule is that
volume follows reversibility. A one-keystroke, no-confirmation, bulk,
irreversible action is the loudest possible affordance for the most dangerous
operation in the app. The banner button is the right and sufficient
affordance; it is pressed once per import, not once per photo.

If a per-photo release is ever wanted, it belongs in DetailView as an
explicit control with the destination named — not on the grid's keyboard
path next to K/M/R.

**No drag affordance either.** Drag is the most reversible gesture in the
system, and pairing it with an irreversible move would misstate the stakes.

---

## 7. Feedback, per the ladder

| Event | Channel | Why |
|---|---|---|
| Card detected | Notification | KaMeRa may be closed |
| Copy / analysis progress | Tab chip | peripheral by design |
| Cull offer | Banner | needs an answer, has context |
| Auto-cull result | Banner | survives; nothing to dismiss |
| Release confirmation | Toast | done and reversible-ish (undo moves it back) |
| Settle timeout | Banner + resume button | actionable in place |
| **Card wipe** | **Modal + typed confirm** | **irreversible — the only one left** |

### The one exception worth keeping

If auto-cull finishes while KaMeRa is **closed**, the result has no screen.
Then a *notification* (not a modal), and the tab holds the banner until
opened. `notify.done()` survives only for that branch.

---

## 8. Error paths

| Failure | Today | Plan |
|---|---|---|
| Settle timeout | Modal naming a foreign button whose state it can't know | Banner in the tab: `8 Fotos hängen` + **Weiter analysieren** |
| KaMeRa down during import | `"Could not start KaMeRa…"` | Copy proceeds — it never needed KaMeRa. Status file waits; the tab appears on next launch |
| Card pulled mid-copy | untested | `phase: interrupted`; re-insert offers `Lauf fortsetzen — 23 von 47`. Idempotency already supports this; only the telling is missing |
| Ledger unreadable | — | `available:false` → no lifecycle, no import tab, nothing else degrades |
| Ledger and KaMeRa disagree | invisible | Classify and show; **neither side authoritative** — the `photos_reconcile.py` pattern |

---

## 9. Motion

Existing durations (`index.css:303`): `box-shadow 180ms`, `opacity 120ms`,
`transform 80ms`, all `ease`.

| Element | Motion | Duration |
|---|---|---|
| Tile arrival | `opacity 0→1` | 120ms |
| Placeholder | existing `shimmer` | 1.8s loop |
| Banner in/out | `opacity` + `translateY(-4px)` | 180ms |
| Archive desaturation | `filter: saturate(1→0.35)` | **400ms** |
| Counters | none — numbers jump | — |

The desaturation is the one deliberate slowness: a photo leaving should be
visible. A tweened counter would imply smooth progress the batch doesn't
have.

`@media (prefers-reduced-motion: reduce)` (`index.css:280`) already covers
shimmer and `ai-border`. **Add:** desaturation becomes instant, tile arrival
0ms.

---

## 10. Sequencing

```
1 ──► 2 ──► 3          (each usable on its own)
      └──►  4 ──► 5 ──► 6
```

- **1 + 2 first.** Self-contained, no Provenance change, immediately useful:
  the import becomes visible.
- **3** needs a Provenance commit; it deletes a dialog.
- **4** is the schema-shaped one — do it alone, with the preview-retention
  consequence handled in the same change.
- **5** is copy plus a hotkey, but is **coupled to 6** through the string in
  Provenance's modal.
- **6** last: by then every result has somewhere to live, so the modal has
  nothing left to say.

---

## What this plan deliberately excludes

- **The `archive` source type.** `provenance_key` makes browsing the Luminar
  library as a read-only source possible. It is a separate decision, not a
  consequence of this one.
- **An event bus.** The backlog's own test — "only worth it if the
  silent-skip ever proves insufficient" — has not been met.
- **Backfilling the 1870 existing rows.** Decided 2026-09-21: the cut runs
  from the next import on.
