// PhotoPager — touch viewport for the cull screen and group view.
//
// Gestures (with non-gesture button alternatives provided by the parent):
//   • Swipe horizontally  → onPrev / onNext
//   • Swipe up            → onSwipeUp (open detail sheet)
//   • Double-tap          → cycle zoom (1× → 2× → 3× → 1×)
//   • Pinch (two-finger)  → continuous zoom
//   • Pan while zoomed    → translate the image
//
// Non-gesture alternatives (WCAG 2.5.7) live in the parent's UI: prev/next
// buttons in the top bar, an Undo button, and a Zoom item in the detail sheet.
//
// The viewport is `touch-action: none` (via .m-pannable) so the browser
// doesn't preempt the gestures with native scroll/pinch.

import { useEffect, useRef, useState } from 'react'
import { API } from '../../api'

const ZOOM_LEVELS = [1, 2, 3]
const SWIPE_THRESHOLD = 60
const SWIPE_UP_THRESHOLD = 80
const DOUBLE_TAP_MS = 280

function distance(t1, t2) {
  const dx = t1.clientX - t2.clientX
  const dy = t1.clientY - t2.clientY
  return Math.hypot(dx, dy)
}

export function PhotoPager({
  image,
  alt,
  onPrev,
  onNext,
  onSwipeUp,
  decisionTint, // 'keep'|'maybe'|'reject'|null
}) {
  const wrapRef = useRef(null)
  const [zoomIdx, setZoomIdx] = useState(0)
  const [pan, setPan]         = useState({ x: 0, y: 0 })
  const stateRef = useRef({
    pointers: new Map(),
    pinchStartDist: 0,
    pinchStartZoom: 1,
    panStart: null,
    swipeStart: null,
    lastTapAt: 0,
    flushNext: null,
  })

  // Reset zoom + pan whenever the image changes.
  useEffect(() => {
    setZoomIdx(0)
    setPan({ x: 0, y: 0 })
  }, [image?.id])

  const zoom = ZOOM_LEVELS[zoomIdx]

  const cycleZoom = () => {
    setZoomIdx(i => {
      const next = (i + 1) % ZOOM_LEVELS.length
      if (ZOOM_LEVELS[next] === 1) setPan({ x: 0, y: 0 })
      return next
    })
  }

  const onPointerDown = (e) => {
    e.currentTarget.setPointerCapture?.(e.pointerId)
    const s = stateRef.current
    s.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (s.pointers.size === 2) {
      const [a, b] = Array.from(s.pointers.values())
      s.pinchStartDist = Math.hypot(a.x - b.x, a.y - b.y)
      s.pinchStartZoom = zoom
      s.swipeStart = null
      return
    }
    if (s.pointers.size === 1) {
      if (zoom > 1) {
        s.panStart = { x: e.clientX - pan.x, y: e.clientY - pan.y }
      } else {
        s.swipeStart = { x: e.clientX, y: e.clientY, t: Date.now() }
      }
    }
  }

  const onPointerMove = (e) => {
    const s = stateRef.current
    if (!s.pointers.has(e.pointerId)) return
    s.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })

    if (s.pointers.size === 2) {
      const [a, b] = Array.from(s.pointers.values())
      const d = Math.hypot(a.x - b.x, a.y - b.y)
      if (s.pinchStartDist > 0) {
        const ratio = d / s.pinchStartDist
        const target = Math.max(1, Math.min(3, s.pinchStartZoom * ratio))
        // Snap zoomIdx to the closest of the discrete ZOOM_LEVELS so we
        // continue to behave nicely with double-tap.
        const closest = ZOOM_LEVELS.reduce(
          (best, lvl, idx) => Math.abs(lvl - target) < Math.abs(ZOOM_LEVELS[best] - target) ? idx : best,
          0,
        )
        setZoomIdx(closest)
      }
      return
    }

    if (s.panStart && zoom > 1) {
      setPan({ x: e.clientX - s.panStart.x, y: e.clientY - s.panStart.y })
    }
  }

  const onPointerUp = (e) => {
    const s = stateRef.current
    const start = s.swipeStart
    s.pointers.delete(e.pointerId)
    if (s.pointers.size === 0) {
      s.pinchStartDist = 0
      s.panStart = null
    }

    if (zoom > 1) {
      // No swipe-to-navigate while zoomed; pan only.
      s.swipeStart = null
      return
    }

    if (start) {
      const dx = e.clientX - start.x
      const dy = e.clientY - start.y
      const dt = Date.now() - start.t
      const horiz = Math.abs(dx) > Math.abs(dy)
      if (horiz && Math.abs(dx) > SWIPE_THRESHOLD && dt < 600) {
        dx < 0 ? onNext?.() : onPrev?.()
        s.swipeStart = null
        return
      }
      if (!horiz && dy < -SWIPE_UP_THRESHOLD) {
        onSwipeUp?.()
        s.swipeStart = null
        return
      }
      // Treat very small movements as a tap → handle double-tap zoom.
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8 && dt < 300) {
        const now = Date.now()
        if (now - s.lastTapAt < DOUBLE_TAP_MS) {
          cycleZoom()
          s.lastTapAt = 0
        } else {
          s.lastTapAt = now
        }
      }
      s.swipeStart = null
    }
  }

  const tintClass = decisionTint
    ? `m-decision-tint-${decisionTint}`
    : 'shadow-[inset_0_0_0_1px_rgba(255,255,255,0.04)]'

  return (
    <div
      ref={wrapRef}
      className={`m-photo-frame m-pannable relative w-full h-full ${tintClass}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      role="img"
      aria-label={alt || (image?.filename ? `Photo: ${image.filename}` : 'Photo viewport')}
    >
      {image ? (
        <img
          src={`${API}/previews/${image.id}`}
          alt={alt || image.filename || ''}
          draggable={false}
          className="absolute inset-0 m-auto max-h-full max-w-full object-contain select-none"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: 'center center',
            transition: stateRef.current.pointers.size > 0 ? 'none' : 'transform 160ms ease',
            willChange: 'transform',
          }}
        />
      ) : null}
    </div>
  )
}
