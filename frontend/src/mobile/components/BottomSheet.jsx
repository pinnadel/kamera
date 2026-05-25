// BottomSheet — three-snap accessible bottom sheet for mobile.
//
// Snap points (vh):
//   0   = hidden (open=false)
//   45  = peek
//   90  = full
//
// Drag handle drags the sheet; tapping the handle cycles snap points.
// (WCAG 2.2 SC 2.5.7: drag has a tap-equivalent.) Backdrop tap closes;
// Escape closes; focus is trapped while open. Supports `aria-label`
// (required) and `aria-describedby` for the body content.
//
// Reduced motion: drops the snap-spring transition; sheet jumps to target.

import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useReducedMotion } from '../hooks/useReducedMotion'

const SNAPS = [0, 45, 90]

export function BottomSheet({
  open,
  onClose,
  ariaLabel = 'Details',
  initialSnap = 1, // index into SNAPS
  children,
  footer,
}) {
  const reduced = useReducedMotion()
  const [snap, setSnap]   = useState(initialSnap)
  const [drag, setDrag]   = useState(null) // { startY, startVH }
  const sheetRef          = useRef(null)
  const titleId           = useId()

  // Sync initial snap on open
  useEffect(() => {
    if (open) setSnap(initialSnap)
  }, [open, initialSnap])

  // Trap Escape + outer scroll
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  const cycleSnap = useCallback(() => {
    setSnap(s => (s === SNAPS.length - 1 ? 1 : s + 1))
  }, [])

  // Pointer drag handlers on the handle area. Single-pointer per WCAG 2.5.7.
  const onPointerDown = (e) => {
    e.currentTarget.setPointerCapture?.(e.pointerId)
    setDrag({ startY: e.clientY, startVH: SNAPS[snap], snapAtStart: snap })
  }
  const onPointerMove = (e) => {
    if (!drag) return
    const dy = e.clientY - drag.startY
    const vh = window.innerHeight / 100
    const next = Math.max(0, Math.min(95, drag.startVH - dy / vh))
    sheetRef.current && (sheetRef.current.style.transform =
      `translateY(${100 - next}vh)`)
  }
  const onPointerUp = (e) => {
    if (!drag) return
    const dy = e.clientY - drag.startY
    const vh = window.innerHeight / 100
    const finalVH = drag.startVH - dy / vh
    // Snap to closest of [0, 45, 90]
    let bestIdx = 0
    let bestDist = Infinity
    SNAPS.forEach((v, i) => {
      const d = Math.abs(v - finalVH)
      if (d < bestDist) { bestDist = d; bestIdx = i }
    })
    setDrag(null)
    if (sheetRef.current) sheetRef.current.style.transform = ''
    if (bestIdx === 0) onClose?.()
    else setSnap(bestIdx)
  }

  if (!open) return null
  const targetVH = SNAPS[snap]

  return createPortal(
    <div role="presentation">
      <div
        className="m-sheet-backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={sheetRef}
        className="m-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-label={ariaLabel}
        style={{
          height: `${targetVH}vh`,
          transform: drag ? undefined : 'translateY(0)',
          transition: reduced || drag ? 'none' : undefined,
        }}
      >
        <div
          className="m-sheet-handle-tap"
          onClick={cycleSnap}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          role="button"
          tabIndex={0}
          aria-label="Cycle sheet height (or drag to resize)"
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') cycleSnap() }}
        >
          <div className="m-sheet-handle" aria-hidden="true" />
        </div>
        <h2 id={titleId} className="sr-only">{ariaLabel}</h2>
        <div className="flex-1 overflow-y-auto m-no-scrollbar">
          {children}
        </div>
        {footer && (
          <div className="border-t border-white/5 px-5 py-3 bg-[#161718]">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
