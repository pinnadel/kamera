import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API } from '../api'

// useGroupScrub — hover-scrub state for group tiles.
//
// Sweeping the pointer horizontally across a group tile steps through that
// burst's members and opens a large panel anchored to the tile. Moving into
// the panel freezes the scrub and hands over to its controls, so the panel is
// interactive — hence the grace period below rather than a bare mouseleave.
//
// Called ONCE from App.jsx, so only one panel can ever be open. Tiles don't
// hold scrub state — they just forward pointer events plus their own member
// list, which App already has in memory (no fetch).
//
// The returned `scrub` object is memoized to a stable identity so handing it
// to the memo()'d tiles doesn't defeat their memoization.

// Dwell before opening. Without it the panel strobes as the pointer crosses a
// grid full of group tiles on its way somewhere else.
const DWELL_MS = 180

// Grace period after the pointer leaves the tile before the panel closes. The
// panel is interactive, so the pointer has to be able to travel the gap
// between tile and panel without the target vanishing underneath it. The
// panel's own enter handler cancels this.
const GRACE_MS = 240

// Cap on how many members we warm per group. Bursts are usually well under
// this; the cap just stops a pathological 200-frame group from queueing 200
// image requests on a single hover.
const PREFETCH_LIMIT = 24

export function useGroupScrub({ suspended = false } = {}) {
  // null when closed. { rect, members, index } when open.
  const [state, setState] = useState(null)

  const timerRef  = useRef(null)      // dwell-before-open
  const closeRef  = useRef(null)      // grace-before-close
  const elRef     = useRef(null)      // tile element currently hovered
  const membersRef = useRef(null)     // its member list
  const xRef      = useRef(0)         // last pointer clientX
  const warmedRef = useRef(new Set()) // image ids already prefetched

  const clearTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }
  const clearCloseTimer = () => {
    if (closeRef.current) {
      clearTimeout(closeRef.current)
      closeRef.current = null
    }
  }

  const close = useCallback(() => {
    clearTimer()
    clearCloseTimer()
    elRef.current = null
    membersRef.current = null
    setState(prev => (prev === null ? prev : null))
  }, [])

  // Leaving the tile doesn't close immediately — the pointer may be on its way
  // into the panel. Entering the panel (or another tile) cancels this.
  const scheduleClose = useCallback(() => {
    clearTimer()
    clearCloseTimer()
    closeRef.current = setTimeout(() => {
      closeRef.current = null
      close()
    }, GRACE_MS)
  }, [close])

  // Map the pointer's x within the tile onto a member index. The rect is read
  // live rather than cached at open — the grid can reflow underneath us.
  const measure = useCallback(() => {
    const el = elRef.current
    const members = membersRef.current
    if (!el || !members || members.length < 2) return null
    const rect = el.getBoundingClientRect()
    if (rect.width <= 0) return null
    const t = (xRef.current - rect.left) / rect.width
    const index = Math.min(members.length - 1, Math.max(0, Math.floor(t * members.length)))
    return { rect, members, index }
  }, [])

  // Warm every member's preview so the second frame onward is a cache hit.
  // The backend disk-caches all formats, so a member is slow exactly once —
  // this just moves that cost off the scrub itself.
  const prefetch = useCallback((members) => {
    for (const m of members.slice(0, PREFETCH_LIMIT)) {
      if (warmedRef.current.has(m.id)) continue
      warmedRef.current.add(m.id)
      const img = new Image()
      img.src = `${API}/previews/${m.id}`
    }
  }, [])

  // Takes the event itself so the entry x is recorded up front — a pointer
  // that enters and stops dead still opens on the right frame.
  const onEnter = useCallback((e, members) => {
    const el = e?.currentTarget
    if (!el || !Array.isArray(members) || members.length < 2) return
    clearTimer()
    clearCloseTimer()
    // Arriving at a DIFFERENT tile drops the old panel at once. Letting it
    // linger through the new tile's dwell would show one group's frames while
    // the pointer is demonstrably on another's.
    if (elRef.current && elRef.current !== el) {
      setState(prev => (prev === null ? prev : null))
    }
    elRef.current = el
    membersRef.current = members
    xRef.current = e.clientX
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      const next = measure()
      if (!next) return
      prefetch(next.members)
      setState(next)
    }, DWELL_MS)
  }, [measure, prefetch])

  // Always record x — including during the dwell window — so the panel opens
  // on the frame already under the cursor instead of snapping from index 0.
  const onMove = useCallback((e) => {
    xRef.current = e.clientX
    if (!elRef.current) return
    setState(prev => {
      if (prev === null) return prev
      const next = measure()
      if (!next) return prev
      // Skip the re-render when nothing the panel reads actually moved. It
      // reads all four of these, so all four have to match.
      if (next.index === prev.index
        && next.rect.left === prev.rect.left
        && next.rect.top === prev.rect.top
        && next.rect.width === prev.rect.width
        && next.rect.bottom === prev.rect.bottom) return prev
      return next
    })
  }, [measure])

  const onLeave = useCallback(() => { scheduleClose() }, [scheduleClose])

  // Pointer is inside the panel: hold it open, and stop scrubbing (no
  // mousemove reaches the tile from here, so the index freezes on its own).
  const onPanelEnter = useCallback(() => { clearCloseTimer() }, [])
  const onPanelLeave = useCallback(() => { scheduleClose() }, [scheduleClose])

  const scrub = useMemo(() => ({ onEnter, onMove, onLeave }), [onEnter, onMove, onLeave])

  // Any full-screen surface taking over, or select mode being entered, kills
  // the panel outright — those cases don't reliably produce a mouseleave.
  useEffect(() => {
    if (suspended) close()
  }, [suspended, close])

  // Scroll detaches the panel from its anchor. Close rather than reposition:
  // once the grid has moved, the pointer is no longer meaningfully on the tile
  // that opened this.
  //
  // dragstart is in here for a different reason: once a drag begins the
  // browser stops firing mouse events entirely, so mouseleave will never
  // arrive and an open panel would hang around over the drop targets.
  useEffect(() => {
    if (state === null) return
    const onScroll = () => close()
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('wheel', onScroll, { capture: true, passive: true })
    window.addEventListener('resize', onScroll)
    window.addEventListener('dragstart', onScroll, true)
    return () => {
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('wheel', onScroll, { capture: true })
      window.removeEventListener('resize', onScroll)
      window.removeEventListener('dragstart', onScroll, true)
    }
  }, [state !== null, close])

  useEffect(() => () => { clearTimer(); clearCloseTimer() }, [])

  const preview = state
    ? {
        anchorRect: state.rect,
        members: state.members,
        index: state.index,
        onPanelEnter,
        onPanelLeave,
      }
    : null

  return { scrub, preview }
}
