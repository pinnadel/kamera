import { useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useHotkeys } from 'react-hotkeys-hook'
import { ArrowLeftRight } from 'lucide-react'
import { API } from '../api'

// GroupScrubPreview — the peek-and-fix panel that hangs off a group tile.
// Clicking the tile pins it open (hooks/useGroupScrub.js) and scrolls that
// tile under the header so this gets the whole remaining viewport; sweeping
// the tile, the position bar below, or the arrow keys then step the burst.
//
// It walks the WHOLE original burst, not just the members visible in the
// current filter view, and states each frame's bucket (border tint + pill +
// a decision-coloured position bar). That's the point of the feature: seeing
// that a group of Rejects kept one Maybe — and disagreeing with which one.
//
// Portalled to document.body: the grid clips and the tiles carry their own
// stacking contexts, so an in-tree panel would be cut off.
//
// It is dismissed by Esc, by a click anywhere outside it, or by clicking its
// own tile again — not by moving the pointer away. It is a pinned surface.
//
// While it's on screen the panel OWNS the keyboard — ←/→ step the burst,
// K/M/R re-cull the shown frame and advance to the one on its right, U /
// Cmd+Z takes the last one back, Space/Enter opens it in DetailView, Esc
// closes. useKeyboard.js backs off wholesale for as long as the panel is up
// (its `scrubOpen` gate); without that both layers answer the same keypress
// and fire two competing /decision posts for one press.
//
// Pointer and keyboard drive ONE index, never two: sweeping the tile or the
// panel's position bar moves it, arrow keys move it, and whichever gesture
// acted last is the one showing (hooks/useGroupScrub.js::RETAKE_PX).

const MARGIN        = 16   // viewport breathing room
const GAP           = 12   // tile ↔ panel; must clear the caret
const CARET_W       = 18
const CARET_H       = 9
const RADIUS        = 10
const PAD           = 10   // panel inner padding
const BAR_H         = 15   // position bar: active segment height (was 5)
const BAR_BLOCK     = BAR_H + 8   // position bar + its top margin
const ACTION_BLOCK  = 30   // action row + its top margin

// The panel is sized to FILL whichever side of the tile has more room — the
// whole point of a peek is seeing the frame big enough to judge, and a fixed
// size meant a tile near the top of the viewport got a cramped panel while
// most of the screen sat empty above it.
const IMG_FLOOR     = 120  // never smaller than this, even in a cramped viewport
const IMG_CAP       = 900  // sanity ceiling on very tall displays
// Width tracks height at the cameras' native 3:2, so a landscape frame fills
// the box exactly and a portrait one still gains from the extra height.
// Growing height alone would just add black bars to a 480px-wide box.
const BOX_ASPECT    = 1.5
const PANEL_MIN_W   = 420
const PANEL_MAX_W   = 960
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
// (index.css, data-ui-scale S/M/L), which splits the page into two coordinate
// spaces that this panel straddles:
//
//   • getBoundingClientRect() reports SCREEN px — zoom already applied.
//   • a position:fixed node portalled into <body> still lays out INSIDE the
//     zoomed root, so the left/top we set get multiplied by zoom again.
//
// So everything here works in "fixed space" (screen px ÷ zoom), and the
// anchor rect is divided by this once, up front.
//
// Do NOT reach for documentElement.clientWidth/clientHeight for the viewport
// bounds: those report the real viewport, NOT the zoomed root's space, so at
// scale M/L they overstate the room by the zoom factor — which sends the panel
// off the bottom fold when it hangs below, and far above its tile when it
// flips. window.innerWidth/Height are unambiguously the real viewport, so
// dividing them by zoom lands in the same space as everything else.
function uiScaleZoom() {
  const z = parseFloat(getComputedStyle(document.documentElement).zoom)
  return Number.isFinite(z) && z > 0 ? z : 1
}

// place — centre the panel on the tile, keep it fully on screen, prefer
// hanging below. Same below-else-flip-above rule as ui/primitives.jsx's
// placePopover, but centred rather than left-aligned, and it aims a caret.
function place(rect, zoom) {
  // Viewport expressed in fixed space (see uiScaleZoom).
  const vw = window.innerWidth  / zoom
  const vh = window.innerHeight / zoom

  // Into fixed space — the space `left`/`top`/`bottom` are interpreted in.
  const left0   = rect.left   / zoom
  const top0    = rect.top    / zoom
  const bottom0 = rect.bottom / zoom
  const width0  = rect.width  / zoom

  // Vertical: the largest image each side could seat without crossing a fold.
  // + 4: the panel's and the image box's 1px borders, which sit outside imageHeight.
  const chrome = PAD * 2 + BAR_BLOCK + ACTION_BLOCK + 4
  const roomBelow = vh - bottom0 - GAP - MARGIN - chrome
  const roomAbove = top0 - GAP - MARGIN - chrome

  // Whichever side has more room wins outright — no preference for below.
  // Preferring below "when it's good enough" is what left the panel small
  // near the top of the grid while the whole screen sat empty above it.
  const below = roomBelow >= roomAbove
  const room  = Math.max(below ? roomBelow : roomAbove, 0)
  // Fill that room. Never larger than it — that's what keeps the panel inside
  // the viewport. IMG_FLOOR only bites when a tall tile in a short viewport
  // leaves neither side usable; the position clamp below backstops that.
  const imageHeight = Math.max(IMG_FLOOR, Math.min(room, IMG_CAP))

  // Horizontal: width follows the height, then the panel is centred on the
  // tile and slid inward just enough to fit. That's what makes it sit left /
  // middle / right according to the room available, without losing its aim.
  // + PAD*2 so the IMAGE BOX is 3:2, not the panel including its padding.
  const width = Math.max(
    Math.min(PANEL_MIN_W, vw - MARGIN * 2),
    Math.min(imageHeight * BOX_ASPECT + PAD * 2, PANEL_MAX_W, vw - MARGIN * 2),
  )
  const centre = left0 + width0 / 2
  const left = Math.max(MARGIN, Math.min(centre - width / 2, vw - width - MARGIN))

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
  anchorRect, members, index, onDecide, onSwap,
  onScrubTo, onStep, onClose, onUndo, onOpenDetail,
  // Which member is the group's pick, and whether that pick is an actual
  // vision-LLM ranking (backend/routers/model.py::_annotate_prerank_state)
  // rather than the score-based fallback. Only the former earns the
  // position bar's rainbow marker below — an unranked burst's best-guess
  // hero shouldn't wear the "AI decided this" chrome.
  bestImageId = null, bestIsAiPick = false,
}) {
  // Everything below the hooks may bail out, but the hooks themselves can't —
  // so the open/active resolution happens first and the guards come after.
  const count  = Array.isArray(members) ? members.length : 0
  const open   = !!anchorRect && count > 0
  const idx    = open ? Math.min(Math.max(index, 0), count - 1) : 0
  const active = open ? members[idx] : null

  const barRef = useRef(null)

  // Hovering the position bar scrubs, same as sweeping the tile. Once the
  // pointer is inside the panel no mousemove reaches the tile any more, so
  // without this the scrub would be frozen exactly where the controls are.
  const scrubFromBar = useCallback((e) => {
    const el = barRef.current
    if (!el || count === 0) return
    const rect = el.getBoundingClientRect()
    if (rect.width <= 0) return
    const t = (e.clientX - rect.left) / rect.width
    onScrubTo?.(Math.floor(t * count))   // clamped by the hook
  }, [count, onScrubTo])

  // Decide the shown frame, then step right — the gesture is "judge this one,
  // show me the next". Sends even when the frame already sits in that bucket:
  // pressing K on an auto-culled Keep is the user CONFIRMING it, and only a
  // manual decision writes a training sample (backend/routers/decisions.py).
  // The file move itself is a no-op when the destination hasn't changed.
  const decideAndAdvance = useCallback((kind) => {
    if (!active) return
    onDecide?.(active.id, kind)
    onStep?.(+1)
  }, [active, onDecide, onStep])

  const undoActive = useCallback(() => {
    if (!active) return
    onUndo?.(active.id)
  }, [active, onUndo])

  const openActive = useCallback((e) => {
    e.preventDefault()
    if (active) onOpenDetail?.(active.id)
  }, [active, onOpenDetail])

  useHotkeys('arrowRight', (e) => { e.preventDefault(); onStep?.(+1) },
    { enabled: open, enableOnFormTags: false }, [open, onStep])
  useHotkeys('arrowLeft',  (e) => { e.preventDefault(); onStep?.(-1) },
    { enabled: open, enableOnFormTags: false }, [open, onStep])

  useHotkeys('k', () => decideAndAdvance('keep'),   { enabled: open }, [open, decideAndAdvance])
  useHotkeys('m', () => decideAndAdvance('maybe'),  { enabled: open }, [open, decideAndAdvance])
  useHotkeys('r', () => decideAndAdvance('reject'), { enabled: open }, [open, decideAndAdvance])

  useHotkeys('u',      undoActive, { enabled: open }, [open, undoActive])
  useHotkeys('meta+z', undoActive, { enabled: open }, [open, undoActive])

  useHotkeys('space', openActive, { enabled: open }, [open, openActive])
  useHotkeys('enter', openActive, { enabled: open }, [open, openActive])

  useHotkeys('escape', () => onClose?.(), { enabled: open }, [open, onClose])

  if (!open || !active) return null

  const zoom = uiScaleZoom()
  const p = place(anchorRect, zoom)
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
      data-group-scrub="true"
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

        {/* The panel's keys aren't guessable from a hover affordance, and
            hovering IS the whole entry point — so it says so, quietly, in the
            corner of the frame rather than as another row of chrome. */}
        <span style={{
          position: 'absolute', bottom: 8, left: 8,
          ...chipStyle('rgba(255,255,255,0.55)'),
          fontWeight: 500,
          letterSpacing: 0.2,
        }}>
          ← → step · K M R decide · U undo
        </span>

        <span style={{
          position: 'absolute', top: 8, right: 8,
          ...chipStyle('#cecece'),
          fontVariantNumeric: 'tabular-nums',
        }}>
          {idx + 1} / {members.length}
        </span>
      </div>

      {/* Position bar — one segment per member, tinted by that member's own
          decision. Doubles as a map of how the whole burst was culled: three
          coral and one amber says at a glance that the moment survived. The
          AI's rank-1 pick (when the burst has actually been vision-ranked —
          bestIsAiPick) additionally wears the app's rainbow "AI" stroke
          (.ai-border-sm, same cue as GroupLoupe's ranking-in-progress chip),
          so which frame the AI picked reads at a glance without opening the
          loupe. An unranked/score-based best gets no marker here — the peek
          panel otherwise carries no "AI decided this" claim it can't back. */}
      <div
        ref={barRef}
        onMouseMove={scrubFromBar}
        onClick={(e) => { e.stopPropagation(); scrubFromBar(e) }}
        title="Sweep to step through the burst"
        style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'flex-end',
          gap: 2,
          marginTop: 8,
          height: BAR_H,
          cursor: 'pointer',
        }}
      >
        {/* A 5px-tall target is not a target. This grows the hit area into the
            dead margins above and below without changing the layout `place()`
            budgeted for (BAR_BLOCK). */}
        <div aria-hidden style={{ position: 'absolute', left: 0, right: 0, top: -6, bottom: -6 }} />
        {members.map((m, i) => {
          const tone = DECISION[m.decision]
          const on = i === idx
          const isAiPick = bestIsAiPick && m.id === bestImageId
          return (
            <div
              key={m.id}
              className={isAiPick ? 'ai-border-sm' : undefined}
              title={isAiPick ? "AI's pick for this burst" : undefined}
              style={{
                position: 'relative',
                flex: 1,
                height: on || isAiPick ? BAR_H : BAR_H * 3 / 5,
                borderRadius: 4,
                backgroundColor: tone ? tone.color : UNDECIDED_COLOR,
                opacity: on || isAiPick ? 1 : 0.35,
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
