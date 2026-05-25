# KaMeRa — Design System

> Adapted from the Raycast DESIGN.md (VoltAgent / getdesign.md) for a photographer-first,
> color-accurate dark UI built with React, Tailwind CSS v4, and Vite.
>
> Source reference cached at: `.claude/design/raycast-DESIGN.md`
>
> **Translation philosophy:** Keep Raycast's macOS-native chrome — near-black blue-tinted
> canvas, multi-layer inset shadows, positive letter-spacing, pill CTAs — but **strip the
> brand red**. Photographers need their displays to be neutral so that color decisions on
> RAW files are not biased by UI tint. The single brand accent becomes a **warm amber /
> off-white** used sparingly for "active" / "selected" / "best-in-group" states. All
> decision states (Keep / Maybe / Reject) use desaturated semantic hues that read as UI
> chrome rather than image content.

---

## 1. Visual Theme & Atmosphere

The app should feel like the inside of a calibrated viewing booth — a darkroom-grade,
color-managed surface where the photograph is the only thing that radiates color. The
background is the same near-black, slightly blue-cold tone Raycast uses (`#07080a`)
because it sits well below typical RAW shadow values and avoids competing with image
midtones. Every chrome surface, border, and shadow is deliberately monochrome and
ultra-low-saturation so that on-screen photos are judged on their own merit.

The signature move is borrowed directly from Raycast: **multi-layer box-shadows with
inset highlights** that simulate physical depth. Thumbnails feel like contact-sheet
prints sitting on a black light table; the inspector feels like a floating loupe
panel; hotkey badges (K / X / M) feel like physical key caps. Type uses **Inter**
with positive letter-spacing (+0.2px) for an airy, readable dark-mode voice and
**GeistMono** for EXIF metadata and numeric scores.

The one departure from Raycast: **no red brand stripe**. The hero accent is **Warm
Amber** (`#E8B84A`), used only for the "best photo" ring in similarity groups, the
selected thumbnail in the contact sheet, and active-state outlines on focused
controls. Decision states use muted semantic colors (sage for Keep, dim amber for
Maybe, faded coral for Reject) that read as UI chrome, never compete with the image.

**Key Characteristics:**
- Near-black blue-tinted canvas (`#07080a`) — neutral enough for color-critical viewing
- macOS-native multi-layer inset shadows on every interactive surface
- Cool Cyan (`#5BB8D4`) for selection / focus rings; Warm Amber (`#E8B84A`) for "best" badge in groups and Maybe state
- Inter with positive letter-spacing (+0.2px), weight 500 baseline
- GeistMono for EXIF, scores, file paths, and any numeric / technical content
- Subtle rgba white borders (0.06–0.10 opacity) for chrome containment
- Hotkey badges styled as physical key caps with gradient and 5-layer shadow
- Decision states use desaturated semantic hues — never primary brand color

---

## 2. Color Palette & Roles

### Primary
| Name | Hex / HSL | Role |
|------|-----------|------|
| Near-Black Blue | `#07080a` | Primary canvas — full-screen inspector, page background |
| Pure White | `#ffffff` | Highest-emphasis text, score values in inspector |
| Warm Amber | `#E8B84A` / `hsl(43, 78%, 60%)` | Maybe state + "best" badge in similarity groups |
| Cool Cyan | `#5BB8D4` / `hsl(197, 60%, 58%)` | Selection ring (focused thumbnail), focus rings, active tab underline |

### Surface & Background
| Name | Hex | Role |
|------|-----|------|
| Deep Background | `#07080a` | Page canvas — darkest surface |
| Surface 100 | `#101111` | Toolbar, side panels, EXIF panel background |
| Surface 200 | `#161718` | Thumbnail card surface (idle) |
| Card Surface | `#1b1c1e` | Score-bar tracks, badge backgrounds, tag fills |
| Key Start | `#121212` | Hotkey cap gradient start |
| Key End | `#0d0d0d` | Hotkey cap gradient end |
| Inspector Veil | `rgba(7, 8, 10, 0.92)` | Full-screen inspector backdrop over grid |

### Neutrals & Text
| Name | Hex / HSL | Role |
|------|-----------|------|
| Near White | `#f9f9f9` / `hsl(240, 11%, 96%)` | Primary body text, score numerals |
| Light Gray | `#cecece` | Secondary body text, EXIF values |
| Silver | `#c0c0c0` | Tertiary text, panel labels |
| Medium Gray | `#9c9c9d` | Default link / nav, undecided meta text |
| Dim Gray | `#6a6b6c` | Disabled text, low-emphasis labels, info-hint microcopy paired with ⓘ icon (footnotes below action surfaces — e.g. the "Decisions immediately move…" line under the K/M/R hotkey card on the empty state). Embedded literals inside hint text step up to Medium Gray `#9c9c9d` for readability. |
| Dark Gray | `#434345` | Inactive borders, score-bar fill (low score) |
| Border | `hsl(195, 5%, 15%)` / `~#252829` | Card and divider border |
| Dark Border | `#2f3031` | Toolbar separators, table rows |

### Decision State Colors (desaturated — read as chrome)
| Name | Hex / HSL | Role |
|------|-----------|------|
| Keep Sage | `#7DB89A` / `hsl(150, 30%, 60%)` | Keep state — thumbnail outline, K badge fill, decision dot |
| Maybe Amber | `#E8B84A` / `hsl(43, 78%, 60%)` | Maybe state — outline, M badge fill (Warm Amber, same as "best" badge) |
| Reject Coral | `#C97B7B` / `hsl(0, 40%, 64%)` | Reject state — outline, X badge fill |
| Keep Tint | `rgba(125, 184, 154, 0.12)` | Subtle fill behind kept thumbnails |
| Maybe Tint | `rgba(200, 168, 100, 0.12)` | Subtle fill behind maybe thumbnails |
| Reject Tint | `rgba(201, 123, 123, 0.12)` | Subtle fill behind rejected thumbnails (also dims thumbnail to 0.45 opacity) |

### Score-Bar Color Encoding
Score bars (sharpness, exposure, IQA, aesthetic, personal, face) use a **single neutral
fill** of `#a8a8a8` (Mid Gray) over a `#1b1c1e` track. The bar **never** uses
red/green/yellow encoding so that the chrome stays color-neutral. The fill is intentionally
softer than body text (`#f9f9f9`) so the bar reads as supporting chrome, not as the loudest
element on the row. Personal score is the only exception — it gets a subtle **indigo tint**
(`#7B82C9`, `hsl(234, 38%, 63%)`) at its filled portion to mark it as ML-derived.

| Bar | Fill Color (100%) | Notes |
|-----|-------------------|-------|
| Sharpness | `#a8a8a8` | Neutral mid gray |
| Exposure | `#a8a8a8` | Neutral mid gray |
| IQA (TOPIQ) | `#a8a8a8` | Neutral mid gray |
| Aesthetic (LAION) | `#a8a8a8` | Neutral mid gray |
| Face (composite) | `#a8a8a8` | Neutral mid gray. Frontend-only 0–100 derivation in `frontend/src/ui/format.js::faceQualityScore()` (face sharpness − closed-eye penalty + framing bonus); no backend column. Display-only, never feeds the personal model. |
| Personal (ML) | `#7B82C9` (indigo) | Sole exception — ML-derived score gets a tint |

### Semantic & Feedback (toasts, alerts only — never on photo chrome)
| Name | HSL / Hex | Role |
|------|-----------|------|
| Error Coral | `hsl(0, 40%, 64%)` / `#C97B7B` | Move-failure toast, Reject confirmation |
| Success Sage | `hsl(150, 30%, 60%)` / `#7DB89A` | "Decision saved", model trained |
| Warning Amber | `hsl(43, 78%, 60%)` / `#E8B84A` | Stop-analysis prompt, Maybe state |
| Info Steel | `hsl(202, 25%, 65%)` / `#94B0C2` | Model download toast, neutral notifications |

### Score-Chip Band Tints (DetailView right rail)
Score chips in the inspector right rail (Technical Overall, AI sub-chips, Content
sub-chips, Personal Your-model) communicate **quality bands** — Excellent / Good /
Fair / Poor. They use a stepped cool-slate luminance scale that is **distinct from
the decision palette** so a Maybe-decided photo with a Fair Aesthetic doesn't
display amber from two unrelated reasons on the same screen.

| Tier | Hex | Role |
|------|-----|------|
| Tier 4 — Pearl | `#C8D8E4` | Excellent band — bright cool slate |
| Tier 3 — Steel | `#9CADBB` | Good band — mid cool slate |
| Tier 2 — Stone | `#A09480` | Fair band — mid warm slate |
| Tier 1 — Iron  | `#8A7878` | Poor band — dim warm slate |

Quality reads as **luminance + warmth**: high band = bright/cool, low band =
dim/warm. No saturated hues, so the chips never compete with the photo content
above them. Cutoffs are metric-specific (mirroring `phase2_quality/iqa_scorer.py`
75/55/35 vs `aesthetic_scorer.py` 70/50/30) but the visual ladder reads as one
family across every section.

**Section-summary chips** (AI Quality, Content Signals): when a section has
multiple independent metrics that don't share a defined composite, the section
chip displays only the **band word** of the section's worst-available signal —
not a fake aggregate number. This keeps chip semantics honest: Technical
Overall and Personal Your-model display a number because their composites are
real (defined math / learned model); AI Quality and Content Signals display
only a band word because they aren't. The chip still answers the scannable
question "is this layer flagged?" without inventing a misleading aggregate;
expand the section to see which specific signal carries the band.

### Color-role boundaries (the rule the chip palette resolves)
The app uses three orthogonal color systems. Each role owns its hues; never
borrow across roles or the user gets two reasons for the same color on screen
at once.

| Role | Owns | Used for |
|------|------|----------|
| **Decision** | Sage `#7DB89A` · Amber `#E8B84A` · Coral `#C97B7B` | K/M/X buttons, ImageCard rings, decision badges, decision-pill backgrounds |
| **Band tint** | Pearl · Steel · Stone · Iron (above) | Score-chip backgrounds in the inspector — quality band of a metric |
| **Identity** | Indigo `#7B82C9` (Personal score), Bronze `#8C7A5E` ("other N" pooled), Cool Cyan `#5BB8D4` (selection / focus state) | Section/object identity, never quality |

Within a role, hue is meaningful. Across roles, hue is reserved — Sage in a
chip background would read as "this is a Keep candidate" because the decision
ring on the same screen also paints sage. The band tint scale exists precisely
to keep band readings independent of decision state.

### Aggregate / "Other" Tones
Used when a segmented chart needs a category to represent **pooled remainder** —
not empty space, but a distinct unranked group. Must read as warm fill (so it
clearly isn't an empty track) yet sit visually subordinate to the named/primary
segments around it. Never used for state, decisions, or actionable elements.

| Name | Hex / HSL | Role |
|------|-----------|------|
| Muted Bronze | `#8C7A5E` / `hsl(36, 21%, 46%)` | Pooled "other N" segment in stacked bars (e.g. `LearnedSignals` in DetailView). Warm-cool contrast against indigo/cyan/sage named segments; lower saturation keeps it subordinate; distinct from Warm Amber so it can't be misread as Maybe/best. |

### Decorative Tints
- **Selection Glow (Cool Cyan)**: `rgba(91, 184, 212, 0.18)` — focus ring on selected thumbnail / focused controls
- **Best-Photo Glow**: `rgba(232, 184, 74, 0.10) 0px 0px 24px 4px` — soft halo behind the best photo in a similarity group
- **Inspector Loupe Glow**: `rgba(255, 255, 255, 0.04) 0px 0px 80px 20px` — barely-there ambient halo behind the photo in full-screen inspector

---

## 3. Typography Rules

### Font Family
- **Primary**: `Inter` — humanist sans-serif, used across UI chrome. Fallbacks: `Inter Fallback`, `system-ui`, `-apple-system`, `Segoe UI`, sans-serif
- **Monospace**: `GeistMono` — used for **all numeric content**: EXIF values, score numerals, file paths, focal length, ISO, aperture, shutter, file sizes. Fallbacks: `ui-monospace`, `SFMono-Regular`, `Menlo`, `Monaco`, monospace
- **OpenType features (Inter)**: `calt`, `kern`, `liga`, `ss03` enabled globally — gives Inter a slightly more geometric, tool-like quality consistent with Raycast
- **OpenType features (GeistMono)**: `tnum` (tabular numerals) enabled — score columns and EXIF rows must align vertically

### Hierarchy

| Role | Size | Weight | Line Height | Letter Spacing | Notes |
|------|------|--------|-------------|----------------|-------|
| Inspector Filename | 22px | 500 | 1.15 | 0px | Filename in inspector header (Inter) |
| Section Heading | 20px | 500 | 1.40 | 0.2px | "Similarity Groups", "EXIF", "Personal Model" panels |
| Card Heading | 16px | 600 | 1.20 | 0.2px | Score-bar group titles |
| Body | 14px | 500 | 1.50 | 0.2px | Default UI text — labels, panel rows |
| Body Tight | 14px | 400 | 1.20 | 0.1px | Compact rows (EXIF table left column) |
| Button | 14px | 600 | 1.15 | 0.3px | Toolbar action labels |
| Caption | 12px | 500 | 1.33 | 0.2px | Thumbnail meta (filename, decision pill) |
| Caption Bold | 12px | 600 | 1.33 | 0px | Group headers, badge labels |
| Small | 11px | 600 | 1.30 | 0.4px | Hotkey hints next to button labels |
| Score Numeral | 18px | 500 (GeistMono) | 1.0 | 0px | Score values (e.g. "82") in inspector |
| Score Numeral Small | 12px | 500 (GeistMono) | 1.0 | 0px | Score badge on thumbnail overlay |
| EXIF Value | 13px | 400 (GeistMono) | 1.30 | 0px | f/2.8 · 1/250s · ISO 400 · 35mm |
| File Path | 12px | 400 (GeistMono) | 1.40 | 0px | Folder path input, source folder display |

### Principles
- **Positive tracking on dark**: All Inter text uses +0.2px tracking (buttons +0.3px, hotkey hints +0.4px). This is the Raycast hallmark and counter-intuitive for dark UIs — it works.
- **Weight 500 baseline**: Body text never drops below weight 500 in Inter. Weight 400 is reserved for data tables (EXIF) where the monospace already provides visual mass.
- **Numbers are monospace, always**: Any numeric content (scores, EXIF, file sizes, counts) uses GeistMono with `tnum`. This is a calibration-tool convention — numbers must align.
- **No display sizes**: This is a tool, not marketing. The largest text is the inspector filename at 22px. There is no 64px hero anywhere.

---

## 4. Component Stylings

### 4.1 Toolbar (Action Bar — adapted from Raycast Command Bar)

The toolbar is a horizontal strip that sits at the bottom of the inspector and at the
top of the contact-sheet grid. It hosts the three decision actions (Keep / Maybe /
Reject) and contextual buttons (Analyze, Stop, Settings).

- **Surface**: `#101111` background, 1px solid `rgba(255, 255, 255, 0.06)` border
- **Height**: 56px
- **Padding**: 0 16px
- **Radius**: 12px (floating toolbar) or 0 (edge-attached)
- **Shadow**: Level 5 (Floating) — see §6 — when free-floating; Level 2 (Ring) when edge-attached
- **Item gap**: 8px between buttons, 16px between groups

**Decision Buttons (K / X / M):**
- Pill shape, 32px height, 14px horizontal padding
- Border-radius: 8px (rectangular, not full pill — these are tool buttons, not CTAs)
- Idle: transparent background, `1px solid rgba(255, 255, 255, 0.10)` border, white text
- Hover: opacity 0.7 (NEVER background-color change — Raycast signature)
- Active (decision applied to current photo): semi-tinted background using the matching decision tint (Keep Tint / Maybe Tint / Reject Tint), border switches to the decision color at 0.5 alpha
- Embedded **hotkey badge** sits on the right inside the button (see §4.6)

**Stop / Cancel buttons:**
- Same chrome, but text uses Reject Coral (`#C97B7B`) for "Stop Analysis"
- Hover: opacity 0.7

### 4.2 Thumbnail Card (Contact-Sheet Grid item — adapted from Raycast List Item)

Thumbnails are the workhorse of the grid view. Treat them like contact-sheet prints
sitting on a light table.

- **Surface**: `#161718` background until image loads (avoid pure black so the loading
  state is visible)
- **Aspect ratio**: 3:2 fixed (matches RAW aspect; pad with surface color for vertical shots)
- **Radius**: 6px (matches Raycast's "workhorse" radius)
- **Border**: 1px solid `rgba(255, 255, 255, 0.06)` — barely visible, structurally essential
- **Shadow**: Level 1 (Subtle) — `rgba(0, 0, 0, 0.28) 0px 1.189px 2.377px`
- **Idle padding**: 0 (image fills card)
- **Hover**: border opacity → 0.18, no scale or transform
- **Selected (current focus)**: 2px solid Cool Cyan (`#5BB8D4`) ring drawn **outside** the card via box-shadow (`0 0 0 2px #5BB8D4`), and a subtle cyan glow halo (`rgba(91,184,212,0.18)`)
- **Decision states**:
  - Keep: 1.5px solid Keep Sage outline + Keep Tint background
  - Maybe: 1.5px solid Maybe Amber outline + Maybe Tint background
  - Reject: 1.5px solid Reject Coral outline + Reject Tint background + image opacity 0.45 (struck-through visual)

**Thumbnail Overlays (absolutely positioned over the image):**
- **Score badge** (top-right): GeistMono numeral at 12px weight 500, white on `rgba(0, 0, 0, 0.6)` pill, 4px radius, 4px padding. Hidden when score is null.
- **Decision pill** (top-left): K / M / X glyph in Caption Bold (12px/600), 16x16px square pill in matching decision color. Hidden when undecided.
- **Filename** (bottom strip): GeistMono 11px in `#cecece` over a 24px gradient veil from `rgba(0,0,0,0.0)` to `rgba(0,0,0,0.7)`. Truncate with ellipsis.
- **Best-in-group badge** (top-center, Groups view only): "BEST" in 11px weight 600 Inter, Warm Amber on `rgba(0, 0, 0, 0.7)` pill, 4px radius

### 4.3 Inspector Panel (Full-screen Detail View — adapted from Raycast Detail Panel)

The inspector takes over the full viewport when a thumbnail is opened. It is the
photographer's loupe.

- **Backdrop**: `Inspector Veil` `rgba(7, 8, 10, 0.92)` covering the entire viewport
- **Layout**:
  - Center: photo, max 90vh / 90vw, object-fit contain
  - Right rail (380px): score panel + EXIF + face panel
  - Bottom: floating toolbar (decision actions)
- **Photo container**: 12px radius, Inspector Loupe Glow behind it
- **Right rail surface**: `#101111`, 1px solid `rgba(255, 255, 255, 0.06)` border, 12px radius, 24px internal padding
- **Section dividers**: 1px solid `#2f3031`, 16px vertical spacing
- **Header**: Filename (22px Inter weight 500) on top, EXIF row below (GeistMono 13px in Light Gray)
- **Close affordance**: ESC keyboard hint badge at top-right, no visible X button

### 4.4 Score Bar (composite component used in the right rail)

- **Layout**: label (left) — track (center, flex-grow) — value numeral (right)
- **Label**: Body 14px weight 500 in Light Gray (`#cecece`)
- **Track**: 6px tall, full width, 3px radius, background `#1b1c1e`
- **Fill**: 6px tall, 3px radius, color per the Score-Bar Color Encoding table above
- **Value numeral**: GeistMono 14px weight 500 in Near White, right-aligned, fixed 32px width for column alignment
- **Spacing**: 12px vertical between bars
- **Transition**: width transitions over 240ms ease-out when score updates

### 4.5 EXIF Panel (data table)

- **Surface**: inherits right-rail surface
- **Row layout**: Two-column. Left = label in Body Tight (`#9c9c9d`), Right = value in EXIF Value (GeistMono 13px in Near White)
- **Row height**: 24px
- **Row separator**: none (rely on tabular alignment)
- **Standard rows**: Camera, Lens, Focal length, Aperture, Shutter, ISO, EV, Date, File size, Dimensions

### 4.6 Hotkey Badge (Key Cap — adapted from Raycast Keyboard Shortcut)

This is the one component where the Raycast key-cap treatment is preserved verbatim —
it gives the K / X / M decisions tactile authority.

- **Size**: 18px square (inside toolbar buttons), 24px square (standalone hint badges)
- **Surface**: linear gradient `#121212` (top) → `#0d0d0d` (bottom)
- **Border-radius**: 4px
- **Shadow stack** (Level 4 Key — five layers):
  ```css
  box-shadow:
    0 1.5px 0.5px 2.5px rgba(0, 0, 0, 0.4),
    inset 0 1px 0 rgba(255, 255, 255, 0.08),
    inset 0 -1px 0 rgba(0, 0, 0, 0.4),
    0 1px 0 rgba(0, 0, 0, 0.5),
    0 0 0 1px rgba(255, 255, 255, 0.04);
  ```
- **Text**: Inter 11px weight 600, Near White, centered, letter-spacing 0
- **Active-key flash** (keyboard event): backdrop briefly shifts to Warm Amber tint for 120ms

### 4.7 Folder Path Input (adapted from Raycast Search Input)

- **Surface**: `#07080a` background (sits on `#101111` panel, so darker = "input well")
- **Border**: 1px solid `rgba(255, 255, 255, 0.08)`
- **Radius**: 8px
- **Height**: 36px
- **Padding**: 0 12px
- **Text**: GeistMono 13px in Near White (paths are mono)
- **Placeholder**: GeistMono 13px in Dim Gray (`#6a6b6c`)
- **Focus**: border brightens to `rgba(255, 255, 255, 0.18)`, **Warm Amber glow ring** appears: `box-shadow: 0 0 0 3px rgba(232, 184, 74, 0.18)`
- **Reset button** (right side, inline): Ghost button, 11px caption "Reset"

### 4.8 Similarity Groups Filmstrip (adapted from Raycast Extensions list)

The Groups view shows clusters of visually similar photos as horizontal filmstrips.

- **Container**: `#101111` surface, 1px solid `rgba(255, 255, 255, 0.06)`, 12px radius, 16px padding
- **Header**: Section Heading (20px) + group count caption + similarity threshold badge
- **Strip**: horizontal flex, 12px gap, scroll-x with momentum
- **Thumbnail in strip**: same as §4.2 but fixed 160px width
- **Best photo in group**: gets the Warm Amber 2px ring + Best-Photo Glow (see §6)
- **"Reject all but best" action**: Secondary button at top-right of group header

### 4.9 Toast / Notification (Model Download, Move-Failure)

- **Surface**: `#101111`, 1px solid `rgba(255, 255, 255, 0.10)`, 10px radius
- **Width**: 320–400px, position: fixed bottom-right, 24px from edges
- **Padding**: 16px
- **Shadow**: Level 5 (Floating)
- **Layout**: spinner / icon (left) + text block (right)
- **Variant tints** (left border only, 3px wide):
  - Info (model download): Info Steel `#94B0C2`
  - Success (decision saved): Keep Sage `#7DB89A`
  - Error (move failure): Reject Coral `#C97B7B`
- **Text**: Body 14px weight 500 in Near White (title) + Caption 12px in Light Gray (detail)

### 4.10 Personal Model Panel

- **Surface**: same as toolbar
- **Progress bar**: 4px tall, `#1b1c1e` track, indigo `#7B82C9` fill (matches personal score)
- **Train / Retrain button**: Secondary button style with Warm Amber outline when ready
- **Top features list**: GeistMono caption rows with feature name (left) + weight bar (right)

---

## 5. Layout Principles

### Spacing System
- **Base unit**: 8px
- **Scale**: 2px, 4px, 6px, 8px, 12px, 16px, 20px, 24px, 32px, 40px, 64px
- **Toolbar internal padding**: 0 16px
- **Right-rail (inspector) padding**: 24px
- **Card padding**: 16px (panels), 0 (thumbnails)
- **Component gap**: 8–12px between related controls

### Grid (Contact Sheet)
- **Thumbnail size**: 240px wide on desktop default, configurable via density slider
  - Density presets: Comfortable (240px), Compact (180px), Dense (140px)
- **Grid gap**: 12px
- **Container**: full viewport width, 24px horizontal padding
- **Max width**: none — contact sheet always fills available width
- **Rows**: CSS Grid `repeat(auto-fill, minmax(<size>, 1fr))`

### Inspector Layout
- **Center photo container**: max 90vh, max calc(100vw - 380px - 64px), object-fit contain
- **Right rail**: fixed 380px wide
- **Toolbar**: floating 56px above bottom edge, max 720px wide, centered

### Whitespace Philosophy
- **Tool, not theatre**: Spacing is functional. No 120px hero gaps.
- **Density is a feature**: Photographers cull hundreds of photos at a time — the contact-sheet view should feel dense and efficient, not airy.
- **Inspector is the breath**: When a single photo is open, the surrounding void is generous (90vh framing) so the photo can be judged.

### Border Radius Scale
| Radius | Use |
|--------|-----|
| 2px | Inline numeric badges, micro-indicators |
| 3px | Score-bar tracks |
| 4px | Hotkey caps, score badges on thumbnails |
| 6px | Thumbnails, decision pills, panel inputs |
| 8px | Toolbar buttons, folder path input |
| 10px | Toasts |
| 12px | Toolbar (floating), inspector right rail, group containers, photo container |
| 16px | (reserved for future feature cards) |
| 86px+ | NOT USED — this is a tool, not a marketing pill |

### Z-Index Layers
- 0: Page canvas
- 10: Contact-sheet grid
- 50: Floating toolbar
- 90: Toast container
- 100: Inspector backdrop
- 110: Inspector content
- 200: Settings modal / Conflict modal

---

## 6. Depth & Elevation

| Level | Treatment | Use |
|-------|-----------|-----|
| Level 0 (Void) | No shadow, `#07080a` surface | Page background |
| Level 1 (Subtle) | `rgba(0, 0, 0, 0.28) 0px 1.2px 2.4px` | Thumbnail cards in grid |
| Level 2 (Ring) | `rgb(27, 28, 30) 0 0 0 1px` outer + `rgb(7, 8, 10) 0 0 0 1px inset` | Right-rail panels, EXIF panel — double-ring containment |
| Level 3 (Button) | `inset 0 1px 0 rgba(255,255,255,0.05)` + `0 0 0 1px rgba(255,255,255,0.10)` + `inset 0 -1px 0 rgba(0,0,0,0.20)` | Toolbar decision buttons — macOS pressed look |
| Level 4 (Key) | Five-layer stack — see §4.6 | Hotkey caps |
| Level 5 (Floating) | `rgba(0,0,0,0.5) 0 0 0 2px` + `rgba(255,255,255,0.10) 0 0 14px` + insets | Floating toolbar, toasts, modals |
| Level 6 (Loupe Halo) | `rgba(255,255,255,0.04) 0 0 80px 20px` | Subtle ambient glow behind inspector photo |
| Level 7 (Best-Photo Halo) | `rgba(232,184,74,0.10) 0 0 24px 4px` | Best photo in similarity group — Warm Amber halo |

### Shadow Philosophy
- Multi-layer shadows are mandatory on all interactive surfaces. Single-layer drop
  shadows look flat and break the macOS-native illusion.
- Inset top highlights (`rgba(255,255,255,0.05–0.10)`) simulate light from above.
- Inset bottom darks (`rgba(0,0,0,0.20)`) simulate shadow underneath.
- The photograph itself never gets a shadow — it sits flush in its container with
  only the radius defining its edge. (Shadows under a photo would alter perceived
  contrast, which is unacceptable for color-critical work.)

---

## 7. Do's and Don'ts

### Do
- Use `#07080a` (not pure black) as the canvas — the cool tint is calibrated to sit below RAW shadow values
- Keep all chrome **monochrome or extremely desaturated** — saturation belongs to the photograph, not the UI
- Use Cool Cyan (`#5BB8D4`) for selection / focus rings; Warm Amber (`#E8B84A`) for "best" badge and Maybe state only — never for default chrome, never for danger
- Apply positive letter-spacing (+0.2px Inter, +0.3px buttons) — Raycast signature
- Use GeistMono with tabular numerals for **every** number on screen
- Use multi-layer inset shadows for buttons, key caps, floating panels
- Use opacity transition (0.6–0.7) on hover, never color swap — Raycast interaction signature
- Use weight 500 as Inter baseline; weight 400 only inside data tables
- Render decision states as desaturated outlines (sage / amber / coral), never as bright traffic-light colors
- Keep score-bar fills neutral white (Personal score is the only colored exception)

### Don't
- Use pure black (`#000000`) — the cool blue tint is what makes the UI feel like a calibrated viewing booth, not a generic dark theme
- Use saturated red/green/yellow for decision states — they will compete with photo colors and bias judgement
- Apply background-color hover transitions — use opacity instead
- Apply shadows to the photograph itself — it must sit flush
- Use display-size typography (>22px) — this is a tool, not a marketing site
- Use Inter for numeric content — always GeistMono for numbers
- Mix warm and cool borders — stick to the cool gray (`hsl(195, 5%, 15%)`) palette
- Use single-layer drop shadows — pair them with insets, always
- Add gradients, glows, or color washes behind the photo viewing area
- Use Cool Cyan (`#5BB8D4`) for anything other than selection/focus — cyan is exclusively for the active selection ring and tab indicators

---

## 8. Responsive Behavior

The app is designed primarily for desktop (photographers cull on a calibrated monitor),
but it must remain usable on a 13" MacBook screen and a tablet for triage on the go.

### Breakpoints
| Name | Width | Key Changes |
|------|-------|-------------|
| Mobile | <600px | Inspector becomes single-column (photo top, panels bottom sheet); contact-sheet drops to 2 columns; toolbar collapses to icon-only with overflow menu |
| Tablet | 600–1024px | Right rail in inspector slides in/out; contact-sheet uses 3–4 columns; toolbar shows icons + hotkey badges |
| Laptop | 1024–1440px | Full layout; right rail 320px; contact-sheet 5–6 columns at default density |
| Desktop | 1440–2560px | Full layout; right rail 380px; contact-sheet 7–9 columns at default density |
| Large Display | >2560px | Right rail stays 380px; contact-sheet expands to 10+ columns; max useful density |

### Touch Targets
- Decision buttons: min 44px touch area (visual 32px + 6px padding all sides)
- Thumbnails: tap = open inspector, long-press = quick-decision menu (mobile/tablet)
- Score bars: not interactive (read-only)

### Collapsing Strategy
- **Toolbar**: full labels + hotkey badges → icons + hotkey badges → icons only
- **Right rail**: 380px fixed → 320px → bottom sheet
- **Contact sheet**: density slider hidden on mobile (auto-set to Compact)
- **Filmstrip in Groups view**: stays horizontal at all sizes — scroll is the primary affordance
- **Hotkey hints**: hidden entirely on touch devices (no keyboard)

### Image Behavior
- Inspector photo: object-fit contain, fill available area, never upscale beyond native pixels
- Thumbnails: object-fit cover, 3:2 letterbox with surface color fill
- Loading state: shimmer in `#161718` → `#1b1c1e` (subtle, no skeleton text)

---

## 9. Agent Prompt Guide

### Quick Color Reference

| Token | Value | Role |
|-------|-------|------|
| `--bg-canvas` | `#07080a` | Page background |
| `--bg-surface-100` | `#101111` | Panels, toolbar |
| `--bg-surface-200` | `#161718` | Thumbnail card |
| `--bg-card` | `#1b1c1e` | Score-bar track, badges |
| `--text-primary` | `#f9f9f9` | Body |
| `--text-secondary` | `#cecece` | Labels, EXIF values |
| `--text-tertiary` | `#9c9c9d` | EXIF labels, meta |
| `--text-disabled` | `#6a6b6c` | Disabled, placeholder |
| `--border-subtle` | `rgba(255,255,255,0.06)` | Card borders |
| `--border-default` | `hsl(195,5%,15%)` | Standard divider |
| `--accent-amber` | `#E8B84A` | Maybe state, "best" badge in groups |
| `--accent-cyan` | `#5BB8D4` | Selection ring, focus rings, active tab |
| `--state-keep` | `#7DB89A` | Keep |
| `--state-maybe` | `#E8B84A` | Maybe |
| `--state-reject` | `#C97B7B` | Reject |
| `--score-personal` | `#7B82C9` | Personal-model score fill |
| `--font-sans` | `Inter, system-ui, sans-serif` | UI |
| `--font-mono` | `GeistMono, ui-monospace, Menlo, monospace` | Numbers, EXIF, paths |

### Tailwind v4 Theme Snippet

```css
@import "tailwindcss";

@theme {
  --color-canvas: #07080a;
  --color-surface-100: #101111;
  --color-surface-200: #161718;
  --color-card: #1b1c1e;
  --color-text-primary: #f9f9f9;
  --color-text-secondary: #cecece;
  --color-text-tertiary: #9c9c9d;
  --color-text-disabled: #6a6b6c;
  --color-border-subtle: rgba(255,255,255,0.06);
  --color-border-default: hsl(195 5% 15%);
  --color-accent-amber: #E8B84A;
  --color-accent-cyan: #5BB8D4;
  --color-state-keep: #7DB89A;
  --color-state-maybe: #E8B84A;
  --color-state-reject: #C97B7B;
  --color-score-personal: #7B82C9;

  --font-sans: "Inter", system-ui, sans-serif;
  --font-mono: "GeistMono", ui-monospace, Menlo, monospace;

  --radius-thumb: 6px;
  --radius-panel: 12px;
  --radius-input: 8px;
  --radius-key: 4px;

  --shadow-card: 0 1.2px 2.4px rgba(0,0,0,0.28);
  --shadow-ring: 0 0 0 1px rgb(27,28,30), inset 0 0 0 1px rgb(7,8,10);
  --shadow-floating:
    0 0 0 2px rgba(0,0,0,0.5),
    0 0 14px rgba(255,255,255,0.10),
    inset 0 1px 0 rgba(255,255,255,0.05);
  --shadow-key:
    0 1.5px 0.5px 2.5px rgba(0,0,0,0.4),
    inset 0 1px 0 rgba(255,255,255,0.08),
    inset 0 -1px 0 rgba(0,0,0,0.4),
    0 1px 0 rgba(0,0,0,0.5),
    0 0 0 1px rgba(255,255,255,0.04);
  --shadow-best-halo: 0 0 24px 4px rgba(232,184,74,0.10);
  --shadow-loupe-halo: 0 0 80px 20px rgba(255,255,255,0.04);
}

html { font-feature-settings: "calt", "kern", "liga", "ss03"; }
.font-mono { font-feature-settings: "tnum"; }
body { background: var(--color-canvas); color: var(--color-text-primary); letter-spacing: 0.2px; }
```

### Example Component Prompts

Use these prompts when handing off implementation work to the `frontend-developer` or
`ui-designer` subagents.

**Contact-sheet thumbnail card:**
> "Build a React thumbnail card for a photo culling contact sheet. Surface `#161718`,
> 6px radius, `1px solid rgba(255,255,255,0.06)` border, Level 1 shadow
> (`0 1.2px 2.4px rgba(0,0,0,0.28)`). Image fills 3:2 area with `object-fit: cover`.
> Overlay a GeistMono 12px score badge top-right on `rgba(0,0,0,0.6)` 4px-radius pill.
> Overlay a 16x16 K/M/X decision pill top-left in matching state color. Bottom 24px
> gradient veil with GeistMono 11px filename in `#cecece`. Hover: border opacity 0.18.
> Selected: outer 2px Warm Amber (`#E8B84A`) ring via box-shadow + Selection Glow halo
> (`rgba(232,184,74,0.18)`). Reject state: image opacity 0.45, 1.5px Reject Coral
> (`#C97B7B`) outline."

**Full-screen inspector with score bars:**
> "Build a React full-screen photo inspector. Backdrop `rgba(7,8,10,0.92)` covers
> viewport. Center photo at 12px radius, max 90vh / `calc(100vw - 444px)`,
> object-fit contain, with Loupe Halo behind it (`0 0 80px 20px rgba(255,255,255,0.04)`).
> Right rail 380px wide, surface `#101111`, 12px radius, Level 2 ring shadow, 24px
> internal padding. Stack: filename (Inter 22px/500), EXIF row (GeistMono 13px in
> `#cecece`), divider, score bars (sharpness, exposure, IQA, aesthetic, face,
> personal). Each score bar: 14px Inter label left in `#cecece`, 6px-tall track in
> `#1b1c1e` center with neutral white fill (Personal score uses `#7B82C9` indigo
> fill), GeistMono 14px numeral right. ESC key badge top-right. Floating toolbar at
> bottom with K / X / M decision buttons including embedded hotkey caps."

**Floating toolbar with hotkey caps:**
> "Build a 56px-tall floating toolbar pinned 24px above the viewport bottom, max-width
> 720px, centered. Surface `#101111`, 12px radius, Level 5 floating shadow, `1px solid
> rgba(255,255,255,0.06)` border. Three decision buttons (Keep / Maybe / Reject) in 32px
> tall 8px-radius pills. Each button: transparent background, 1px white-10% border,
> Inter 14px/600 white label with letter-spacing 0.3px, plus an embedded 18px hotkey
> badge on the right. Hotkey badge: gradient `#121212` → `#0d0d0d`, 4px radius,
> 5-layer Level 4 shadow, Inter 11px/600 centered text (K / M / X). Hover: opacity
> 0.7, never color swap. Active state for current photo's decision: button background
> picks up the matching tint (Keep `rgba(125,184,154,0.12)` etc) and border switches
> to that state color at 0.5 alpha."

**Similarity group filmstrip:**
> "Build a similarity-group container. Surface `#101111`, 12px radius, `1px solid
> rgba(255,255,255,0.06)`, 16px padding. Header row: Inter 20px/500 'Group N' on left,
> caption 12px count + similarity threshold badge in middle, Secondary button 'Reject
> all but best' on right. Below: horizontal scrollable filmstrip with 12px gap and
> 160px-wide thumbnail cards (using the standard thumbnail-card component). The single
> 'best' photo in the group gets an extra 2px Warm Amber (`#E8B84A`) ring via
> box-shadow plus a Best-Photo Halo (`0 0 24px 4px rgba(232,184,74,0.10)`) and a
> 'BEST' pill at the top-center."

**Folder path input:**
> "Build a folder path input. Width 100% of parent, 36px tall, 8px radius, `#07080a`
> background, `1px solid rgba(255,255,255,0.08)` border, 0 12px padding. Text and
> placeholder are GeistMono 13px (Near White `#f9f9f9` / Dim Gray `#6a6b6c`). On focus,
> border brightens to `rgba(255,255,255,0.18)` and a 3px Warm Amber glow ring appears
> (`box-shadow: 0 0 0 3px rgba(232,184,74,0.18)`). Inline 'Reset' ghost button on the
> right side, Caption 11px in Medium Gray, hover opacity 0.7."

### Iteration Guide

When refining or reviewing implementations of this design system:
1. **Canvas tint**: Confirm background is `#07080a`, not pure black. The cool blue is
   what keeps RAW judgement honest.
2. **Chrome saturation**: Open the screen and squint — chrome should read as monochrome.
   If anything other than a thumbnail or photo has saturated color, it is wrong (the
   only allowed accents are the desaturated decision states and the Warm Amber selection).
3. **Score-bar neutrality**: All score fills are white except Personal (`#7B82C9`).
   No traffic-light encoding.
4. **Letter-spacing**: Inter body text must have +0.2px tracking. Without it, the UI
   loses the airy Raycast quality.
5. **Numbers in mono**: Every numeric field — scores, EXIF, file sizes, counts — must
   use GeistMono with `tnum`. Inter numbers in data contexts are a regression.
6. **Shadow layering**: Buttons, key caps, floating panels must use multi-layer shadows
   with both inset highlights and inset darks. Single-layer drop shadows are wrong.
7. **Hover transitions**: opacity 0.6–0.7, never background-color. This is the Raycast
   interaction signature and KaMeRa preserves it.
8. **Photo never gets a shadow**: The photograph in the inspector and in thumbnails
   must sit flush. Drop shadows under a photo bias contrast perception.
9. **Warm Amber discipline**: Reserve `#E8B84A` exclusively for selection / focus /
   "best in group". If it appears anywhere else, demote it to a desaturated state color.

---

## Implementation Notes for the KaMeRa Codebase

This design system maps onto the existing codebase as follows:

| File | Components Affected |
|------|---------------------|
| `frontend/src/index.css` | Tailwind v4 theme block from §9 |
| `frontend/src/App.jsx` — grid view | Thumbnail Card (§4.2), Toolbar (§4.1) |
| `frontend/src/App.jsx` — DetailView | Inspector Panel (§4.3), Score Bar (§4.4), EXIF Panel (§4.5), Hotkey Badge (§4.6) |
| `frontend/src/App.jsx` — GroupsView | Similarity Groups Filmstrip (§4.8) |
| `frontend/src/App.jsx` — TrainingModeView | Inspector Panel without score bars; toolbar persists |
| `frontend/src/App.jsx` — DownloadToast | Toast (§4.9) |
| `frontend/src/App.jsx` — PersonalModelPanel | Personal Model Panel (§4.10) |
| `frontend/src/App.jsx` — SettingsModal / FolderInput | Folder Path Input (§4.7) |

**Recommended next agent handoffs:**
- `frontend-developer` — implement the Tailwind v4 theme block and refactor App.jsx
  components to use the new tokens
- `ui-designer` — produce a single reference screen showing all nine component
  patterns side by side for visual QA
- `qa-expert` — validate against the Iteration Guide checklist (§9) once the refactor
  lands

---

**Version:** 1.0 · **Last Updated:** 2026-05-01
**Source:** Adapted from Raycast DESIGN.md (https://getdesign.md/raycast/design-md)
**Cached source:** `.claude/design/raycast-DESIGN.md`
