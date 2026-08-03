import { createPortal } from 'react-dom'
import { ArrowLeftRight } from 'lucide-react'
import { API } from '../api'

// GroupScrubPreview — the peek-and-fix panel that hangs off a group tile.
// Sweeping the pointer across the tile scrubs the burst (hooks/useGroupScrub.js);
// moving into the panel lets you act on the frame you landed on.
//
// It walks the WHOLE original burst, not just the members visible in the
// current filter view, and states each frame's bucket (border tint + pill +
// a decision-coloured position bar). That's the point of the feature: seeing
// that a group of Rejects kept one Maybe — and disagreeing with which one.
//
// Portalled to document.body: the grid clips and the tiles carry their own
// stacking contexts, so an in-tree panel would be cut off.

const PANEL_WIDTH   = 480
const MARGIN        = 16   // viewport breathing room
const GAP           = 12   // tile ↔ panel; must clear the caret
const CARET_W       = 18
const CARET_H       = 9
const RADIUS        = 10
const PAD           = 10   // panel inner padding
const BAR_BLOCK     = 13   // position bar + its top margin
const ACTION_BLOCK  = 30   // action row + its top margin
const IMG_MIN       = 220  // the size we'd LIKE — drives the flip decision
const IMG_FLOOR     = 120  // last-resort floor when neither side has room
const IMG_MAX       = 560
const SURFACE       = '#161718'
const HAIRLINE      = 'rgba(255,255,255,0.10)'

// Canonical decision palette (docs/DESIGN.md). Inline hexes because a
// portalled node can't rely on the Tailwind classes the tiles use.
const DECISION = {
  keep:   { color: '#7DB89A', label: 'Keep' },
  maybe:  { color: '#E8B84A', label: 'Maybe' },
  reject: { color: '#C97B7B', label: 'Reject' },
}
const ORDER = ['keep', 'maybe', 'reject']
const UNDECIDED_COLOR = 'rgba(255,255,255,0.22)'

// uiScaleZoom — the app scales its whole UI with `zoom` on <html>
// (index.css, data-ui-scale S/M/L). That splits the page into two coordinate
// spaces and this panel straddles both:
//
//   • getBoundingClientRect() reports VISUAL px — already multiplied by zoom.
//   • a position:fixed node portalled into <body> is laid out INSIDE the
//     zoomed root, so the left/top we set get multiplied by zoom again.
//   • documentElement.clientWidth/Height are UNZOOMED layout px.
//
// Feeding a raw rect into fixed coordinates therefore double-applies the zoom
// and the panel drifts away from its tile — further the further right the
// tile sits. Everything below works in unzoomed layout px, so the rect gets
// divided by this once, up front.
function uiScaleZoom() {
  const z = parseFloat(getComputedStyle(document.documentElement).zoom)
  return Number.isFinite(z) && z > 0 ? z : 1
}

// place — centre the panel on the tile, keep it fully on screen, prefer
// hanging below. Same below-else-flip-above rule as ui/primitives.jsx's
// placePopover, but centred rather than left-aligned, and it aims a caret.
function place(rect, zoom) {
  const vw = document.documentElement.clientWidth  || window.innerWidth
  const vh = document.documentElement.clientHeight || window.innerHeight

  // Into unzoomed layout space — the space `left`/`top` are interpreted in.
  const left0   = rect.left   / zoom
  const top0    = rect.top    / zoom
  const bottom0 = rect.bottom / zoom
  const width0  = rect.width  / zoom

  const width = Math.min(PANEL_WIDTH, vw - MARGIN * 2)

  // Horizontal: centred on the tile, then slid inward just enough to fit.
  // This is what makes the panel sit left / middle / right according to the
  // room available, without ever losing its aim.
  const centre = left0 + width0 / 2
  const left = Math.max(MARGIN, Math.min(centre - width / 2, vw - width - MARGIN))

  // Vertical: the largest image each side could seat without crossing a fold.
  const chrome = PAD * 2 + BAR_BLOCK + ACTION_BLOCK
  const roomBelow = vh - bottom0 - GAP - MARGIN - chrome
  const roomAbove = top0 - GAP - MARGIN - chrome

  // Hang below when below is genuinely comfortable; otherwise take whichever
  // side has more room. This is the flip: a tile low in the viewport (or
  // scrolled near the bottom) puts the panel above itself rather than off the
  // bottom edge.
  const below = roomBelow >= IMG_MIN || roomBelow >= roomAbove
  const room  = below ? roomBelow : roomAbove
  // Never larger than the room we actually have — that's what keeps the panel
  // inside the viewport. IMG_FLOOR only bites when a tall tile in a short
  // viewport leaves neither side usable; the position clamp below is the
  // backstop for that case.
  const imageHeight = Math.max(IMG_FLOOR, Math.min(room, IMG_MAX))

  // Backstop: whatever the arithmetic above decided, force the panel fully
  // inside the viewport. It can only bite when IMG_FLOOR exceeded the room on
  // both sides, and a panel that's slightly detached from its tile is a much
  // better failure than one bleeding off the fold.
  const panelHeight = imageHeight + chrome
  const maxTop = Math.max(MARGIN, vh - MARGIN - panelHeight)
  const top    = Math.min(Math.max(MARGIN, bottom0 + GAP), maxTop)
  const bottom = Math.min(Math.max(MARGIN, vh - top0 + GAP), Math.max(MARGIN, vh - MARGIN - panelHeight))

  // The caret always aims at the tile's centre. Sliding the panel changes
  // where that lands inside it, so the caret is measured from the panel's own
  // left edge and pulled in far enough to clear the rounded corners.
  const caretInset = RADIUS + CARET_W / 2
  const caretLeft = Math.max(caretInset, Math.min(centre - left, width - caretInset))

  return { left, width, imageHeight, below, caretLeft, ...(below ? { top } : { bottom }) }
}

function chipStyle(color) {
  return {
    height: 20,
    padding: '0 7px',
    display: 'inline-flex',
    alignItems: 'center',
    borderRadius: 4,
    backgroundColor: 'rgba(7,8,10,0.72)',
    backdropFilter: 'blur(4px)',
    color,
    fontSize: 11,
    fontWeight: 600,
    lineHeight: 1,
  }
}

function ActionButton({ tint, active, disabled, title, onClick, children }) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={(e) => { e.stopPropagation(); onClick?.() }}
      style={{
        height: 22,
        padding: '0 8px',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        borderRadius: 6,
        border: 'none',
        backgroundColor: active ? `${tint}26` : 'rgba(255,255,255,0.04)',
        boxShadow: `inset 0 0 0 1px ${active ? tint : 'rgba(255,255,255,0.10)'}`,
        color: tint,
        fontSize: 11,
        fontWeight: 600,
        lineHeight: 1,
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'opacity 120ms',
      }}
    >
      {children}
    </button>
  )
}

export function GroupScrubPreview({
  anchorRect, members, index, onPanelEnter, onPanelLeave, onDecide, onSwap,
}) {
  if (!anchorRect || !members || members.length === 0) return null

  const active = members[Math.min(index, members.length - 1)]
  if (!active) return null

  const p = place(anchorRect, uiScaleZoom())
  const activeTone = DECISION[active.decision]
  const borderColor = activeTone ? activeTone.color : HAIRLINE

  // A swap exchanges places, so it only makes sense from a frame that HAS a
  // place. Offer one per bucket the burst actually occupies elsewhere.
  const swapTargets = active.decision
    ? ORDER.filter(b => b !== active.decision && members.some(m => m.decision === b))
    : []

  // Caret drawn as a CSS triangle rather than a rotated square: no border seam
  // to hide, and it reads unambiguously as "this belongs to that tile".
  // Overlaps the panel edge by 1px so the hairline doesn't cut across it.
  const caretStyle = {
    position: 'absolute',
    left: p.caretLeft - CARET_W / 2,
    width: 0,
    height: 0,
    borderLeft: `${CARET_W / 2}px solid transparent`,
    borderRight: `${CARET_W / 2}px solid transparent`,
    ...(p.below
      ? { top: -(CARET_H - 1), borderBottom: `${CARET_H}px solid ${SURFACE}` }
      : { bottom: -(CARET_H - 1), borderTop: `${CARET_H}px solid ${SURFACE}` }),
  }

  return createPortal(
    <div
      onMouseEnter={onPanelEnter}
      onMouseLeave={onPanelLeave}
      style={{
        position: 'fixed',
        left: p.left,
        width: p.width,
        ...(p.below ? { top: p.top } : { bottom: p.bottom }),
        boxSizing: 'border-box',
        padding: PAD,
        backgroundColor: SURFACE,
        border: `1px solid ${HAIRLINE}`,
        borderRadius: RADIUS,
        boxShadow: '0 12px 32px rgba(0,0,0,0.6), 0 4px 12px rgba(0,0,0,0.4)',
        zIndex: 9999,
      }}
    >
      {/* Invisible bridge across the tile↔panel gap. Without it the pointer
          crosses dead space on its way to the controls, and only the grace
          timer keeps the panel alive; with it the travel is continuous. */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          height: GAP + CARET_H,
          ...(p.below ? { top: -(GAP + CARET_H) } : { bottom: -(GAP + CARET_H) }),
        }}
      />

      <div aria-hidden style={caretStyle} />

      {/* Fixed-height letterbox. Holding the box constant is what keeps the
          panel from resizing as the scrub crosses portrait/landscape frames. */}
      <div
        style={{
          position: 'relative',
          height: p.imageHeight,
          backgroundColor: '#07080a',
          borderRadius: 6,
          border: `1px solid ${borderColor}`,
          overflow: 'hidden',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {/* No key: remounting on every step would blank the box between
            frames. Changing src alone lets the warmed cache fill it. */}
        <img
          src={`${API}/previews/${active.id}`}
          alt={active.filename}
          draggable={false}
          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', display: 'block' }}
        />

        {/* The bucket in words. The border tint alone can't be read against a
            photo that happens to share its hue, and "which of these survived"
            is the question this panel exists to answer. */}
        <span style={{
          position: 'absolute', top: 8, left: 8,
          ...chipStyle(activeTone ? activeTone.color : '#9c9c9d'),
          boxShadow: `inset 0 0 0 1px ${activeTone ? activeTone.color : HAIRLINE}`,
        }}>
          {activeTone ? activeTone.label : 'Undecided'}
        </span>

        <span style={{
          position: 'absolute', top: 8, right: 8,
          ...chipStyle('#cecece'),
          fontVariantNumeric: 'tabular-nums',
        }}>
          {index + 1} / {members.length}
        </span>
      </div>

      {/* Position bar — one segment per member, tinted by that member's own
          decision. Doubles as a map of how the whole burst was culled: three
          coral and one amber says at a glance that the moment survived. */}
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, marginTop: 8, height: 5 }}>
        {members.map((m, i) => {
          const tone = DECISION[m.decision]
          const on = i === index
          return (
            <div
              key={m.id}
              style={{
                flex: 1,
                height: on ? 5 : 3,
                borderRadius: 2,
                backgroundColor: tone ? tone.color : UNDECIDED_COLOR,
                opacity: on ? 1 : 0.35,
              }}
            />
          )
        })}
      </div>

      {/* Action row. Left: re-cull this frame outright. Right: exchange places
          with the burst's occupant of another bucket, which leaves the bucket
          counts untouched — "this frame deserves the Keep slot, not that one". */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 8, height: 22 }}>
        {ORDER.map(kind => (
          <ActionButton
            key={kind}
            tint={DECISION[kind].color}
            active={active.decision === kind}
            disabled={active.decision === kind}
            title={active.decision === kind
              ? `Already a ${DECISION[kind].label}`
              : `Move this frame to ${DECISION[kind].label}`}
            onClick={() => onDecide?.(active.id, kind)}
          >
            {DECISION[kind].label}
          </ActionButton>
        ))}

        {swapTargets.length > 0 && (
          <div style={{ width: 1, height: 14, backgroundColor: HAIRLINE, margin: '0 2px' }} />
        )}

        {swapTargets.map(bucket => {
          // Group order is hero-first then score-desc, so the last occupant of
          // the target bucket is its weakest — the right one to give up the
          // slot when this frame takes it.
          const occupants = members.filter(m => m.decision === bucket)
          const demote = occupants[occupants.length - 1]
          return (
            <ActionButton
              key={bucket}
              tint={DECISION[bucket].color}
              title={`Swap places: this frame becomes the ${DECISION[bucket].label}, and ${demote.filename} drops to ${DECISION[active.decision].label}`}
              onClick={() => onSwap?.({
                promoteId: active.id,
                promoteTo: bucket,
                demoteId: demote.id,
                demoteTo: active.decision,
              })}
            >
              <ArrowLeftRight size={11} />
              {DECISION[bucket].label}
            </ActionButton>
          )
        })}
      </div>
    </div>,
    document.body,
  )
}
