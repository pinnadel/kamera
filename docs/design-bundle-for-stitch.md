# KaMeRa — Design Reference for Google Stitch

> KaMeRa is a local-only, AI-assisted photo culling tool for a working photographer
> (RAF/NEF/3FR RAW files from Fujifilm, Nikon, and Hasselblad cameras). This document
> describes the current design system so Stitch can generate new screens or explore a
> redesign direction that stays recognizably KaMeRa — same DNA, new execution welcome.
>
> **How to use this doc:** everything below is *reference*, not a spec to replicate
> pixel-for-pixel. Treat §1–2 (concept + palette) as the load-bearing identity of the
> app; treat §3–6 (type, components, layout, elevation) as the current expression of
> that identity, open to reinterpretation.

---

## 1. What this app is and who it's for

KaMeRa is used by a photographer to rapidly triage hundreds to thousands of RAW photos
per session — deciding Keep / Maybe / Reject while an on-device AI model scores
sharpness, exposure, quality, and composition in the background. It is used for hours
at a stretch, on a calibrated monitor, in a workflow closer to a darkroom loupe or a
professional DAM (Lightroom, Photo Mechanic, Capture One's culling view) than to a
consumer app.

**This shapes the design more than any single style choice:**
- **Color-critical neutrality.** The UI chrome must never bias how the photographer
  judges color/exposure in the photo itself. This is the single most important
  constraint — it's why the palette is almost monochrome and why saturated color is
  reserved and rationed.
- **Density and speed over polish-for-its-own-sake.** Photographers are scanning grids
  of 100+ thumbnails and making rapid binary/ternary decisions with keyboard shortcuts
  (K = Keep, M = Maybe, X = Reject). The UI is a tool, not a marketing surface.
  Whitespace is functional, not decorative.
  When a single photo is opened full-screen, though, the surrounding void becomes
  generous — that's the one moment the interface should feel like it's holding its
  breath so the photo can be judged.
- **A calibrated-viewing-booth mood**, not a generic "dark mode app." Think: the inside
  of a darkroom, a light table, a film loupe. macOS-native depth (soft multi-layer
  shadows, inset highlights) rather than flat/Material-style design.

---

## 2. Visual identity & mood

**Reference point:** Raycast's dark theme (near-black blue-tinted canvas, multi-layer
inset shadows, positive letter-spacing, tactile key-cap badges) — but with Raycast's
signature red stripped out entirely, because red/saturated brand color would compete
with photo content.

**Describe it to Stitch as:** a color-accurate, macOS-native dark tool UI — the visual
cousin of Lightroom's culling view crossed with Raycast's command-bar aesthetic. Near-
black, cool-tinted, quietly tactile, almost entirely desaturated except for two
reserved accent colors and three muted semantic states.

**Mood words:** calibrated, tactile, quiet, precise, dense-but-legible, tool-not-theatre.

**Explicitly not:** flat Material Design, bright/white SaaS dashboard, playful/rounded
consumer app, marketing-site energy, anything with a loud brand color wash.

---

## 3. Color

### Canvas & surfaces (near-black, blue-cold, never pure black)
| Role | Value | Notes |
|---|---|---|
| Page canvas | `#07080a` | Deliberately not `#000000` — a cool blue-black that sits below RAW shadow detail |
| Panel / toolbar surface | `#101111` | One step up from canvas |
| Card surface (thumbnails, idle) | `#161718` | Two steps up |
| Inset track / badge fill | `#1b1c1e` | Score-bar tracks, chip backgrounds |
| Full-screen backdrop veil | `rgba(7,8,10,0.92)` | Behind the full-screen photo inspector |

### The two reserved accents
This is the most important rule in the whole system: **only two colors are allowed to
be "loud," and each owns exactly one meaning.**

| Accent | Value | Means, and only means |
|---|---|---|
| **Cool Cyan** | `#5BB8D4` | Selection / focus / "this control is currently active and narrowing what you see" |
| **Warm Amber** | `#E8B84A` | "Best photo in this group" + the Maybe decision state |

Everything else in the interface is monochrome grays or the three desaturated
decision colors below. If a redesign wants to introduce new saturated color, it should
come at the cost of one of these being used more narrowly, not by adding a third loud
hue.

### Decision states (deliberately muted — read as UI chrome, not traffic lights)
| State | Value | Feel |
|---|---|---|
| Keep | `#7DB89A` (sage green) | Confirming, calm |
| Maybe | `#E8B84A` (warm amber) | Same hue as "best" badge — intentional, both mean "worth a second look" |
| Reject | `#C97B7B` (faded coral) | Negative but not alarming — never a hard stop-sign red |

Each has a ~12%-opacity tint version for background fills behind decided thumbnails.
Rejected photos additionally drop to 45% image opacity (a "struck through" feel).

### Text (grayscale ramp, warm-to-cool neutral)
`#f9f9f9` primary → `#cecece` secondary → `#9c9c9d` tertiary/meta → `#6a6b6c`
disabled/placeholder. Borders live in the same family: `rgba(255,255,255,0.06–0.10)`
for hairline containment, `hsl(195,5%,15%)` for standard dividers.

### One quiet third accent: indigo for "the AI's opinion"
The personal ML model's score (as opposed to raw technical scores) gets its own
identity color — indigo `#7B82C9` — used only on that one score bar/fill. It signals
"this number came from learning your taste," distinct from both the neutral technical
scores and the two reserved accents.

### Color-role discipline (important for Stitch to preserve or deliberately break)
There are three non-overlapping color "vocabularies" in the app: **decision state**
(sage/amber/coral), **selection/active** (cyan), and **quality-band tint** (a separate
cool-to-warm slate scale from bright/cool=excellent to dim/warm=poor, used only on
score-quality chips). They never borrow each other's hues — e.g. sage never appears
outside a Keep-related context — because a photographer scanning fast reads color as
meaning, and two different meanings sharing a color is a real usability bug here, not
a nitpick.

---

## 4. Typography

- **UI font:** Inter, with a slightly unusual **positive letter-spacing** (+0.2px body,
  +0.3px buttons) — this is what gives the Raycast-derived look its airy, precise
  quality on a dark background. Weight 500 is the body baseline (not 400).
- **Numeric font:** a monospace (currently GeistMono) for **every number on screen** —
  scores, EXIF values (f-stop, shutter, ISO), file sizes, counts — with tabular
  numerals so columns of numbers align. This is a deliberate "calibration instrument"
  convention, borrowed from camera/measurement UIs, not a stylistic accident.
- **Scale is small and restrained:** largest text in the entire app is 22px (the
  filename in the full-screen inspector). No hero/display type anywhere — this is a
  tool, not a landing page.

---

## 5. Signature components (describe these to Stitch as the app's core "scenes")

### 5.1 Contact-sheet grid (the primary/home view)
A dense CSS-grid of thumbnail cards, 3:2 aspect ratio, adjustable density (Comfortable
240px → Compact 180px → Dense 140px). Each thumbnail is a self-contained "contact
sheet print": subtle card surface, barely-visible hairline border, small soft shadow.
Overlaid on the image itself: a monospace score badge (top-right), a small K/M/X
decision-glyph pill (top-left) once decided, and a filename in a bottom gradient veil.
The currently-focused thumbnail gets an outer cyan ring; decided thumbnails get a
matching desaturated outline + tint wash; rejected ones dim to near-transparency.
Hover states are opacity-only — never a background color swap.

### 5.2 Full-screen inspector / loupe (the "judgment" view)
Opening a photo covers the viewport in the near-black veil. The photo sits centered,
large, with a soft ambient white glow behind it (never a shadow — a shadow under the
photo would visually bias contrast judgment). A fixed right rail (~380px) holds
filename, EXIF data (all monospace, two-column, no row dividers — alignment alone
separates rows), and a stack of score bars (sharpness, exposure, IQA, aesthetic, face,
personal-model). Score bars are neutral white fills on a dark track — flat, calm,
non-alarming — except the personal/ML score, which alone gets the indigo tint. A
floating pill toolbar at the bottom center holds the three decision buttons.

### 5.3 Toolbar & decision buttons + hotkey caps
Rectangular pill buttons (not fully-rounded — these are instrument controls, not
marketing CTAs), each with an embedded physical "key cap" badge showing its hotkey
letter (K/M/X). The key cap is the one place the design gets overtly skeuomorphic: a
tiny gradient, multi-layer shadow, genuinely trying to look like a physical keyboard
key. It's meant to give the decision action tactile, satisfying weight — this is a
tool people press hundreds of times a session.

### 5.4 Similarity-group filmstrip (burst/duplicate triage)
Horizontal scrolling strips of near-duplicate photos (same panel/container language as
the rest of the chrome), with the single algorithmically-best photo in each group
picked out by the amber ring + a soft amber halo + a small "BEST" badge — the one
place in the whole app where amber marks quality rather than a decision.

### 5.5 Other screens worth knowing about (less prescriptive — free to design fresh)
- **Compare view** — side-by-side photo comparison for close calls
- **Group Loupe** — full-screen version of the filmstrip triage, same visual language
  as 5.2
- **Dashboard** — session/folder overview, tab bar across open folders
- **Settings modal** — thresholds, decision-color legend, model controls
- **Toasts** — bottom-right, colored left-edge stripe only (info steel / success sage /
  error coral), otherwise neutral panel chrome

---

## 6. Shape, depth, and spacing language

- **Radius scale is small and utilitarian:** 4px (key caps, small badges) → 6px
  (thumbnails, pills) → 8px (buttons, inputs) → 12px (panels, floating toolbar). Never
  pill-shaped/fully-rounded buttons — this app deliberately avoids the "friendly SaaS"
  rounded look. Nothing above ~12px radius.
- **Depth via multi-layer shadows, not flat Material elevation.** Every interactive
  surface — buttons, key caps, floating panels — combines an outer soft shadow with
  inset top highlight + inset bottom dark, simulating a physically-lit surface from
  above. Single flat drop-shadows read as "wrong" in this system.
- **8px base spacing unit**, restrained scale (2/4/6/8/12/16/20/24/32/40/64). Density
  over air in working views; the one deliberate exception is the full-screen inspector,
  where generous surrounding void (90vh photo framing) is the point.

---

## 7. What makes this recognizably KaMeRa (preserve these even in a fresh direction)

1. **Near-black cool canvas, never pure black.**
2. **Exactly two loud accent colors, each single-purpose** (cyan = selection/active,
   amber = best/maybe) — resist the urge to add more saturated brand color.
3. **Decision states are desaturated, not traffic-light bright** — sage/amber/coral,
   never pure red/green.
4. **Every number is monospace with tabular alignment.**
5. **Hover = opacity change, never a color swap.**
6. **The photograph itself never gets a shadow or color wash** — it must sit
   photometrically neutral in its frame.
7. **Density in list/grid views, generosity only in the single-photo focus view.**

Everything else — exact radii, exact shadow recipes, the specific key-cap
skeuomorphism, Inter/GeistMono as the specific font pair — is fair game to reinterpret.

---

**Source:** distilled from `docs/DESIGN.md` (KaMeRa's internal, code-level design
system doc) for hand-off to Google Stitch. Last generated: 2026-07-27.
