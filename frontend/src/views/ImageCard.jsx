import { memo, useState } from 'react'
import { Check } from 'lucide-react'
import { API } from '../api'
import { HighlightedText } from '../ui/primitives'
import { middleTruncate, needsReview } from '../ui/format'
import { SELECTION_RING, SELECTION_RING_STRONG } from '../ui/buttons'

// ImageCard — one tile in the grid.
//
// Tile-chrome contract: the 4:3 image carries no status chrome. Decision state
// reads from the tile's colored ring (always), plus one of two mutually
// exclusive presentations of the same signal:
//   • filenames shown → the bottom footer strip is decision-tinted (and holds
//     the filename);
//   • filenames hidden, All view → a right-edge decision bar (reserved on every
//     tile so image widths stay identical; light-gray on undecided, decision-
//     colored on decided). Suppressed in filtered decision views.
// There is NO K/M/R chip — the color already says it. When filenames are hidden
// the footer is gone, so the image reclaims that height and thumbnails grow.
// Group tiles mirror this structure so photo and group tiles stay aligned.
//
// The only thing ever drawn over the image is the multi-select check badge
// (interaction feedback, select-mode only) and the transient load shimmer.
//
// Memoized: in a 741-photo batch every polling tick used to trigger a re-render
// of all 741 cards because App.jsx's `setTabs` returned a new tabs array. With
// memo + stable handlers in App.jsx, only cards whose own props actually change
// (image data, selection, search query) re-render.
//
// Derive a short subfolder label from the photo's file_path relative to its
// source_folder. Returns null when the photo lives directly in the analysis
// root (no subfolder). Strips the conventional decision dirs (_Keeps etc.)
// so a moved photo's badge points at its ORIGINAL subfolder, not the bucket
// it landed in.
function subfolderLabel(image) {
  const path = image?.file_path
  const root = image?.source_folder
  if (!path || !root) return null
  if (!path.startsWith(root)) return null
  const rel = path.slice(root.length).replace(/^\/+/, '')
  const parts = rel.split('/')
  if (parts.length <= 1) return null
  // Drop trailing decision-bucket segments (we want the originating
  // subfolder, not the cull destination).
  const dropDirs = new Set(['_Keeps', '_Maybes', '_Trash'])
  const dirSegments = parts.slice(0, -1).filter(p => !dropDirs.has(p))
  if (dirSegments.length === 0) return null
  return dirSegments.join('/')
}

function ImageCardImpl({ image, isSelected, isMultiSelected = false, isSelectMode = false, isDropHover = false, searchQuery,
  // Filename row visibility — global Display preference (toggle / F key).
  showFilename = true,
  // True on the "All" view (no decision filter active). On All, a decided
  // photo's decision must stay readable per-tile even with filenames hidden —
  // so when the footer is gone we render a right-edge decision bar. In the
  // filtered decision views this is redundant (you're already looking at one
  // decision), so the bar is suppressed there.
  isAllView = false,
  // Drag-and-drop is opt-in: when `draggable` is true, the card carries
  // the supplied dataTransfer payload on dragstart. Source-of-truth lives
  // in the parent (App.jsx for grid, GroupLoupe for loupe).
  draggable = false, onDragStart, onDragEnd,
  // True when this tile sits inside a shared group-run border (Rejects/Maybes).
  // The group border is an outer coral/amber frame drawn by App.jsx; a normal
  // (outset) cyan selection ring would collide with it in the grid gutter, so
  // when set, selection is instead drawn as an absolute-overlay border that
  // hugs the inside of the tile — see `selectionOverlayBorder` below.
  inGroupBorder = false,
  // Mark-reviewed affordance (docs/PLAN-keeps-reviewed.md Piece 2) — a
  // hover-only top-right check button, shown only when needsReview(image)
  // is true (auto-culled keeper still sitting in plain _Keeps) and not in
  // select mode (avoids competing with the multi-select badge/intent).
  onMarkReviewed,
}) {
  // Per-card load state. RAW previews are generated on first /previews/<id>
  // request (1–3s for demosaicing), so a freshly-analyzed card needs its own
  // shimmer underlay; otherwise the trailing-skeleton slot disappears the
  // moment the image row hits /images but the <img> is still in flight,
  // leaving a dark empty tile.
  const [loaded, setLoaded] = useState(false)

  // Decision-colored ring so the K/M/R state reads immediately on the tile.
  // Selection / multi-select / drop-hover (interaction feedback) win over the
  // decision ring so the cyan focus signal is never ambiguous.
  //
  // Inside a shared group border the per-tile rings would draw vertical lines
  // in the gutter between members, breaking the single-enclosure read. So when
  // `inGroupBorder`, selection is drawn as a separate absolute-overlay real
  // `border` (`selectionOverlayBorder`, rendered in the JSX below) instead of
  // this box-shadow ring — a box-shadow ring-inset would paint UNDER the
  // opaque, edge-to-edge image content and be invisible. The decision ring is
  // also dropped entirely inside a group run: every member shares the view's
  // decision, which the group frame's colour already conveys.
  let border
  let selectionOverlayBorder = null
  if (inGroupBorder) {
    if (isDropHover) {
      selectionOverlayBorder = 'border-[3px] border-[#5BB8D4]'
    } else if (isMultiSelected || isSelected) {
      selectionOverlayBorder = 'border-2 border-[#5BB8D4]'
    }
    // No per-tile decision ring — the group frame is the boundary.
    border = ''
  } else if (isDropHover) {
    border = `${SELECTION_RING_STRONG} scale-[1.02]`
  } else if (isMultiSelected) {
    border = SELECTION_RING
  } else if (isSelected) {
    border = SELECTION_RING
  } else if (image.decision === 'keep') {
    border = 'ring-1 ring-[rgba(125,184,154,0.40)] hover:ring-[rgba(125,184,154,0.65)]'
  } else if (image.decision === 'maybe') {
    border = 'ring-1 ring-[rgba(232,184,74,0.40)] hover:ring-[rgba(232,184,74,0.65)]'
  } else if (image.decision === 'reject') {
    border = 'ring-1 ring-[rgba(201,123,123,0.40)] hover:ring-[rgba(201,123,123,0.65)]'
  } else {
    border = 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.18)]'
  }

  // The shared group-run border is NOT drawn here — it lives on the run's
  // wrapper container in App.jsx (one amber outline enclosing all adjacent
  // members + the gutters between them). Per-tile edges can't span the grid
  // gutter, so the border is an absolute overlay a level up.

  // Visual emphasis cues for the active selection set:
  //   - selected tiles stay full opacity (already the case)
  //   - non-selected tiles in select mode dim to 70% so the selection
  //     reads as the "active set" instead of getting lost in the grid
  //   - draggable selected tiles get a `grab` cursor (downgrades to
  //     `grabbing` while dragging via the browser default)
  const dimmedBySelectMode = isSelectMode && !isMultiSelected ? 'opacity-70' : ''
  const dragCursor = draggable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'
  const rejectedOpacity = image.decision === 'reject' ? 'opacity-[0.75]' : ''

  const sub = subfolderLabel(image)

  // Decision-tint for the footer strip. Shared by the filename footer (as its
  // background) and the thin no-filename strip on the All view.
  const decisionTint =
    image.decision === 'keep'   ? 'rgba(125,184,154,0.14)' :
    image.decision === 'maybe'  ? 'rgba(232,184,74,0.14)'  :
    image.decision === 'reject' ? 'rgba(201,123,123,0.14)' :
    undefined
  // Right-edge decision bar. When filenames are hidden there's no footer to
  // tint, so on the All view we surface the decision as a vertical bar on the
  // tile's right edge instead. The slice is RESERVED on every tile in this
  // state (undecided + decided) so all images stay the same width and the grid
  // rows align; only decided tiles fill the bar with colour. In the filtered
  // decision views the bar is suppressed (you're already in one decision — the
  // ring is enough), matching the "keep filtered views minimal" rule.
  const reserveBar = isAllView && !showFilename
  // Undecided tiles show a light-gray bar so the slice reads as a consistent
  // element; decided tiles "light up" in their decision colour against it.
  const barColor =
    image.decision === 'keep'   ? 'rgba(125,184,154,0.85)' :
    image.decision === 'maybe'  ? 'rgba(232,184,74,0.85)'  :
    image.decision === 'reject' ? 'rgba(201,123,123,0.85)' :
    'rgba(255,255,255,0.14)'

  return (
    <div
      className={`group relative bg-[#161718] rounded-lg overflow-hidden transition-all flex items-stretch ${border} ${dimmedBySelectMode || rejectedOpacity} ${dragCursor}`}
      draggable={draggable}
      onDragStart={draggable ? onDragStart : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
    >
      {/* Selection ring, grouped-tile case only: a real `border` (not a
          box-shadow ring) drawn as a separate ABSOLUTE overlay on top of all
          other children. A box-shadow ring-inset here would get painted UNDER
          the opaque, edge-to-edge image block and be invisible; an absolute
          overlay always paints last regardless of the flush layout beneath
          it, and `border` (vs. `inset` box-shadow) needs no z-order help
          because it's the topmost sibling in DOM order. z-20 clears the
          select-mode check badge (z auto) and shimmer overlay. No layout
          shift: this is overlaid, not part of the box model, so the image
          never resizes when selected. */}
      {selectionOverlayBorder && (
        <div
          aria-hidden="true"
          className={`pointer-events-none absolute inset-0 z-20 rounded-lg ${selectionOverlayBorder}`}
        />
      )}
      {/* Left column — image + optional footer. `min-w-0` so the flex child can
          shrink to make room for the right-edge bar without overflowing. */}
      <div className="min-w-0 flex-1">
      {/* Thumbnail — aspect-ratio scales the preview with the cell width. The
          only overlays are the select-mode check badge and the load shimmer. */}
      <div className="bg-[#07080a] aspect-[4/3] flex items-center justify-center overflow-hidden relative">
        <img
          src={`${API}/previews/${image.id}`}
          alt={image.filename}
          loading="lazy"
          onLoad={() => setLoaded(true)}
          onError={() => setLoaded(true)}
          className="max-h-full max-w-full object-contain"
        />
        {/* Multi-select check badge — top-left, select-mode only. Interaction
            feedback, not decision state, so it's allowed over the image. */}
        {isSelectMode && (
          <span
            className={`absolute top-1.5 left-1.5 inline-flex items-center justify-center h-5 w-5 rounded-full transition-colors
              ${isMultiSelected
                ? 'bg-[#5BB8D4] text-[#07080a]'
                : 'bg-[rgba(7,8,10,0.55)] ring-1 ring-[rgba(255,255,255,0.30)] text-transparent'}`}
            aria-hidden="true"
          >
            <Check size={12} strokeWidth={3} />
          </span>
        )}
        {/* Mark-reviewed hover action — top-right, only for auto-culled
            keepers still sitting in plain _Keeps (needsReview). Hidden in
            select mode so it never competes with the multi-select badge or
            intercepts a select-mode click. Hover-only (group-hover) keeps
            the tile's normal chrome contract intact when idle — see the
            file header's "no K/M/R chip" note; this is a review action, not
            decision-state display, so it's allowed as an interaction
            affordance the same way the select badge is. */}
        {!isSelectMode && needsReview(image) && onMarkReviewed && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onMarkReviewed(image.id) }}
            title="Mark reviewed — release to Provenance"
            className="absolute top-1.5 right-1.5 z-10 inline-flex items-center justify-center h-6 w-6 rounded-full bg-[rgba(7,8,10,0.65)] ring-1 ring-[rgba(125,184,154,0.50)] text-[#7DB89A] opacity-0 group-hover:opacity-100 hover:bg-[rgba(125,184,154,0.25)] transition-opacity"
          >
            <Check size={13} strokeWidth={2.5} />
          </button>
        )}
        {/* Shimmer overlay sits ON TOP of the <img> until it loads.
            Keeping the <img> at full opacity (rather than fading it in)
            ensures the browser's lazy loader treats it as visible and
            actually fires the GET — opacity-0 + lazy was deferring
            requests indefinitely. The overlay covers any partial-load
            flash; pointer-events-none lets clicks pass through. */}
        {!loaded && (
          <div className="absolute inset-0 shimmer pointer-events-none" aria-hidden="true" />
        )}
      </div>

      {/* Footer — the filename row, gated by the global preference. Tinted by
          decision so K/M/R also reads from the strip. When filenames are hidden
          the footer is gone → the image reclaims the height and the thumbnail
          grows. */}
      {showFilename && (
        <div className="px-2 py-1.5" style={{ backgroundColor: decisionTint }}>
          <div className="h-[30px] space-y-0.5 overflow-hidden">
            {sub && (
              <p className="text-[10px] font-mono text-[#5BB8D4] truncate leading-tight" title={`Subfolder: ${sub}`}>
                {sub}/
              </p>
            )}
            <p className="text-xs text-[#9c9c9d] truncate font-mono leading-tight" title={image.filename}>
              {/* Middle-truncate the filename so both the date prefix and the
                  frame-number suffix stay visible. Skip it while a filename
                  search is active — truncating away the matched substring
                  would hide the very thing the user searched for. */}
              <HighlightedText
                text={searchQuery ? image.filename : middleTruncate(image.filename)}
                query={searchQuery}
              />
            </p>
          </div>
        </div>
      )}
      </div>

      {/* Right-edge decision bar — All view with filenames hidden. Reserved on
          every tile (light-gray on undecided, decision-colored on decided) so
          image widths stay identical and the grid aligns. `items-stretch` on
          the outer flex makes this span the full tile height. */}
      {reserveBar && (
        <div className="w-1.5 flex-none" style={{ backgroundColor: barColor }} aria-hidden="true" />
      )}
    </div>
  )
}

export const ImageCard = memo(ImageCardImpl)
