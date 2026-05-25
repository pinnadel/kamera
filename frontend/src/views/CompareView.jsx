import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useHotkeys } from 'react-hotkeys-hook'
import { X } from 'lucide-react'
import { API } from '../api'
import { BTN_ICON } from '../ui/buttons'
import { DecisionBadge, ScoreBadge } from '../ui/primitives'
import { pickHeadlineScore } from '../ui/format'

// CompareView — full-screen overlay for side-by-side comparison of 2–4 photos.
//
// Synchronized zoom/pan mirrors GroupLoupe's approach exactly: a single
// zoomOrigin (0..1 x/y) drives the CSS transform on every panel simultaneously
// so the user always compares the same relative region across frames. Clicking
// any panel while zoomed re-centers the shared origin at the click point.
//
// K/M/R decides the focused photo; focus auto-advances to the next undecided
// photo after each decision. When all photos are decided a toast fires and
// the overlay closes.

// Click cycles through these scales: 1× (off) → 2× → 3× → back to 1×.
// Same model as GroupLoupe so the gesture feels consistent across the app.
const ZOOM_SCALES = [1, 2, 3]

function buildTransform(zoomScale, zoomOrigin, isDragging) {
  if (zoomScale <= 1) return { transition: 'transform 0.15s ease-out' }
  return {
    transform: `scale(${zoomScale})`,
    transformOrigin: `${zoomOrigin.x * 100}% ${zoomOrigin.y * 100}%`,
    transition: isDragging ? 'none' : 'transform 0.15s ease-out',
  }
}

export function CompareView({ images, onClose, onDecide, onBulkDecide, onUndoImage, onUndo, addToast, modelInfo }) {
  const [focusedIdx, setFocusedIdx] = useState(0)
  const [zoomLevel, setZoomLevel]   = useState(0)
  const zoomScale = ZOOM_SCALES[zoomLevel]
  const zoomOn    = zoomLevel > 0
  const [zoomOrigin, setZoomOrigin] = useState({ x: 0.5, y: 0.5 })
  // Click cycle: 1× → 2× → 3× → back to 1× (zoom off). Same model as
  // DetailView's fullscreen zoom; an optional origin re-anchors so the
  // clicked point stays under the cursor on the next step.
  const cycleZoom = useCallback((origin) => {
    if (origin) setZoomOrigin(origin)
    setZoomLevel(z => (z + 1) % ZOOM_SCALES.length)
  }, [])

  // Drag-to-pan — same model as DetailView/GroupLoupe so the gesture feels
  // identical app-wide. Pan updates the shared zoomOrigin so all panels move
  // together. dragRef holds the live drag; lastWasDragRef suppresses the
  // synthesised click that follows mouseup.
  const dragRef        = useRef(null)
  const lastWasDragRef = useRef(false)
  const [isDragging, setIsDragging] = useState(false)

  // Local decision state so badges update instantly without a network round-trip.
  // Seeded from image.decision on mount; updated optimistically on K/M/R.
  const [localDecisions, setLocalDecisions] = useState(() =>
    Object.fromEntries(images.map(img => [img.id, img.decision]))
  )

  // Lock background scroll while mounted (same pattern as GroupLoupe).
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  // Clamp focusedIdx if images shrink (shouldn't happen here but defensive).
  const safeIdx = Math.min(focusedIdx, images.length - 1)
  const focusedImage = images[safeIdx] ?? null

  const transformStyle = useMemo(
    () => buildTransform(zoomScale, zoomOrigin, isDragging),
    [zoomScale, zoomOrigin, isDragging]
  )

  // ── Pan handlers (drag + trackpad) ─────────────────────────────────────────
  // Both update the shared zoomOrigin so every panel pans in lockstep. The
  // panel that captures the gesture supplies its own bounding rect to convert
  // pixel deltas into normalised origin deltas — panels can be different
  // sizes (2-up vs 3-up vs 2×2 grid) and that's fine.
  const handlePanMouseDown = useCallback((e) => {
    if (!zoomOn) return  // not zoomed → fall through to click cycle
    e.preventDefault()
    e.stopPropagation()
    const rect = e.currentTarget.getBoundingClientRect()
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      originAtStart: { ...zoomOrigin },
      containerW: rect.width,
      containerH: rect.height,
      moved: false,
    }
    document.body.style.cursor = 'grabbing'
  }, [zoomOn, zoomOrigin])

  const handlePanMouseMove = useCallback((e) => {
    if (!dragRef.current || !zoomOn) return
    const dx = e.clientX - dragRef.current.startX
    const dy = e.clientY - dragRef.current.startY
    if (!dragRef.current.moved && Math.hypot(dx, dy) < 4) return
    if (!dragRef.current.moved) setIsDragging(true)
    dragRef.current.moved = true
    const w = dragRef.current.containerW || 1
    const h = dragRef.current.containerH || 1
    const ox = dragRef.current.originAtStart.x - dx / w / (1 - 1 / zoomScale)
    const oy = dragRef.current.originAtStart.y - dy / h / (1 - 1 / zoomScale)
    setZoomOrigin({ x: Math.max(0, Math.min(1, ox)), y: Math.max(0, Math.min(1, oy)) })
  }, [zoomOn, zoomScale])

  const handlePanMouseUp = useCallback(() => {
    if (!dragRef.current) return
    lastWasDragRef.current = dragRef.current.moved
    dragRef.current = null
    setIsDragging(false)
    document.body.style.cursor = ''
    setTimeout(() => { lastWasDragRef.current = false }, 0)
  }, [])

  // Global listeners so a drag that leaves the panel still pans smoothly.
  useEffect(() => {
    if (!zoomOn) {
      document.body.style.cursor = ''
      dragRef.current = null
      setIsDragging(false)
      return
    }
    window.addEventListener('mousemove', handlePanMouseMove)
    window.addEventListener('mouseup',   handlePanMouseUp)
    return () => {
      window.removeEventListener('mousemove', handlePanMouseMove)
      window.removeEventListener('mouseup',   handlePanMouseUp)
    }
  }, [zoomOn, handlePanMouseMove, handlePanMouseUp])

  // Trackpad two-finger scroll pans the zoomed view (same as DetailView).
  const handlePanWheel = useCallback((e) => {
    if (!zoomOn) return
    e.preventDefault()
    const rect = e.currentTarget.getBoundingClientRect()
    const ox = zoomOrigin.x + e.deltaX / rect.width  / (1 - 1 / zoomScale)
    const oy = zoomOrigin.y + e.deltaY / rect.height / (1 - 1 / zoomScale)
    setZoomOrigin({ x: Math.max(0, Math.min(1, ox)), y: Math.max(0, Math.min(1, oy)) })
  }, [zoomOn, zoomOrigin, zoomScale])

  // ── Navigation ─────────────────────────────────────────────────────────────
  const stepFocus = useCallback((delta) => {
    setFocusedIdx(prev => Math.max(0, Math.min(images.length - 1, prev + delta)))
  }, [images.length])

  useHotkeys('arrowLeft',  (e) => { e.preventDefault(); stepFocus(-1) }, { enabled: true, enableOnFormTags: false }, [stepFocus])
  useHotkeys('arrowRight', (e) => { e.preventDefault(); stepFocus(+1) }, { enabled: true, enableOnFormTags: false }, [stepFocus])

  // ── Decide ──────────────────────────────────────────────────────────────────
  // K is comparative: "this is the pick." The focused photo becomes Keep and
  // every other photo in the compare set is auto-rejected — overruling any
  // prior K/M/R since the user's compare-time judgment is the most recent.
  // M and R stay per-photo (individual judgments, not comparative picks).
  const decideFocused = useCallback(async (decision) => {
    if (!focusedImage) return
    const id = focusedImage.id

    if (decision === 'keep' && images.length > 1) {
      const restIds = images.filter(img => img.id !== id).map(img => img.id)

      // Optimistic local decisions for every panel — keep on focused, reject on rest.
      setLocalDecisions(() => {
        const next = {}
        for (const img of images) next[img.id] = img.id === id ? 'keep' : 'reject'
        return next
      })

      // Two parallel calls so the focused photo and the bulk reject hit the
      // backend together. Both go through _apply_decision, which INSERT OR
      // REPLACE-s prior decisions and re-moves the file from its current
      // location — so prior K/M/R on any photo gets overridden cleanly.
      await Promise.all([
        onDecide(id, 'keep'),
        onBulkDecide?.(restIds, 'reject'),
      ])

      addToast({
        type: 'success',
        message: `Kept ${focusedImage.filename} · Rejected ${restIds.length} other${restIds.length === 1 ? '' : 's'}`,
        duration: 3500,
      })
      onClose()
      return
    }

    // Per-photo path (M, R, or K with only one photo in the set).
    setLocalDecisions(prev => ({ ...prev, [id]: decision }))

    await onDecide(id, decision)

    addToast({
      type: 'info',
      message: `${focusedImage.filename} → ${decision}`,
      duration: 3000,
    })

    // Auto-advance to next undecided photo.
    const updatedDecisions = { ...localDecisions, [id]: decision }
    const undecided = images.filter(img => !updatedDecisions[img.id])
    if (undecided.length === 0) {
      addToast({ type: 'success', message: 'All photos decided', duration: 3000 })
      onClose()
      return
    }
    // Find the nearest undecided going forward then backward.
    for (let step = 1; step < images.length; step++) {
      const nextIdx = (safeIdx + step) % images.length
      if (!updatedDecisions[images[nextIdx].id]) {
        setFocusedIdx(nextIdx)
        return
      }
    }
  }, [focusedImage, localDecisions, safeIdx, images, onDecide, onBulkDecide, addToast, onClose])

  // U / Cmd+Z — pops the app-global undo stack first; falls back to per-photo
  // undo of the focused photo if the stack is empty.
  const undoFocused = useCallback(async () => {
    if (onUndo) {
      const handled = await onUndo()
      if (handled) return
    }
    if (!focusedImage) return
    const id = focusedImage.id
    if (!localDecisions[id]) return
    onUndoImage?.(id)
    setLocalDecisions(prev => {
      const next = { ...prev }
      delete next[id]
      return next
    })
  }, [focusedImage, localDecisions, onUndo, onUndoImage])

  useHotkeys('k', () => decideFocused('keep'),   { enabled: true }, [decideFocused])
  useHotkeys('m', () => decideFocused('maybe'),  { enabled: true }, [decideFocused])
  useHotkeys('r', () => decideFocused('reject'), { enabled: true }, [decideFocused])
  useHotkeys('u',      undoFocused, { enabled: true }, [undoFocused])
  useHotkeys('meta+z', undoFocused, { enabled: true }, [undoFocused])
  useHotkeys('z', () => cycleZoom(), { enabled: true }, [cycleZoom])
  useHotkeys('escape', () => onClose(), { enabled: true }, [onClose])

  // ── Tile click ──────────────────────────────────────────────────────────────
  // Mirrors DetailView's fullscreen zoom: clicking a panel always cycles the
  // shared zoom level, anchored at the click point so every panel zooms into
  // the same relative region. Unfocused panels also become focused.
  const handlePanelClick = useCallback((img, idx, e) => {
    // Suppress the click that follows a pan-drag — mouseup already
    // handled the user's intent.
    if (lastWasDragRef.current) return
    if (idx !== safeIdx) setFocusedIdx(idx)
    const rect = e.currentTarget.getBoundingClientRect()
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    const y = Math.max(0, Math.min(1, (e.clientY - rect.top)  / rect.height))
    cycleZoom({ x, y })
  }, [safeIdx, cycleZoom])

  // ── Grid columns: 2-up or 3-up or 2×2 ─────────────────────────────────────
  // 2 photos → 2 columns; 3 photos → 3 columns; 4 photos → 2×2 grid.
  const gridStyle = useMemo(() => {
    const cols = images.length <= 2 ? 2 : images.length === 3 ? 3 : 2
    const rows = images.length === 4 ? 2 : 1
    return {
      gridTemplateColumns: `repeat(${cols}, 1fr)`,
      gridTemplateRows: rows === 2 ? 'repeat(2, 1fr)' : '1fr',
    }
  }, [images.length])

  return (
    <div className="fixed inset-0 z-50 bg-[#07080a] flex flex-col">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-5 py-2.5 border-b border-[rgba(255,255,255,0.06)] bg-[#161718] flex-shrink-0">
        <span className="text-sm font-medium text-[#f0f0f0]">Compare</span>
        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono text-[#9c9c9d] bg-[rgba(255,255,255,0.06)] border border-[rgba(255,255,255,0.08)]">
          {images.length} photos
        </span>

        <div className="flex-1" />

        {/* Zoom-level indicator — only visible when zoomed in */}
        {zoomOn && (
          <span className="px-2 py-0.5 rounded-md bg-[#1a1b1d] text-[#5BB8D4] text-[11px] font-mono border border-[rgba(91,184,212,0.40)] flex-shrink-0">
            {zoomScale}×
          </span>
        )}

        {/* × close button — Esc shortcut lives in the footer legend */}
        <button
          onClick={onClose}
          className={`${BTN_ICON} flex-shrink-0`}
          title="Close (Esc)"
          aria-label="Close"
        >
          <X size={18} />
        </button>
      </div>

      {/* ── Panels ──────────────────────────────────────────────────────── */}
      <div
        className="flex-1 min-h-0 grid gap-2 p-2"
        style={gridStyle}
      >
        {images.map((img, idx) => {
          const isFocused = idx === safeIdx
          const decision  = localDecisions[img.id] ?? null
          const ring = isFocused
            ? 'ring-1 ring-[#5BB8D4]'
            : 'ring-1 ring-[rgba(255,255,255,0.06)] hover:ring-[rgba(255,255,255,0.18)]'

          const panelCursor = zoomOn
            ? (isDragging ? 'cursor-grabbing' : 'cursor-grab')
            : (isFocused ? 'cursor-zoom-in' : 'cursor-pointer')
          return (
            <div
              key={img.id}
              onClick={(e) => handlePanelClick(img, idx, e)}
              className={`relative bg-[#101111] rounded-lg overflow-hidden flex flex-col ${panelCursor} transition-all ${ring} ${decision === 'reject' ? 'opacity-[0.55]' : ''}`}
            >
              {/* Preview area */}
              <div
                className="flex-1 min-h-0 bg-[#07080a] flex items-center justify-center overflow-hidden"
                onMouseDown={handlePanMouseDown}
                onWheel={handlePanWheel}
              >
                <img
                  src={`${API}/previews/${img.id}`}
                  alt={img.filename}
                  className="max-h-full max-w-full object-contain select-none"
                  style={transformStyle}
                  draggable={false}
                />
              </div>

              {/* Bottom overlay: filename + badges */}
              <div className="px-2 py-1.5 flex items-center gap-1.5 bg-[#101111] border-t border-[rgba(255,255,255,0.04)] flex-shrink-0">
                <ScoreBadge score={pickHeadlineScore(img, modelInfo)} />
                {decision && <DecisionBadge decision={decision} />}
                <span className="ml-auto text-[10px] font-mono text-[#9c9c9d] truncate" title={img.filename}>
                  {img.filename}
                </span>
                {isFocused && (
                  <span className="w-1.5 h-1.5 rounded-full bg-[#5BB8D4] flex-shrink-0" title="Focused" />
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Footer keymap ────────────────────────────────────────────────── */}
      <div className="flex items-center gap-4 px-5 py-2 border-t border-[rgba(255,255,255,0.06)] bg-[#101111] text-[10px] text-[#6a6b6c] flex-shrink-0">
        <Hint k="←/→">Navigate</Hint>
        <Hint k="K">{images.length > 1 ? 'Keep · Reject rest' : 'Keep'}</Hint>
        <Hint k="M">Maybe</Hint>
        <Hint k="R">Reject</Hint>
        <Hint k="Z">Sync zoom</Hint>
        {zoomOn && <span className="text-[#6a6b6c]">Drag or scroll to pan</span>}
        <Hint k="Esc">Close</Hint>
        {focusedImage && (
          <span className="ml-auto font-mono text-[#9c9c9d] truncate">{focusedImage.filename}</span>
        )}
      </div>

    </div>
  )
}

function Hint({ k, children }) {
  return (
    <span className="inline-flex items-center gap-1">
      <kbd className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 bg-gradient-to-b from-[#181818] to-[#0d0d0d] rounded-[3px] text-[10px] font-semibold text-[#cecece] border border-[rgba(255,255,255,0.06)] leading-none">
        {k}
      </kbd>
      <span>{children}</span>
    </span>
  )
}
