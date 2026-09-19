import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API } from '../api'

// useGroupScrub — the group tile's peek-and-fix panel.
//
// CLICK opens it (it was hover until 2026-09-17). A click on a group tile
// selects it as before AND pins its panel open; the panel then stays until the
// user dismisses it. That is why the hover machinery this file used to carry —
// dwell, grace period, latch, a document-level mousemove watcher — is gone:
// none of it is needed once opening is a deliberate act and so is closing.
//
// What survived, because it is about steering rather than about opening:
//   · sweeping the tile horizontally still scrubs the burst
//   · so do the panel's own position bar and the arrow keys
//   · the axis lock still freezes the scrub the moment the hand turns
//     vertical, so walking down into the panel doesn't drag the frame along
//
// Opening also SCROLLS the tile up under the header so the panel gets the
// whole remaining viewport (it sizes itself to the room it is given — see
// GroupScrubPreview's place()). The scroll runs before the measurement, never
// after, or the panel would be placed against a rect that is still moving.
//
// Called ONCE from App.jsx, so only one panel can ever be open. Tiles hold no
// scrub state — they forward events plus their own member list, which App
// already has in memory (no fetch).

// Delay between the click and the panel appearing. It exists only to let a
// DOUBLE-click through: double-click still opens GroupLoupe / the partners
// overlay, and without this window the first of its two clicks would open the
// panel and scroll the grid before the second one arrived. Comparable to the
// hover dwell it replaced (180ms), so it doesn't read as slower.
const CLICK_ARM_MS = 220

// Cap on waiting for the smooth scroll to settle before measuring. The scroll
// is judged finished when the tile's top has held still for SCROLL_STABLE_FRAMES
// frames; this cap only keeps the panel from never opening if it never does.
// (A fixed 420ms timer was too short for a long scroll: the panel was placed
// against a rect that was still moving, so it landed detached from its tile.)
const SCROLL_SETTLE_MS = 1500
const SCROLL_STABLE_FRAMES = 4

// Scrolling closes the panel — it detaches from its anchor. That rule has to
// ignore OUR OWN opening scroll, including the momentum tail that lands after
// the panel is already up.
const SCROLL_GUARD_MS = 700

// Breathing room between the header and the tile we scroll under it.
const ANCHOR_GAP = 10

// Cap on how many members we warm per group. Bursts are usually well under
// this; the cap just stops a pathological 200-frame group from queueing 200
// image requests on a single open.
const PREFETCH_LIMIT = 24

// How far the pointer has to travel before it takes the scrub back from the
// keyboard. Arrow keys and the pointer drive the same index, so without a
// dead zone the smallest jitter of a resting hand would yank the panel back
// to whatever frame sits under the cursor the instant you pressed →.
const RETAKE_PX = 6

// Axis intent. Sweeping the tile horizontally scrubs — but the trip from the
// tile down into the panel passes over the rest of the tile on the way out,
// and on a 13-frame burst a 40px sideways drift during that trip is three
// photos. So once the hand turns vertical the scrub freezes, and it only
// comes back on movement that is clearly horizontal again.
//
// The direction is judged over a fixed window of TRAVEL, not of time — that is
// what makes it hold at both speeds. A time window has to compare a fast
// exit's 50px against a slow sweep's 6px, so the slow sweep's hand wobble is
// the same size as its actual progress and the ratio is noise; over 18px of
// travel a sweep reads as 18 across / 3 down whether it took 300ms or 4s.
//
// Measured straight-line from the newest sample that is already AXIS_EVAL_PX
// away, so a resting hand's jitter never reaches the test at all.
const AXIS_EVAL_PX    = 18   // travel a segment needs before its direction counts
const AXIS_MAX_AGE_MS = 500  // ...and how stale the far end of it may be
const AXIS_LOCK_RATIO   = 1.0 // vertical has only to lead to stop the scrub
const AXIS_UNLOCK_RATIO = 1.3 // horizontal has to lead clearly to restart it

export function useGroupScrub({ suspended = false, topInset } = {}) {
  // null when closed. { rect, members, index } when open.
  const [state, setState] = useState(null)

  const timerRef   = useRef(null)      // click-arm (the double-click window)
  const elRef      = useRef(null)      // the anchor tile
  const membersRef = useRef(null)      // its member list
  const xRef       = useRef(0)         // last pointer clientX
  const warmedRef  = useRef(new Set()) // image ids already prefetched
  const pinnedXRef = useRef(null)      // pointer x when the keyboard last stepped
  const trailRef   = useRef([])        // recent pointer samples, for axis intent
  const axisLockRef = useRef(false)    // true while the gesture reads as vertical
  const stateRef   = useRef(null)      // mirror of `state` for the imperative paths
  const stashRef   = useRef(null)      // a pass parked while DetailView has the screen
  const scrollGuardRef = useRef(0)     // performance.now() until which scroll doesn't close

  // Props the timer callbacks read long after the render that scheduled them.
  const suspendedRef = useRef(suspended)
  const topInsetRef  = useRef(topInset)
  useEffect(() => { suspendedRef.current = suspended }, [suspended])
  useEffect(() => { topInsetRef.current = topInset }, [topInset])
  useEffect(() => { stateRef.current = state }, [state])

  const resetAxis = () => {
    trailRef.current = []
    axisLockRef.current = false
  }

  // Feed a pointer sample; returns true while the scrub should stay frozen.
  // Sign is deliberately ignored — up and down are both "not scrubbing", and
  // the panel can hang on either side of the tile.
  //
  // Asymmetric ratios are hysteresis: turning away from the scrub is cheap,
  // coming back costs a clearly horizontal segment. Without it a gesture at
  // roughly 45° would flap between the two states.
  const axisLocked = (e) => {
    const now = performance.now()
    const trail = trailRef.current
    trail.push({ x: e.clientX, y: e.clientY, t: now })
    // Keep two samples however slow the pointer is, so there's always a delta.
    while (trail.length > 2 && now - trail[0].t > AXIS_MAX_AGE_MS) trail.shift()

    // Newest sample that's already far enough away — the SHORTEST segment
    // still carrying a real direction, so the verdict tracks the hand now
    // rather than the whole path it took to get here.
    let dx = 0, dy = 0, found = false
    for (let i = trail.length - 2; i >= 0; i--) {
      const sx = e.clientX - trail[i].x
      const sy = e.clientY - trail[i].y
      if (Math.hypot(sx, sy) >= AXIS_EVAL_PX) {
        dx = Math.abs(sx); dy = Math.abs(sy); found = true
        break
      }
    }
    if (!found) return axisLockRef.current   // hasn't travelled enough to have a direction

    if (axisLockRef.current) {
      if (dx > dy * AXIS_UNLOCK_RATIO) axisLockRef.current = false
    } else if (dy > dx * AXIS_LOCK_RATIO) {
      axisLockRef.current = true
    }
    return axisLockRef.current
  }

  const clearTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  const close = useCallback(() => {
    clearTimer()
    resetAxis()
    pinnedXRef.current = null
    elRef.current = null
    membersRef.current = null
    stateRef.current = null
    setState(prev => (prev === null ? prev : null))
  }, [])

  // Map the pointer's x within the tile onto a member index. The rect is read
  // live rather than cached at open — the grid can reflow underneath us, and
  // the opening scroll moves it on purpose.
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

  // Put the tile directly under the sticky header, then open against where it
  // actually ended up. `scroll-margin-top` rather than arithmetic on
  // window.scrollY: it lets the browser handle whichever ancestor is the
  // scroller — the grid has none of its own today, but may grow one.
  const scrollThenOpen = useCallback((el) => {
    const open = () => {
      if (suspendedRef.current) return
      const next = measure()
      if (!next) return
      prefetch(next.members)
      setState(next)
    }

    const inset = (topInsetRef.current?.() ?? 0) + ANCHOR_GAP
    const delta = el.getBoundingClientRect().top - inset
    if (Math.abs(delta) < 2) { open(); return }   // already in place

    scrollGuardRef.current = performance.now() + SCROLL_GUARD_MS
    el.style.scrollMarginTop = `${inset}px`
    el.scrollIntoView({ block: 'start', behavior: 'smooth' })

    // Measure once the scroll has actually landed: the tile's top has stopped
    // moving. Polled per frame rather than trusting `scrollend`, which fires
    // early on some engines and not at all on others.
    let done = false
    let last = null
    let stable = 0
    const started = performance.now()
    const finish = () => {
      if (done) return
      done = true
      if (elRef.current !== el) return   // closed or retargeted while scrolling
      // Push the guard out again: momentum and the smooth-scroll tail keep
      // firing scroll events for a moment after the panel is on screen, and
      // those must not be read as the user scrolling away from it.
      scrollGuardRef.current = performance.now() + SCROLL_GUARD_MS
      open()
    }
    const tick = () => {
      if (done) return
      const top = el.getBoundingClientRect().top
      stable = last !== null && Math.abs(top - last) < 0.5 ? stable + 1 : 0
      last = top
      if (stable >= SCROLL_STABLE_FRAMES || performance.now() - started > SCROLL_SETTLE_MS) finish()
      else requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }, [measure, prefetch])

  // Click on a group tile. Selecting the tile is App's business and happens
  // alongside; this owns the panel only.
  const onClick = useCallback((e, members) => {
    const el = e?.currentTarget
    if (!el || !Array.isArray(members) || members.length < 2) return

    // Second click on the SAME tile while the first is still armed = a
    // double-click. Drop the pending open and let onDoubleClick have the
    // gesture. On a DIFFERENT tile it's just an impatient user moving on:
    // abandon the old pending open and arm this one instead.
    if (timerRef.current) {
      const wasSameTile = elRef.current === el
      clearTimer()
      if (wasSameTile) return
    }

    // Click on the tile whose panel is already up = close it.
    if (stateRef.current !== null && elRef.current === el) { close(); return }

    // A different tile drops the old panel at once rather than letting it
    // linger through the arm window over someone else's burst.
    if (elRef.current && elRef.current !== el) {
      stateRef.current = null
      setState(prev => (prev === null ? prev : null))
    }
    elRef.current = el
    membersRef.current = members
    xRef.current = e.clientX
    pinnedXRef.current = null
    resetAxis()

    timerRef.current = setTimeout(() => {
      timerRef.current = null
      if (suspendedRef.current) return
      scrollThenOpen(el)
    }, CLICK_ARM_MS)
  }, [close, scrollThenOpen])

  // Sweeping the anchor tile scrubs, exactly as it did under hover.
  const onMove = useCallback((e) => {
    // Only the tile that owns the open panel steers it. Tiles hand this
    // handler out unconditionally, so without the check the pointer crossing
    // a neighbour would scrub someone else's burst by x.
    if (!elRef.current || e.currentTarget !== elRef.current) return

    // Sampled on EVERY move of the anchor, before any early return — the
    // tracker needs the whole path, not just the events that end up scrubbing.
    const frozen = axisLocked(e)

    // Record x either way — the scrub resumes from where the pointer actually
    // is, not from where it was when the hand turned. Frozen returns BEFORE
    // the keyboard pin is consulted, so a trip down to the panel can't spend
    // the pin on movement that was never meant to scrub.
    if (frozen) { xRef.current = e.clientX; return }

    // Keyboard has the index until the pointer genuinely moves again (see
    // RETAKE_PX). Both gestures drive the same index; the last real one wins.
    if (pinnedXRef.current !== null) {
      if (Math.abs(e.clientX - pinnedXRef.current) <= RETAKE_PX) {
        xRef.current = e.clientX
        return
      }
      pinnedXRef.current = null
    }
    xRef.current = e.clientX
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

  // Move the scrub to an absolute index, clamped to the burst. Shared by the
  // arrow keys and by hovering the panel's own position bar — the pointer
  // inside the panel can't reach the tile's mousemove, so the bar is the
  // panel's stand-in for the tile's horizontal sweep.
  const scrubToIndex = useCallback((next) => {
    setState(prev => {
      if (prev === null) return prev
      const i = Math.min(prev.members.length - 1, Math.max(0, next))
      if (i === prev.index) return prev
      return { ...prev, index: i }
    })
  }, [])

  // Arrow-key step. Pins the pointer so a stationary cursor over the tile
  // doesn't immediately undo it. No wrap at either end — the position bar
  // shows where you are, and silently jumping to the far end of the burst
  // after a decision would hide which frame you just acted on.
  const stepIndex = useCallback((delta) => {
    pinnedXRef.current = xRef.current
    setState(prev => {
      if (prev === null) return prev
      const i = Math.min(prev.members.length - 1, Math.max(0, prev.index + delta))
      if (i === prev.index) return prev
      return { ...prev, index: i }
    })
  }, [])

  // ── Park & resume ────────────────────────────────────────────────────────
  // Opening DetailView from the panel suspends the hook, which wipes the pass
  // entirely. Space/Esc in there has to come back to the SAME burst on the
  // SAME frame, so the pass is parked first and rebuilt afterwards — the
  // pointer may be nowhere near the tile by then, so it can't be re-derived.
  const stash = useCallback(() => {
    const s = stateRef.current
    if (!s) return false
    stashRef.current = { el: elRef.current, rect: s.rect, members: s.members }
    return true
  }, [])

  // Reopen the parked pass on `imageId`. The anchor is re-measured when it's
  // still mounted; the parked rect is the fallback for a tile the cull has
  // since removed from the filtered grid — a panel a little off its tile
  // beats no panel at all. Members here are only a length/order reference:
  // App re-reads the live list for rendering.
  const restore = useCallback((imageId) => {
    const st = stashRef.current
    stashRef.current = null
    if (!st || !st.members || st.members.length === 0) return false

    let rect = st.rect
    if (st.el && st.el.isConnected) {
      const live = st.el.getBoundingClientRect()
      if (live.width > 0) rect = live
    }
    if (!rect || rect.width <= 0) return false

    const i = st.members.findIndex(m => m.id === imageId)
    elRef.current = st.el
    membersRef.current = st.members
    resetAxis()
    // Pin at the pointer's last known x. The user came back on a keypress, so
    // the hand hasn't moved and the restored frame must stand; the first
    // genuine sideways move (RETAKE_PX) hands the scrub back as usual.
    pinnedXRef.current = xRef.current
    const next = { rect, members: st.members, index: i >= 0 ? i : 0 }
    stateRef.current = next
    setState(next)
    return true
  }, [])

  const scrub = useMemo(() => ({ onClick, onMove }), [onClick, onMove])

  // Any full-screen surface taking over, or select mode being entered, kills
  // the panel outright.
  useEffect(() => {
    if (suspended) close()
  }, [suspended, close])

  // ── Dismissal ────────────────────────────────────────────────────────────
  // A pinned panel needs explicit ways out: a click anywhere that is neither
  // the panel nor its own tile, and scrolling (which detaches it from its
  // anchor). Esc is bound by the panel itself.
  //
  // Clicking the ANCHOR is deliberately not handled here — onClick above
  // toggles it, and closing on mousedown would let that toggle re-open it a
  // moment later, since mousedown lands first.
  useEffect(() => {
    if (state === null) return

    const onDocDown = (e) => {
      const t = e.target
      if (!(t instanceof Element)) return
      if (t.closest('[data-group-scrub]')) return
      const el = elRef.current
      if (el && el.isConnected && el.contains(t)) return
      close()
    }
    const onScroll = () => {
      if (performance.now() < scrollGuardRef.current) return
      close()
    }

    document.addEventListener('mousedown', onDocDown, true)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('wheel', onScroll, { capture: true, passive: true })
    window.addEventListener('resize', onScroll)
    // Once a drag begins the browser stops firing mouse events entirely, so an
    // open panel would otherwise hang around over the drop targets.
    window.addEventListener('dragstart', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDocDown, true)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('wheel', onScroll, { capture: true })
      window.removeEventListener('resize', onScroll)
      window.removeEventListener('dragstart', onScroll, true)
    }
  }, [state !== null, close])

  useEffect(() => () => { clearTimer() }, [])

  const preview = state
    ? {
        anchorRect: state.rect,
        members: state.members,
        index: state.index,
        onScrubTo: scrubToIndex,
        onStep: stepIndex,
        onClose: close,
      }
    : null

  // Memoised so the object identity is stable across renders. It is passed
  // straight to the memo()'d grid tiles, and a fresh literal here re-rendered
  // every group tile on every keystroke — the same trap as an inline arrow.
  return useMemo(
    () => ({ scrub, preview, stash, restore }),
    [scrub, preview, stash, restore]
  )
}
