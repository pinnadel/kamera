// GroupPartnersOverlay — opened by clicking a group's partner chip in the
// Rejects/Maybes grid. Answers "which other members of this burst were culled
// differently?" and lets the user re-cull any of them (or the current photo)
// in place.
//
// Layout: SURVIVOR buckets first (Keeps, then Maybes), then a divider, then the
// current view's own bucket (which includes the clicked photo). Only non-empty
// sections render. Each member shows a thumbnail + a K/M/R re-cull row.
//
// Live behaviour: `members` is the group's live member list (from
// enrichedGroups via App.jsx), so a re-cull moves the photo into its new
// section on the next render — the overlay never goes stale. When no member
// differs from the current view's bucket anymore, there's nothing left to
// compare, so the overlay auto-closes.
//
// Close: X button, backdrop click, or ESC.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X, ChevronLeft, ChevronRight } from 'lucide-react'
import { API } from '../api'

// Section order + labels. Survivors first; the divider is drawn before the
// current view's bucket (see render).
const KIND_LABEL = { keep: 'Keeps', maybe: 'Maybes', reject: 'Rejects' }
const KIND_TONE = {
  keep:   '#7DB89A',
  maybe:  '#E8B84A',
  reject: '#C97B7B',
}

// Zoom tiers for the inspect layer — same model as DetailView's fullscreen so
// the click-to-cycle gesture is identical app-wide: 1× → 1.5× → 3× → exit.
const FS_ZOOM_SCALES = [1, 1.5, 3]

// InspectLayer — lightweight full-screen photo peek stacked ABOVE the overlay
// (z-[95], overlay is z-[80]). Opened by clicking a member thumbnail; shows one
// photo big on a dark backdrop. Click the photo to cycle zoom (last click
// exits), drag / two-finger scroll to pan when zoomed, ←/→ to step through the
// overlay's members in section order. Dismiss with the dark backdrop, the ×
// button, or ESC (which is caught here first so the overlay stays open).
//
// `order` is the flattened member list (survivor sections first, then the
// current bucket). `index`/`setIndex` drive which photo shows and the stepping.
// `onDecide(imageId, decision)` re-culls the inspected photo, then we
// cull-and-advance to the next member.
function InspectLayer({ order, index, setIndex, onDecide, onClose }) {
  const image = order[index]
  const [zoomLevel, setZoomLevel] = useState(0)         // 0|1|2 → 1×|1.5×|3×
  const [zoomOrigin, setZoomOrigin] = useState({ x: 0.5, y: 0.5 })
  const [isDragging, setIsDragging] = useState(false)
  const zoomScale = FS_ZOOM_SCALES[zoomLevel]
  const dragRef = useRef(null)
  const lastWasDragRef = useRef(false)
  // After a re-cull, `order` reshuffles (the photo changed section). We record
  // the id we want to land on next, then re-point to it once the new order
  // arrives — so "advance" is stable regardless of how the list reorders.
  const advanceToIdRef = useRef(null)

  // Reset zoom whenever the inspected photo changes (fresh 1× on every step).
  useEffect(() => {
    setZoomLevel(0); setZoomOrigin({ x: 0.5, y: 0.5 })
  }, [image?.id])

  // Cull-and-advance: on decision, remember the NEXT member's id (before the
  // re-cull), fire the decision, and let the effect below re-point to it once
  // `order` reflects the change. Falls back to the previous member, then to
  // closing when the inspected photo was the last one.
  const decideAndAdvance = useCallback((decision) => {
    if (!image || image.decision === decision) return
    const nextId = order[index + 1]?.id ?? order[index - 1]?.id ?? null
    advanceToIdRef.current = nextId
    onDecide(image.id, decision)
  }, [image, order, index, onDecide])

  // Resolve a pending advance once `order` updates after the re-cull.
  useEffect(() => {
    const target = advanceToIdRef.current
    if (target == null) return
    advanceToIdRef.current = null
    const nextIdx = order.findIndex(m => m.id === target)
    if (nextIdx >= 0) setIndex(nextIdx)
    else onClose()   // nothing left to advance to
  }, [order, setIndex, onClose])

  const cycleZoom = useCallback((origin) => {
    if (origin) setZoomOrigin(origin)
    setZoomLevel(z => {
      const next = z + 1
      if (next >= FS_ZOOM_SCALES.length) { onClose(); return 0 }
      return next
    })
  }, [onClose])

  // ESC closes inspect first (capture + stopPropagation so the overlay's own
  // ESC handler underneath doesn't also fire). ←/→ step members (only when not
  // zoomed, so arrows can't be confused with panning intent).
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); return }
      if (zoomLevel === 0 && e.key === 'ArrowLeft')  { e.stopPropagation(); setIndex(i => Math.max(0, i - 1)) }
      if (zoomLevel === 0 && e.key === 'ArrowRight') { e.stopPropagation(); setIndex(i => Math.min(order.length - 1, i + 1)) }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [onClose, setIndex, order.length, zoomLevel])

  const onPanDown = useCallback((e) => {
    if (zoomLevel === 0) return
    e.preventDefault(); e.stopPropagation()
    const rect = e.currentTarget.getBoundingClientRect()
    dragRef.current = {
      startX: e.clientX, startY: e.clientY,
      originAtStart: { ...zoomOrigin },
      containerW: rect.width, containerH: rect.height, moved: false,
    }
    document.body.style.cursor = 'grabbing'
  }, [zoomLevel, zoomOrigin])

  const onPanMove = useCallback((e) => {
    if (!dragRef.current || zoomLevel === 0) return
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
  }, [zoomLevel, zoomScale])

  const onPanUp = useCallback(() => {
    if (!dragRef.current) return
    lastWasDragRef.current = dragRef.current.moved
    dragRef.current = null
    setIsDragging(false)
    document.body.style.cursor = ''
    setTimeout(() => { lastWasDragRef.current = false }, 0)
  }, [])

  useEffect(() => {
    if (zoomLevel === 0) { document.body.style.cursor = ''; dragRef.current = null; setIsDragging(false); return }
    window.addEventListener('mousemove', onPanMove)
    window.addEventListener('mouseup', onPanUp)
    return () => {
      window.removeEventListener('mousemove', onPanMove)
      window.removeEventListener('mouseup', onPanUp)
    }
  }, [zoomLevel, onPanMove, onPanUp])

  const onWheel = useCallback((e) => {
    if (zoomLevel === 0) return
    e.preventDefault()
    const rect = e.currentTarget.getBoundingClientRect()
    const ox = zoomOrigin.x + e.deltaX / rect.width  / (1 - 1 / zoomScale)
    const oy = zoomOrigin.y + e.deltaY / rect.height / (1 - 1 / zoomScale)
    setZoomOrigin({ x: Math.max(0, Math.min(1, ox)), y: Math.max(0, Math.min(1, oy)) })
  }, [zoomLevel, zoomOrigin, zoomScale])

  if (!image) return null
  const hasPrev = index > 0
  const hasNext = index < order.length - 1

  return (
    <div
      className="fixed inset-0 z-[95] bg-[#07080a] flex items-center justify-center cursor-zoom-out"
      onClick={onClose}
    >
      <img
        src={`${API}/previews/${image.id}`}
        alt={image.filename}
        style={{
          transform: `scale(${zoomScale})`,
          transformOrigin: `${zoomOrigin.x * 100}% ${zoomOrigin.y * 100}%`,
          transition: isDragging ? 'none' : 'transform 0.18s ease-out',
        }}
        className={`max-h-screen max-w-screen object-contain select-none ${zoomLevel > 0 ? (isDragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-zoom-in'}`}
        draggable={false}
        onMouseDown={onPanDown}
        onWheel={onWheel}
        onClick={(e) => {
          e.stopPropagation()
          if (lastWasDragRef.current) return
          const rect = e.currentTarget.getBoundingClientRect()
          const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
          const y = Math.max(0, Math.min(1, (e.clientY - rect.top)  / rect.height))
          cycleZoom({ x, y })
        }}
      />

      {/* Bottom-center chrome: current decision + re-cull controls above the
          filename caption. Controls are interactive; the caption isn't. Hidden
          while zoomed so they don't obstruct panning. */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2">
        {zoomLevel === 0 && (
          <div
            className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg bg-[rgba(7,8,10,0.78)] backdrop-blur-sm"
            onClick={(e) => e.stopPropagation()}
          >
            {['keep', 'maybe', 'reject'].map(kind => (
              <DecisionButton
                key={kind}
                kind={kind}
                active={image.decision === kind}
                onClick={() => decideAndAdvance(kind)}
              />
            ))}
          </div>
        )}
        <div className="px-2.5 py-1 rounded-md bg-[rgba(7,8,10,0.70)] text-[#cecece] text-[11px] font-mono pointer-events-none select-none">
          {image.filename} · {index + 1}/{order.length}
        </div>
      </div>

      {/* Zoom indicator */}
      {zoomLevel > 0 && (
        <div className="absolute top-4 left-4 px-2 py-1 rounded-md bg-[rgba(7,8,10,0.70)] text-[#cecece] text-[11px] font-mono pointer-events-none select-none">
          {zoomScale}×
        </div>
      )}

      {/* Prev / next — hidden while zoomed (arrows pan instead). Stepping keeps
          you in inspect so you can rifle through the differently-culled set. */}
      {zoomLevel === 0 && hasPrev && (
        <button
          onClick={(e) => { e.stopPropagation(); setIndex(i => Math.max(0, i - 1)) }}
          className="absolute left-3 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-[rgba(7,8,10,0.70)] hover:opacity-70 text-[#f9f9f9] transition-opacity flex items-center justify-center"
          title="Previous (←)"
        ><ChevronLeft size={20} /></button>
      )}
      {zoomLevel === 0 && hasNext && (
        <button
          onClick={(e) => { e.stopPropagation(); setIndex(i => Math.min(order.length - 1, i + 1)) }}
          className="absolute right-3 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-[rgba(7,8,10,0.70)] hover:opacity-70 text-[#f9f9f9] transition-opacity flex items-center justify-center"
          title="Next (→)"
        ><ChevronRight size={20} /></button>
      )}

      {/* Close */}
      <button
        onClick={(e) => { e.stopPropagation(); onClose() }}
        className="absolute top-4 right-4 w-9 h-9 rounded-full bg-[rgba(7,8,10,0.70)] hover:opacity-70 text-[#f9f9f9] transition-opacity flex items-center justify-center"
        title="Close (Esc)"
      ><X size={16} /></button>
    </div>
  )
}

// Re-cull button — tinted to its target decision, dimmed unless it's the
// member's current decision.
function DecisionButton({ kind, active, onClick }) {
  const tone = KIND_TONE[kind]
  const label = { keep: 'Keep', maybe: 'Maybe', reject: 'Reject' }[kind]
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-opacity ${active ? '' : 'opacity-55 hover:opacity-90'}`}
      style={{
        color: tone,
        backgroundColor: active ? `${tone}22` : 'transparent',
        borderColor: active ? `${tone}80` : 'rgba(255,255,255,0.10)',
      }}
      aria-pressed={active}
    >
      {label}
    </button>
  )
}

function MemberRow({ image, onDecide, onInspect }) {
  return (
    <div className="flex items-center gap-3 py-2">
      {/* Thumbnail doubles as the inspect affordance — click to view full-screen. */}
      <button
        type="button"
        onClick={onInspect}
        className="w-20 h-16 flex-none bg-[#07080a] rounded-md overflow-hidden flex items-center justify-center cursor-zoom-in hover:ring-1 hover:ring-[rgba(255,255,255,0.25)] transition-shadow"
        title="Inspect full-screen"
      >
        <img
          src={`${API}/previews/${image.id}`}
          alt={image.filename}
          className="max-h-full max-w-full object-contain pointer-events-none"
        />
      </button>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-mono text-[#cecece] truncate" title={image.filename}>
          {image.filename}
        </p>
      </div>
      <div className="flex items-center gap-1.5 flex-none">
        {['keep', 'maybe', 'reject'].map(kind => (
          <DecisionButton
            key={kind}
            kind={kind}
            active={image.decision === kind}
            onClick={() => { if (image.decision !== kind) onDecide(image.id, kind) }}
          />
        ))}
      </div>
    </div>
  )
}

// SectionBulkBar — per-section "move this whole bucket at once" control, shown
// in a section header. Offers the target decisions that aren't this section's
// own kind (Keeps section → Maybe all / Reject all, etc.). Acts on the section's
// members in one call via onBulkDecide.
function SectionBulkBar({ sectionKind, imageIds, onBulkDecide }) {
  const targets = ['keep', 'maybe', 'reject'].filter(k => k !== sectionKind)
  return (
    <div className="flex items-center gap-1.5">
      {targets.map(kind => {
        const tone = KIND_TONE[kind]
        const label = { keep: 'Keep all', maybe: 'Maybe all', reject: 'Reject all' }[kind]
        return (
          <button
            key={kind}
            type="button"
            onClick={() => onBulkDecide(imageIds, kind)}
            className="px-2 py-0.5 rounded-md text-[11px] font-medium border opacity-70 hover:opacity-100 transition-opacity"
            style={{ color: tone, borderColor: 'rgba(255,255,255,0.10)' }}
            title={`Move all ${imageIds.length} ${KIND_LABEL[sectionKind]} to ${label.replace(' all', '')}`}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}

export function GroupPartnersOverlay({ members, viewDecision, onDecide, onBulkDecide, onClose }) {
  // Which member (if any) is open in the full-screen inspect layer, by its
  // index into `order` (below). null = inspect closed.
  const [inspectIdx, setInspectIdx] = useState(null)

  // ESC closes the overlay — but ONLY when the inspect layer isn't open (it
  // owns ESC while up, closing itself first). Capture phase + stopPropagation.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && inspectIdx == null) { e.stopPropagation(); onClose() }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose, inspectIdx])

  // Partition live members by decision. Undecided members (shouldn't normally
  // appear for a decided burst, but be safe) are ignored.
  const byKind = useMemo(() => {
    const b = { keep: [], maybe: [], reject: [] }
    for (const m of members) {
      if (m.decision === 'keep' || m.decision === 'maybe' || m.decision === 'reject') {
        b[m.decision].push(m)
      }
    }
    return b
  }, [members])

  // Survivor buckets to show ABOVE the divider — the buckets that differ from
  // the current view. Rejects view → Keeps + Maybes; Maybes view → Keeps.
  const survivorKinds = viewDecision === 'maybe' ? ['keep'] : ['keep', 'maybe']
  const differentlyCulled = survivorKinds.reduce((sum, k) => sum + byKind[k].length, 0)

  const survivorSections = survivorKinds.filter(k => byKind[k].length > 0)
  const currentSection = byKind[viewDecision] || []

  // Flattened member list in visual (section) order — survivors first, then the
  // current bucket. Drives the inspect layer's ←/→ stepping so it matches what
  // the user sees. Recomputed live, so a re-cull re-orders it too.
  const order = useMemo(
    () => [...survivorSections.flatMap(k => byKind[k]), ...currentSection],
    [survivorSections, byKind, currentSection],
  )

  // Open inspect on a specific member — resolve its index in `order` by id so
  // the flattened order and the clicked row always agree.
  const openInspect = useCallback((imageId) => {
    const idx = order.findIndex(m => m.id === imageId)
    if (idx >= 0) setInspectIdx(idx)
  }, [order])

  // Keep the inspect index valid as `order` changes under re-culls: clamp into
  // range, or close inspect if the list emptied.
  useEffect(() => {
    if (inspectIdx == null) return
    if (order.length === 0) { setInspectIdx(null); return }
    if (inspectIdx > order.length - 1) setInspectIdx(order.length - 1)
  }, [order.length, inspectIdx])

  // Auto-close once nothing is culled differently anymore (e.g. the user
  // re-culled every survivor into the current bucket). Runs after render so the
  // final re-cull is visible for a beat via the live `members` update.
  useEffect(() => {
    if (differentlyCulled === 0) onClose()
  }, [differentlyCulled, onClose])

  if (differentlyCulled === 0) return null

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-[80] flex items-center justify-center bg-[rgba(7,8,10,0.82)] backdrop-blur-sm"
        onClick={onClose}
      >
        <div
          className="w-[520px] max-w-[92vw] max-h-[82vh] flex flex-col bg-[#161718] rounded-xl border border-[rgba(255,255,255,0.08)] shadow-2xl overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-[rgba(255,255,255,0.06)] flex-none">
            <h2 className="text-sm font-semibold text-[#e8e8e8]">Group members</h2>
            <button
              type="button"
              onClick={onClose}
              className="p-1 rounded-md text-[#9c9c9d] hover:text-[#e8e8e8] hover:bg-[rgba(255,255,255,0.06)] transition-colors"
              aria-label="Close"
            >
              <X size={16} />
            </button>
          </div>

          <div className="px-4 py-2 overflow-y-auto">
            {survivorSections.map(kind => (
              <section key={kind} className="py-1">
                <div className="flex items-center justify-between pt-2 pb-1">
                  <h3 className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: KIND_TONE[kind] }}>
                    {KIND_LABEL[kind]} <span className="text-[#6b6b6c]">({byKind[kind].length})</span>
                  </h3>
                  {onBulkDecide && byKind[kind].length >= 2 && (
                    <SectionBulkBar
                      sectionKind={kind}
                      imageIds={byKind[kind].map(m => m.id)}
                      onBulkDecide={onBulkDecide}
                    />
                  )}
                </div>
                <div className="divide-y divide-[rgba(255,255,255,0.04)]">
                  {byKind[kind].map(m => (
                    <MemberRow key={m.id} image={m} onDecide={onDecide} onInspect={() => openInspect(m.id)} />
                  ))}
                </div>
              </section>
            ))}

            {currentSection.length > 0 && (
              <>
                <div className="my-2 border-t border-[rgba(255,255,255,0.10)]" />
                <section className="py-1">
                  <div className="flex items-center justify-between pt-1 pb-1">
                    <h3 className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: KIND_TONE[viewDecision] }}>
                      {KIND_LABEL[viewDecision]} <span className="text-[#6b6b6c]">({currentSection.length}) · this view</span>
                    </h3>
                    {onBulkDecide && currentSection.length >= 2 && (
                      <SectionBulkBar
                        sectionKind={viewDecision}
                        imageIds={currentSection.map(m => m.id)}
                        onBulkDecide={onBulkDecide}
                      />
                    )}
                  </div>
                  <div className="divide-y divide-[rgba(255,255,255,0.04)]">
                    {currentSection.map(m => (
                      <MemberRow key={m.id} image={m} onDecide={onDecide} onInspect={() => openInspect(m.id)} />
                    ))}
                  </div>
                </section>
              </>
            )}
          </div>
        </div>
      </div>

      {inspectIdx != null && (
        <InspectLayer
          order={order}
          index={inspectIdx}
          setIndex={setInspectIdx}
          onDecide={onDecide}
          onClose={() => setInspectIdx(null)}
        />
      )}
    </>,
    document.body,
  )
}
