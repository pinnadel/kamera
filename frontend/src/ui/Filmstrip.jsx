import { useEffect, useRef } from 'react'
import { ChevronsDown, ChevronsUp } from 'lucide-react'

// Shared filmstrip primitive used by DetailView and GroupLoupe. The two
// surfaces share the toolbar (collapse chevron + drag-to-resize handle),
// the thumbnail row layout, and the auto-scroll-to-focused-item behavior.
// They differ in:
//   · per-thumb chrome (DetailView mixes solo+group cells with score badges;
//     GroupLoupe shows hero/select/drag overlays + file-format badge)
//   · outer positioning (DetailView pins absolute with a right-offset for the
//     side panel; GroupLoupe renders inline at the bottom of LoupePane)
//   · whether auto-scroll is desired (DetailView yes, GroupLoupe no)
// All of those vary via props; the toolbar + thumb-row shell is identical.
//
// Layout reminder (from the previous duplicated implementations):
//   Toolbar height defaults to 40 px (FILMSTRIP_TOOLBAR_HEIGHT). The thumb
//   row uses px-4 pb-3 padding around the cells. Cells themselves are
//   `flex-shrink-0` with a caller-controlled width; the caller renders them.

export const FILMSTRIP_TOOLBAR_HEIGHT = 40

// FilmstripToolbar — 40 px bar above the thumbnail row.
//   collapsed         — bool; hides thumb row when true
//   onToggleCollapsed — chevron click handler
//   onStartResize     — mousedown on the top-edge handle to begin drag-resize
//   label             — optional left-aligned content (e.g. "In group · 5")
//   controls          — optional right-aligned slot (pill controls etc.)
export function FilmstripToolbar({ collapsed, onToggleCollapsed, onStartResize = null, height = FILMSTRIP_TOOLBAR_HEIGHT, label = null, controls = null }) {
  const Icon = collapsed ? ChevronsUp : ChevronsDown
  return (
    <div
      className="relative flex items-center gap-2 px-3 border-b border-[rgba(255,255,255,0.04)]"
      style={{ height: `${height}px` }}
    >
      {/* 1.5 px resize handle along the top edge. Mirrors the side-panel
          handle pattern: cyan hover/active fill, row-resize cursor. */}
      {onStartResize && (
        <div
          onMouseDown={onStartResize}
          aria-label="Resize filmstrip"
          className="absolute top-0 left-0 right-0 h-1.5 cursor-row-resize hover:bg-[rgba(91,184,212,0.30)] active:bg-[rgba(91,184,212,0.50)] transition-colors z-10"
        />
      )}
      {onToggleCollapsed && (
        <button
          type="button"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? 'Expand filmstrip' : 'Collapse filmstrip'}
          title={collapsed ? 'Expand filmstrip' : 'Collapse filmstrip'}
          className="w-7 h-7 inline-flex items-center justify-center rounded-md text-[#9c9c9d] hover:text-[#f0f0f0] hover:bg-[rgba(255,255,255,0.06)] transition-colors"
        >
          <Icon size={16} aria-hidden="true" />
        </button>
      )}
      {label && <div className="flex items-center gap-2">{label}</div>}
      {controls && <div className="ml-auto flex items-center gap-1">{controls}</div>}
    </div>
  )
}

// Filmstrip — full shell: toolbar + thumbnail row.
//   items                  — array of opaque entries; rendered by `renderThumb`
//   focusedIndex           — index used for auto-scroll bookkeeping
//   renderThumb(item, idx) — returns the JSX for one cell. The cell MUST set
//                            data-filmstrip-idx={idx} on its root so the
//                            auto-scroll selector can find it.
//   autoScrollToFocused    — when true, center the focused thumb on first
//                            appearance (mount or expand-after-collapse).
//                            Does NOT track subsequent focus changes — use
//                            `trackFocusedScroll` for that.
//   trackFocusedScroll     — when true, keep the focused thumb in view on
//                            every focus change (e.g. K/M/R culling inside
//                            GroupLoupe). Independent of `autoScrollToFocused`.
//   collapsed, onToggleCollapsed, onStartResize, toolbarHeight, toolbarLabel,
//   toolbarControls        — passed through to FilmstripToolbar.
//   className              — extra classes on the outer wrapper (caller can
//                            position absolute/right-offset etc).
export function Filmstrip({
  items,
  focusedIndex = -1,
  renderThumb,
  autoScrollToFocused = false,
  trackFocusedScroll = false,
  collapsed = false,
  onToggleCollapsed = null,
  onStartResize = null,
  toolbarHeight = FILMSTRIP_TOOLBAR_HEIGHT,
  toolbarLabel = null,
  toolbarControls = null,
  className = '',
  style = undefined,
  onClick = undefined,
}) {
  const stripRef = useRef(null)

  // Center the focused thumb on the FIRST appearance only (mount, or
  // expand-after-collapse). Subsequent focus changes never move the strip —
  // clicking a thumb, pressing arrow keys, or stepping through prev/next
  // all leave scrollLeft alone. If the user wants the focused thumb in
  // view they scroll the strip manually.
  //
  // The effect deliberately doesn't list `focusedIndex` as a dependency so
  // it doesn't re-run on focus changes. It listens on the items array
  // identity so a fresh list (different photo set) re-arms the center.
  const hasCenteredRef = useRef(false)
  useEffect(() => {
    if (!autoScrollToFocused) return
    if (collapsed) { hasCenteredRef.current = false; return }
    if (hasCenteredRef.current) return
    const strip = stripRef.current
    if (!strip) return

    const tryCenter = () => {
      const el = strip.querySelector(`[data-filmstrip-idx="${focusedIndex}"]`)
      if (!el) return false
      const stripRect = strip.getBoundingClientRect()
      // Wait for the strip to actually have a width — DetailView mounts can
      // run the effect before the layout has computed the strip's box.
      if (stripRect.width === 0) return false
      const elRect = el.getBoundingClientRect()
      if (elRect.width === 0) return false
      // offsetLeft is more reliable than getBoundingClientRect + scrollLeft
      // (avoids subpixel drift from the parent transform stack).
      const elCenter = el.offsetLeft + el.offsetWidth / 2
      strip.scrollLeft = elCenter - strip.clientWidth / 2
      return true
    }

    // Try immediately; retry on the next two RAFs if the strip isn't laid
    // out yet.
    if (tryCenter()) { hasCenteredRef.current = true; return }
    let cancelled = false
    const raf1 = requestAnimationFrame(() => {
      if (cancelled) return
      if (tryCenter()) { hasCenteredRef.current = true; return }
      requestAnimationFrame(() => {
        if (cancelled) return
        if (tryCenter()) hasCenteredRef.current = true
      })
    })
    return () => { cancelled = true; cancelAnimationFrame(raf1) }
  // Re-arm only on remount (no deps that change during a session). Reset
  // for expand-after-collapse is handled by the explicit guard above.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed, autoScrollToFocused])

  // Continuous focus tracking — opt-in via `trackFocusedScroll`. Used by
  // GroupLoupe where K/M/R culling repeatedly advances `focusedIndex` and
  // the user expects the strip to follow. `inline: 'nearest'` only moves
  // the strip when the focused thumb has actually left the viewport, so
  // step-by-step nav doesn't yank the rail around unnecessarily.
  useEffect(() => {
    if (!trackFocusedScroll) return
    if (collapsed) return
    if (focusedIndex < 0) return
    const strip = stripRef.current
    if (!strip) return
    const el = strip.querySelector(`[data-filmstrip-idx="${focusedIndex}"]`)
    if (!el) return
    el.scrollIntoView({ inline: 'nearest', block: 'nearest', behavior: 'smooth' })
  }, [focusedIndex, collapsed, trackFocusedScroll])

  // Anchor on resize. When the user drags the toolbar handle the thumb cells
  // grow/shrink in width; without intervention the strip's scrollLeft stays
  // put and the thumbs underneath the cursor drift sideways. We watch the
  // first child cell for size changes via ResizeObserver and counter-scroll
  // so an anchor thumb (the focused one if it's in view, otherwise the
  // leftmost in-view thumb) keeps its horizontal viewport position.
  const anchorRef = useRef(null)  // { idx, leftBeforeResize }
  useEffect(() => {
    if (collapsed) return
    const strip = stripRef.current
    if (!strip) return
    const firstCell = strip.querySelector('[data-filmstrip-idx]')
    if (!firstCell) return

    const pickAnchor = () => {
      const stripRect = strip.getBoundingClientRect()
      // Prefer the focused thumb when it's currently in view; otherwise pick
      // the first cell whose right edge is past the strip's left edge.
      const focused = strip.querySelector(`[data-filmstrip-idx="${focusedIndex}"]`)
      if (focused) {
        const r = focused.getBoundingClientRect()
        if (r.right > stripRect.left && r.left < stripRect.right) {
          return { idx: focusedIndex, left: r.left - stripRect.left }
        }
      }
      const cells = strip.querySelectorAll('[data-filmstrip-idx]')
      for (const c of cells) {
        const r = c.getBoundingClientRect()
        if (r.right > stripRect.left) {
          return { idx: Number(c.dataset.filmstripIdx), left: r.left - stripRect.left }
        }
      }
      return null
    }

    // Seed the anchor synchronously so the first ResizeObserver tick has a
    // reference point. From then on, each width change adjusts scrollLeft to
    // pin the anchor's offset, then re-picks for the next tick.
    anchorRef.current = pickAnchor()

    const obs = new ResizeObserver(() => {
      const anchor = anchorRef.current
      if (!anchor) { anchorRef.current = pickAnchor(); return }
      const el = strip.querySelector(`[data-filmstrip-idx="${anchor.idx}"]`)
      if (!el) { anchorRef.current = pickAnchor(); return }
      const stripRect = strip.getBoundingClientRect()
      const elRect    = el.getBoundingClientRect()
      const currentLeft = elRect.left - stripRect.left
      const delta = currentLeft - anchor.left
      if (delta) strip.scrollLeft += delta
      // Re-read after the adjustment so the next tick's delta is computed
      // against the post-adjust position (avoids drift accumulating).
      anchorRef.current = pickAnchor()
    })
    obs.observe(firstCell)
    return () => obs.disconnect()
  }, [collapsed, focusedIndex])

  return (
    <div className={className} style={style} onClick={onClick}>
      <FilmstripToolbar
        collapsed={collapsed}
        onToggleCollapsed={onToggleCollapsed}
        onStartResize={onStartResize}
        height={toolbarHeight}
        label={toolbarLabel}
        controls={toolbarControls}
      />
      {!collapsed && (
        <div ref={stripRef} className="flex gap-2 overflow-x-auto px-4 pt-1 pb-3">
          {items.map((item, idx) => renderThumb(item, idx))}
        </div>
      )}
    </div>
  )
}
